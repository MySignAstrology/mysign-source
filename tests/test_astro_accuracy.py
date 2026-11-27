"""
The astrology-accuracy audit, one test per finding. Each one failed before its
fix: the numbers in the docstrings are what the app said, and what is true.
"""
import datetime as dt
import random

import pytest
from fastapi.testclient import TestClient

from app import server
from engine import (
    classical,
    compat,
    corpus,
    interpret,
    lines,
    localtime,
    placement,
    rectify,
    salience,
    synastry,
    tools,
    tradition,
)
from engine.chart import aspects, day_phase, jd_ut, natal, positions, with_time
from tests.test_app import sign_in

ANGLES = {"Ascendant", "Midheaven"}


@pytest.fixture
def c():
    return TestClient(server.app)


def _onboard(c, date="1993-03-15", time="", city="New York City"):
    r = c.post("/api/onboard", json={"name": "Lena", "date": date, "time": time, "city": city})
    assert r.status_code == 200, r.text
    return r.json()


# ===================================================== H1: no time, no angles

def test_aspects_drop_angles_and_moon_for_an_unknown_time():
    """157 of 365 days headlined a noon-guess Ascendant or Midheaven for a
    no-time chart, and 140 a ±6.5° Moon at 0.1°. A chart marked unknown has
    neither as a target; the same chart with a time has both."""
    blind = with_time(natal(1993, 3, 15, 17.0, 40.71, -74.01), False)
    sure = with_time(natal(1993, 3, 15, 17.0, 40.71, -74.01), True)
    hit_blind, hit_sure = set(), set()
    for n in range(120):
        tp = positions(jd_ut(2026, 1, 1, 12.0) + n)
        hit_blind |= {a["natal"] for a in aspects(tp, blind)}
        hit_sure |= {a["natal"] for a in aspects(tp, sure)}
    assert not hit_blind & (ANGLES | {"Moon"})
    assert hit_sure >= ANGLES and "Moon" in hit_sure


def test_no_time_user_never_sees_an_angle_on_today_the_line_or_the_sky(c):
    sign_in(c, "astro-unk1@x.com")
    on = _onboard(c)
    # Onboard: no houses, no angles for a time she doesn't have.
    assert on["angles"] == {} and all(v["house"] is None for v in on["chart"].values())
    from tests.test_app import last_uid
    uid = last_uid()
    for n in range(40):
        day = dt.date(2026, 1, 1) + dt.timedelta(days=n)
        sky = server._sky(uid, day)
        assert not {a["natal"] for a in sky["aspects_to_natal"]} & (ANGLES | {"Moon"})
        assert sky["active_houses"] == []
    t = c.get("/api/today").json()
    text = " ".join(t["headlines"]) + " " + (t["line"] or {}).get("why", "")
    for word in ("Midheaven", "Ascendant", "rising", "your Moon"):
        assert word not in text
    # The chat tools agree with the briefing.
    nat = server._natal(uid)
    assert tools.get_natal(uid, nat)["angles"] == {}
    sky = tools.get_sky(uid, nat, date="2026-01-05")
    assert all(p["her_house"] is None for p in sky["placements"].values())
    assert not {a["her"] for a in sky["aspects_to_her_chart"]} & ANGLES


def test_the_sky_cache_key_moved_so_old_skies_with_angles_are_not_served():
    assert server._sky_key(dt.date(2026, 1, 1), "placidus").startswith("sky-v3-")


# ============================================ M1/M2: polar charts, honestly

TROMSO = (69.6496, 18.956)


