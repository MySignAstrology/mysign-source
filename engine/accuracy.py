"""
The evidence behind "accurate", computed rather than asserted.

The accuracy page and the model's briefing both read from here, so neither can
claim something the engine is not doing:

  verify_against_nasa()   reruns the comparison with NASA JPL Horizons on the
                          server that answers her, not on a laptop once
  record_reply()          scores every live AI reply for invented placements
  live_stats()            the running count the page shows
  methodology()           which astrology, stated: zodiac, houses, orbs

Nothing here holds anything she wrote. The failure log keeps which claim was
unsupported — "Moon in Scorpio" — and when, never the reply or the user id.
"""
import datetime as dt
import functools
import json
import pathlib

import swisseph as swe

from engine import ephemeris, grounding, tradition
from engine.chart import ASPECTS
from engine.store import counter, incr, load, save

REFERENCE = pathlib.Path(__file__).resolve().parent / "reference" / "horizons.json"
ECLIPSES = pathlib.Path(__file__).resolve().parent / "reference" / "eclipses.json"
CLAIM_ARCSEC = 1.0          # what the page promises; tests/test_accuracy.py holds us to it
GLOBAL = "_accuracy"        # store partition for site-wide counters, beside "_auth"
KEEP_FAILURES = 200
# Which grounding checker the live count was scored by. Version 1 let a transit
# line vouch for a natal claim and never looked at rising signs or aspects, so
# it called a wrong Sun sign grounded (audit HIGH-3). Its replies cannot be
# re-scored — the context each one was checked against is not kept — so the
# page counts afresh under the stricter checker and says from when, rather than
# carrying a number the new rules would not have given.
CHECKER = 2
COUNTER = "grounding-v2"
LEGACY_COUNTER = "grounding"

_IDS = {"Sun": swe.SUN, "Moon": swe.MOON, "Mercury": swe.MERCURY, "Venus": swe.VENUS,
        "Mars": swe.MARS, "Jupiter": swe.JUPITER, "Saturn": swe.SATURN,
        "Uranus": swe.URANUS, "Neptune": swe.NEPTUNE, "Pluto": swe.PLUTO,
        "Chiron": swe.CHIRON}


@functools.lru_cache(maxsize=1)
def verify_against_nasa():
    """Our position for every body at every reference instant, against NASA's.
    264 calculations — milliseconds — so it is rerun on each cold start rather
    than trusted from a build log."""
    ref = json.loads(REFERENCE.read_text())
    per_body, n = {}, 0
    for row in ref["instants"]:
        for name, nasa in row["lon"].items():
            ours = swe.calc_ut(row["jd_ut"], _IDS[name], ephemeris.FLAGS)[0][0]
            err = abs((ours - nasa + 180) % 360 - 180) * 3600
            per_body[name] = max(per_body.get(name, 0.0), err)
            n += 1
    years = [int(r["utc"][:4]) for r in ref["instants"]]
    worst = max(per_body.values())
    return {"reference": ref["source"], "retrieved": ref["retrieved"],
            "comparisons": n, "instants": len(ref["instants"]),
            "years": [min(years), max(years)],
            "per_body": {k: round(v, 2) for k, v in per_body.items()},
            "max_arcsec": round(worst, 2),
            "claim_arcsec": CLAIM_ARCSEC, "passes": worst < CLAIM_ARCSEC}


@functools.lru_cache(maxsize=1)
def verify_eclipses():
    """Each NASA catalogue eclipse, found by our engine from ten days before,
    and how many seconds apart the two times of greatest eclipse are."""
    import datetime as _dt
    ref = json.loads(ECLIPSES.read_text())
    offsets = []
    for kind, finder in (("solar", lambda j: swe.sol_eclipse_when_glob(j, ephemeris.BASE, 0, False)),
                         ("lunar", lambda j: swe.lun_eclipse_when(j, ephemeris.BASE, 0, False))):
        for e in ref[kind]:
            t = _dt.datetime.fromisoformat(e["ut"])
            nasa = swe.julday(t.year, t.month, t.day, t.hour + t.minute / 60 + t.second / 3600)
            _flag, tret = finder(nasa - 10)
            offsets.append(abs(tret[0] - nasa) * 86400)
    return {"reference": ref["source"], "eclipses": len(offsets),
            "max_seconds": round(max(offsets), 1),
            "years": [int(ref["solar"][0]["ut"][:4]), int(ref["lunar"][-1]["ut"][:4])]}


def record_reply(text, context, surface="chat", withheld=False):
    """Score one live AI reply — the model's own words — and count it.
    `withheld`: the chat's grounding guard (engine/guard.py) held the failing
    sentence back, so she never saw it. It still counts against the model."""
    ok, problems = grounding.check(text, context)
    record_outcome(ok, problems, surface, withheld=withheld)
    return ok, problems


def record_outcome(ok, problems, surface, withheld=False):
    """Count a reply already checked elsewhere. `withheld` is for surfaces that
    can still drop a failing reply before she sees it (the compatibility
    reading falls back to its itemised report)."""
    if load(GLOBAL, "grounding_since", None) is None:
        save(GLOBAL, "grounding_since", dt.date.today().isoformat())
    incr(GLOBAL, COUNTER, "checked")
    if ok:
        incr(GLOBAL, COUNTER, "grounded")
        return
    if withheld:
        incr(GLOBAL, COUNTER, "withheld")
    fails = load(GLOBAL, "grounding_failures", [])[-(KEEP_FAILURES - 1):]
    fails.append({"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                  "surface": surface, "withheld": withheld, "checker": CHECKER,
                  "problems": list(problems)[:5]})
    save(GLOBAL, "grounding_failures", fails)


def live_stats():
    checked = counter(GLOBAL, COUNTER, "checked")
    grounded = counter(GLOBAL, COUNTER, "grounded")
    withheld = counter(GLOBAL, COUNTER, "withheld")
    shown_bad = checked - grounded - withheld
    old_checked = counter(GLOBAL, LEGACY_COUNTER, "checked")
    return {"replies_checked": checked, "replies_grounded": grounded,
            "replies_withheld": withheld,
            # What she could actually have seen: withheld replies never reached her.
            "shown_ungrounded": shown_bad,
            "grounded_pct": round(100 * grounded / checked, 2) if checked else None,
            "checker": CHECKER, "since": load(GLOBAL, "grounding_since", None),
            # Kept for the record, never shown as the headline: scored by the
            # looser checker, so it overstates.
            "legacy": ({"checker": 1, "replies_checked": old_checked,
                        "replies_grounded": counter(GLOBAL, LEGACY_COUNTER, "grounded")}
                       if old_checked else None)}


def methodology(houses=None, rulers=None):
    t = tradition.describe(houses, rulers)
    return {"zodiac": "tropical — 0° Aries is the March equinox",
            "houses": t["houses_label"], "rulerships": t["rulerships"],
            "aspects": [{"name": n, "angle": a, "orb": o} for n, a, o in ASPECTS],
            "bodies": list(_IDS) + ["North Node (true)", "Ascendant", "Midheaven"]}


def report(places=None):
    """Everything the public page shows, in one payload. `places` is the size
    of the birthplace index, which lives in the app rather than the engine."""
    return {"ephemeris": ephemeris.SOURCE, "time_zones": ephemeris.time_zone_rules(),
            "nasa": verify_against_nasa(), "eclipses": verify_eclipses(),
            "live": live_stats(), "methodology": methodology(), "places": places,
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
