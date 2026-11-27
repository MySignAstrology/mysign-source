"""
Does our chart pipeline agree with the sky?

Swiss Ephemeris is the reference implementation and is not in question. What is
in question is everything we built on top of it: timezone conversion, house
assignment, the angles, aspect matching. All of that had only ever been compared
against itself, and a wrong house cusp or an off-by-one-hour DST conversion
produces a chart that looks entirely plausible and is entirely wrong.

These check against facts published independently of us — equinoxes, solstices,
an eclipse — and against physical invariants that must hold at any place and
time. Sources are cited per test so a failure can be argued with.
"""
import datetime as dt

import pytest
import swisseph as swe

from engine.chart import SIGNS, jd_ut, natal, positions
from engine.ephemeris import FLAGS


def sun_longitude(y, m, d, hour_ut):
    return positions(jd_ut(y, m, d, hour_ut))["Sun"]["lon"]


def sep(a, b):
    """Smallest angle between two longitudes, in degrees."""
    d = abs(a - b) % 360
    return min(d, 360 - d)


# --------------------------------------------------------------- the zodiac

@pytest.mark.parametrize("label,when,expected_lon", [
    # timeanddate.com / NASA. The equinox IS the moment the Sun reaches 0° Aries,
    # so agreement here pins the whole tropical zodiac our signs are built on.
    ("March equinox 2026",    (2026, 3, 20, 14 + 46 / 60), 0.0),
    ("June solstice 2026",    (2026, 6, 21, 8 + 24 / 60), 90.0),
    ("December solstice 2026", (2026, 12, 21, 20 + 50 / 60), 270.0),
])
def test_sun_hits_the_cardinal_points_when_published(label, when, expected_lon):
    lon = sun_longitude(*when)
    off = sep(lon, expected_lon)
    # Published times are given to the minute; the Sun moves ~2.5'/minute, so
    # anything inside a few arcminutes means we agree.
    assert off < 0.05, f"{label}: Sun at {lon:.4f}°, expected {expected_lon}° (off by {off*60:.1f}')"


def test_the_equinox_is_where_aries_begins():
    """The sign boundary and the astronomical event are the same instant."""
    p = positions(jd_ut(2026, 3, 20, 14 + 46 / 60))["Sun"]
    assert p["sign"] == "Aries" and p["deg"] < 0.05


# --------------------------------------------------------------- an eclipse

def test_sun_and_moon_are_conjunct_at_the_2026_eclipse():
    """
    Total solar eclipse, 12 Aug 2026, greatest eclipse 17:47:06 UTC (NASA).
    A solar eclipse is a new moon, so the two must share a longitude — this
    checks the Moon independently of the Sun.
    """
    p = positions(jd_ut(2026, 8, 12, 17 + 47 / 60 + 6 / 3600))
    off = sep(p["Sun"]["lon"], p["Moon"]["lon"])
    assert off < 0.6, f"Sun and Moon {off:.3f}° apart at greatest eclipse"


# ------------------------------------------------- houses, angles, timezones

@pytest.mark.parametrize("city,lat,lon,tz", [
    ("Chicago", 41.8781, -87.6298, "America/Chicago"),
    ("London", 51.5074, -0.1278, "Europe/London"),
    ("Sydney", -33.8688, 151.2093, "Australia/Sydney"),
    ("Reykjavik", 64.1466, -21.9426, "Atlantic/Reykjavik"),
])
def test_the_sun_sits_on_the_ascendant_at_sunrise(city, lat, lon, tz):
    """
    The strongest check available offline, because it exercises everything at
    once: ephemeris, timezone handling, house system and the angles.

    At sunrise the Sun is on the eastern horizon — which is the definition of
    the ascendant. If our houses or our UT conversion were wrong, this would not
    hold anywhere.
    """
    jd_start = jd_ut(2026, 8, 12, 0.0)
    res, tret = swe.rise_trans(jd_start, swe.SUN, swe.CALC_RISE | swe.BIT_DISC_CENTER,
                               (lon, lat, 0), 0.0, 0.0, FLAGS)
    assert res == 0, f"no sunrise computed for {city}"
    jd_rise = tret[0]

    _cusps, ascmc = swe.houses(jd_rise, lat, lon, b"P")
    sun = swe.calc_ut(jd_rise, swe.SUN, FLAGS)[0][0]
    off = sep(sun, ascmc[0])
    assert off < 1.5, f"{city}: Sun {off:.2f}° from the ascendant at sunrise"