def test_polar_fallback_is_named_not_called_placidus():
    """Tromsø 1995-12-21: Placidus can't be drawn, Porphyry was used, and every
    surface said "Placidus"."""
    ch = natal(1995, 12, 21, 11.0, *TROMSO)
    assert ch["houses_used"]["fallback"] and ch["houses_used"]["system"] == "porphyry"
    d = tradition.describe("placidus", "traditional", ch)
    assert d["houses_label"].startswith("Porphyry") and d["houses_fallback"]
    assert tradition.describe("placidus", "traditional", natal(1995, 12, 21, 11.0, 40.7, -74))[
        "houses_label"] == "Placidus"
    from engine import context
    block = "\n".join(context.chart_lines(with_time(ch, True), {"birth_time_known": True}))
    assert "Porphyry" in block and "Placidus houses" not in block


def test_polar_tradition_endpoint_says_porphyry(c):
    sign_in(c, "astro-tromso1@x.com")
    _onboard(c, "1995-12-21", "12:00", "Tromsø")
    t = c.get("/api/tradition").json()
    assert t["houses_label"].startswith("Porphyry") and t["houses_fallback"]
    assert c.get("/api/reading").json()["tradition"]["houses_label"].startswith("Porphyry")


def test_rectify_angles_exist_inside_the_polar_circle():
    """/api/rectify 500'd for every Tromsø birth: the angles asked Placidus."""
    a = rectify._angles(jd_ut(1995, 12, 21, 11.0), *TROMSO, houses="placidus")
    assert set(a) == {"ASC", "DSC", "MC", "IC"}
    cands = rectify.candidates("1995-12-21", *TROMSO, "Europe/Oslo", houses="placidus")
    assert len(cands) == 24 * 60 // rectify.STEP_MIN


def test_rising_speed_fact_survives_a_polar_birth():
    items = placement.astronomy([1995, 12, 21, 11.0, *TROMSO], "rising", True)
    assert any(i["kind"] == "horizon" for i in items)


# ===================================================== M3: symmetric scores

def _random_births(n, seed=11):
    rng = random.Random(seed)
    return [[rng.randint(1970, 2005), rng.randint(1, 12), rng.randint(1, 28),
             round(rng.random() * 24, 4), rng.choice([40.71, 51.5, -33.87]),
             rng.choice([-74.0, -0.13, 151.2])] for _ in range(n)]


def test_compatibility_does_not_depend_on_who_added_whom():
    """150 random pairs differed by 2.6 points on average, 9 at worst."""
    b = _random_births(60)
    for x, y in zip(b[::2], b[1::2]):
        ca, cb = natal(*x), natal(*y)
        assert synastry.score(ca, cb)["overall"] == synastry.score(cb, ca)["overall"]
    for x, y in list(zip(b[::2], b[1::2]))[:6]:
        for pa, pb in (("exact", "exact"), ("exact", "unknown")):
            r1 = compat.deep({"birth_ut": x}, {"birth_ut": y}, precision_a=pa, precision_b=pb)
            r2 = compat.deep({"birth_ut": y}, {"birth_ut": x}, precision_a=pb, precision_b=pa)
            assert r1["score"]["overall"] == r2["score"]["overall"]
            assert r1["score"]["overall_range"] == r2["score"]["overall_range"]


def test_stored_rows_from_an_older_report_are_rescored_on_read(c):
    sign_in(c, "astro-lazy@x.com")
    _onboard(c, "1990-05-01", "08:00", "Chicago IL")
    row = c.post("/api/connections", json={"name": "Sam", "date": "1991-02-02",
                                           "time": "09:30", "city": "Chicago IL"}).json()
    from engine.store import load, save
    from tests.test_app import last_uid
    uid = last_uid()
    rows = load(uid, "connections", [])
    rows[0].update({"report_v": None, "overall": 1})
    save(uid, "connections", rows)
    rep = load(uid, f"compat-{row['id']}", None)
    rep["version"] = 2
    save(uid, f"compat-{row['id']}", rep)
    listed = c.get("/api/connections").json()["connections"][0]
    assert listed["overall"] == row["overall"] != 1
    assert load(uid, "connections", [])[0]["report_v"] == compat.REPORT_VERSION


# ================================================= M4: an uncertain Moon

NYC_1993 = with_time(natal(1993, 3, 15, 17.0, 40.71, -74.01), False)


