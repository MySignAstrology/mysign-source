"""
Narrowing an unknown birth time from life events.

The ascendant crosses all twelve signs in a day and moves about a degree every
four minutes, so an unknown birth time costs you the houses, the chart ruler and
the angles — most of what makes a reading specific. Astrologers recover it by
rectification: comparing dated life events against transits to the angles, and
seeing which candidate time explains them.

This is a NARROWING AID, not a determination. Real rectification is skilled
manual work over many events. What this does is score candidate times and return
a window with an honest confidence, so someone who does not know their time gets
something better than noon and is told plainly how much better.

It will not beat asking your mother. The flow says so.
"""
import datetime as dt

import swisseph as swe

from engine.chart import angles_only, jd_ut
from engine.ephemeris import FLAGS

# Which angle a kind of event speaks to. These are the standard associations:
# the 4th/IC is home and roots, the 7th/DSC is partnership, the 10th/MC is
# career and public standing, the 1st/ASC is the self and how it is presented.
EVENT_ANGLES = {
    "moved":        ("IC", "home and where you live"),
    "relationship": ("DSC", "partnership"),
    "breakup":      ("DSC", "partnership"),
    "career":       ("MC", "work and public life"),
    "loss":         ("IC", "family and roots"),
    "child":        ("IC", "home and family"),
    "reinvention":  ("ASC", "how you present yourself"),
}

# Slow movers only. A Mars transit hits every angle every couple of years and
# proves nothing; Saturn or Pluto on an angle is a once-a-decade event.
MARKERS = [("Jupiter", swe.JUPITER), ("Saturn", swe.SATURN),
           ("Uranus", swe.URANUS), ("Neptune", swe.NEPTUNE), ("Pluto", swe.PLUTO)]

ASPECTS = [("conjunction", 0, 1.0), ("opposition", 180, 0.8),
           ("square", 90, 0.6)]
ORB = 3.0
STEP_MIN = 4          # the ascendant moves ~1° in four minutes
NULL_ROUNDS = 120     # shuffles used to build the chance distribution
BEAT_FLOOR = 0.99     # must beat this share of shuffled runs to say anything


def _angles(jd, lat, lon, houses=None):
    # The angles don't depend on the house system (`houses` is kept for
    # callers). Asking for Placidus raised inside the polar circles, and
    # /api/rectify answered every Tromsø birth with a 500.
    asc, mc = angles_only(jd, lat, lon)
    return {"ASC": asc, "DSC": (asc + 180) % 360, "MC": mc, "IC": (mc + 180) % 360}


