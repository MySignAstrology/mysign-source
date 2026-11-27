"""
The ops index (app/opsindex.py) and the paged /ops lists built on it: no
request walks every account, every page is audited, and the index heals.
Also /api/ops/health (app/health.py), with CloudWatch stubbed.
"""
import datetime as dt
import os
import pathlib
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-opsidx-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import activity, health, operators, opsindex, server
from engine import store
from engine.store import load, save
from tests.test_app import last_uid, onboard, sign_in

OPS = {"x-ops-token": "t0k"}


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "t0k")
    opsindex.backfill(None)
    return TestClient(server.app)


def _walk(c, path, key, limit):
    """Every row of a paged list, and how many pages it took."""
    rows, cursor, pages = [], None, 0
    while True:
        q = f"{path}{'&' if '?' in path else '?'}limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        j = c.get(q, headers=OPS).json()
        rows += j[key]
        pages += 1
        cursor = j["next_cursor"]
        if not cursor:
            return rows, pages
        assert pages < 500


# ------------------------------------------------------------------ index

def test_sign_up_is_indexed_newest_first(c):
    sign_in(c, "idx.new@x.com")
    uid = last_uid()
    rows, _ = opsindex.page(None, 5000, budget=50000)
    assert uid in [u for u, _ in rows]
    assert opsindex.users_total() >= 1


def test_coming_back_moves_the_row_and_stale_rows_are_dropped(c):
    sign_in(c, "idx.move@x.com")
    uid = last_uid()
    acct = load(uid, "account", None)
    old = opsindex.when(acct)
    opsindex.put(uid, old - 5000)                 # a leftover from a crash
    acct["last_seen"] = int(time.time()) + 10
    opsindex.put(uid, acct["last_seen"])
    save(uid, "account", acct)
    opsindex.moved(uid, old, acct["last_seen"])
    rows, _ = opsindex.page(None, 500, budget=5000)
    assert [u for u, _ in rows].count(uid) == 1
    assert load(opsindex.PK, opsindex.row_sk(uid, old - 5000), None) is None


def test_a_filter_that_matches_nobody_answers_with_a_cursor_not_a_scan(c):
    for i in range(3):
        sign_in(c, f"idx.budget{i}@x.com")
    rows, nxt = opsindex.page(None, 5, accept=lambda u, a: False, budget=2)
    assert rows == [] and nxt and opsindex.SK_RE.match(opsindex._dec(nxt, opsindex.SK_RE))


def test_backfill_indexes_accounts_from_before_the_index(c):
    sign_in(c, "idx.old@x.com")
    uid = last_uid()
    store.delete(opsindex.PK, opsindex.row_sk(uid, opsindex.when(load(uid, "account", None))))
    store.delete(opsindex.PK, opsindex.META)
    opsindex._done = False
    assert opsindex.users_total() is None
    while not opsindex.ensure()["done"]:
        pass
    rows, _ = opsindex.page(None, 500, budget=5000)
    assert uid in [u for u, _ in rows]
    assert opsindex.users_total() == len(set(store.members("users")) | set(load("_auth", "users", []) or []))


def test_deleting_an_account_leaves_no_row_and_no_flags(c):
    sign_in(c, "idx.gone@x.com"); onboard(c)
    uid = last_uid()
    at = dt.datetime.now().isoformat(timespec="seconds")
    save(uid, "reports", [{"id": "r1", "reason": "", "at": at}])
    opsindex.flag(uid, at, "report", "r1", kind="report")
    assert any(r["uid"] == uid for r in opsindex.flagged(at[:10])[0])
    before = opsindex.users_total()
    assert c.delete("/api/account").status_code in (200, 204)
    assert all(r["uid"] != uid for r in opsindex.flagged(at[:10])[0])
    assert uid not in [u for u, _ in opsindex.page(None, 500, budget=5000)[0]]
    assert opsindex.users_total() == before - 1


# ------------------------------------------------------------------ paged lists

def test_people_list_pages_through_everyone_once(c):
    mine = set()
    for i in range(5):
        sign_in(c, f"idx.page{i}@x.com"); onboard(c)
        mine.add(last_uid())
    rows, pages = _walk(c, "/api/ops/activity/users?days=7", "users", 4)
    uids = [r["uid"] for r in rows]
    assert len(uids) == len(set(uids)) and mine <= set(uids) and pages >= 2


def test_people_list_finds_an_exact_address_and_filters_by_stage(c):
    sign_in(c, "idx.exact.person@x.com")
    uid = last_uid()
    j = c.get("/api/ops/activity/users?q=idx.exact.person@x.com", headers=OPS).json()
    assert j["exact"] and [r["uid"] for r in j["users"]] == [uid] and j["next_cursor"] is None
    j = c.get(f"/api/ops/activity/users?q={uid}", headers=OPS).json()
    assert [r["uid"] for r in j["users"]] == [uid]
    j = c.get("/api/ops/activity/users?stage=onboarded&limit=200", headers=OPS).json()
    assert j["users"] and all(r["stage"] == "onboarded" for r in j["users"])
    assert uid not in [r["uid"] for r in j["users"]]      # signed in, never onboarded