def test_uncertain_moon_is_not_a_stellium_a_dignity_or_a_temperament_vote():
    """The Moon ran 24°58' Sagittarius to 7°36' Capricorn that day; the reading
    said "3 planets in Capricorn" and "Moon in Capricorn — working uphill"."""
    from engine import reading
    a = interpret.analyze(NYC_1993, False, (1993, 3, 15))
    assert not a["moon_sign_certain"] and a["moon_signs"] == ["Sagittarius", "Capricorn"]
    assert not any("Moon" in s["planets"] for s in a["stelliums"])
    r = reading.build(NYC_1993, want_prose=False, time_known=False, birth_ymd=(1993, 3, 15))
    assert not any("Moon in" in x["title"] for x in r["standout"])
    t = classical.temperament(NYC_1993, NYC_1993["planets"]["Sun"]["lon"], 270, False)
    assert "your Moon's sign" not in [v["factor"] for v in t["votes"]]


def test_unknown_time_moon_is_a_range_on_the_placement_page():
    st = placement.structure(NYC_1993, "moon", False)
    assert "24°58' Sagittarius" in st["degree_range"] and "7°36' Capricorn" in st["degree_range"]
    assert st["decan"] is None and st["dignity"] is None and st["sign_certain"] is False
    text = placement.facts_text(st, NYC_1993, interpret.analyze(NYC_1993, False, (1993, 3, 15)),
                                [], [])
    assert "Sagittarius or Capricorn" in text and "Decan" not in text
    # No constellation claimed for a Moon that could be six degrees either way.
    assert not placement.astronomy([1993, 3, 15, 17.0, 40.71, -74.01], "moon", False)


# ================================================== M5: her tradition, everywhere

def test_ruler_chain_follows_her_rulership_setting():
    chart = natal(1990, 11, 10, 12.0, 40.7, -74.0)
    trad = placement._dispositors(chart, "Scorpio", "traditional")
    modern = placement._dispositors(chart, "Scorpio", "modern")
    assert trad[0]["planet"] == "Mars" and modern[0]["planet"] == "Pluto"


def test_people_reports_are_drawn_in_her_house_system():
    a, b = [1990, 5, 1, 13.0, 41.88, -87.63], [1991, 2, 2, 15.5, 41.88, -87.63]
    ws = compat.deep({"birth_ut": a}, {"birth_ut": b}, houses="whole_sign")
    pl = compat.deep({"birth_ut": a}, {"birth_ut": b}, houses="placidus")
    assert ws["houses"] == "whole_sign" and pl["houses"] == "placidus"
    assert compat.samples(a, "exact", "whole_sign")[0]["houses_used"]["system"] == "whole_sign"


# ================================================= M6: the real constellation

# Moon positions where the ecliptic-crossing table named the wrong
# constellation; the expected names are Skyfield's IAU map (DE440s).
MOON_CASES = [(2438949.11527, "Libra"), (2451531.29567, "Pisces"),
              (2447777.48892, "Scorpius"), (2454970.31924, "Pisces"),
              (2450049.5527, "Aquarius"), (2456223.63926, "Aquarius"),
              (2454410.0134, "Leo"), (2437077.49017, "Cetus"), (2438278.1242, "Cetus"),
              (2434123.50574, "Pisces"), (2450961.0112, "Orion"),
              (2455022.63192, "Aquarius")]


@pytest.mark.parametrize("jd,expected", MOON_CASES)
def test_stars_behind_the_moon_use_its_real_position(jd, expected):
    import swisseph as swe
    assert placement.body_constellation(jd, swe.MOON) == expected


def test_constellation_lookup_landmarks():
    assert placement.constellation_at(0.0, 89.9) == "Ursa Minor"
    assert placement.constellation_at(83.8, -5.4) == "Orion"         # M42
    assert placement.constellation_at(266.4, -29.0) == "Sagittarius"  # Galactic centre


# ======================================================== the LOW findings

