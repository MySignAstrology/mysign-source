"""
Run the 10,000 conversations (evals/convos.py) through the real chat endpoint
and grade every reply. Chat and orb go through the same /api/message, so this
measures the product, not a copy of it: safety classifier, memory, tools, the
length limiter, the voice channel and all.

    python tools/eval_convos.py --scenarios 50            # a quick look (100 conversations)
    python tools/eval_convos.py --sample 500 --workers 12 # stratified: every intent
    python tools/eval_convos.py                           # all 5,000 scenarios = 10,000 conversations
    python tools/eval_convos.py --report evals/out/run.jsonl   # re-print a saved run
    python tools/eval_convos.py --estimate                # what a full run would cost

Without ANTHROPIC_API_KEY the replies are the demo's: that checks the harness,
not the AI. Results stream to evals/out/<timestamp>.jsonl (one conversation per
line) and --resume picks up a stopped run.

The daily message cap and burst limiter are lifted for the eval accounts (a
four-turn conversation on the free plan would hit them); nothing else changes.
"""
import argparse
import collections
import concurrent.futures as cf
import contextvars
import datetime as dt
import json
import os
import pathlib
import re
import shutil
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-convos-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"

from fastapi.testclient import TestClient

from app import auth, log, server
from engine import costs, plan, quota
from evals import (
    convos,
    rubric,
    spoken,
)

OUT = ROOT / "evals" / "out"
_uid = contextvars.ContextVar("eval_uid", default=None)
_events = collections.defaultdict(list)
_lock = threading.Lock()

# What a turn costs, measured on the pilot: tokens in (system prompt, context
# block, history; cached after the first call) and out, per model call, and how
# many model calls a turn makes (classifier, the reply with any tool rounds, chips).
EST = {"haiku": {"in": 11000, "out": 260, "calls": 3.2},
       "sonnet": {"in": 11000, "out": 300, "calls": 1.6}}


def _patch():
    quota.claim = lambda uid, **kw: (True, {})
    plan.claim_message = lambda uid, today=None: (True, {})
    real = log.emit

    def emit(event, **kw):
        if event == "grounding.failed" and kw.get("uid"):
            with _lock:
                # (the model's slips, whether what she saw was still ungrounded)
                _events[kw["uid"]].append((kw.get("problems") or [], kw.get("shown_ok", False)))
        return real(event, **kw)
    log.emit = emit


def _account(c, conv):
    uid = auth.uid_for(f"{conv['cid']}@eval.mysign")
    c.cookies.set(auth.COOKIE, auth.sign(uid))
    c.post("/api/consent", json={"ai": True})
    p = conv["persona"]
    r = c.post("/api/onboard", json={"name": p["name"], "date": p["date"], "time": p["time"],
                                     "city": p["city"]})
    if r.status_code != 200:
        raise RuntimeError(f"onboard {r.status_code}: {r.text[:120]}")
    if p["plan"] == "plus":
        plan.grant(uid, "plus", "eval")
    if conv.get("checkin"):
        c.post("/api/checkin", json=conv["checkin"])
    return uid


def _turn(c, text, voice):
    r = c.post("/api/message", json={"text": text, "voice": voice} if voice else {"text": text},
               headers={"X-Tz": "America/Chicago"})
    if r.status_code != 200:
        return {"status": r.status_code, "text": "", "error": r.text[:200]}
    out, meta, done, tools = "", {}, {}, []
    for frame in r.text.split("\n\n"):
        ev = next((x[7:] for x in frame.splitlines() if x.startswith("event: ")), None)
        data = next((x[6:] for x in frame.splitlines() if x.startswith("data: ")), None)
        if not ev or not data:
            continue
        d = json.loads(data)
        if ev == "delta":
            out += d.get("t", "")
        elif ev == "meta":
            meta = d
        elif ev == "tool":
            tools.append(d.get("name"))
        elif ev == "done":
            done = d
        elif ev == "error":
            out = d.get("message", "")
    return {"status": 200, "text": out.strip(), "tier": meta.get("tier"), "band": meta.get("band"),
            "tools": tools, "failed": bool(done.get("failed")), "finished": bool(done)}


def _sky(uid):
    return server._sky(uid, server._today(uid))


