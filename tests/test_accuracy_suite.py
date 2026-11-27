"""
Astronomical / astrological accuracy regression suite.

Every reference value here was obtained independently of this codebase and is
cited next to the test. Tolerances are explicit. Where the current code is
wrong, the test is marked xfail with the reason, so the suite passes today and
flips to XPASS the moment the bug is fixed.

Sources
  [JPL]  NASA/JPL Horizons API (ssd.jpl.nasa.gov/api/horizons.api), DE441,
         geocentric (500@399), QUANTITIES=31 = apparent ecliptic lon/lat of
         date, TIME_TYPE=UT. Fetched 2026-10-27.
  [JPLN] True (osculating) lunar node derived from Horizons geocentric Moon
         state vectors (ICRF), h = r x v, precessed IAU-1976 to date, rotated
         by mean obliquity, plus IAU nutation in longitude (4-term). Accuracy of
         the reduction itself ~0.2'.
  [AT]   astrotheme.com chart pages (which reproduce Astro-Databank data and
         Rodden ratings) for Asc/MC, Sun, Moon. astro.com itself is behind a
         bot check and was not fetched.
  [TZB]  IANA tzdb `backzone` file (github.com/eggert/tz) — the pre-1970
         history that the main tzdb dropped when it merged zones in 2022.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

import pytest
import swisseph as swe

from app import cities
from engine import compat, interpret, synastry
from engine.chart import SIGNS, aspects, fmt, house_of, jd_ut, moon_phase, natal, positions

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sep(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def signed(a, b):
    return (a - b + 540) % 360 - 180


def dms(deg, minutes, sign):
    return SIGNS.index(sign) * 30 + deg + minutes / 60


# ===================================================== 1. planet positions

# Julian days (UT) of the seven reference instants, 1950-2010.
REF_JD = (
    2433282.5,        # 1950-01-01 00:00 UT
    2437700.5,        # 1962-02-05 00:00 UT (solar eclipse day)
    2440423.345139,   # 1969-07-20 20:17 UT (Apollo 11 landing)
    2443371.75,       # 1977-08-16 06:00 UT
    2447025.5,        # 1987-08-18 00:00 UT
    2451401.960417,   # 1999-08-11 11:03 UT (total solar eclipse)
    2455197.5,        # 2010-01-01 00:00 UT
)

# [JPL] apparent geocentric ecliptic longitude of date, degrees.
JPL = {
    "Sun": (280.0048301, 315.7062562, 117.9107303, 143.2607802, 144.5369314, 138.3496683, 280.4511513),
    "Moon": (61.4154091, 315.6098314, 187.8737533, 159.5814702, 73.2962655, 138.3001370, 103.2443922),
    "Mercury": (299.4472523, 316.9342808, 115.8428989, 169.0418987, 142.1900420, 120.1610340, 288.9958744),
    "Venus": (316.9794775, 317.7665446, 75.0374088, 105.4749514, 143.0894886, 152.1560837, 277.8499553),
    "Mars": (182.2111967, 302.3595508, 242.7732832, 79.9893392, 146.9293852, 226.8458396, 138.8174871),
    "Jupiter": (306.5053249, 318.6133282, 180.7462243, 89.2563060, 29.7249555, 34.6809808, 326.3588373),
    "Saturn": (169.4374218, 303.8050320, 38.0979955, 140.7777955, 254.5351944, 46.8765466, 184.5065709),
    "Uranus": (92.6827205, 149.0475472, 180.6900011, 218.0985883, 262.8078908, 314.6691503, 353.0900719),
    "Neptune": (197.2660392, 223.4696807, 236.0229075, 253.3942455, 275.4706763, 302.5469758, 324.5820819),
    "Pluto": (137.7983813, 159.4244550, 173.0071871, 192.2287557, 217.4173483, 247.7536862, 273.3087995),
}

# [JPLN] true node, degrees.
TRUE_NODE = (12.5608, 138.0304, 352.4768, 196.3972, 3.1492, 132.9700, 291.0803)

# Measured max error against [JPL]: 0.92" (Moon), 0.73" (Pluto), <0.3" others.
# engine.chart.positions rounds to 1e-4 deg (0.36"), so 2" is a safe ceiling
# that still catches any flag/frame regression (a missing aberration or
# nutation correction is 10-20").
ARCSEC_TOL = 2.0


@pytest.mark.parametrize("body", sorted(JPL))
@pytest.mark.parametrize("i", range(len(REF_JD)))
def test_planet_longitude_matches_jpl_horizons(body, i):
    got = positions(REF_JD[i])[body]["lon"]
    err = abs(signed(got, JPL[body][i])) * 3600
    assert err < ARCSEC_TOL, f'{body} @ JD {REF_JD[i]}: off by {err:.2f}"'


@pytest.mark.parametrize("i", range(len(REF_JD)))
def test_true_node_matches_osculating_node_from_horizons(i):
    got = positions(REF_JD[i])["TrueNode"]["lon"]
    err = abs(signed(got, TRUE_NODE[i])) * 60
    # Measured 0.05'-0.21'; most of that is the reference reduction.
    assert err < 0.5, f"True node off by {err:.2f}'"


def test_eclipse_sanity_sun_moon_conjunct_1999():
    p = positions(REF_JD[5])
    assert sep(p["Sun"]["lon"], p["Moon"]["lon"]) < 0.1


# ============================================== 2. angles of known charts

def pipeline(query, y, m, d, hh, mm, houses=None):
    """Exactly what app.server.onboard does: city -> tz -> UT -> natal()."""
    hits = cities.search(query, 1)
    assert hits, f"{query!r} not found"
    c = cities.resolve(hits[0]["id"])
    local = dt.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(c["tz"]))
    u = local.astimezone(dt.timezone.utc)
    return natal(u.year, u.month, u.day, round(u.hour + u.minute / 60, 4),
                 c["lat"], c["lon"], houses=houses), local, c


# (query, local birth, [AT] Asc, MC, Sun, Moon, Rodden rating)
FAMOUS = [
    ("Honolulu", (1961, 8, 4, 19, 24), (18, 3, "Aquarius"), (28, 53, "Scorpio"),
     (12, 33, "Leo"), (3, 21, "Gemini"), "AA"),      # Barack Obama
    ("Bay City, MI", (1958, 8, 16, 7, 5), (8, 15, "Virgo"), (3, 45, "Gemini"),
     (23, 7, "Leo"), (11, 33, "Virgo"), "AA"),       # Madonna
    ("Los Angeles", (1975, 6, 4, 9, 9), (28, 53, "Cancer"), (17, 52, "Aries"),
     (13, 25, "Gemini"), (13, 5, "Aries"), "AA"),    # Angelina Jolie
    ("London", (1948, 11, 14, 21, 14), (5, 23, "Leo"), (13, 16, "Aries"),
     (22, 25, "Scorpio"), (0, 26, "Taurus"), "A"),   # Charles III
    ("Prayagraj", (1917, 11, 19, 23, 11), (20, 5, "Leo"), (17, 44, "Taurus"),
     (26, 50, "Scorpio"), (28, 18, "Capricorn"), "A"),  # Indira Gandhi (Allahabad)
    ("Liverpool", (1942, 6, 18, 14, 0), (25, 18, "Virgo"), (23, 44, "Gemini"),
     (26, 37, "Gemini"), (17, 26, "Leo"), "A"),      # Paul McCartney — UK double summer time
]


@pytest.mark.parametrize("q,local,asc,mc,sun,moon,rating", FAMOUS,
                         ids=[f[0] for f in FAMOUS])
def test_famous_chart_angles_through_full_pipeline(q, local, asc, mc, sun, moon, rating):
    n, _, _ = pipeline(q, *local)
    # Angles: 5' — astrotheme's coordinates are rounded to the arcminute and
    # GeoNames' city centroid differs from them by a few km; the MC moves ~1'
    # per 1.8 km of longitude. Measured: AA charts <=1', London/Liverpool 2-3'.
    for key, ref in (("Ascendant", asc), ("Midheaven", mc)):
        err = sep(n["angles"][key]["lon"], dms(*ref)) * 60
        assert err < 5, f"{key} off by {err:.1f}'"
    # Planets do not depend on coordinates: 1'.
    for key, ref in (("Sun", sun), ("Moon", moon)):
        err = sep(n["planets"][key]["lon"], dms(*ref)) * 60
        assert err < 1, f"{key} off by {err:.1f}'"


def test_obama_exact_coordinates_to_the_arcminute():
    """Literature coordinates 21N18 157W51, 19:24 HST = 05:24 UT next day."""
    n = natal(1961, 8, 5, 5.4, 21.3, -(157 + 51 / 60))
    assert sep(n["angles"]["Ascendant"]["lon"], dms(18, 3, "Aquarius")) * 60 < 1.5
    assert sep(n["angles"]["Midheaven"]["lon"], dms(28, 53, "Scorpio")) * 60 < 1.5


def test_onboard_endpoint_reproduces_obama(tmp_path):
    out = _onboard_subprocess(tmp_path, [
        {"date": "1961-08-04", "time": "19:24", "city": "Honolulu|Hawaii|US"}])[0]
    assert out["status"] == 200
    assert out["body"]["angles"]["Ascendant"].startswith("18°0")
    assert out["body"]["angles"]["Midheaven"].startswith("28°5")
    assert out["profile"]["birth_ut"][:4] == [1961, 8, 5, 5.4]


# ============================================== 3. time zones and DST

# (zone, local datetime, expected UTC offset in hours, citation)
TZ_FACTS = [
    ("America/Phoenix", (1967, 7, 1, 12), -6, "Arizona observed DST in 1967 only"),
    ("America/Phoenix", (1968, 7, 1, 12), -7, "...and not from 1968"),
    ("America/Indiana/Indianapolis", (1960, 7, 1, 12), -5, "tzdb: EST year-round 1958-2005"),
    ("America/Chicago", (1955, 7, 1, 12), -5, "CDT"),
    ("Europe/London", (1941, 1, 15, 12), 1, "BST kept through winter 1940-45"),
    ("Europe/London", (1944, 6, 1, 12), 2, "British Double Summer Time"),
    ("Europe/London", (1947, 5, 1, 12), 2, "BDST again in 1947"),
    ("Europe/London", (1969, 1, 15, 12), 1, "British Standard Time 1968-71"),
    ("Asia/Kolkata", (1943, 6, 1, 12), 6.5, "Indian war time +06:30"),
    ("Asia/Kolkata", (1960, 6, 1, 12), 5.5, "IST"),
    ("Asia/Kathmandu", (1980, 6, 1, 12), 5.5, "Nepal +05:30 until 1986"),
    ("Asia/Kathmandu", (1990, 6, 1, 12), 5.75, "Nepal +05:45 from 1986"),
    ("Australia/Adelaide", (1990, 1, 15, 12), 10.5, "ACDT"),
    ("Australia/Adelaide", (1990, 7, 15, 12), 9.5, "ACST"),
    ("Asia/Manila", (1943, 6, 1, 12), 9, "Japanese occupation"),
    ("America/Sao_Paulo", (1990, 1, 15, 12), -2, "Brazil summer time"),
    ("America/Sao_Paulo", (2020, 1, 15, 12), -3, "Brazil abolished DST 2019"),
    ("America/Mexico_City", (1950, 6, 1, 12), -5, "Mexico DST Feb-Jul 1950"),
    ("Africa/Lagos", (1950, 6, 1, 12), 1, "WAT since 1919"),
]


@pytest.mark.parametrize("zone,when,hours,why", TZ_FACTS,
                         ids=[f"{z}-{w[0]}-{w[1]:02d}" for z, w, _h, _y in TZ_FACTS])
def test_historical_utc_offsets(zone, when, hours, why):
    off = dt.datetime(*when, tzinfo=ZoneInfo(zone)).utcoffset()
    assert off == dt.timedelta(hours=hours), f"{zone} {when}: {off} ({why})"


# [TZB] Zones merged into another in tzdb 2022+ lost their own pre-1970 history.
# The app now ships tzdb compiled with backzone (engine/zoneinfo/, see
# engine/localtime.py); before that it read the tzdata wheel, which does not
# carry it. 3,068 of the 34,106 bundled cities (9%, ~311M people, 86 zones) sit
# in such zones: Malaysia, Netherlands, Sweden, Norway, Denmark, Ghana, most of
# East/Central Africa, Iceland, Montreal, Kuala Lumpur, ...
BACKZONE = [
    ("Atlantic/Reykjavik", (1960, 1, 15, 12), -1, "Iceland was -01 until 1968"),
    ("Europe/Oslo", (1960, 7, 1, 12), 2, "Norway DST 1959-1965"),
    ("Africa/Accra", (1952, 10, 1, 12), 0.5, "Ghana +00:30 Sep-Dec 1950-55"),
    ("Europe/Amsterdam", (1935, 6, 1, 12), 1 + 20 / 60 - 0.5 / 60, "NL summer +01:19:32"),
]


@pytest.mark.parametrize("zone,when,hours,why", BACKZONE, ids=[b[0] for b in BACKZONE])
def test_pre_1970_offsets_for_merged_zones(zone, when, hours, why):
    off = dt.datetime(*when, tzinfo=ZoneInfo(zone)).utcoffset()
    assert abs(off.total_seconds() / 3600 - hours) < 0.01, f"{zone} {when}: {off} ({why})"


def test_dst_gap_and_overlap_current_behaviour_is_fold0():
    """
    What onboard() does today (dt.datetime(..., tzinfo=ZoneInfo(tz)), fold=0):
      nonexistent 02:30 on spring-forward  -> read with the pre-gap offset
      ambiguous   01:30 on fall-back       -> the FIRST (daylight) occurrence
    Both silently. Pinned so a change is deliberate.
    """
    ny = ZoneInfo("America/New_York")
    gap = dt.datetime(2000, 4, 2, 2, 30, tzinfo=ny).astimezone(dt.timezone.utc)
    assert gap == dt.datetime(2000, 4, 2, 7, 30, tzinfo=dt.timezone.utc)
    amb = dt.datetime(2000, 10, 29, 1, 30, tzinfo=ny).astimezone(dt.timezone.utc)
    assert amb == dt.datetime(2000, 10, 29, 5, 30, tzinfo=dt.timezone.utc)
    other = dt.datetime(2000, 10, 29, 1, 30, fold=1, tzinfo=ny).astimezone(dt.timezone.utc)
    assert other - amb == dt.timedelta(hours=1)


def test_onboard_flags_ambiguous_local_time(tmp_path):
    out = _onboard_subprocess(tmp_path, [
        {"date": "2000-10-29", "time": "01:30", "city": "New York City|New York|US"}])[0]
    assert out["status"] == 200
    body = json.dumps(out["body"]).lower()
    assert "happened twice" in body or "ambiguous" in body


# ===================================== 4. retrograde, phases, boundaries, orbs

def test_mercury_retrograde_matches_horizons_stations():
    """[JPL] 6-hourly longitudes: Mercury stationed retrograde 2009-12-26
    12-18 UT and direct 2010-01-15 18:00-2010-01-16 00:00 UT."""
    def r(y, m, d, h):
        return positions(jd_ut(y, m, d, h))["Mercury"]["retrograde"]
    assert not r(2009, 12, 25, 12)
    assert r(2009, 12, 27, 0)
    assert r(2010, 1, 15, 0)
    assert not r(2010, 1, 16, 12)


def test_retrograde_flag_agrees_with_longitude_motion():
    for jd in REF_JD:
        a, b = positions(jd - 0.05), positions(jd + 0.05)
        for k in ("Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Pluto"):
            moving_back = signed(b[k]["lon"], a[k]["lon"]) < 0
            if abs(a[k]["speed"]) > 0.002:
                assert a[k]["retrograde"] == moving_back, (k, jd)


@pytest.mark.parametrize("when,label", [
    ((1999, 8, 11, 11 + 3 / 60), "new moon"),       # total solar eclipse, NASA
    ((2000, 1, 21, 4 + 44 / 60), "full moon"),      # total lunar eclipse, NASA
])
def test_moon_phase_label_at_eclipses(when, label):
    p = moon_phase(jd_ut(*when))
    assert p["phase"] == label
    assert p["illumination_pct"] in (0.0, 100.0)


def test_moon_phase_label_width_is_plus_minus_22_5_degrees():
    """Design pin: 'full moon' is any elongation 157.5-202.5 deg (~±1.8 days).
    A user reading "full moon" two days early is getting this convention."""
    p = moon_phase(jd_ut(2000, 1, 19, 12))     # ~1.7 days before full
    assert p["elongation_deg"] < 157.5 and p["phase"] == "waxing gibbous"


def test_get_lunations_finds_the_jan_2000_full_moon():
    from engine.tools import get_lunations
    out = get_lunations("u", {"house_cusps": [i * 30.0 for i in range(12)]},
                        "2000-01-15", 10)
    assert [(x["kind"], x["date"], x["sign"]) for x in out["lunations"]] == \
        [("full moon", "2000-01-21", "Leo")]


def test_sign_boundaries():
    assert fmt(29.9999)["sign"] == "Aries"
    assert fmt(30.0)["sign"] == "Taurus"
    assert fmt(359.9999)["sign"] == "Pisces"
    assert fmt(0.0)["sign"] == "Aries"
    assert compat._sign(360.0) == "Aries"


@pytest.mark.parametrize("lon", [59.9999, 14.995, 359.9999])
def test_label_never_shows_60_minutes(lon):
    f = fmt(lon)
    assert "60'" not in f["label"] and f["deg"] < 30


def test_aspects_wrap_through_0_aries():
    nat = {"planets": {"Venus": {"lon": 359.5}}, "angles": {}}
    tr = {"Mars": {"lon": 0.5, "retrograde": False}}
    a = aspects(tr, nat)
    assert a and a[0]["aspect"] == "conjunction" and a[0]["orb"] == 1.0
    # square across the wrap: 355 vs 85 = 90
    a = aspects({"Mars": {"lon": 85.0, "retrograde": False}},
                {"planets": {"Venus": {"lon": 355.0}}, "angles": {}})
    assert a[0]["aspect"] == "square" and a[0]["orb"] == 0.0


def test_transit_orb_edges_are_inclusive_at_3_degrees():
    nat = {"planets": {"Sun": {"lon": 10.0}}, "angles": {}}
    assert aspects({"Mars": {"lon": 13.0, "retrograde": False}}, nat)
    assert not aspects({"Mars": {"lon": 13.0001, "retrograde": False}}, nat)
    # 97.0 is 87 from 10 => square at orb 3.0 exactly
    assert aspects({"Mars": {"lon": 97.0, "retrograde": False}}, nat)[0]["aspect"] == "square"


def test_synastry_and_natal_aspects_wrap():
    a = {"planets": {"Sun": {"lon": 358.0}}, "angles": {}}
    b = {"planets": {"Moon": {"lon": 4.0}}, "angles": {}}
    x = synastry.cross_aspects(a, b)
    assert x[0]["aspect"] == "conjunction" and x[0]["orb"] == 6.0
    na = interpret.natal_aspects({"planets": {"Sun": {"lon": 358.0}, "Moon": {"lon": 178.5}},
                                  "angles": {}})
    assert na[0]["aspect"] == "opposition" and na[0]["orb"] == 0.5


def test_composite_midpoint_takes_the_short_arc_through_0_aries():
    assert compat._midpoint(350.0, 10.0) == pytest.approx(0.0)
    assert compat._midpoint(10.0, 350.0) == pytest.approx(0.0)
    assert compat._midpoint(340.0, 30.0) == pytest.approx(5.0)
    assert compat._midpoint(100.0, 200.0) == pytest.approx(150.0)


def test_house_of_handles_the_cusp_that_crosses_0_aries():
    cusps = [300.0 + 30 * i for i in range(12)]
    cusps = [c % 360 for c in cusps]              # 1st cusp 300, 3rd cusp 0
    assert house_of(330.0, cusps) == 2
    assert house_of(359.99, cusps) == 2
    assert house_of(0.0, cusps) == 3
    assert house_of(300.0, cusps) == 1             # on the cusp -> that house


def test_polar_birthplace_gets_a_chart():
    n = natal(1990, 1, 1, 12.0, 69.6496, 18.9560)       # Tromsø
    assert len(n["house_cusps"]) == 12


# ================================================ 5. unknown birth time

def test_moon_sign_flagged_uncertain_when_moon_changes_sign_in_the_ut_day():
    # First UT day of 1995 with a lunar ingress, found by scan.
    d = dt.date(1995, 1, 1)
    while True:
        a = int(positions(jd_ut(d.year, d.month, d.day, 0))["Moon"]["lon"] // 30)
        b = int(positions(jd_ut(d.year, d.month, d.day, 23.99))["Moon"]["lon"] // 30)
        if a != b:
            break
        d += dt.timedelta(days=1)
    certain, s0, s1 = interpret.moon_sign_certain(d.year, d.month, d.day)
    assert not certain and s0 != s1


def _local_day_moon_signs(y, m, d, tz):
    z = ZoneInfo(tz)
    out = set()
    for h in range(0, 24):
        u = dt.datetime(y, m, d, h, tzinfo=z).astimezone(dt.timezone.utc)
        out.add(SIGNS[int(positions(jd_ut(u.year, u.month, u.day, u.hour + u.minute / 60))
                          ["Moon"]["lon"] // 30)])
    u = dt.datetime(y, m, d, 23, 59, tzinfo=z).astimezone(dt.timezone.utc)
    out.add(SIGNS[int(positions(jd_ut(u.year, u.month, u.day, u.hour + u.minute / 60))
                      ["Moon"]["lon"] // 30)])
    return out


def test_moon_sign_uncertainty_uses_the_local_birth_day():
    assert _local_day_moon_signs(1995, 1, 16, "America/Los_Angeles") == {"Cancer", "Leo"}
    from engine.reading import build
    n = natal(1995, 1, 16, 20.0, 34.0522, -118.2437)      # noon PST default
    r = build(n, want_prose=False, time_known=False, birth_ymd=(1995, 1, 16))
    moon = next(c for c in r["cards"] if c["label"] == "Moon")
    assert moon["sub"] == "not certain"


def test_sun_sign_uncertainty_surfaced_on_ingress_day():
    # Sun entered Aries 1990-03-20 ~21:19 UT. Noon in Los Angeles = 20:00 UT ->
    # Pisces; anyone born after ~13:19 PST is an Aries.
    n = natal(1990, 3, 20, 20.0, 34.05, -118.24)
    assert n["planets"]["Sun"]["sign"] == "Pisces"
    a = interpret.analyze(n, time_known=False, birth_ymd=(1990, 3, 20))
    assert a.get("sun_sign_certain") is False


def test_unknown_time_hides_rising_houses_and_angle_aspects():
    n = natal(1995, 1, 3, 6.5, 22.57, 88.36)
    a = interpret.analyze(n, time_known=False, birth_ymd=(1995, 1, 3))
    assert a["big3"]["rising"] is None
    assert a["big3"]["moon"]["house"] is None and a["chart_ruler"] is None
    assert all("Ascendant" not in (t["a"], t["b"]) for t in a["tight_aspects"])


# ======================================================== 6. Chiron

def test_chiron_is_computed_from_the_bundled_ephemeris():
    """Moshier has no asteroid theory, and the error used to be swallowed —
    Chiron silently never appeared. engine/ephe ships seas_18.se1."""
    from engine import chart
    assert chart.FLAGS & swe.FLG_SWIEPH
    ch = positions(2451545.0)["Chiron"]                    # J2000
    assert ch["sign"] == "Sagittarius" and 0 <= ch["deg"] < 30


def test_chiron_reaches_the_compatibility_report():
    import random
    rng = random.Random(11)
    seen = False
    for _ in range(30):
        p = lambda: {"birth_ut": [rng.randint(1990, 2004), rng.randint(1, 12),  # noqa: E731
                                  rng.randint(1, 28), 12.0, 41.88, -87.63]}
        rep = compat.deep(p(), p())
        healing = [t for t in rep["themes"] if t["key"] == "healing"]
        if healing and healing[0]["contacts"]:
            seen = True
            break
    assert seen, "no Chiron contact in thirty pairs"


# ===================================================== 7. city coverage

FOUND = [
    ("Springfield, IL", "Springfield|Illinois|US"),
    ("Springfield MO", "Springfield|Missouri|US"),
    ("Portland Maine", "Portland|Maine|US"),
    ("Portland, OR", "Portland|Oregon|US"),
    ("Paris, TX", "Paris|Texas|US"),
    ("Paris", "Paris|Île-de-France|FR"),
    ("Birmingham, AL", "Birmingham|Alabama|US"),
    ("Kingston upon Hull", "Kingston upon Hull|England|GB"),
    ("Glasgow", "Glasgow|Scotland|GB"),
    ("Cardiff", "Cardiff|Wales|GB"),
    ("Lagos", "Lagos|Lagos|NG"),
    ("Ibadan", "Ibadan|Oyo State|NG"),
    ("Port Harcourt", "Port Harcourt|Rivers State|NG"),
    ("Enugu", "Enugu|Enugu State|NG"),
    ("Onitsha", "Onitsha|Anambra|NG"),
    ("Bombay", "Mumbai|Maharashtra|IN"),
    ("Bangalore", "Bengaluru|Karnataka|IN"),
    ("Kolkata", "Kolkata|West Bengal|IN"),
    ("Lucknow", "Lucknow|Uttar Pradesh|IN"),
    ("Quezon City", "Quezon City|National Capital Region|PH"),
    ("Cebu", "Cebu City|Central Visayas|PH"),
    ("Davao", "Davao|Davao Region|PH"),
    ("Iloilo", "Iloilo|Western Visayas|PH"),
    ("Mexico City", "Mexico City|Mexico City|MX"),
    ("Guadalajara", "Guadalajara|Jalisco|MX"),
    ("Tijuana", "Tijuana|Baja California|MX"),
    ("Sao Paulo", "São Paulo|São Paulo|BR"),
    ("São Paulo", "São Paulo|São Paulo|BR"),
    ("Rio de Janeiro", "Rio de Janeiro|Rio de Janeiro|BR"),
    ("Brasilia", "Brasília|Federal District|BR"),
    ("Recife", "Recife|Pernambuco|BR"),
    ("Bogota", "Bogotá|Bogota D.C.|CO"),
    ("Bogotá", "Bogotá|Bogota D.C.|CO"),
    ("Zurich", "Zürich|Zurich|CH"),
    ("Zürich", "Zürich|Zurich|CH"),
    ("St. Louis", "St. Louis|Missouri|US"),
    ("Washington DC", "Washington|District of Columbia|US"),
    ("Honolulu", "Honolulu|Hawaii|US"),
    ("Tupelo", "Tupelo|Mississippi|US"),
    ("Cologne", "Köln|North Rhine-Westphalia|DE"),
]


@pytest.mark.parametrize("q,cid", FOUND, ids=[f[0] for f in FOUND])
def test_city_resolves_first(q, cid):
    hits = cities.search(q, 8)
    assert hits and hits[0]["id"] == cid, [h["id"] for h in hits[:3]]


@pytest.mark.parametrize("q,cid,tz", [
    ("Springfield", "Springfield|Massachusetts|US", "America/New_York"),
    ("Springfield", "Springfield|Illinois|US", "America/Chicago"),
    ("Portland", "Portland|Maine|US", "America/New_York"),
])
def test_ambiguous_name_offers_every_major_match(q, cid, tz):
    hits = {h["id"]: h["tz"] for h in cities.search(q, 8)}
    assert hits.get(cid) == tz


MISSES = [
    # small towns under GeoNames' 15k threshold (Oprah: Kosciusko MS)
    ("Kosciusko, MS", "Kosciusko", "Mississippi"), ("Plains, GA", "Plains", "Georgia"),
    ("Yazoo City", "Yazoo City", "Mississippi"), ("Hope, AR", "Hope", "Arkansas"),
    ("Paris, KY", "Paris", "Kentucky"),
    # pre-rename Indian names still on birth certificates
    ("Allahabad", "Prayagraj", "Uttar Pradesh"), ("Gurgaon", "Gurugram", "Haryana"),
    ("Trivandrum", "Thiruvananthapuram", "Kerala"), ("Cochin", "Kochi", "Kerala"),
    ("Mysore", "Mysuru", "Karnataka"), ("Benares", "Varanasi", "Uttar Pradesh"),
    # common abbreviations / local names
    ("St Paul MN", "Saint Paul", "Minnesota"), ("St. Paul", "Saint Paul", "Minnesota"),
    ("Ft Worth", "Fort Worth", "Texas"), ("Ft. Lauderdale", "Fort Lauderdale", "Florida"),
    ("Washington, D.C.", "Washington", "District of Columbia"),
    ("NYC", "New York City", "New York"), ("CDMX", "Mexico City", "Mexico City"),
    ("Ciudad de México", "Mexico City", "Mexico City"), ("Genève", "Geneva", "Geneva"),
    ("Saint Louis", "St. Louis", "Missouri"),
]


# Plains, GA has ~600 people — under even the 5,000 threshold.
SMALL_TOWNS = [m for m in MISSES if m[0] == "Plains, GA"]
FIXED = [m for m in MISSES if m[0] != "Plains, GA"]


@pytest.mark.parametrize("q,name,region", FIXED, ids=[m[0] for m in FIXED])
def test_city_names_people_actually_type(q, name, region):
    """Old names, St/Ft/Mt, D.C., NYC, CDMX, local-language forms."""
    hits = cities.search(q, 1)
    assert hits and hits[0]["name"] == name and hits[0]["region"] == region, hits


def test_a_state_abbreviation_matches_a_word_not_a_substring():
    """"AR" is Arkansas — not the "ar" inside "North Carolina"."""
    assert cities.search("Hope, AR", 1)[0]["label"] == "Hope, AR, US"


@pytest.mark.parametrize("q", ["La Paz", "La Plata", "Lagos", "Dallas", "San Francisco"])
def test_short_aliases_do_not_hijack_real_cities(q):
    hits = cities.search(q, 1)
    assert hits and hits[0]["name"].lower().startswith(q.split()[0].lower()[:2]), hits


# Covered since #60 bundled cities500 (every place GeoNames records with 500+).
@pytest.mark.parametrize("q,name,region", SMALL_TOWNS, ids=[m[0] for m in SMALL_TOWNS])
def test_city_coverage_misses(q, name, region):
    hits = cities.search(q, 1)
    assert hits and hits[0]["name"] == name and hits[0]["region"] == region, hits


# ============================================================ helpers

_SCRIPT = r"""
import json, os, sys
os.environ["MYSIGN_DATA"] = sys.argv[1]
os.environ["MYSIGN_DEV_AUTH"] = "1"
for k in ("ANTHROPIC_API_KEY", "MYSIGN_TABLE", "MYSIGN_FROM_EMAIL", "AWS_LAMBDA_FUNCTION_NAME"):
    os.environ.pop(k, None)