def _sep(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def candidates(birth_date, lat, lon, tz, step_min=STEP_MIN, houses=None):
    """Every candidate birth time across the day, with its angles."""
    from zoneinfo import ZoneInfo
    y, m, d = (int(x) for x in birth_date.split("-"))
    out = []
    for minute in range(0, 24 * 60, step_min):
        local = dt.datetime(y, m, d, minute // 60, minute % 60, tzinfo=ZoneInfo(tz))
        utc = local.astimezone(dt.timezone.utc)
        jd = jd_ut(utc.year, utc.month, utc.day, utc.hour + utc.minute / 60)
        out.append({"local": f"{minute // 60:02d}:{minute % 60:02d}",
                    "minute": minute, "angles": _angles(jd, lat, lon, houses)})
    return out


def _transits_for(dates_and_kinds):
    out = []
    for date, kind in dates_and_kinds:
        y, m, d = (int(x) for x in date.split("-"))
        j = jd_ut(y, m, d, 12.0)
        out.append({"date": date, "kind": kind, "angle": EVENT_ANGLES[kind][0],
                    "planets": {n: swe.calc_ut(j, c, FLAGS)[0][0] for n, c in MARKERS}})
    return out


def _score_all(cands, transits):
    scored = []
    for cand in cands:
        total, hits = 0.0, []
        for t in transits:
            target = cand["angles"][t["angle"]]
            best = None
            for pname, plon in t["planets"].items():
                for aname, exact, weight in ASPECTS:
                    orb = abs(_sep(plon, target) - exact)
                    if orb <= ORB:
                        sc = weight * (1 - orb / ORB)
                        if best is None or sc > best[0]:
                            best = (sc, pname, aname, round(orb, 2))
            if best:
                total += best[0]
                hits.append({"date": t["date"], "kind": t["kind"], "planet": best[1],
                             "aspect": best[2], "angle": t["angle"], "orb": best[3]})
        scored.append({**cand, "score": round(total, 3), "hits": hits})
    return scored


def _null_max(cands, kinds, span, rng, rounds=NULL_ROUNDS):
    """
    What the best score looks like when the dates mean nothing.

    A z-score was not enough — the maximum of 360 overlapping candidates is not
    normally distributed, so a lucky peak still stood three-plus deviations
    clear. Shuffling the dates and re-taking the maximum gives the right null,
    because it has the same shape as the real statistic.
    """
    lo, hi = span
    out = []
    for _ in range(rounds):
        fake = [(_random_date(lo, hi, rng), k) for k in kinds]
        out.append(max(c["score"] for c in _score_all(cands, _transits_for(fake))))
    return sorted(out)


def _random_date(lo, hi, rng):
    d = lo + dt.timedelta(days=rng.randrange((hi - lo).days or 1))
    return d.isoformat()


def score(birth_date, lat, lon, tz, events, step_min=STEP_MIN, rng=None):
    """
    Score every candidate time against the events.

    An event scores when a slow planet was within orb of the angle that event
    speaks to, on the date it happened.
    """
    usable = [e for e in events if e.get("kind") in EVENT_ANGLES and e.get("date")]
    if not usable:
        return {"ok": False, "reason": "no usable events"}

    import random
    rng = rng or random.Random(0)

    cands = candidates(birth_date, lat, lon, tz, step_min)
    transits = _transits_for([(e["date"], e["kind"]) for e in usable])
    scored = _score_all(cands, transits)

    scored.sort(key=lambda c: -c["score"])
    top = scored[0]
    if top["score"] <= 0:
        return {"ok": False, "reason": "nothing lined up", "events_used": len(usable)}

    # The gate that was missing, and without which this is astrology-flavoured
    # noise. With five slow planets, three aspects and a three-degree orb, some
    # planet is near some angle for almost any candidate — so the best of 360
    # candidates always looks impressive. Measured: eight of eight random event
    # sets produced a confident nine-minute window, and a z-score against the
    # candidate spread still passed most of them.
    #
    # The real null is "these same events on meaningless dates". Shuffle, take
    # the maximum, repeat — then require the true maximum to beat almost all of
    # it.
    dates = sorted(dt.date.fromisoformat(e["date"]) for e in usable)
    span = (dates[0] - dt.timedelta(days=365), dates[-1] + dt.timedelta(days=365))
    null = _null_max(cands, [e["kind"] for e in usable], span, rng)
    beat = sum(1 for n in null if top["score"] > n) / len(null)

    # A window, not a point. Everything within 80% of the best score.
    cut = top["score"] * 0.8
    near = sorted(c["minute"] for c in scored if c["score"] >= cut)
    window = _widest_run(near, step_min)

    span_h = (window[1] - window[0]) / 60

    if beat < BEAT_FLOOR:
        return {"ok": False, "reason": "no better than chance",
                "events_used": len(usable), "beats_chance": round(beat, 3)}

    if beat >= 0.995 and len(usable) >= 4 and span_h <= 3:
        confidence = "worth trying"
    else:
        confidence = "rough"

    return {
        "ok": True,
        "best": top["local"],
        "window": [f"{window[0] // 60:02d}:{window[0] % 60:02d}",
                   f"{window[1] // 60:02d}:{window[1] % 60:02d}"],
        "window_hours": round(span_h, 1),
        "confidence": confidence,
        "events_used": len(usable),
        "events_matched": len(top["hits"]),
        "hits": top["hits"],
        "beats_chance": round(beat, 3),
    }


def _widest_run(minutes, step):
    """The longest unbroken stretch of high-scoring candidates."""
    if not minutes:
        return (0, 0)
    best = run_start = minutes[0]
    best_end = prev = minutes[0]
    for m in minutes[1:]:
        if m - prev > step:
            if prev - run_start > best_end - best:
                best, best_end = run_start, prev
            run_start = m
        prev = m
    if prev - run_start > best_end - best:
        best, best_end = run_start, prev
    return (best, best_end)