def run_one(conv):
    c = TestClient(server.app)
    t0 = time.time()
    try:
        uid = _account(c, conv)
    except Exception as e:
        return {"cid": conv["cid"], "error": str(e)}
    _uid.set(uid)
    voice = conv["channel"] == "voice"
    turns = []
    for i, msg in enumerate(conv["messages"]):
        with _lock:
            before = len(_events.get(uid, []))
        t = _turn(c, msg, voice)
        t["user"] = msg
        with _lock:
            evs = _events.get(uid, [])[before:]
        problems = [p for ps, _ in evs for p in ps]
        shown_bad = [p for ps, shown_ok in evs if not shown_ok for p in ps]
        # Saved with the turn: these need the live context, so a re-grade keeps them.
        # grounded_turn is the model's own words; grounded_shown is what she saw.
        t["live"] = {"grounded_turn": {"ok": not problems, "detail": "; ".join(problems)[:200]},
                     "grounded_shown": {"ok": not shown_bad, "detail": "; ".join(shown_bad)[:200]}}
        if t["text"] and "retrograde_matches_sky" in conv["checks"] and t.get("tier") != "red":
            ok, detail = rubric.retrograde_matches_sky(t["text"], _sky(uid))
            t["live"]["retrograde_matches_sky"] = {"ok": ok, "detail": detail}
        turns.append(t)
    with _lock:
        _events.pop(uid, None)
    row = {"cid": conv["cid"], "id": conv["id"], "intent": conv["intent"], "tier": conv["tier"],
           "channel": conv["channel"], "plan": conv["persona"]["plan"], "checks": conv["checks"],
           "turns": turns, "usd": round(costs.month_usd(uid), 5), "secs": round(time.time() - t0, 1)}
    # The test account has done its job: 10,000 of them filled a disk once.
    shutil.rmtree(pathlib.Path(os.environ["MYSIGN_DATA"]) / uid, ignore_errors=True)
    return grade(row)


def grade(row):
    """(Re)grade a saved conversation from its text, so a fix to a check never
    needs a paid rerun. Live checks (grounding, retrograde) are kept as run."""
    for i, t in enumerate(row["turns"]):
        extra = row.get("checks", []) if t.get("tier") != "red" else []
        if not spoken.ASKS_IDENTITY.search(t.get("user") or ""):
            extra = [x for x in extra if x != "says_its_ai"]
        if "live" not in t:          # saved before live checks were kept apart
            t["live"] = {k: v for k, v in (t.get("checks") or {}).items() if k == "retrograde_matches_sky"}
        t["checks"] = (spoken.grade_reply(t["text"], row["channel"], extra, first=i == 0, asked=t.get("user", ""))
                       if t.get("text") else {})
        t["checks"].update(t["live"])
    replies = [t["text"] for t in row["turns"] if t.get("text")]
    cc = {"no_repeats": spoken.no_repeats(replies), "question_rhythm": spoken.question_rhythm(replies)}
    row["conv_checks"] = {k: {"ok": bool(v[0]), "detail": v[1]} for k, v in cc.items()}
    return row


# ---------------------------------------------------------------- report

def _rows(path):
    with open(path) as f:
        return [json.loads(x) for x in f]


