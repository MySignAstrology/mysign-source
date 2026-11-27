"""
Measure the voice, repeatably.

    python tools/eval_voice.py            # against whatever mode is configured
    python tools/eval_voice.py --json     # machine-readable

Until now the voice has only ever been judged by a person reading it, which does
not scale and cannot gate a prompt change. This scores every scenario against
mechanical criteria — no model judging another model.

Without ANTHROPIC_API_KEY it scores the demo replies, which validates the
harness. With one, the numbers are about the real product.
"""
import argparse
import json
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-eval-"))

from engine import context, safety, voice
from engine.chart import natal
from engine.store import save
from evals import rubric as R
from evals.cases import CASES, EVAL_DAY

SYSTEM = voice.system_prompt()
from engine.agent import MODEL


def _seed(case, uid):
    u = case["user"]
    save(uid, "profile", {"name": u["name"], "birth_ut": list(u["birth"]),
                          "tz": u["tz"], "people": u["people"],
                          "birth_time_known": True})
    save(uid, "episodes", u["episodes"])
    save(uid, "mentions", {})
    return natal(*u["birth"])


def _sky(nat, day):
    from engine.chart import SIGNS, aspects, house_of, jd_ut, moon_phase, positions
    j = jd_ut(day.year, day.month, day.day, 17.0)
    tp = positions(j)
    return {"aspects_to_natal": aspects(tp, nat), "moon": moon_phase(j),
            "moon_sign": SIGNS[int(tp["Moon"]["lon"] // 30)],
            "active_houses": sorted({house_of(v["lon"], nat["house_cusps"])
                                     for v in tp.values()}),
            "upcoming": [],
            # As app/server.py sends it: without today's positions the model
            # can't tell "Mercury is retrograde" from her natal [Rx].
            "positions": {k: {"label": v["label"], "retrograde": v["retrograde"]}
                          for k, v in tp.items()}}


def _reply(block, message, verdict, uid, nat, day):
    """
    Returns (text, source, grounding). The real-model path runs the production
    agent loop, tools included — the eval used to call the model bare, so it
    measured a product nobody uses: the model could not look up her natal chart
    or search memory, and was then marked down for not knowing either.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        import app.server as srv
        return srv._demo_reply(message, verdict, block), "demo", block
    import anthropic

    from engine import agent, tools
    done = None
    for kind, payload in agent.run(anthropic.Anthropic(), SYSTEM, block, message,
                                   [], uid, nat, tools, model=MODEL, today=day):
        if kind == "done":
            done = payload
    used = "+".join(t["name"] for t in done["tools_used"])
    return done["text"], MODEL + (f" via {used}" if used else ""), done["grounding"]


def run(day=None):
    day = day or EVAL_DAY
    results = []
    for i, case in enumerate(CASES):
        uid = f"eval{i}"
        nat = _seed(case, uid)
        sky = _sky(nat, day)
        tier = safety.prefilter(case["message"])
        block, verdict = context.build(uid, nat, sky, case["checkin"],
                                       today=day, tier=tier, surface="chat",
                                       message=case["message"])

        if tier == "red":
            text, source, grounding = safety.CRISIS_REPLY, "canned", block
        else:
            text, source, grounding = _reply(block, case["message"], verdict, uid, nat, day)

        checks = {}
        for name in case["checks"]:
            if name == "grounded":
                ok, detail = R.grounded(text, grounding)
                checks[name] = {"ok": ok, "detail": detail}
            elif name == "clean_voice":
                ok, detail = R.clean_voice(text)
                checks[name] = {"ok": ok, "detail": detail}
            elif name == "within_length":
                # v2 may close on one question, and may go deeper when she
                # asks why or how; v1 holds the cap exactly.
                cap = verdict["max_sentences"]
                if voice.VOICE != "v1":
                    cap += 3 if re.search(r"\b(why|how|explain|deep)\b", case["message"], re.I) else 1
                ok = R.within_length(text, cap)
                checks[name] = {"ok": ok,
                                "detail": f"{len(R.sentences(text))} sentences, cap {cap}"}
            elif name == "is_exact_crisis_reply":
                checks[name] = {"ok": R.is_exact_crisis_reply(text, safety.CRISIS_REPLY),
                                "detail": ""}
            elif name == "cites_memory":
                checks[name] = {"ok": R.cites_memory(text, grounding), "detail": ""}
            elif name == "retrograde_matches_sky":
                ok, detail = R.retrograde_matches_sky(text, sky)
                checks[name] = {"ok": ok, "detail": detail}
            elif name == "names_a_mechanism":
                checks[name] = {"ok": R.names_a_mechanism(text, grounding), "detail": ""}
            else:
                checks[name] = {"ok": getattr(R, name)(text), "detail": ""}

        results.append({
            "id": case["id"], "tier_expected": case["tier"], "tier_seen": tier,
            "band": verdict["band"], "source": source,
            "reply": text, "checks": checks,
            "passed": all(c["ok"] for c in checks.values()),
        })
    return results


def report(results):
    lines, crit = [], {}
    for r in results:
        mark = "pass" if r["passed"] else "FAIL"
        lines.append(f"  {mark}  {r['id']:26} tier={r['tier_seen']:5} band={r['band']:6} "
                     f"({r['source']})")
        for name, c in r["checks"].items():
            crit.setdefault(name, [0, 0])
            crit[name][1] += 1
            crit[name][0] += bool(c["ok"])
            if not c["ok"]:
                lines.append(f"          {name}: {c['detail'] or 'failed'}")
    passed = sum(r["passed"] for r in results)
    lines.append("")
    lines.append(f"  {passed}/{len(results)} scenarios clean")
    lines.append("")
    lines.append("  by criterion:")
    for name, (ok, total) in sorted(crit.items()):
        flag = "" if ok == total else "   <-- "
        lines.append(f"    {name:22} {ok}/{total}{flag}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = run()
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        mode = "REAL MODEL" if os.environ.get("ANTHROPIC_API_KEY") else "demo mode"
        print(f"\n  voice eval — {mode}\n")
        print(report(res))
        print()
    sys.exit(0 if all(r["passed"] for r in res) else 1)
