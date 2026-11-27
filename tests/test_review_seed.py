"""The App Review account: signs in with its fixed code and has something to show."""
import datetime as dt
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.server as server
from engine import funnel, unlocks
from engine.store import load

CODE = "482139076512"
EMAIL = "review@mysignapp.com"
TODAY = dt.date(2026, 11, 3)


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setenv("MYSIGN_REVIEW_EMAIL", EMAIL)
    monkeypatch.setenv("MYSIGN_REVIEW_CODE", CODE)
    spec = importlib.util.spec_from_file_location(
        "seed_review_account", Path(__file__).parent.parent / "tools/seed_review_account.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _signed_in():
    c = TestClient(server.app)
    assert c.post("/api/auth/request", json={"email": EMAIL}).json() == {"sent": True, "channel": "email"}
    assert c.post("/api/auth/verify", json={"email": EMAIL, "code": CODE}).status_code == 200
    return c


def test_a_reviewer_signs_in_to_a_full_account(tool):
    done = tool.seed(TODAY)
    c = _signed_in()
    state = c.get("/api/state").json()
    assert state["signed_in"] and not state.get("needs_consent")
    assert len(c.get("/api/connections").json()["connections"]) == 3
    memory = c.get("/api/memory").json()
    assert memory["days"] == 100 and memory["episodes"][0]["summary"]
    assert memory["patterns"], "Memory is what the review notes point at"
    assert load(done["uid"], f"checkin-{TODAY.isoformat()}", None) is None, "today is the reviewer's"
    assert load(done["uid"], f"checkin-{(TODAY - dt.timedelta(days=1)).isoformat()}", None)


def test_every_pattern_passes_the_real_verifier(tool):
    done = tool.seed(TODAY)
    assert done["patterns"]
    for p in done["patterns"]:
        assert unlocks.reverify(done["uid"], p)
    assert any("Sunday" in str(p["condition"]) for p in done["patterns"])
    assert len(unlocks.pending(done["uid"])) == 1, "one unlock sheet, not a queue of them"


def test_memory_does_not_quote_the_same_line_back_to_back(tool):
    done = tool.seed(TODAY)
    said = [e["summary"] for e in load(done["uid"], "episodes", [])]
    assert len(set(said)) >= 20
    assert not any(a == b for a, b in zip(said, said[1:]))


def test_a_reviewer_is_not_stopped_by_the_free_limit_or_counted_as_a_sale(tool):
    before = funnel._counts(dt.date.today())
    done = tool.seed(TODAY)
    from engine import plan
    assert plan.get(done["uid"])["tier"] == "plus"
    assert funnel._counts(dt.date.today()) == before


def test_running_it_again_moves_the_dates_and_adds_nothing(tool):
    done = tool.seed(TODAY)
    later = tool.seed(TODAY + dt.timedelta(days=7))
    assert later["uid"] == done["uid"] and later["people"] == 3
    eps = load(done["uid"], "episodes", [])
    assert len({e["date"] for e in eps}) == len(eps) == 107
    assert eps[-1]["date"] == (TODAY + dt.timedelta(days=6)).isoformat()


def test_it_gets_past_the_origin_guard_on_a_real_stack(tool, monkeypatch):
    """Every stack's secret has an origin token, and this calls the app directly."""
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "the-token")
    assert tool.seed(TODAY)["patterns"]


def test_it_will_not_touch_a_real_account(tool, monkeypatch):
    from app import auth
    monkeypatch.setenv("MYSIGN_REVIEW_EMAIL", "someone@real.example")
    c = TestClient(server.app)
    uid = auth.uid_for("someone@real.example")
    server.save(uid, "account", {**(server.load(uid, "account", {}) or {}), "consented_at": "2026-10-01T09:00:00"})
    with pytest.raises(SystemExit):
        tool.seed(TODAY)
    assert c.get("/api/state").status_code == 200


def test_it_needs_the_review_address_set(tool, monkeypatch):
    monkeypatch.delenv("MYSIGN_REVIEW_CODE")
    with pytest.raises(SystemExit):
        tool.seed(TODAY)


def test_the_sign_in_box_takes_the_whole_review_code():
    page = (Path(__file__).parent.parent / "app/static/index.html").read_text()
    assert "replace(/\\D/g,'').slice(0,12)" in page and 'id="fCode" inputmode="numeric" autocomplete="one-time-code" maxlength="12"' in page
    assert len(CODE) == 12 and CODE.isdigit()


def test_the_support_page_is_public_and_says_how_to_reach_a_person():
    r = TestClient(server.app).get("/support")
    assert r.status_code == 200
    assert "support@mysignapp.com" in r.text and "988" in r.text and "Delete everything" in r.text


def test_the_support_page_lists_phone_and_legal_address():
    # App Store Connect: the Support URL must lead to a legal address, email and phone.
    r = TestClient(server.app).get("/support")
    assert "tel:+16107511606" in r.text and "301 174th St, Sunny Isles Beach, FL 33160" in r.text
