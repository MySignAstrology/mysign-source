"""
Placement deep-dives: tap Sun, Moon or Rising and read everything about it.
"""
import json
import os
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-placement-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import server
from engine import interpret as I
from engine import placement
from engine import placement_kb as KB
from engine.chart import natal
from tests.test_app import onboard, sign_in

UT = [1999, 3, 14, 9.7, 41.88, -87.63]


@pytest.fixture(scope="module")
def chart():
    return natal(*UT)


@pytest.fixture(scope="module")
def analysis(chart):
    return I.analyze(chart, True, (1999, 3, 14))


@pytest.fixture
def c():
    return TestClient(server.app)


# ------------------------------------------------------------------ tables

@pytest.mark.parametrize("lon,decan,ruler", [
    (0.5, 1, "Mars"), (12, 2, "Sun"), (25, 3, "Venus"),      # Aries: Mars, Sun, Venus
    (35, 1, "Mercury"), (45, 2, "Moon"), (55, 3, "Saturn"),  # Taurus: Mercury, Moon, Saturn
    (355, 3, "Mars"),                                          # last face of Pisces is Mars
])
def test_chaldean_faces(lon, decan, ruler):
    assert KB.face(lon) == (decan, ruler)


@pytest.mark.parametrize("lon,ruler", [
    (3, "Jupiter"), (13, "Mercury"), (29, "Saturn"),          # Aries terms
    (183, "Saturn"), (205, "Venus"), (215, "Mars"),           # Libra; 215 is Scorpio 5°
    (335, "Venus"), (357, "Mars"),                             # Pisces
])
def test_egyptian_terms(lon, ruler):
    assert KB.term(lon) == ruler


def test_every_sign_has_the_full_kb():
    keys = {"image", "gift", "shadow", "need", "growth", "love", "stress", "best"}
    for sign, kb in KB.SIGN.items():
        assert set(kb) == keys, sign
        assert all(v and len(v) < 160 for v in kb.values()), sign
    assert set(KB.HOUSE) == set(range(1, 13))


# ------------------------------------------------------------------ structure

def test_structure_is_real_geometry(chart):
    st = placement.structure(chart, "sun")
    assert st["sign"] == chart["planets"]["Sun"]["sign"]
    assert st["house"] == chart["planets"]["Sun"]["house"]
    assert (st["decan"]["n"], st["decan"]["ruler"]) == KB.face(chart["planets"]["Sun"]["lon"])
    assert all(a["orb"] <= 6 for a in st["aspects"])


def test_the_ruler_chain_ends(chart):
    for p in placement.POINTS:
        chain = placement.structure(chart, p)["ruler_chain"]
        assert 1 <= len(chain) <= 4
        assert len({c["planet"] for c in chain}) == len(chain), "a loop was followed twice"


def test_rising_needs_a_birth_time(chart, analysis):
    out = placement.build(chart, analysis, "rising", time_known=False)
    assert out["unavailable"]


def test_rising_names_no_house_for_itself(chart):
    st = placement.structure(chart, "rising")
    assert st["house"] is None and st["house_kb"] is None


# ------------------------------------------------------------------ her data

def _eps(n, moon_sign="Aquarius", mood_in=8, mood_out=4):
    out = []
    for i in range(n):
        inside = i % 3 == 0
        out.append({"date": f"2026-0{1 + i // 28}-{1 + i % 28:02d}",
                    "mood": mood_in if inside else mood_out,
                    "sky_state": {"moon_sign": moon_sign if inside else "Leo",
                                  "moon_phase": "full moon" if inside else "waning crescent"}})
    return out


def test_not_enough_data_says_so(chart):
    d = placement.her_data(_eps(5), chart, "moon")
    assert not d["enough"] and d["need_more"] == placement.MIN_CHECKINS - 5
    assert d["findings"] == []


def test_a_real_difference_is_reported_with_its_numbers(chart):
    moon_sign = chart["planets"]["Moon"]["sign"]
    d = placement.her_data(_eps(40, moon_sign=moon_sign), chart, "moon")
    f = next(f for f in d["findings"] if moon_sign in f["in"])
    assert f["mean_in"] == 8 and f["mean_out"] == 4 and f["verdict"] == "a clear difference"
    assert f["n_in"] + f["n_out"] == 40


