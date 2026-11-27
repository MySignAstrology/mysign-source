"""
Your people today: the ranked daily list, the cheap brief behind it, and the
check-in streak. The list is the People tab's daily reason to open — it has to
be honest (every word from the engine), stable (only the sky reorders it) and
fast (reads don't grow six-per-person).
"""
import datetime as dt
import os
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-ppl-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import server
from engine import compat, daily
from tests.test_app import last_uid, onboard, sign_in

PEOPLE = [("Theo", "2000-11-02", "12:00", "Chicago IL"),
          ("Jordan", "1997-04-19", "08:30", "Atlanta GA"),
          ("Sam", "2002-01-27", "", "New York NY")]


@pytest.fixture
def c():
    return TestClient(server.app)


def _add(c, name, date, time, city):
    r = c.post("/api/connections", json={"name": name, "relation": "friend",
                                          "date": date, "time": time, "city": city})
    assert r.status_code == 200, r.text
    return r.json()


def _setup(c, email, people=PEOPLE):
    sign_in(c, email); onboard(c)
    return [_add(c, *p) for p in people]


# --------------------------------------------------------------------- engine

def test_brief_is_the_same_day_as_the_full_view():
    her = {"birth_ut": [2001, 6, 9, 18.4167, 33.749, -84.388], "birth_time_known": True}
    row = {"id": "x", "name": "Theo", "birth_ut": [2000, 11, 2, 18.0, 41.88, -87.63],
           "birth_time_known": True}
    rep = compat.deep({"birth_ut": her["birth_ut"]}, {"birth_ut": row["birth_ut"]}, name_b="Theo")
    day, tz = dt.date(2026, 10, 28), dt.timezone.utc
    full, brief = daily.for_pair(her, row, rep, day, tz), daily.brief_for_pair(her, row, rep, day, tz)
    for k in ("score", "delta", "mood", "headline", "reasons"):
        assert brief[k] == full[k]
    assert "series" not in brief


def test_ranking_is_by_movement_then_score_then_name():
    b = [{"id": "a", "name": "Ann", "score": 60, "delta": 2},
         {"id": "b", "name": "Bea", "score": 70, "delta": -9},
         {"id": "c", "name": "Cy", "score": 80, "delta": 9},
         {"id": "d", "name": "Di", "score": 50, "delta": 5}]
    out = daily.rank_people(b)
    assert [r["id"] for r in out["ranked"]] == ["c", "b", "d", "a"]
    assert out["spotlight"] == {"warmest": "c", "most_charged": "c", "tender": "b"}
    assert out["quiet"] is False


def test_a_quiet_day_names_nobody():
    out = daily.rank_people([{"id": "a", "name": "A", "score": 60, "delta": 3},
                             {"id": "b", "name": "B", "score": 61, "delta": -3}])
    assert out["quiet"] and out["spotlight"] == {"warmest": None, "most_charged": None,
                                                 "tender": None}
    assert daily.rank_people([])["ranked"] == []


def _days(today, offsets):
    return {(today - dt.timedelta(days=i)).isoformat() for i in offsets}


# 2026-10-28 is a Wednesday; ISO week runs Mon 26 - Sun 1.
WED = dt.date(2026, 10, 28)


def test_streak_unbroken_run():
    s = daily.streak(_days(WED, range(0, 6)), WED)
    assert s == {"days": 6, "today_done": True, "rest_used_this_week": False, "best": 6}


def test_streak_today_missing_does_not_break_it():
    s = daily.streak(_days(WED, range(1, 5)), WED)
    assert s["days"] == 4 and not s["today_done"]


def test_streak_one_missed_day_a_week_is_forgiven():
    # Missed Tuesday (1 day ago); checked in Wed, Mon, Sun, Sat.
    s = daily.streak(_days(WED, [0, 2, 3, 4]), WED)
    assert s["days"] == 4 and s["rest_used_this_week"]


def test_streak_two_misses_in_one_week_break_it():
    sun = dt.date(2026, 11, 1)          # week of Mon 26 Oct - Sun 1 Nov
    # Done Sun, Fri, Wed, Tue, Mon; missed Sat and Thu. Sat is the week's rest
    # day; Thu is a second miss in the same week, so the run stops there.
    s = daily.streak(_days(sun, [0, 2, 4, 5, 6]), sun)
    assert s["days"] == 2 and s["rest_used_this_week"]


