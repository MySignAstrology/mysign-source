"""
Accuracy fixes and the 'where you are now' chapter.

Every check here recomputes the fact independently from Swiss Ephemeris rather
than trusting the function under test to grade itself.
"""
import datetime as dt
import re

import swisseph as swe

from engine import compat, daily, lines, progressed
from engine.chart import SIGNS, jd_ut, moon_phase, natal, phase_name, positions
from engine.ephemeris import FLAGS
from tests.test_app import c, last_uid, onboard, sign_in  # noqa: F401  (fixture re-use)

BIRTH = [1996, 3, 14, 18.5, 40.7, -74.0]
TODAY = dt.date(2026, 10, 28)


def _moon_sign_at(birth_ut, day):
    base = jd_ut(*birth_ut[:4])
    pjd = base + (jd_ut(day.year, day.month, day.day, 12.0) - base) / progressed.YEAR
    return SIGNS[int(swe.calc_ut(pjd, swe.MOON, FLAGS)[0][0] // 30)]


# ------------------------------------------------------------ progressions

def test_progressed_moon_is_the_day_for_a_year_position():
    p = progressed.compute(BIRTH, TODAY, "exact")
    assert p["moon"]["sign"] == _moon_sign_at(BIRTH, TODAY)
    assert p["age"] == 30


def test_progressed_moon_entry_and_exit_dates_bracket_a_sign_change():
    p = progressed.compute(BIRTH, TODAY, "exact")
    mo = p["moon"]
    entered = dt.datetime.strptime(mo["entered"], "%B %Y").date()
    leaves = dt.datetime.strptime(mo["leaves"], "%B %Y").date()
    assert entered <= TODAY <= leaves
    # A sign is 30°; the progressed Moon takes roughly two to three years.
    assert 600 < (leaves - entered).days < 1200
    # Two months before entry it was in the previous sign; two after exit, the next.
    assert _moon_sign_at(BIRTH, entered - dt.timedelta(days=60)) != mo["sign"]
    assert _moon_sign_at(BIRTH, leaves + dt.timedelta(days=60)) == mo["next_sign"]


def test_progressed_phase_matches_the_measured_elongation():
    p = progressed.compute(BIRTH, TODAY, "exact")
    base = jd_ut(*BIRTH[:4])
    pjd = base + (jd_ut(2026, 10, 28, 12.0) - base) / progressed.YEAR
    e = (swe.calc_ut(pjd, swe.MOON, FLAGS)[0][0] - swe.calc_ut(pjd, swe.SUN, FLAGS)[0][0]) % 360
    assert progressed.PHASES[int(e // 45)][0] == p["phase"]["key"]


def test_unknown_birth_time_never_names_a_sign_the_window_disagrees_on():
    # Scan birthdays: whenever the ±12h window disagrees on the progressed Moon,
    # no sign may be named, and whenever it's named every sample must agree.
    named = unsure = 0
    for d in range(1, 29):
        bu = [2001, 8, d, 17.0, 34.05, -118.24]
        p = progressed.compute(bu, TODAY, "unknown")
        signs = {_moon_sign_at(compat._shift(bu, h), TODAY) for h in progressed.WINDOW["unknown"]}
        if len(signs) == 1:
            assert p["moon"]["sign"] == signs.pop(); named += 1
        else:
            assert p["moon"]["sign"] is None and set(p["moon"]["between"]) == signs
            unsure += 1
            card = progressed.describe(p)[0]
            assert card.get("missing") and "depending on your birth time" in card["body"]
    assert named and unsure                     # both paths actually exercised


def test_progressed_copy_names_the_tradition_and_predicts_nothing():
    for prec in ("exact", "approximate", "unknown"):
        for it in progressed.describe(progressed.compute(BIRTH, TODAY, prec)):
            body = it["body"].lower()
            assert not re.search(r"\bwill (happen|meet|come|arrive)|\byou are (a|an|the)\b", body)
        txt = " ".join(i["body"] for i in progressed.describe(progressed.compute(BIRTH, TODAY, prec)))
        assert "tradition" in txt


def test_reading_carries_where_you_are_now(c):  # noqa: F811
    sign_in(c, "now1@x.com"); onboard(c)
    r = c.get("/api/reading").json()
    kinds = [x["kind"] for x in r["now"]]
    assert "progressed_moon" in kinds and "progressed_phase" in kinds
    # And it is not frozen into the cache: a second (cached) read still has it.
    assert c.get("/api/reading").json()["now"] == r["now"]


# ------------------------------------------------------------ moon phase

def test_full_moon_is_a_moment_not_three_days():
    days = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(365)]
    full = [d for d in days if moon_phase(jd_ut(d.year, d.month, d.day, 12.0))["phase"] == "full moon"]
    # ~12.4 full moons a year, each labelled for about one noon (±6° ≈ ±12h).
    assert 10 <= len(full) <= 16
    assert phase_name(180) == "full moon" and phase_name(170) == "waxing gibbous"
    assert phase_name(0.5) == "new moon" and phase_name(359) == "new moon"
    assert phase_name(30) == "waxing crescent" and phase_name(300) == "waning crescent"


def test_true_node_is_never_flagged_retrograde():
    for y in range(1990, 2030, 3):
        assert positions(jd_ut(y, 6, 1, 12.0))["TrueNode"]["retrograde"] is False


# ------------------------------------------------------------ lines

def test_line_of_the_day_never_predicts_an_outside_event():
    banned = ("heading your way", "being noticed", "circling you", "is coming")
    for planet in lines.LINES.values():
        for group in planet.values():
            for ln in group:
                assert not any(b in ln for b in banned), ln
    with open(lines.__file__) as fh:
        src = fh.read()
    assert not any(b in src for b in banned)


# ------------------------------------------------------------ durations

def test_lasts_comes_from_real_speed():
    assert daily.lasts("Moon", 13.2, 5.0) == "passes by tomorrow"
    assert daily.lasts("Mercury", 1.6, 2.0) == "lasts a few days"
    assert daily.lasts("Mercury", 0.05, 2.0).startswith("lingers")
    assert daily.lasts("Jupiter", 0.1, 1.5) == "lasts weeks"
    # A real station: Mercury in 2026 crawls below 0.2°/day for days at a time.
    slow = [d for d in range(365)
            if abs(positions(jd_ut(2026, 1, 1, 12.0) + d)["Mercury"]["speed"]) < 0.2]
    assert slow
    j = jd_ut(2026, 1, 1, 12.0) + slow[0]
    assert daily.lasts("Mercury", positions(j)["Mercury"]["speed"], 2.0).startswith("lingers")


# ------------------------------------------------------------ one sky per day

def test_today_and_people_read_the_same_instant(c):  # noqa: F811
    from app import server
    sign_in(c, "sky1@x.com"); onboard(c)
    uid = last_uid()
    # Sydney: local noon is ~02:00 UT, sixteen hours from the old fixed 17:00 UT.
    acct = server.load(uid, "account", {}) or {}
    server.save(uid, "account", {**acct, "tz": "Australia/Sydney"})
    day, tz = server._today(uid), server._tz(uid)
    sky = server._sky(uid, day)
    want = positions(daily.local_noon_jd(day, tz))["Moon"]["label"]
    old = positions(jd_ut(day.year, day.month, day.day, 17.0))["Moon"]["label"]
    assert sky["positions"]["Moon"]["label"] == want != old


# ------------------------------------------------------------ pair balance

def test_balance_drops_a_moon_the_birth_window_cannot_fix():
    # Find a birthday on which the Moon changed sign.
    for d in range(1, 29):
        bu = [1998, 5, d, 17.0, 40.7, -74.0]
        sb = compat.samples(bu, "unknown")
        if not compat._moon_stable(sb):
            break
    else:
        raise AssertionError("no sign-changing Moon in a month")
    sa = compat.samples(BIRTH, "exact")
    bal = compat._balance(sa[0], sb[0], True, False,
                          moon_ok_a=True, moon_ok_b=compat._moon_stable(sb))
    assert bal["moons"]["certain"] is False
    assert bal["moons"]["theirs"] == compat.MOON_UNSURE["theirs"]
    assert bal["moons"]["yours"] == natal(*BIRTH)["planets"]["Moon"]["sign"]
    rep = compat.deep({"birth_ut": BIRTH}, {"birth_ut": bu}, precision_b="unknown")
    assert rep["balance"]["moons"]["certain"] is False
