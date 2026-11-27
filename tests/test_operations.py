"""
Quota, export, and the operator view.

Everything here is about what happens around a conversation rather than inside
one: what it costs, what someone can take with them, and what a human running a
pilot can see.
"""
import os
import pathlib
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-ops-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)

import app.server as server
from engine import quota
from engine.store import load
from tests.test_app import last_uid, onboard, sign_in


@pytest.fixture
def c():
    return TestClient(server.app)


# ------------------------------------------------------------------- quota

def test_burst_is_refused_with_a_retry_after():
    uid = "quota-burst"
    now = time.time()
    for _ in range(quota.BURST_MAX):
        assert quota.check(uid, now)[0]
        quota.record(uid, now)
    ok, info = quota.check(uid, now)
    assert not ok and info["reason"] == "burst" and info["retry_after"] > 0


def test_burst_clears_once_the_window_passes():
    uid = "quota-window"
    now = time.time()
    for _ in range(quota.BURST_MAX):
        quota.record(uid, now)
    assert not quota.check(uid, now)[0]
    assert quota.check(uid, now + quota.BURST_WINDOW + 1)[0]


def test_a_refusal_does_not_consume_the_quota_it_was_refused_by():
    uid = "quota-norecord"
    now = time.time()
    for _ in range(quota.BURST_MAX):
        quota.record(uid, now)
    before = quota.usage(uid, now)["today"]
    quota.check(uid, now)
    quota.check(uid, now)
    assert not quota.claim(uid, now)[0]
    assert quota.usage(uid, now)["today"] == before


def test_the_daily_message_is_not_a_scolding():
    """Someone sending a hundred messages in a day is usually not abusing
    anything, and being told to go away is a product decision."""
    uid = "quota-daily"
    # Spread across one UTC day, or the burst window trips first and this
    # proves nothing about the daily ceiling.
    start = (time.time() // quota.DAY) * quota.DAY
    for i in range(quota.DAILY_MAX):
        quota.record(uid, start + i * (quota.BURST_WINDOW + 1))
    now = start + 4 * 3600
    ok, info = quota.check(uid, now)
    assert not ok and info["reason"] == "daily"
    assert "here tomorrow" in info["message"]
    for harsh in ("limit", "exceeded", "blocked", "too many"):
        assert harsh not in info["message"].lower()


def test_the_endpoint_enforces_it(c):
    sign_in(c, "qq@x.com"); onboard(c)
    seen = []
    for _ in range(quota.BURST_MAX + 2):
        with c.stream("POST", "/api/message", json={"text": "hi"}) as r:
            "".join(r.iter_text())
            seen.append(r.status_code)
    assert 429 in seen, "the model endpoint has no ceiling"
    i = seen.index(429)
    assert i <= quota.BURST_MAX


def test_claim_is_atomic_under_concurrency():
    """The timestamp list was read-modify-write: parallel requests all passed."""
    from concurrent.futures import ThreadPoolExecutor
    uid, now = "quota-race", time.time()
    with ThreadPoolExecutor(16) as ex:
        got = list(ex.map(lambda _: quota.claim(uid, now)[0], range(40)))
    assert sum(got) == quota.BURST_MAX


# ------------------------------------------------------------------ export

def test_export_carries_everything_and_downloads(c):
    sign_in(c, "xx@x.com"); onboard(c)
    c.post("/api/checkin", json={"mood": 4, "text": "didn't sleep"})
    with c.stream("POST", "/api/message", json={"text": "should i answer him"}) as r:
        "".join(r.iter_text())

    r = c.get("/api/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    d = r.json()
    for key in ("account", "profile", "thread", "check_ins", "episodes",
                "questions", "patterns", "connections", "life_events", "reading"):
        assert key in d, f"export is missing {key}"
    assert d["account"]["email"] == "xx@x.com"
    assert len(d["thread"]) >= 2


def test_export_needs_a_session():
    assert TestClient(server.app).get("/api/export").status_code == 401


# ---------------------------------------------------------------- operator

def test_operator_view_is_absent_unless_configured(c, monkeypatch):
    """A 404 rather than a 401 — its existence is not advertised."""
    monkeypatch.delenv("MYSIGN_OPS_TOKEN", raising=False)
    for path in ("/api/ops/roster", "/api/ops/flagged", "/api/ops/user/u1"):
        assert c.get(path).status_code == 404, path


def test_operator_view_rejects_a_wrong_token(c, monkeypatch):
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "right")
    assert c.get("/api/ops/roster",
                 headers={"x-ops-token": "wrong"}).status_code == 401


def test_operator_sees_tiers_and_can_read_a_thread(c, monkeypatch):
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "right")
    sign_in(c, "pilot@x.com"); onboard(c)
    # Every fixture user is called Maya, so identify by uid or this picks
    # someone else's row.
    uid = last_uid()
    for msg in ("i want to kill myself", "should i end it with theo", "hello"):
        with c.stream("POST", "/api/message", json={"text": msg}) as r:
            "".join(r.iter_text())

    hdr = {"x-ops-token": "right"}
    rows = c.get("/api/ops/roster", headers=hdr).json()["participants"]
    me = [p for p in rows if p["uid"] == uid]
    assert me, "participant missing from the roster"
    row = me[0]
    assert row["red"] >= 1 and row["amber"] >= 1
    # Her Chicago date against the server's: a day apart for six hours of every
    # UTC day, which is when this used to fail on CI.
    assert row["quiet_days"] <= 1

    flagged = c.get("/api/ops/flagged", headers=hdr).json()["flagged"]
    assert {f["tier"] for f in flagged} >= {"red", "amber"}

    convo = c.get(f"/api/ops/user/{row['uid']}", headers=hdr).json()
    assert convo["name"] == "Maya" and len(convo["messages"]) >= 6


