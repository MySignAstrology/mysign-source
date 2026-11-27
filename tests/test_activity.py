"""
The activity dashboard: screens and taps in, a journey out — and nothing
anyone typed, ever.
"""
import json
import os
import pathlib
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-act-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import activity, server
from engine import store
from tests.test_app import last_uid, onboard, sign_in

OPS = {"x-ops-token": "t0k"}


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "t0k")
    return TestClient(server.app)


def _batch(c, events, session="sess-abc123"):
    return c.post("/api/activity", json={"session": session, "events": events})


def _ev(type_, name, screen="home", **kw):
    return {"type": type_, "name": name, "screen": screen, "at": time.time() * 1000, **kw}


# ------------------------------------------------------------------ intake

def test_screens_and_taps_are_kept(c):
    sign_in(c, "act1@x.com"); onboard(c)
    r = _batch(c, [_ev("screen", "home"), _ev("tap", "nav_ppl"), _ev("screen", "people", "people")])
    assert r.json()["kept"] == 3
    j = c.get(f"/api/ops/activity/user/{last_uid()}", headers=OPS).json()
    names = [e["name"] for s in j["sessions"] for e in s["events"] if e["type"] != "server"]
    assert names == ["home", "nav_ppl", "people"]
    assert j["sessions"][0]["path"][-2:] == ["home", "people"]


def test_nothing_typed_gets_through(c):
    """Free text in any field is dropped, not stored: props are an allowlist
    of short enumerable things, names must look like control ids."""
    sign_in(c, "act2@x.com"); onboard(c)
    uid = last_uid()
    secret = "I think my husband is cheating"
    _batch(c, [
        _ev("tap", secret),                                   # not an id
        _ev("tap", "send", props={"text": secret}),            # not an allowed prop
        _ev("tap", "send", props={"kind": {"nested": secret}}),
        _ev("message", "send"),                               # not a type
        _ev("server", "message_sent"),                        # the app can't forge server events
    ])
    raw = json.dumps([store.load(uid, k, None) for k in store.keys(uid) if k.startswith("act-")])
    assert secret not in raw and "cheating" not in raw
    j = c.get(f"/api/ops/activity/user/{uid}", headers=OPS).json()
    app_events = [e for s in j["sessions"] for e in s["events"] if e["type"] != "server"]
    assert [e["name"] for e in app_events] == ["send", "send"]
    assert all(e["props"] == {} for e in app_events)


def test_long_prop_values_are_cut(c):
    sign_in(c, "act3@x.com"); onboard(c)
    _batch(c, [_ev("change", "n_time", props={"value": "x" * 500})])
    j = c.get(f"/api/ops/activity/user/{last_uid()}", headers=OPS).json()
    ev = next(e for s in j["sessions"] for e in s["events"] if e["name"] == "n_time")
    assert len(ev["props"]["value"]) == 16


def test_a_phone_clock_in_the_future_is_corrected(c):
    sign_in(c, "act4@x.com"); onboard(c)
    _batch(c, [{"type": "tap", "name": "send", "at": (time.time() + 10 * 365 * 86400) * 1000}])
    j = c.get(f"/api/ops/activity/user/{last_uid()}", headers=OPS).json()
    ev = next(e for s in j["sessions"] for e in s["events"] if e["name"] == "send")
    assert abs(ev["at"] - time.time()) < 60


def test_batches_are_capped(c):
    sign_in(c, "act5@x.com"); onboard(c)
    r = _batch(c, [_ev("tap", "send")] * 50)
    assert r.json()["kept"] == 50
    assert _batch(c, [_ev("tap", "send")] * 51).status_code == 422


def test_needs_consent(c):
    code = c.post("/api/auth/request", json={"email": "act6@x.com"}).json()["dev_code"]
    c.post("/api/auth/verify", json={"email": "act6@x.com", "code": code})
    assert _batch(c, [_ev("tap", "send")]).status_code in (401, 403)


# ----------------------------------------------------------------- opt-out

def test_switching_it_off_stops_and_deletes(c):
    sign_in(c, "act7@x.com"); onboard(c)
    uid = last_uid()
    _batch(c, [_ev("tap", "send")])
    assert any(k.startswith("act-") for k in store.keys(uid))
    assert c.post("/api/privacy/analytics", json={"off": True}).json() == {"off": True}
    assert not any(k.startswith("act-") for k in store.keys(uid))
    assert _batch(c, [_ev("tap", "send")]).json()["kept"] == 0
    c.post("/api/checkin", json={"mood": 6})                 # server-side events too
    assert not any(k.startswith("act-") for k in store.keys(uid))
    assert c.get("/api/state").json()["analytics_off"] is True
    row = next(r for r in c.get("/api/ops/activity/users", headers=OPS).json()["users"] if r["uid"] == uid)
    assert row["active_days"] == 0 and row["analytics_off"]