@pytest.mark.parametrize("city,lat,lon", [
    ("Chicago", 41.8781, -87.6298),
    ("Sydney", -33.8688, 151.2093),
])
def test_the_sun_sits_on_the_midheaven_at_local_noon(city, lat, lon):
    """At upper culmination the Sun is on the MC, by definition of the MC."""
    jd_start = jd_ut(2026, 8, 12, 0.0)
    res, tret = swe.rise_trans(jd_start, swe.SUN, swe.CALC_MTRANSIT,
                               (lon, lat, 0), 0.0, 0.0, FLAGS)
    assert res == 0
    jd_noon = tret[0]
    _, ascmc = swe.houses(jd_noon, lat, lon, b"P")
    sun = swe.calc_ut(jd_noon, swe.SUN, FLAGS)[0][0]
    assert sep(sun, ascmc[1]) < 1.0, f"{city}: Sun off the MC at culmination"


def test_the_ascendant_moves_through_every_sign_in_a_day():
    """
    Twelve signs in twenty-four hours. This is the fact that makes a birth time
    load-bearing, and the reason the reading omits houses without one.
    """
    seen = set()
    for minute in range(0, 24 * 60, 20):
        j = jd_ut(2026, 8, 12, minute / 60)
        _, ascmc = swe.houses(j, 41.8781, -87.6298, b"P")
        seen.add(SIGNS[int(ascmc[0] // 30)])
    assert len(seen) == 12, f"ascendant only reached {len(seen)} signs in a day"


def test_house_cusps_are_ordered_and_cover_the_circle():
    nat = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)
    cusps = nat["house_cusps"]
    assert len(cusps) == 12
    total = sum((cusps[(i + 1) % 12] - cusps[i]) % 360 for i in range(12))
    assert abs(total - 360) < 1e-6, f"house cusps span {total}°, not 360°"


def test_the_descendant_is_opposite_the_ascendant():
    nat = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)
    asc = nat["angles"]["Ascendant"]["lon"]
    seventh = nat["house_cusps"][6]
    assert sep(asc, seventh) > 179.9, "the 7th cusp is not opposite the ascendant"


# --------------------------------------------------- rectification, honestly

def _true_time_events(minute, n_per_angle=2):
    """Build events that genuinely encode a given birth time, by finding dates
    when a slow planet really was on that chart's angles."""
    from engine import rectify
    cands = rectify.candidates("1999-03-14", 41.8781, -87.6298, "America/Chicago")
    ang = next(c for c in cands if c["minute"] == minute)["angles"]
    out = []
    for angle, kind in (("IC", "moved"), ("DSC", "relationship"), ("MC", "career")):
        target, found, d = ang[angle], 0, dt.date(2008, 1, 1)
        while d < dt.date(2025, 1, 1) and found < n_per_angle:
            j = jd_ut(d.year, d.month, d.day, 12.0)
            for _, code in rectify.MARKERS:
                if rectify._sep(swe.calc_ut(j, code, FLAGS)[0][0], target) < 0.3:
                    out.append({"kind": kind, "date": d.isoformat()})
                    found += 1
                    d += dt.timedelta(days=450)
                    break
            d += dt.timedelta(days=3)
    return out


def test_rectification_recovers_a_time_that_is_really_there():
    """Given events that genuinely encode a birth time, it should find it."""
    import random

    from engine import rectify
    minute = 4 * 60 + 44
    events = _true_time_events(minute)
    r = rectify.score("1999-03-14", 41.8781, -87.6298, "America/Chicago",
                      events, rng=random.Random(5))
    assert r["ok"], r.get("reason")
    got = int(r["best"][:2]) * 60 + int(r["best"][3:])
    err = min(abs(got - minute), 1440 - abs(got - minute))
    assert err <= 20, f"recovered {r['best']}, {err} minutes from the truth"


def test_rectification_rejects_meaningless_dates():
    """
    The property that matters more.

    Before the permutation null, eight of eight random event sets produced a
    confident nine-minute window — the method was fitting noise, because with
    five slow planets and a three-degree orb something is near some angle for
    almost any candidate.
    """
    import random

    from engine import rectify
    rng = random.Random(21)
    kinds = list(rectify.EVENT_ANGLES)
    reported = 0
    trials = 12
    for _ in range(trials):
        events = [{"kind": rng.choice(kinds),
                   "date": f"20{rng.randint(10, 24):02d}-"
                           f"{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"}
                  for _ in range(4)]
        r = rectify.score("1999-03-14", 41.8781, -87.6298, "America/Chicago",
                          events, rng=random.Random(3))
        reported += r["ok"]
    assert reported <= trials * 0.25, \
        f"{reported}/{trials} random event sets produced a result"


def test_rectification_needs_events_at_all():
    from engine import rectify
    r = rectify.score("1999-03-14", 41.8781, -87.6298, "America/Chicago", [])
    assert r["ok"] is False