def test_reading_a_thread_is_logged(c, monkeypatch, capsys):
    """Looking at someone's conversation is a thing that should leave a trace."""
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "right")
    from engine.store import save
    sign_in(c, "audit@x.com"); onboard(c)
    uid = last_uid()
    save(uid, "reports", [{"id": "x", "reason": "", "at": "2026-10-27T10:00:00"}])
    capsys.readouterr()
    assert c.get(f"/api/ops/user/{uid}", headers={"x-ops-token": "right"}).status_code == 200
    assert '"event": "ops.read_thread"' in capsys.readouterr().out


def test_an_unflagged_conversation_cannot_be_opened(c, monkeypatch):
    """The consent screen says nobody reads conversations except on a safety
    flag or a report. The operator console has to make that true."""
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "right")
    sign_in(c, "private@x.com"); onboard(c)
    uid = last_uid()
    with c.stream("POST", "/api/message", json={"text": "just a normal day"}) as r:
        "".join(r.iter_text())
    assert c.get(f"/api/ops/user/{uid}", headers={"x-ops-token": "right"}).status_code == 403
    with c.stream("POST", "/api/message", json={"text": "i want to kill myself"}) as r:
        "".join(r.iter_text())
    assert c.get(f"/api/ops/user/{uid}", headers={"x-ops-token": "right"}).status_code == 200


# ------------------------------------------------------- salience per user

def test_onboarding_fits_thresholds_to_the_chart(c):
    """
    Calibration is wrapped in a try/except so a failure cannot break a signup.
    That also means a NameError in it is invisible — which is exactly what
    happened. This asserts the bands actually land.
    """
    sign_in(c, "cal@x.com"); onboard(c)
    uid = last_uid()
    bands = load(uid, "salience_bands", None)
    assert bands, "no per-user thresholds were stored"
    assert len(bands) == 3
    high, medium, low = (b[0] for b in bands)
    assert high > medium > low == 0
    assert [b[1] for b in bands] == ["high", "medium", "low"]


def test_the_context_uses_her_thresholds_not_the_global_ones(c):
    import datetime as dt

    from engine import context
    from engine.chart import aspects, jd_ut, natal, positions

    sign_in(c, "cal2@x.com"); onboard(c)
    uid = last_uid()

    nat = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)
    day = dt.date(2026, 8, 22)
    j = jd_ut(day.year, day.month, day.day, 17.0)
    sky = {"aspects_to_natal": aspects(positions(j), nat), "upcoming": [],
           "moon": {"phase": "waxing gibbous", "illumination_pct": 74},
           "moon_sign": "Sagittarius", "active_houses": [1]}

    _, with_hers = context.build(uid, nat, sky, {"mood": 5, "text": ""}, today=day)
    from engine.store import save
    save(uid, "salience_bands", None)
    _, with_global = context.build(uid, nat, sky, {"mood": 5, "text": ""}, today=day)

    # Same sky, same score — the banding is what differs.
    assert with_hers["score"] == with_global["score"]
    assert {with_hers["band"], with_global["band"]} <= {"high", "medium", "low"}


# ------------------------------------------------------------------ metrics

def test_metrics_are_emitted_in_a_shape_cloudwatch_understands(c, capsys):
    import json as _json
    sign_in(c, "met@x.com"); onboard(c)
    capsys.readouterr()
    with c.stream("POST", "/api/message", json={"text": "i want to kill myself"}) as r:
        "".join(r.iter_text())
    lines = [_json.loads(x) for x in capsys.readouterr().out.splitlines()
             if x.startswith("{")]
    metrics = [d for d in lines if d.get("event") == "metric"]
    assert metrics, "no metrics emitted"
    names = set()
    for d in metrics:
        block = d["_aws"]["CloudWatchMetrics"][0]
        assert block["Namespace"] == "MySign"
        for m in block["Metrics"]:
            names.add(m["Name"])
            assert m["Name"] in d, "the metric value is not on the record"
        # dimensions must be present as top-level keys, or CloudWatch drops them
        for dim in block["Dimensions"][0]:
            assert dim in d
    assert {"Messages", "RedTierEvents", "RequestLatency"} <= names


def test_metric_dimensions_never_include_a_user(c, capsys):
    """One metric per person is a bill, not an insight."""
    import json as _json
    sign_in(c, "met2@x.com"); onboard(c)
    capsys.readouterr()
    with c.stream("POST", "/api/message", json={"text": "hello"}) as r:
        "".join(r.iter_text())
    for line in capsys.readouterr().out.splitlines():
        if not line.startswith("{"):
            continue
        d = _json.loads(line)
        if d.get("event") != "metric":
            continue
        for dim in d["_aws"]["CloudWatchMetrics"][0]["Dimensions"][0]:
            assert dim not in ("uid", "user", "email"), f"high-cardinality dim: {dim}"