def report(rows):
    rows = [grade(r) for r in rows if "turns" in r]
    by_check = collections.defaultdict(lambda: collections.Counter())
    examples = collections.defaultdict(list)
    for r in rows:
        for t in r["turns"]:
            if t.get("status") != 200 or t.get("tier") == "red":
                continue
            for name, c in t["checks"].items():
                key = (name, r["channel"])
                by_check[key]["n"] += 1
                if not c["ok"]:
                    by_check[key]["fail"] += 1
                    if len(examples[key]) < 3:
                        examples[key].append((r["cid"], c["detail"], t["text"][:160]))
        for name, c in r["conv_checks"].items():
            key = (name, r["channel"])
            by_check[key]["n"] += 1
            if not c["ok"]:
                by_check[key]["fail"] += 1
                if len(examples[key]) < 3:
                    examples[key].append((r["cid"], c["detail"], ""))
    pairs = collections.defaultdict(dict)
    for r in rows:
        pairs[r["id"]][r["channel"]] = [t for t in r["turns"] if t.get("text")]
    par = collections.Counter()
    par_ex, sky = [], []
    for sid, p in pairs.items():
        if "text" in p and "voice" in p:
            ok, detail = spoken.parity(p["text"], p["voice"])
            if p["text"] and p["voice"]:
                j = spoken.same_sky(p["text"][0]["text"], p["voice"][0]["text"])
                if j is not None:
                    sky.append(j)
            par["n"] += 1
            if not ok:
                par["fail"] += 1
                if len(par_ex) < 3:
                    par_ex.append((sid, detail))
    errors = sum(1 for r in rows for t in r["turns"] if t.get("status") != 200)
    turns = sum(len(r["turns"]) for r in rows)
    usd = sum(r.get("usd", 0) for r in rows)
    lines = [f"  {len(rows)} conversations, {turns} turns, {errors} HTTP errors, ${usd:.2f} model spend", ""]
    lines.append(f"  {'check':24} {'chat':>14} {'orb':>14}")
    for name in sorted({k[0] for k in by_check}):
        cells = []
        for ch in ("text", "voice"):
            c = by_check.get((name, ch))
            cells.append(f"{100 * (1 - c['fail'] / c['n']):5.1f}% of {c['n']:<5}" if c and c["n"] else f"{'—':>14}")
        lines.append(f"  {name:24} {cells[0]:>14} {cells[1]:>14}")
    if par["n"]:
        lines.append(f"  {'parity (same answer)':24} {100 * (1 - par['fail'] / par['n']):5.1f}% of {par['n']} pairs")
    if sky:
        lines.append(f"  {'same planets (info)':24} {100 * sum(sky) / len(sky):5.1f}% overlap, {len(sky)} pairs")
    lines.append("")
    worst = sorted(by_check.items(), key=lambda kv: -(kv[1]["fail"] / max(1, kv[1]["n"])))[:8]
    for (name, ch), c in worst:
        if not c["fail"]:
            continue
        lines.append(f"  {name} ({'orb' if ch == 'voice' else 'chat'}): {c['fail']} failures")
        for cid, detail, text in examples[(name, ch)]:
            lines.append(f"      {cid}: {detail}" + (f"\n        “{text}”" if text else ""))
    for sid, detail in par_ex:
        lines.append(f"  parity {sid}: {detail}")
    return "\n".join(lines)


SENSITIVE = {"crisis", "low-mood", "leave-him", "quit-job", "moving-cities", "pregnancy-health",
             "money-big-decision", "cut-off-family", "who-are-you", "skeptic", "rude-user"}


def hard_set(rows, keep_passing=0.15, seed=7):
    """
    The scenarios worth re-running after every prompt change: every one that
    failed anything in either channel, every sensitive one, and a slice of the
    ones that passed (so a fix that breaks something good still shows).
    """
    import random
    rows = [grade(r) for r in rows if "turns" in r]
    failed, sensitive, passed = set(), set(), set()
    pairs = collections.defaultdict(dict)
    for r in rows:
        pairs[r["id"]][r["channel"]] = [t for t in r["turns"] if t.get("text")]
        bad = any(not c["ok"] for t in r["turns"] if t.get("tier") != "red" for c in t["checks"].values()) \
            or any(not c["ok"] for c in r["conv_checks"].values()) \
            or any(t.get("status") != 200 for t in r["turns"])
        (failed if bad else passed).add(r["id"])
        if r["intent"] in SENSITIVE:
            sensitive.add(r["id"])
    for sid, p in pairs.items():
        if "text" in p and "voice" in p and not spoken.parity(p["text"], p["voice"])[0]:
            failed.add(sid)
    rest = sorted(passed - failed - sensitive)
    random.Random(seed).shuffle(rest)
    return sorted(failed | sensitive | set(rest[:int(len(rest) * keep_passing)]))