def test_roster_pages_too(c):
    for i in range(3):
        sign_in(c, f"idx.roster{i}@x.com"); onboard(c)
    rows, pages = _walk(c, "/api/ops/roster", "participants", 2)
    uids = [r["uid"] for r in rows]
    assert len(uids) == len(set(uids)) and pages >= 2
    assert {"red", "amber", "quiet_days", "quota"} <= set(rows[0])


@pytest.mark.parametrize("path", ["/api/ops/roster", "/api/ops/activity/users", "/api/ops/flagged"])
def test_a_bad_cursor_is_a_400(c, path):
    assert c.get(f"{path}?cursor=nope", headers=OPS).status_code == 400


def test_every_page_is_audited(c, monkeypatch):
    seen = []
    monkeypatch.setattr(operators, "audit", lambda ctx, action, subject=None, **p: seen.append((action, p)))
    for i in range(2):
        sign_in(c, f"idx.audit{i}@x.com")
    j = c.get("/api/ops/activity/users?limit=1", headers=OPS).json()
    c.get(f"/api/ops/activity/users?limit=1&cursor={j['next_cursor']}", headers=OPS)
    j = c.get("/api/ops/roster?limit=1", headers=OPS).json()
    c.get(f"/api/ops/roster?limit=1&cursor={j['next_cursor']}", headers=OPS)
    assert [(a, p.get("page")) for a, p in seen] == [
        ("activity.users", False), ("activity.users", True), ("roster", False), ("roster", True)]


def test_no_list_reads_every_account(c, monkeypatch):
    """The point of the index: a page reads its page."""
    for i in range(12):
        sign_in(c, f"idx.reads{i}@x.com"); onboard(c)
    reads = []
    real = activity.load

    def counting(pk, sk, default=None):
        if sk == "account":
            reads.append(pk)
        return real(pk, sk, default)
    monkeypatch.setattr(opsindex, "load", counting)
    monkeypatch.setattr(activity, "load", counting)
    c.get("/api/ops/activity/users?limit=5", headers=OPS)
    assert len(reads) <= 15
    reads.clear()
    c.get("/api/ops/activity/overview?days=7", headers=OPS)
    assert reads == []


# ------------------------------------------------------------------ health

class FakeCW:
    def __init__(self):
        self.calls = 0

    def get_metric_data(self, MetricDataQueries, StartTime, EndTime, **kw):
        self.calls += 1
        t = StartTime
        out = []
        for q in MetricDataQueries:
            v = {"app_invocations": 100.0, "app_errors": 2.0, "app_duration_p95_ms_all": 812.345,
                 "app_m_messages": 7.0, "ses_send": 3.0}.get(q["Id"])
            out.append({"Id": q["Id"], "Timestamps": [t] if v is not None else [],
                        "Values": [v] if v is not None else [], "StatusCode": "Complete"})
        return {"MetricDataResults": out}


class FakeLogs:
    def __init__(self, status="Complete"):
        self.status, self.started, self.polled = status, 0, 0

    def start_query(self, **kw):
        assert "@initDuration" in kw["queryString"] and kw["logGroupName"] == "/mysign/app"
        self.started += 1
        return {"queryId": "q1"}

    def get_query_results(self, queryId):
        self.polled += 1
        if self.status != "Complete":
            return {"status": self.status, "results": []}
        return {"status": "Complete", "results": [[
            {"field": "bin(1d)", "value": "2026-10-27 00:00:00.000"},
            {"field": "cold", "value": "4"}, {"field": "init_ms", "value": "4500.5"},
            {"field": "init_max", "value": "6100"}]]}


@pytest.fixture
def aws(monkeypatch):
    health.reset()
    monkeypatch.setenv("MYSIGN_FN_NAME", "mysign-Fn")
    monkeypatch.setenv("MYSIGN_CRON_FN_NAME", "mysign-Cron")
    monkeypatch.setenv("MYSIGN_APP_LOG_GROUP", "/mysign/app")
    monkeypatch.setattr(health, "POLL_S", 0)
    fakes = {"cloudwatch": FakeCW(), "logs": FakeLogs()}
    monkeypatch.setattr(health, "_client", lambda svc, region: fakes[svc])
    yield fakes
    health.reset()


