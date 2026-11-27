"""
Comparing the people she has saved.

Saving one person is a party trick; comparing them is what the app is for, and
it is where overclaiming is most tempting — a ranked list looks authoritative
whether or not the gaps mean anything.

Measured: one aspect contact moves a dimension by 6 to 9 points. A smaller gap
cannot correspond to even one different contact, so it is not a difference.
These tests hold that line.
"""
import os
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-cmp-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"

import app.server as server
from engine import synastry as S
from engine import tools
from engine.chart import natal
from tests.test_app import last_uid, onboard, sign_in

ME = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)


def _row(name, overall, spark, ease, staying, confidence="full"):
    return {"name": name, "overall": overall, "confidence": confidence,
            "dimensions": {"spark": spark, "ease": ease, "staying": staying}}


@pytest.fixture
def c():
    return TestClient(server.app)


# ------------------------------------------------------------- the honesty

def test_a_gap_under_resolution_is_reported_as_level():
    """53 vs 50 is not a difference. It is the same instrument twice."""
    r = S.compare([_row("A", 53, 50, 50, 50), _row("B", 50, 50, 50, 50)])
    assert r["overall"][0]["tied"] is True
    assert set(r["overall"][0]["names"]) == {"A", "B"}
    assert "level" in r["verdict"].lower()


def test_a_gap_over_resolution_is_reported_as_real():
    r = S.compare([_row("A", 70, 50, 50, 50), _row("B", 50, 50, 50, 50)])
    assert r["overall"][0]["names"] == ["A"] and r["overall"][0]["tied"] is False
    assert "20 points clear" in r["verdict"]


def test_the_threshold_is_one_aspect_contact():
    """If this drifts, the claim in the verdict stops being true."""
    assert S.RESOLUTION["spark"] == 6
    assert S.RESOLUTION["staying"] == 9
    assert S.RESOLUTION["overall"] == 7


@pytest.mark.parametrize("gap,tied", [(0, True), (6, True), (7, False), (20, False)])
def test_the_boundary_behaves(gap, tied):
    r = S.compare([_row("A", 50 + gap, 50, 50, 50), _row("B", 50, 50, 50, 50)])
    assert r["overall"][0]["tied"] is tied, f"gap {gap}"


def test_everyone_level_says_so_rather_than_picking_one():
    r = S.compare([_row(n, 50, 50, 50, 50) for n in ("A", "B", "C")])
    assert len(r["overall"]) == 1 and r["overall"][0]["tied"]
    assert "smaller than this can actually measure" in r["verdict"]


def test_a_shared_top_spot_is_marked_as_shared():
    """Saying someone is best at what they tie on is a small dishonesty."""
    r = S.compare([_row("A", 50, 70, 40, 40), _row("B", 50, 70, 40, 40),
                   _row("C", 50, 40, 40, 40)])
    a = r["strongest_at"]["A"]
    assert a[0]["dimension"] == "spark"
    assert a[0]["shared_with"] == ["B"]


def test_mixed_confidence_is_flagged():
    """Someone scored without a birth time is not on the same instrument."""
    r = S.compare([_row("A", 70, 50, 50, 50),
                   _row("B", 50, 50, 50, 50, confidence="signs only")])
    assert r["mixed_confidence"] is True
    assert "without a birth time" in r["confidence_note"]


def test_one_person_is_not_a_comparison():
    r = S.compare([_row("A", 50, 50, 50, 50)])
    assert r["ok"] is False and r["have"] == 1


# ------------------------------------------------------------------ wiring

def test_the_endpoint_is_not_shadowed_by_the_id_route(c):
    """FastAPI matches in order; "compare" would otherwise read as a connection id."""
    sign_in(c, "cmp1@x.com"); onboard(c)
    r = c.get("/api/connections/compare")
    assert r.status_code == 200
    assert "ok" in r.json()


def test_comparing_real_saved_people(c):
    sign_in(c, "cmp2@x.com"); onboard(c)
    for name, date, time_, city in [
        ("Theo", "1997-06-02", "14:00", "Chicago IL"),
        ("Sam", "1995-11-19", "07:30", "New York NY"),
        ("Ari", "2000-01-08", "21:00", "Los Angeles CA"),
    ]:
        assert c.post("/api/connections", json={
            "name": name, "date": date, "time": time_, "city": city}).status_code == 200

    r = c.get("/api/connections/compare").json()
    assert r["ok"] and r["people"] == 3
    ranked = [n for g in r["overall"] for n in g["names"]]
    assert sorted(ranked) == ["Ari", "Sam", "Theo"]
    assert r["verdict"]


def test_the_agent_can_compare(c):
    sign_in(c, "cmp3@x.com"); onboard(c)
    for name, date in [("Theo", "1997-06-02"), ("Sam", "1995-11-19")]:
        c.post("/api/connections", json={"name": name, "date": date,
                                         "time": "14:00", "city": "Chicago IL"})
    uid = last_uid()
    out = tools.run("compare_connections", uid, ME, {})
    assert out["ok"] and out["people"] == 2
    assert "compare_connections" in [t["name"] for t in tools.DEFINITIONS]


def test_the_agent_is_told_not_to_compare_from_memory():
    out = tools.run("compare_connections", "nobody-here", ME, {})
    assert out["ok"] is False
    assert "Do not compare from memory" in out["hint"]
