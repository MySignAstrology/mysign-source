"""
Turn fourteen days of pilot metrics into the go / no-go read.

    python tools/pilot_report.py                 # every user on the roster
    python tools/pilot_report.py u1 u2 u3        # specific users

Item 58. Test 2 asks one question: when My Sign recalls something from week
one, does she light up or recoil? Latency and reply length are the proxies; the
verdict is written against the criteria in docs/pilot.md and this script says,
in its own output, that the transcripts decide and the numbers only point.
"""
import os
import pathlib
import statistics
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-report-"))

from app import auth
from engine.store import load

# Written down before the pilot, per item 59. Change them there, not here.
CRITERIA = {
    "callback_latency_ratio": 0.80,   # replies after a callback arrive at ≤80% of the baseline delay
    "callback_length_ratio": 1.15,    # ...and run ≥15% longer
    "edit_distance_week2": 0.15,      # operator edits ≤15% of characters by week two
    "d7_retention": 0.60,             # ≥60% of participants still checking in at day 7
    "red_tier_missed": 0,             # every red-tier event reached a human
}


def _split(uids):
    cb, no_cb, edits = [], [], {}
    for uid in uids:
        for r in load(uid, "metrics", []) or []:
            wk = "w1" if r.get("sent_at", "")[:10] <= _cutoff(uids) else "w2"
            edits.setdefault(wk, []).append(r.get("edit_distance", 0.0))
            if r.get("reply"):
                (cb if r.get("has_callback") else no_cb).append(r["reply"])
    return cb, no_cb, edits


def _cutoff(uids):
    """The midpoint date across every participant's first message."""
    starts = sorted(r["sent_at"][:10] for u in uids
                    for r in (load(u, "metrics", []) or []) if r.get("sent_at"))
    if not starts:
        return "9999-12-31"
    import datetime as dt
    first = dt.date.fromisoformat(starts[0])
    return (first + dt.timedelta(days=7)).isoformat()


def _med(rows, key):
    vals = [r[key] for r in rows if key in r]
    return statistics.median(vals) if vals else None


def run(uids):
    cb, no_cb, edits = _split(uids)
    lines = ["  PILOT REPORT", ""]
    verdicts = {}

    # --- the question
    lines.append("  THE MEMORY QUESTION — does week-two recall land or recoil?")
    if cb and no_cb:
        lat = _med(cb, "latency_s") / max(_med(no_cb, "latency_s"), 1)
        ln = _med(cb, "words") / max(_med(no_cb, "words"), 1)
        verdicts["callback_latency_ratio"] = lat <= CRITERIA["callback_latency_ratio"]
        verdicts["callback_length_ratio"] = ln >= CRITERIA["callback_length_ratio"]
        lines += [f"    replies after a memory callback   n={len(cb)}",
                  f"    replies with no callback           n={len(no_cb)}",
                  f"    latency ratio  {lat:.2f}   (criterion ≤ {CRITERIA['callback_latency_ratio']})  "
                  f"{'ok' if verdicts['callback_latency_ratio'] else 'MISS'}",
                  f"    length ratio   {ln:.2f}   (criterion ≥ {CRITERIA['callback_length_ratio']})  "
                  f"{'ok' if verdicts['callback_length_ratio'] else 'MISS'}"]
    else:
        lines.append("    not enough data on one side — no verdict possible")

    # --- the voice
    lines += ["", "  THE VOICE — is the operator editing less by week two?"]
    for wk in ("w1", "w2"):
        if edits.get(wk):
            lines.append(f"    week {wk[-1]}  mean edit distance {statistics.mean(edits[wk]):.3f}  n={len(edits[wk])}")
    if edits.get("w2"):
        v = statistics.mean(edits["w2"]) <= CRITERIA["edit_distance_week2"]
        verdicts["edit_distance_week2"] = v
        lines.append(f"    criterion ≤ {CRITERIA['edit_distance_week2']}  {'ok' if v else 'MISS'}")

    # --- retention
    lines += ["", "  RETENTION"]
    active7 = 0
    for uid in uids:
        eps = load(uid, "episodes", []) or []
        if len({e['date'] for e in eps}) >= 5:          # ≥5 distinct days in the first week ≈ still here at day 7
            active7 += 1
    if uids:
        rate = active7 / len(uids)
        verdicts["d7_retention"] = rate >= CRITERIA["d7_retention"]
        lines.append(f"    still checking in at day 7   {active7}/{len(uids)} = {rate:.0%}"
                     f"   (criterion ≥ {CRITERIA['d7_retention']:.0%})  "
                     f"{'ok' if verdicts['d7_retention'] else 'MISS'}")

    # --- safety
    lines += ["", "  SAFETY"]
    red = sum(1 for u in uids for m in (load(u, "metrics", []) or []) if m.get("tier") == "red")
    lines.append(f"    red-tier events {red}. Confirm each one was seen by a person within five minutes — "
                 f"the console log says when; this script cannot.")

    # --- verdict
    lines += ["", "  VERDICT"]
    if not verdicts:
        lines.append("    no verdict — run it longer")
    else:
        passed = sum(verdicts.values())
        lines.append(f"    {passed}/{len(verdicts)} criteria met")
        lines.append("    GO" if passed == len(verdicts) else "    NO-GO on the numbers — read the transcripts before deciding")
    lines += ["",
              "  The numbers point. The transcripts decide. A short fast reply after a callback",
              "  can be delight or alarm, and only the text tells you which. Read every one."]
    return "\n".join(lines), verdicts


if __name__ == "__main__":
    ids = sys.argv[1:] or auth.all_users()
    text, _ = run(ids)
    print(text)