def test_health_says_unavailable_off_lambda_quickly(c, monkeypatch):
    health.reset()
    for k in ("MYSIGN_FN_NAME", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(k, raising=False)
    t0 = time.monotonic()
    j = c.get("/api/ops/health", headers=OPS).json()
    assert j["unavailable"] is True and j["reason"] and time.monotonic() - t0 < 2


def test_health_never_raises_when_cloudwatch_fails(c, monkeypatch):
    health.reset()
    monkeypatch.setenv("MYSIGN_FN_NAME", "mysign-Fn")

    def boom(svc, region):
        raise RuntimeError("no credentials")
    monkeypatch.setattr(health, "_client", boom)
    r = c.get("/api/ops/health", headers=OPS)
    assert r.status_code == 200 and r.json()["unavailable"] is True
    health.reset()


def test_health_reads_lambda_app_email_and_cold_starts(c, aws):
    j = c.get("/api/ops/health?from=2026-10-27&to=2026-10-27", headers=OPS).json()
    app_fn = j["functions"]["app"]
    assert app_fn["name"] == "mysign-Fn" and app_fn["totals"]["invocations"] == 100
    assert app_fn["totals"]["error_rate"] == 0.02 and app_fn["totals"]["duration_p95_ms"] == 812.3
    assert j["functions"]["cron"]["totals"]["invocations"] == 0
    assert j["period"] == 3600 and len(app_fn["series"]) == 24
    assert j["app"]["totals"]["messages"] == 7 and j["email"]["totals"]["send"] == 3
    assert j["email"]["totals"]["bounce"] == 0 and j["email"]["totals"]["bounce_rate"] is None
    assert j["cold_starts"] == {"status": "complete", "total": 4, "init_ms_avg": 4500.5,
                                "init_ms_max": 6100.0,
                                "series": [{"t": "2026-10-27T00:00:00Z", "cold": 4, "init_ms": 4500.5}]}


def test_health_reads_edge_traffic_and_survives_without_it(c, aws, monkeypatch):
    url = "/api/ops/health?from=2026-10-27&to=2026-10-27"
    j = c.get(url, headers=OPS).json()
    assert j["edge"]["status"] == "unavailable" and j["functions"]["app"]["totals"]["invocations"] == 100
    health.reset()
    monkeypatch.setenv("MYSIGN_SITE_DISTRIBUTION", "E123")
    regions = []

    class Edge:
        def get_metric_data(self, MetricDataQueries, StartTime, **kw):
            got = {"edge_requests": 900.0, "edge_rate_5xx_all": 1.5}
            return {"MetricDataResults": [{"Id": q["Id"], "Timestamps": [StartTime] if q["Id"] in got else [],
                                           "Values": [got[q["Id"]]] if q["Id"] in got else []}
                                          for q in MetricDataQueries]}

    def client(svc, region):
        regions.append((svc, region))
        return Edge() if region == "us-east-1" else aws[svc]
    monkeypatch.setattr(health, "_client", client)
    j = c.get(url, headers=OPS).json()
    assert ("cloudwatch", "us-east-1") in regions
    assert j["edge"]["totals"]["requests"] == 900 and j["edge"]["totals"]["rate_5xx"] == 0.015
    assert j["edge"]["totals"]["rate_4xx"] is None and len(j["edge"]["series"]) == 24
    health.reset()

    def broken(svc, region):
        if region == "us-east-1":
            raise RuntimeError("no")
        return aws[svc]
    monkeypatch.setattr(health, "_client", broken)
    j = c.get(url, headers=OPS).json()
    assert j["edge"]["status"] == "unavailable" and j["email"]["totals"]["send"] == 3


def test_team_accounts_are_marked(monkeypatch):
    monkeypatch.delenv("MYSIGN_TEAM_MARKERS", raising=False)
    assert activity._is_team("a@example.test") is False            # nothing set: nobody is
    monkeypatch.setenv("MYSIGN_TEAM_MARKERS", "@Example.test, ,+qa")
    assert activity._is_team("A@example.TEST") and activity._is_team("x+qa@gmail.com")
    assert not activity._is_team("x@gmail.com") and not activity._is_team(None)


def test_health_is_cached_and_a_slow_query_is_asked_again_later(c, aws):
    aws["logs"].status = "Running"
    j = c.get("/api/ops/health?from=2026-10-01&to=2026-10-27", headers=OPS).json()
    assert j["cold_starts"] == {"status": "pending"} and j["cached"] is False and j["period"] == 86400
    aws["logs"].status = "Complete"
    j = c.get("/api/ops/health?from=2026-10-01&to=2026-10-27", headers=OPS).json()
    assert j["cached"] is True and j["cold_starts"]["total"] == 4
    assert aws["cloudwatch"].calls == 1 and aws["logs"].started == 1


def test_health_ranges_are_capped(c, aws):
    j = c.get("/api/ops/health?from=2026-01-01&to=2026-10-27", headers=OPS).json()
    a, b = dt.date.fromisoformat(j["from"]), dt.date.fromisoformat(j["to"])
    assert (b - a).days + 1 == health.MAX_DAYS