def test_streak_two_misses_in_a_row_break_it():
    s = daily.streak(_days(WED, [0, 3, 4]), WED)
    assert s["days"] == 1


def test_streak_best_remembers_an_older_run():
    s = daily.streak(_days(WED, [0]) | _days(WED, range(20, 30)), WED)
    assert s["days"] == 1 and s["best"] == 10


# --------------------------------------------------------------------- API

def test_people_today_ranks_everyone_from_the_engine(c):
    rows = _setup(c, "ppl1@x.com")
    r = c.get("/api/people/today")
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 3 and {p["id"] for p in d["ranked"]} == {x["id"] for x in rows}
    key = [(-abs(p["delta"]), -p["score"], p["name"].lower()) for p in d["ranked"]]
    assert key == sorted(key)
    w = d["spotlight"]["warmest"]
    assert w is None or next(p for p in d["ranked"] if p["id"] == w)["delta"] >= daily.STEADY
    assert d["moon_sign"] and d["streak"]["days"] == 0
    # Every "why" is the engine's own reason, verbatim — nothing written here.
    uid = last_uid()
    prof, tz = server._profile(uid), server._tz(uid)
    day = dt.date.fromisoformat(d["date"])
    for p, row in ((p, next(x for x in rows if x["id"] == p["id"])) for p in d["ranked"]):
        stored = next(x for x in server._connections(uid) if x["id"] == row["id"])
        e = daily.brief_for_pair(prof, stored, compat.report_for(uid, stored), day, tz)
        assert p["headline"] == e["headline"] and p["score"] == e["score"]
        assert p["why"] == (e["reasons"][0]["short"] if e["reasons"] else None)
    # Same day, same order: only the sky reorders it.
    assert [p["id"] for p in c.get("/api/people/today").json()["ranked"]] == \
           [p["id"] for p in d["ranked"]]


def test_people_today_matches_each_persons_page(c):
    rows = _setup(c, "ppl2@x.com", PEOPLE[:2])
    ranked = {p["id"]: p for p in c.get("/api/people/today").json()["ranked"]}
    listed = {p["id"]: p["today"] for p in c.get("/api/connections").json()["connections"]}
    for row in rows:
        page = c.get(f"/api/connections/{row['id']}/today").json()
        assert ranked[row["id"]]["score"] == page["score"] == listed[row["id"]]["score"]
        assert ranked[row["id"]]["headline"] == page["headline"]


def test_people_today_carries_checkins_and_streak(c):
    rows = _setup(c, "ppl3@x.com", PEOPLE[:2])
    c.post(f"/api/connections/{rows[0]['id']}/checkin", json={"feel": "closer"})
    d = c.get("/api/people/today").json()
    me = next(p for p in d["ranked"] if p["id"] == rows[0]["id"])
    assert me["feel_today"] == "closer" and d["checked_in_today"] == 1
    assert d["streak"]["days"] == 1 and d["streak"]["today_done"]


def test_people_today_needs_a_chart_and_is_empty_without_people(c):
    sign_in(c, "ppl4@x.com")
    assert c.get("/api/people/today").status_code == 400
    onboard(c)
    d = c.get("/api/people/today").json()
    assert d["count"] == 0 and d["ranked"] == [] and d["quiet"]


def test_the_people_list_no_longer_reads_six_times_per_person(c, monkeypatch):
    _setup(c, "ppl5@x.com", PEOPLE * 4)        # 12 people
    from engine import store
    n = {"loads": 0}
    real = store.load

    def counting(*a, **kw):
        n["loads"] += 1
        return real(*a, **kw)

    # Every module bound `load` at import; count at each binding the list touches.
    for mod in (server, store):
        monkeypatch.setattr(mod, "load", counting)
    monkeypatch.setattr("engine.store.load", counting)
    assert c.get("/api/connections").status_code == 200
    # One report per person, plus a handful of fixed reads (session, account,
    # profile, zone, the list). It was ~6 per person.
    assert n["loads"] <= 12 + 8, n