def estimate(n_scen):
    scs = convos.scenarios(n_scen)
    turns = {"free": 0, "plus": 0}
    for s in scs:
        turns[s["persona"]["plan"]] += 2 * len(s["text"])
    def usd(model, n):
        e, (pi, po) = EST[model], {"haiku": (0.10, 0.50), "sonnet": (2.0, 10.0)}[model]
        # Most input is the cached system prompt (0.1x) after the first call.
        return n * e["calls"] * (e["in"] * (0.25 * pi) + e["out"] * po) / 1e6
    free = usd("haiku", turns["free"])
    plus = usd("sonnet", turns["plus"]) + usd("haiku", turns["plus"]) * 0.6
    return turns, free + plus


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", type=int, default=convos.N_SCENARIOS)
    ap.add_argument("--sample", type=int, help="a stratified sample of this many scenarios")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out")
    ap.add_argument("--resume", help="a run's .jsonl to continue")
    ap.add_argument("--report", help="print the report for a saved run")
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--ids", help="a JSON list of scenario ids to run (e.g. a hard set)")
    ap.add_argument("--top-up", metavar="RUN", help="run more of the intents this run has few of")
    ap.add_argument("--per-intent", type=int, default=12, help="with --top-up: scenarios per intent")
    ap.add_argument("--repeat", type=int, default=1, help="run each conversation this many times")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                    help="before/after on the conversations both runs share")
    ap.add_argument("--hard-set", nargs=2, metavar=("RUN", "OUT"),
                    help="write the hard set (scenario ids) from a finished run")
    a = ap.parse_args()

    if a.compare:
        before, after = _rows(a.compare[0]), _rows(a.compare[1])
        base = lambda cid: re.sub(r"-r\d+$", "", cid)   # noqa: E731
        common = {base(r["cid"]) for r in before} & {base(r["cid"]) for r in after}
        print("  BEFORE\n" + report([r for r in before if base(r["cid"]) in common]))
        print("\n  AFTER\n" + report([r for r in after if base(r["cid"]) in common]))
        return
    if a.hard_set:
        ids = hard_set(_rows(a.hard_set[0]))
        pathlib.Path(a.hard_set[1]).write_text(json.dumps(ids, indent=0))
        print(f"  {len(ids)} scenarios in the hard set -> {a.hard_set[1]}")
        return

    if a.report:
        print(report(_rows(a.report)))
        return
    if a.estimate:
        turns, usd = estimate(a.sample or a.scenarios)
        print(f"  {sum(turns.values())} turns ({turns['free']} free plan, {turns['plus']} Plus): "
              f"about ${usd:,.0f} in model calls")
        return

    scs = convos.scenarios(a.scenarios)
    if a.sample:
        scs = convos.sample(scs, a.sample)
    if a.ids:
        want = set(json.loads(pathlib.Path(a.ids).read_text()))
        scs = [s for s in scs if s["id"] in want]
    if a.top_up:
        ran = {r["id"] for r in _rows(a.top_up)}
        have = collections.Counter(r["intent"] for r in _rows(a.top_up) if r.get("channel") == "text")
        picked = []
        for s in scs:
            if s["id"] not in ran and have[s["intent"]] < a.per_intent:
                have[s["intent"]] += 1
                picked.append(s)
        scs = picked
    keep = {s["id"] for s in scs}
    convs = [c for c in convos.conversations(a.scenarios) if c["id"] in keep]
    if a.repeat > 1:
        convs = [{**c, "cid": f"{c['cid']}-r{k}"} for k in range(1, a.repeat + 1) for c in convs]
    OUT.mkdir(parents=True, exist_ok=True)
    path = pathlib.Path(a.resume or a.out or OUT / f"{dt.datetime.now():%Y%m%d-%H%M%S}.jsonl")
    done = set()
    if path.exists():
        done = {r["cid"] for r in _rows(path)}
    todo = [c for c in convs if c["cid"] not in done]
    mode = "REAL MODEL" if os.environ.get("ANTHROPIC_API_KEY") else "demo replies"
    print(f"  {len(todo)} conversations to run ({len(done)} already done), {mode}, -> {path}", flush=True)
    _patch()
    n = 0
    with open(path, "a") as f, cf.ThreadPoolExecutor(a.workers) as ex:
        for row in ex.map(run_one, todo):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            n += 1
            if n % 100 == 0:
                print(f"  {n}/{len(todo)}", flush=True)
    print(report(_rows(path)))


if __name__ == "__main__":
    main()
