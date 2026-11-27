"""
Exercise salience + pattern verification against real computed sky.

    python tools/engine_check.py [days]

A script, not a pytest module — it prints a report and takes an argument, which
is why it lives in tools/ rather than tests/. Collecting it as a test broke the
suite.
"""
import datetime as dt
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MYSIGN_DATA", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

from engine import memory as M
from engine.chart import SIGNS, aspects, house_of, jd_ut, moon_phase, natal, positions
from engine.salience import aspect_key, score_day
from engine.store import load, log_mention, save

USER = "maya"
NAT = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)
START = dt.date(2026, 4, 25)
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 30

# ---- build 30 days of real sky ------------------------------------------
days = []
for i in range(DAYS):
    d = START + dt.timedelta(days=i)
    j = jd_ut(d.year, d.month, d.day, 17.0)
    tpos = positions(j)
    asps = aspects(tpos, NAT)
    mp = moon_phase(j)
    days.append({
        "date": d.isoformat(),
        "moon_phase": mp["phase"],
        "moon_sign": SIGNS[int(tpos["Moon"]["lon"] // 30)],
        "aspect_keys": [aspect_key(a) for a in asps],
        "active_houses": sorted({house_of(v["lon"], NAT["house_cusps"])
                                 for k, v in tpos.items() if k in ("Sun","Venus","Mars","Jupiter")}),
        "_aspects": asps,
    })

# ---- synthesize episodes with ONE planted rule + noise -------------------
rng = random.Random(7)
episodes = []
for day in days:
    planted = day["moon_sign"] == "Aquarius"          # her natal Moon sign / 12th house
    mood = rng.randint(2, 4) if planted else rng.randint(4, 9)
    if rng.random() < 0.15:                            # 15% noise, breaks the rule
        mood = rng.randint(5, 8) if planted else rng.randint(2, 4)
    episodes.append({
        "date": day["date"], "mood": mood, "summary": "synthetic",
        "her_words": [], "people": [], "topics": ["theo"] if rng.random() < .3 else [],
        "decision": None, "tier": "green",
        "sky_state": {k: v for k, v in day.items() if not k.startswith("_")},
    })
save(USER, "episodes", episodes)

print(f"{DAYS} days of real sky, {sum(1 for d in days if d['moon_sign']=='Aquarius')} with Moon in Aquarius\n")

# ---- TEST A: does the verifier find the true pattern and reject decoys? --
print("=" * 68)
print("PATTERN VERIFICATION  (model proposes, code counts)")
print("=" * 68)

cases = [
    ("TRUE   planted rule",
     M.Proposal(claim="Your mood drops when the Moon is in Aquarius.",
                condition=M.Condition(kind="moon_sign", value="Aquarius", window_days=0),
                outcome_kind="mood_below", outcome_value="5")),
    ("DECOY  plausible but false",
     M.Proposal(claim="You feel worse around the full moon.",
                condition=M.Condition(kind="moon_phase", value="full moon", window_days=0),
                outcome_kind="mood_below", outcome_value="5")),
    ("DECOY  true but no lift over base rate",
     M.Proposal(claim="Your mood drops on days the Moon is in a sign.",
                condition=M.Condition(kind="moon_phase", value="", window_days=0),
                outcome_kind="mood_below", outcome_value="5")),
    ("DECOY  too little support",
     M.Proposal(claim="You feel worse on Tuesdays.",
                condition=M.Condition(kind="weekday", value="Tuesday", window_days=0),
                outcome_kind="mood_below", outcome_value="3")),
]
for label, p in cases:
    v = M.verify(USER, p, n_tests=len(cases))
    if v:
        print(f"  {label:34} KEPT     {v['support']:14} lift {v['lift']:.2f}  [{v['confidence']}]")
        print(f"  {'':34} -> \"{v['claim']} {v['support']}.\"")
    else:
        print(f"  {label:34} REJECTED")

# ---- TEST B: novelty decay ----------------------------------------------
print("\n" + "=" * 68)
print("SALIENCE OVER 30 DAYS  (does a 6-week transit shut up?)")
print("=" * 68)
save(USER, "mentions", {})
bands, pluto_trace = [], []
for day in days:
    d = dt.date.fromisoformat(day["date"])
    mlog = load(USER, "mentions", {})
    verdict = score_day({"aspects_to_natal": day["_aspects"], "upcoming": []},
                        mention_log=mlog, today=d)
    bands.append(verdict["band"])
    pk = "Pluto-conjunction-Neptune"
    from engine.salience import score_aspect
    pa = next((a for a in day["_aspects"] if aspect_key(a) == pk), None)
    sc = score_aspect(pa, mlog, d) if pa else 0.0
    pluto_trace.append((day["date"], verdict["score"], verdict["band"], sc))
    if verdict["band"] in ("high", "medium"):
        log_mention(USER, day["date"], verdict["mention_keys"])

from collections import Counter

c = Counter(bands)
print(f"  band mix over {DAYS} days: " +
      "  ".join(f"{k}={c.get(k,0)} ({c.get(k,0)*100//DAYS}%)" for k in ("high","medium","low")))
print("\n  Pluto conj natal Neptune — in orb the whole month. Score by day:")
for date, tot, band, sc in pluto_trace[:14]:
    bar = "#" * int(sc / 3)
    print(f"    {date}  day={tot:5.1f} {band:6}  pluto={sc:5.1f} {bar}")
print("    ...")