@pytest.mark.parametrize("zone,day,phase", [
    ("Pacific/Honolulu", dt.date(2026, 1, 3), "full moon"),     # exact at 00:02 local
    ("Pacific/Tongatapu", dt.date(2026, 1, 3), "full moon"),    # exact at 23:02 local
    ("Pacific/Honolulu", dt.date(2026, 1, 2), "waxing gibbous"),
    ("Pacific/Honolulu", dt.date(2026, 1, 4), "waning gibbous"),
])
def test_a_named_phase_is_the_day_its_moment_falls_on(zone, day, phase):
    assert day_phase(day, zone)["phase"] == phase


def test_node_is_called_the_north_node_and_points_have_meanings():
    ln = lines.line([{"transit": "TrueNode", "aspect": "conjunction", "natal": "Mars"}], "2026-01-01", "u")
    assert "TrueNode" not in ln["why"] and "North Node" in ln["why"]
    ln = lines.line([{"transit": "Saturn", "aspect": "square", "natal": "TrueNode"}], "2026-01-01", "u")
    assert "TrueNode" not in ln["why"]
    for t, n in (("TrueNode", "Sun"), ("Chiron", "Venus"), ("Saturn", "Ascendant"),
                 ("Pluto", "Midheaven"), ("Mars", "TrueNode")):
        r = corpus.transit(t, n, "square")
        assert r and r["reading"] and "TrueNode" not in r["reading"]


def test_today_headlines_never_say_truenode(c):
    sign_in(c, "astro-node@x.com")
    _onboard(c, "1990-05-01", "08:00", "Chicago IL")
    from tests.test_app import last_uid
    uid = last_uid()
    sky = server._sky(uid)
    sky["aspects_to_natal"].insert(0, {"transit": "TrueNode", "aspect": "conjunction",
                                       "natal": "Sun", "orb": 0.1, "applying": True,
                                       "retrograde": False})
    t = c.get("/api/today").json()
    assert not any("TrueNode" in h for h in t["headlines"])


def test_starters_skip_the_node_and_the_angle_square():
    a = {"big3": {"sun": {"house": None}, "moon": {"house": None}}, "stelliums": [],
         "retrogrades": [], "modality": "mutable", "element": "air",
         "tight_aspects": [{"a": "Mars", "b": "TrueNode", "aspect": "square"},
                           {"a": "Ascendant", "b": "Midheaven", "aspect": "square"}]}
    from engine import reading
    starters = reading._starters(a)
    assert not any("TrueNode" in s or ("Ascendant" in s and "Midheaven" in s) for s in starters)


@pytest.mark.parametrize("args,words", [
    ((2020, 10, 4, 2, 15, "Australia/Lord_Howe"), "half hour the clocks skipped"),
    ((2020, 4, 5, 1, 45, "Australia/Lord_Howe"), "half hour that happened twice"),
    ((1986, 1, 1, 0, 5, "Asia/Kathmandu"), "15 minutes the clocks skipped"),
    ((2005, 4, 3, 2, 30, "America/New_York"), "hour the clocks skipped"),
    ((2011, 12, 30, 12, 0, "Pacific/Apia"), "day the clocks skipped"),
])
def test_clock_change_notes_say_how_much_time_moved(args, words):
    _, issue = localtime.resolve(*args)
    assert words in localtime.describe(issue)


def test_repeated_hour_note_names_the_other_reading_and_the_midheaven():
    _, issue = localtime.resolve(2005, 10, 30, 1, 30, "America/New_York", fold=1)
    note = localtime.describe(issue, "Leo", "Taurus")
    assert "If it was the first" in note and "Midheaven is in Taurus" in note


