"""
Every model call outside the chat is metered per user per UTC day (audit H1).

Before: PATCH a person with an empty body, POST the written read, repeat — ten
Sonnet calls for ten loops, and the same for add / read / delete. No quota was
touched. The model is stubbed in every test here; nothing reaches the network.
"""
import pytest

import app.server as server
from engine import compat_fun, spend
from engine import memory as M
from tests.test_invites import person


@pytest.fixture
def model(monkeypatch):
    """A key in the environment (so the routes believe the model is there) and
    a stub that counts what would have been model calls."""
    calls = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    monkeypatch.setattr(server, "DEMO", False, raising=False)

    def fake(rep, name, *a, **k):
        calls.append(name)
        return {"dynamic": "You two talk past each other on purpose.", "headline": "h"}
    monkeypatch.setattr(compat_fun, "generate", fake)
    return calls


def add(c, name="Target", time_="11:00"):
    cid = c.get("/api/cities", params={"q": "Boston"}).json()[0]["id"]
    r = c.post("/api/connections", json={"name": name, "date": "1991-02-02",
                                         "time": time_, "city": cid})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_an_unchanged_patch_keeps_the_written_read(model):
    c = person("spend-patch@x.com", "Eve")
    cid = add(c)
    for _ in range(10):
        assert c.patch(f"/api/connections/{cid}", json={}).status_code == 200
        assert c.post(f"/api/connections/{cid}/reading").json()["fun"]
    assert len(model) == 1


def test_a_label_or_the_same_kind_again_is_not_a_change(model):
    c = person("spend-label@x.com", "Eve")
    cid = add(c)
    c.post(f"/api/connections/{cid}/reading")
    kind = c.get(f"/api/connections/{cid}").json()["kind"]
    c.patch(f"/api/connections/{cid}", json={"kind": kind})
    c.patch(f"/api/connections/{cid}", json={"time": "11:00"})
    c.post(f"/api/connections/{cid}/reading")
    assert len(model) == 1


def test_a_real_change_rewrites_the_read(model):
    c = person("spend-change@x.com", "Eve")
    cid = add(c)
    c.post(f"/api/connections/{cid}/reading")
    c.patch(f"/api/connections/{cid}", json={"time": "17:30"})
    c.post(f"/api/connections/{cid}/reading")
    assert len(model) == 2


def test_written_reads_are_capped_per_day_with_a_plain_message(model):
    c = person("spend-cap@x.com", "Eve")
    seen = []
    for i in range(spend.FREE_DAILY["compat"] + 3):
        cid = add(c, f"T{i}")
        seen.append(c.post(f"/api/connections/{cid}/reading").json())
        c.delete(f"/api/connections/{cid}")
    assert len(model) == spend.FREE_DAILY["compat"]
    last = seen[-1]
    assert last["capped"] is True and "tomorrow" in last["message"]
    assert last["fun"]                     # the authored read still shows


def test_placement_long_reads_are_capped(model, monkeypatch):
    c = person("spend-place@x.com", "Eve")
    wrote = []
    real = server.placement.build

    def build(*a, want_narrative=False, **k):
        out = real(*a, **k)
        if want_narrative:
            wrote.append(1)
            out["generated"] = False       # a failure: not cached, so it retries
        return out
    monkeypatch.setattr(server.placement, "build", build)
    for _ in range(spend.FREE_DAILY["placement"] + 4):
        r = c.post("/api/placement/sun/narrative")
        body = r.json() if r.headers["content-type"].startswith("application/json") else None
    assert len(wrote) == spend.FREE_DAILY["placement"]
    assert body["capped"] is True and body["message"]


def test_the_natal_reading_falls_back_to_authored_when_spent(model, monkeypatch):
    c = person("spend-natal@x.com", "Eve")
    asked = []
    real = server.reading.build

    def build(*a, want_prose=True, **k):
        asked.append(want_prose)
        return real(*a, want_prose=False, **k)   # never a real model call
    monkeypatch.setattr(server.reading, "build", build)
    for _ in range(spend.FREE_DAILY["natal"] + 2):
        r = c.get("/api/reading")
        assert r.status_code == 200
    assert asked.count(True) == spend.FREE_DAILY["natal"]
    assert asked[-1] is False and r.json()["capped"] is True


def test_memory_extraction_is_metered_and_falls_back_to_the_heuristic(model, monkeypatch):
    c = person("spend-mem@x.com", "Eve")
    uid = server.auth.uid_for("spend-mem@x.com", create=False)
    for _ in range(spend.FREE_DAILY["memory"]):
        assert spend.claim(uid, "memory")
    used = []
    monkeypatch.setattr(M, "extract_day", lambda uid_, iso, *a, **k: used.append("model") or {"date": iso})
    monkeypatch.setattr(M, "extract_day_heuristic",
                        lambda uid_, iso, *a, **k: used.append("heuristic") or {"date": iso})
    monkeypatch.setattr(server.thread, "dates", lambda u: ["2020-01-01"])
    monkeypatch.setattr(server.thread, "for_date", lambda u, d: [])
    c.post("/api/rollover")
    assert used == ["heuristic"]


def test_a_refused_claim_records_nothing():
    for _ in range(spend.FREE_DAILY["natal"]):
        assert spend.claim("spend-unit", "natal")
    assert not spend.claim("spend-unit", "natal")
    assert spend.used("spend-unit", "natal") == spend.FREE_DAILY["natal"]