def test_no_difference_is_a_real_answer(chart):
    sign = chart["planets"]["Sun"]["sign"]
    d = placement.her_data(_eps(40, moon_sign=sign, mood_in=5, mood_out=5), chart, "sun")
    assert all(f["verdict"] == "no real difference" for f in d["findings"])


# ------------------------------------------------------------------ narrative

class FakeClient:
    """Returns a fixed JSON read; one sentence invents a placement."""
    def __init__(self, payload):
        self.payload, self.calls = payload, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        block = type("B", (), {"type": "text", "text": json.dumps(self.payload)})
        return type("R", (), {"stop_reason": "end_turn", "content": [block]})


def test_the_narrative_is_grounded_sentence_by_sentence(chart, analysis):
    sun = chart["planets"]["Sun"]["sign"]
    bogus = next(s for s in KB.SIGNS if s != sun and s not in json.dumps(chart))
    payload = {k: f"Your Sun in {sun} is the point here. Your Venus in {bogus} explains it."
               for k, _ in placement.SECTIONS}
    payload["tagline"] = f"Sun in {sun}."
    out = placement.build(chart, analysis, "sun", want_narrative=True, client=FakeClient(payload))
    assert out["generated"]
    for k, _ in placement.SECTIONS:
        assert sun in out["narrative"][k]
        assert bogus not in out["narrative"][k], "an invented placement survived"


def test_rejected_sections_reach_the_prompt(chart, analysis):
    fake = FakeClient({"tagline": "x", **{k: "x" for k, _ in placement.SECTIONS}})
    placement.build(chart, analysis, "moon", want_narrative=True, client=fake,
                    rejected=["tension"])
    assert "tension" in fake.calls[0]["messages"][0]["content"]


def test_fallback_has_every_section(chart, analysis, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = placement.build(chart, analysis, "moon", want_narrative=True)
    assert not out["generated"]
    assert all(out["narrative"][k] for k, _ in placement.SECTIONS)


# ------------------------------------------------------------------ API

def test_the_routes(c, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sign_in(c, "place1@x.com"); onboard(c)
    d = c.get("/api/placement/sun").json()
    assert d["structure"]["label"] == "Sun" and d["prompts"] and d["honest"]
    assert d.get("narrative") is None
    n = c.post("/api/placement/sun/narrative").json()
    assert n["narrative"]["heart"]
    again = c.get("/api/placement/sun").json()
    assert again["narrative"]["heart"] == n["narrative"]["heart"], "the read is cached"
    assert c.get("/api/placement/venus").status_code == 404

    r = c.post("/api/placement/sun/feedback", json={"section": "tension", "fit": "not_me"})
    assert r.json()["feedback"] == {"tension": "not_me"}
    assert c.get("/api/placement/sun").json()["feedback"] == {"tension": "not_me"}
    assert c.post("/api/placement/sun/feedback",
                  json={"section": "nope", "fit": "me"}).status_code == 400


def test_a_new_birth_time_clears_the_reads(c, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sign_in(c, "place2@x.com"); onboard(c, time="")
    assert c.get("/api/placement/rising").json()["unavailable"]
    c.post("/api/placement/moon/narrative")
    assert c.get("/api/placement/moon").json().get("narrative")
    c.post("/api/birthtime", json={"time": "04:42"})
    assert c.get("/api/placement/moon").json().get("narrative") is None
    assert not c.get("/api/placement/rising").json().get("unavailable")


def test_the_model_path_streams_then_caches(c, monkeypatch):
    """With a key, the route streams (keep-alive spaces, then JSON) so a slow
    read can't hit CloudFront's 60s timeout; the result is still cached."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    calls = []

    def fake(st, *a, **k):
        calls.append(st["label"])
        return {"tagline": "t", **{s: f"{st['label']} {s}" for s, _ in placement.SECTIONS}}
    monkeypatch.setattr(placement, "narrative", fake)
    sign_in(c, "place3@x.com"); onboard(c)
    r = c.post("/api/placement/moon/narrative")
    assert r.status_code == 200
    body = json.loads(r.text)                   # leading spaces are valid JSON
    assert body["generated"] and body["narrative"]["heart"] == "Moon heart"
    assert c.post("/api/placement/moon/narrative").json()["narrative"]["heart"] == "Moon heart"
    assert calls == ["Moon"], "the second request is served from the cache"