def test_connection_keeps_its_clock_change_note_and_honours_fold(c):
    sign_in(c, "astro-fold@x.com")
    _onboard(c, "1990-05-01", "08:00", "Chicago IL")
    r1 = c.post("/api/connections", json={"name": "Rosa", "date": "2000-10-29", "time": "01:30",
                                          "city": "New York City"}).json()
    r2 = c.post("/api/connections", json={"name": "Rosa2", "date": "2000-10-29", "time": "01:30",
                                          "city": "New York City", "fold": 1}).json()
    assert "happened twice" in r1["birth_time_note"]
    assert r2["birth_ut"][3] - r1["birth_ut"][3] == pytest.approx(1.0)


@pytest.mark.parametrize("date", ["1750-06-01", "2099-01-01"])
def test_connection_birth_dates_are_bounded(c, date):
    """1750 was accepted and Chiron silently vanished (the files start at 1800);
    a date in the future was charted too."""
    sign_in(c, f"astro-bounds-{date[:4]}@x.com")
    _onboard(c, "1990-05-01", "08:00", "Chicago IL")
    r = c.post("/api/connections", json={"name": "Old", "date": date, "time": "",
                                         "city": "Chicago IL"})
    assert r.status_code == 400


def test_the_same_chart_twice_is_named_not_scored_as_rare():
    me = [1990, 5, 1, 13.0, 41.88, -87.63]
    twin = [1990, 5, 1, 13.0833, 41.88, -87.63]
    r = compat.deep({"birth_ut": me}, {"birth_ut": twin})
    assert r["score"]["band"] == "same sky" and r["score"]["notes"]
    assert r["uncertainty"][0].startswith("These two charts")


def test_a_baby_is_not_hard_going_and_a_minor_is_not_a_romance():
    parent = [1990, 5, 1, 13.0, 41.88, -87.63]
    baby = [dt.date.today().year - 1, 1, 15, 9.0, 41.88, -87.63]
    fam = compat.deep({"birth_ut": parent}, {"birth_ut": baby}, kind="family")
    assert fam["score"]["band"] not in ("hard going", "effortful")
    rom = compat.deep({"birth_ut": parent}, {"birth_ut": baby}, kind="romantic")
    assert rom["score"]["kind"] == "friend" and "not a romance" in " ".join(rom["uncertainty"])
    assert compat._kind_band("family", 10) == "takes patience"
    assert compat._kind_band("romantic", 10) == "hard going"


def test_salience_is_calibrated_at_her_local_noon(monkeypatch):
    import engine.chart as chart
    her = natal(1990, 5, 1, 13.0, 14.6, 121.0)
    seen = []
    real = chart.positions

    def spy(j):
        seen.append(j)
        return real(j)
    monkeypatch.setattr(chart, "positions", spy)
    salience.calibrate(her, today=dt.date(2026, 1, 1),
                       days=2, tz="Asia/Manila")
    # Noon in Manila is 04:00 UT, not the old fixed 17:00.
    assert seen[0] == pytest.approx(jd_ut(2026, 1, 1, 4.0))


def test_lillys_season_says_whose_season_it_is():
    t = classical.temperament(NYC_1993, NYC_1993["planets"]["Sun"]["lon"], 100, True)
    assert any("northern hemisphere" in v["factor"] for v in t["votes"])


def test_moon_ingress_during_her_day_is_reported():
    """Sydney, 30 October 2026: Today said Moon in Gemini all day; it entered
    Cancer in the afternoon."""
    from zoneinfo import ZoneInfo
    ing = server._moon_ingress(dt.date(2026, 10, 30), ZoneInfo("Australia/Sydney"))
    assert ing and ing["from"] == "Gemini" and ing["to"] == "Cancer"
    assert "12:00" < ing["at"] < "18:00"
    assert server._moon_ingress(dt.date(2026, 10, 22), ZoneInfo("Australia/Sydney")) is None


def test_glossary_midheaven_and_precession_agree_with_the_sky():
    from engine import glossary
    g = glossary.TERMS if hasattr(glossary, "TERMS") else glossary.GLOSSARY
    assert "highest point" not in g["Midheaven"]["what"]
    assert "25°" in g["precession"]["what"]
