"""
Daily compatibility: the pair's usual score, moved by today's sky.
"""
import datetime as dt
import os
import pathlib
import random
import statistics
import sys
import tempfile
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-daily-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import server
from engine import compat, daily, store, tools
from tests.test_app import last_uid, onboard, sign_in

HER = [2001, 6, 9, 18.4167, 33.749, -84.388]
THEM = [2000, 11, 2, 18.0, 41.88, -87.63]
NY = ZoneInfo("America/New_York")


@pytest.fixture
def c():
    return TestClient(server.app)


@pytest.fixture(scope="module")
def pair():
    rep = compat.deep({"birth_ut": HER}, {"birth_ut": THEM}, name_b="Theo")
    return rep, daily._natal(tuple(HER), "exact"), daily._natal(tuple(THEM), "exact")


# ------------------------------------------------------------------ engine

def test_the_score_is_the_usual_one_moved_by_at_most_the_swing(pair):
    rep, a, b = pair
    for i in range(60):
        d = daily.day_score(rep, a, b, dt.date(2026, 1, 1) + dt.timedelta(days=i), NY, "Theo")
        assert d["usual"] == rep["score"]["overall"]
        assert abs(d["delta"]) <= daily.MAX_SWING
        assert d["score"] == d["usual"] + d["delta"]
        assert len(d["reasons"]) <= 3


def test_it_actually_changes_from_day_to_day(pair):
    rep, a, b = pair
    scores = {daily.day_score(rep, a, b, dt.date(2026, 3, 1) + dt.timedelta(days=i), NY)["score"]
              for i in range(14)}
    assert len(scores) >= 5


def test_the_same_day_gives_the_same_answer(pair):
    rep, a, b = pair
    d = dt.date(2026, 10, 27)
    assert daily.day_score(rep, a, b, d, NY) == daily.day_score(rep, a, b, d, NY)


def test_today_is_her_today_not_utc(pair):
    """At 9pm in Los Angeles it is already tomorrow in UTC; the Moon has moved
    six degrees by then. The day is computed at her local noon."""
    assert daily.local_noon_jd(dt.date(2026, 10, 27), ZoneInfo("America/Los_Angeles")) \
        != daily.local_noon_jd(dt.date(2026, 10, 27), ZoneInfo("Asia/Tokyo"))


def test_the_spread_is_calibrated():
    """Most days near usual, a minority notably warm or tense, almost none pinned
    at the cap, and no lurching overnight. Mirrors the numbers in daily.py."""
    rng = random.Random(7)

    def p():
        return [rng.randint(1988, 2004), rng.randint(1, 12), rng.randint(1, 28),
                rng.uniform(0, 24), 40.7, -74.0]

    deltas, steps = [], []
    for _ in range(6):
        a, b = p(), p()
        rep = compat.deep({"birth_ut": a}, {"birth_ut": b})
        na, nb = daily._natal(tuple(a), "exact"), daily._natal(tuple(b), "exact")
        prev = None
        for i in range(0, 180):
            x = daily.day_score(rep, na, nb, dt.date(2026, 1, 1) + dt.timedelta(days=i))["delta"]
            deltas.append(x)
            if prev is not None:
                steps.append(abs(x - prev))
            prev = x
    near = sum(abs(x) < daily.STEADY for x in deltas) / len(deltas)
    strong = sum(abs(x) >= 10 for x in deltas) / len(deltas)
    pinned = sum(abs(x) >= daily.MAX_SWING - 1 for x in deltas) / len(deltas)
    assert 0.25 < near < 0.55, near
    assert 0.05 < strong < 0.25, strong
    assert pinned < 0.04, pinned
    assert statistics.median(steps) <= 6


def test_reasons_are_real_geometry_and_plain_words(pair):
    rep, a, b = pair
    seen = 0
    for i in range(30):
        d = daily.day_score(rep, a, b, dt.date(2026, 5, 1) + dt.timedelta(days=i), NY, "Theo")
        for r in d["reasons"]:
            seen += 1
            assert r["transit"] in daily.FAST
            assert r["orb"] <= daily.ORB[r["transit"]]
            assert r["meaning"][0].isupper() and "oppositions" not in r["text"]
            assert not r["text"].startswith("The Venus")
    assert seen > 20


def test_a_contact_lit_up_names_both_people(pair):
    rep, a, b = pair
    found = None
    for i in range(120):
        d = daily.day_score(rep, a, b, dt.date(2026, 1, 1) + dt.timedelta(days=i), NY, "Theo")
        found = next((r for r in d["reasons"] if r["kind"] == "contact"), None)
        if found:
            break
    assert found, "no synastry contact lit up in four months"
    assert "Theo's" in found["text"] and "your" in found["text"]