sys.path.insert(0, sys.argv[2])
from fastapi.testclient import TestClient
import app.server as s
out = []
for i, req in enumerate(json.loads(sys.argv[3])):
    c = TestClient(s.app, raise_server_exceptions=False)
    email = f"acc{i}@x.com"
    code = c.post("/api/auth/request", json={"email": email}).json()["dev_code"]
    uid = c.post("/api/auth/verify", json={"email": email, "code": code}).json()["uid"]
    # Consent payload has changed shape before; send what the current API wants.
    if c.post("/api/consent", json={"ai": True}).status_code >= 400:
        c.post("/api/consent")
    r = c.post("/api/onboard", json={"name": "T", **req})
    out.append({"status": r.status_code, "body": r.json(),
                "profile": s.load(uid, "profile", None)})
print("@@" + json.dumps(out))
"""


def _onboard_subprocess(tmp_path, reqs):
    """Onboarding in a child process with its own data dir, so this suite
    never shares server state with tests/test_app.py."""
    data = tempfile.mkdtemp(dir=tmp_path)
    p = subprocess.run([sys.executable, "-c", _SCRIPT, data, ROOT, json.dumps(reqs)],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    line = next((ln for ln in p.stdout.splitlines() if ln.startswith("@@")), None)
    assert line, p.stderr[-2000:]
    return json.loads(line[2:])