def test_server_side_actions_are_recorded(c):
    sign_in(c, "act8@x.com"); onboard(c)
    c.post("/api/checkin", json={"mood": 7, "text": "a secret note"})
    j = c.get(f"/api/ops/activity/user/{last_uid()}", headers=OPS).json()
    server_evs = {e["name"]: e for s in j["sessions"] for e in s["events"] if e["type"] == "server"}
    assert {"chart_built", "checked_in"} <= set(server_evs)
    assert server_evs["checked_in"]["props"] == {"mood": 7}
    assert "secret" not in json.dumps(j)


def test_export_includes_usage_and_deletion_removes_it(c):
    sign_in(c, "act9@x.com"); onboard(c)
    uid = last_uid()
    _batch(c, [_ev("tap", "nav_set")])
    ex = c.get("/api/export").json()
    assert any(e["n"] == "nav_set" for day in ex["app_usage"].values() for e in day)
    c.delete("/api/account")
    assert not store.keys(uid)
    assert c.get(f"/api/ops/activity/user/{uid}", headers=OPS).status_code == 404


# -------------------------------------------------------------- dashboard

def test_the_dashboard_apis_need_the_token(c):
    for path in ("overview", "users", "user/uxxxxxxxxxx"):
        assert c.get(f"/api/ops/activity/{path}").status_code == 401
        assert c.get(f"/api/ops/activity/{path}", headers={"x-ops-token": "no"}).status_code == 401


def test_the_dashboard_apis_are_absent_without_ops(c, monkeypatch):
    monkeypatch.delenv("MYSIGN_OPS_TOKEN")
    assert c.get("/api/ops/activity/overview", headers=OPS).status_code == 404


def test_overview_counts(c):
    sign_in(c, "act10@x.com"); onboard(c)
    _batch(c, [_ev("screen", "people", "people"), _ev("tap", "c_go", "people")])
    o = c.get("/api/ops/activity/overview?days=7", headers=OPS).json()
    assert o["kpis"]["active_today"] >= 1 and o["kpis"]["users_total"] >= 1
    assert any(s["name"] == "people" for s in o["top_screens"])
    assert any(a["name"] == "c_go" and a["kind"] == "tap" for a in o["top_actions"])
    assert len(o["series"]) == 7 and o["series"][-1]["events"] >= 2


def test_people_list_masks_email_and_shows_stage(c):
    sign_in(c, "visible.person@x.com"); onboard(c)
    uid = last_uid()
    u = c.get("/api/ops/activity/users", headers=OPS).json()
    row = next(r for r in u["users"] if r["uid"] == uid)
    assert row["email"].startswith("v") and row["email"].endswith("@x.com")
    assert "visible.person" not in json.dumps(u)
    assert row["stage"] == "onboarded" and row["onboarded"]


def test_unknown_person_is_404(c):
    assert c.get("/api/ops/activity/user/unobody00000", headers=OPS).status_code == 404
    assert c.get("/api/ops/activity/user/..%2Fetc", headers=OPS).status_code == 404


def test_sessions_split_on_a_long_gap(c):
    sign_in(c, "act11@x.com"); onboard(c)
    uid = last_uid()
    now = time.time()
    activity.record(uid, "sess-one111", [
        {"type": "screen", "name": "home", "at": (now - 7200) * 1000},
        {"type": "tap", "name": "send", "at": (now - 7190) * 1000}])
    activity.record(uid, "sess-two222", [{"type": "screen", "name": "people", "at": now * 1000}])
    j = activity.journey(uid)
    app_sessions = [s for s in j["sessions"] if s["id"] != "server"]
    assert [s["path"] for s in app_sessions][:2] == [["people"], ["home"]]


def test_the_page_is_served_and_not_cached(c):
    r = c.get("/ops")
    assert r.status_code == 200 and "My Sign" in r.text
    assert r.headers["cache-control"] == "no-store"


# ------------------------------------------------- security audit L2

def test_made_up_names_cannot_grow_the_shared_counter_item_without_bound(c, monkeypatch):
    sign_in(c, "act-bloat@x.com"); onboard(c)
    agg = f"_act#{activity._day()}"
    _batch(c, [_ev("tap", "nav_ppl")])
    before = store.counters("_activity", agg)
    monkeypatch.setattr(activity, "MAX_AGG_FIELDS", len(before) + 3)
    r = _batch(c, [_ev("tap", f"made_up_{i}") for i in range(40)])
    assert r.status_code == 200 and r.json()["kept"] == 40      # events are still kept
    after = store.counters("_activity", agg)
    assert len(after) <= len(before) + 4                          # the cap, plus "tap:other"
    assert after.get("tap:other", 0) >= 36


def test_a_failing_counter_write_never_fails_the_request(c, monkeypatch):
    sign_in(c, "act-fail@x.com"); onboard(c)

    def full(*a, **k):
        raise RuntimeError("ValidationException: Item size has exceeded the maximum")
    monkeypatch.setattr(activity, "_bounded", full)
    assert _batch(c, [_ev("tap", "nav_ppl")]).status_code == 200