def test_an_unknown_birth_time_drops_that_moon(pair):
    rep, a, b = pair
    for i in range(30):
        d = daily.day_score(rep, a, b, dt.date(2026, 2, 1) + dt.timedelta(days=i), NY,
                            "Theo", moon_theirs=False)
        assert not any(r["text"].endswith("Theo's Moon") for r in d["reasons"])


def test_a_hard_day_says_when_it_eases(pair):
    rep, *_ = pair
    prof, row = {"birth_ut": HER}, {"birth_ut": THEM, "name": "Theo", "birth_time_known": True}
    for i in range(60):
        d = daily.for_pair(prof, row, rep, dt.date(2026, 1, 1) + dt.timedelta(days=i), NY)
        if d["delta"] <= -daily.STEADY:
            assert d["outlook"] and d["headline"].startswith("Things are touchier with Theo")
            return
    pytest.fail("no tender day in two months")


def test_checkins_keep_one_per_day_and_age_out():
    day = dt.date(2026, 10, 27)
    log = daily.record_checkin({}, day, "strained", "argued about the trip")
    log = daily.record_checkin(log, day, "steady")
    assert log[day.isoformat()]["feel"] == "steady"
    old = daily.record_checkin({"2026-01-01": {"feel": "closer"}}, day, "closer")
    assert "2026-01-01" not in old
    with pytest.raises(ValueError):
        daily.record_checkin({}, day, "furious")


# --------------------------------------------------------------------- API

def _add(c, **kw):
    body = {"name": "Theo", "relation": "boyfriend", "date": "2000-11-02",
            "time": "12:00", "city": "Chicago IL", **kw}
    return c.post("/api/connections", json=body)


def test_today_and_the_list_carry_the_daily_score(c):
    sign_in(c, "daily1@x.com"); onboard(c)
    row = _add(c).json()
    d = c.get(f"/api/connections/{row['id']}/today").json()
    assert d["person"]["name"] == "Theo"
    assert d["usual"] == row["overall"] and abs(d["delta"]) <= daily.MAX_SWING
    assert len(d["series"]) == 17 and d["date"] in [s["date"] for s in d["series"]]
    assert d["headline"] and d["checkin"] is None
    listed = c.get("/api/connections").json()["connections"][0]
    assert listed["today"]["score"] == d["score"]


def test_a_checkin_is_stored_shown_and_forgotten_with_them(c):
    sign_in(c, "daily2@x.com"); onboard(c)
    uid = last_uid()
    row = _add(c).json()
    r = c.post(f"/api/connections/{row['id']}/checkin",
               json={"feel": "strained", "note": "argued about the trip"})
    assert r.status_code == 200
    d = r.json()
    assert d["checkin"]["feel"] == "strained" and d["checkin"]["note"] == "argued about the trip"
    assert next(s for s in d["series"] if s["date"] == d["date"])["feel"] == "strained"
    assert c.post(f"/api/connections/{row['id']}/checkin", json={"feel": "furious"}).status_code == 400
    assert f"pulse-{row['id']}" in store.keys(uid)
    c.delete(f"/api/connections/{row['id']}")
    assert f"pulse-{row['id']}" not in store.keys(uid)


def test_the_chat_sees_today_and_her_checkins(c):
    sign_in(c, "daily3@x.com"); onboard(c)
    uid = last_uid()
    row = _add(c).json()
    c.post(f"/api/connections/{row['id']}/checkin", json={"feel": "strained", "note": "fight"})
    out = tools.run("get_synastry", uid, server._natal(uid), {"name": "theo"})
    t = out["today"]
    assert t and "score" in t and t["headline"]
    assert t["her_checkins"][-1]["feel"] == "strained" and t["her_checkins"][-1]["note"] == "fight"


def test_the_people_ui_contract_holds(c):
    # The People cards draw their rings from overall (usual) and today.score,
    # and the check-in payoff counts days from checkins. Keep those fields.
    sign_in(c, "daily-ui@x.com"); onboard(c)
    row = _add(c).json()
    listed = c.get("/api/connections").json()["connections"][0]
    assert {"overall", "band"} <= listed.keys()
    assert {"score", "delta", "mood"} <= listed["today"].keys()
    assert c.get(f"/api/connections/{row['id']}/today").json()["checkins"] == 0
    d = c.post(f"/api/connections/{row['id']}/checkin", json={"feel": "closer"}).json()
    assert d["checkins"] == 1
    # A second check-in the same day replaces the first; the count does not grow.
    d = c.post(f"/api/connections/{row['id']}/checkin", json={"feel": "steady"}).json()
    assert d["checkins"] == 1
