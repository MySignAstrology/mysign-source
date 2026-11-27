"""
The analytics rollups (deploy/rollups): the SQL they send to Athena, what they
keep (no ids, small segments folded, a size cap), and the Lambda that runs
them — against a stubbed Athena, and, where duckdb is installed, the real SQL
on a handful of events.
"""
import ast
import datetime as dt
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
ROLLUPS = ROOT / "deploy" / "rollups"
sys.path.append(str(ROLLUPS))

import rollup_shape as S
import rollup_sql as Q

spec = importlib.util.spec_from_file_location("rollups_handler", ROLLUPS / "handler.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)

DAY = "2026-10-27"


def all_queries(db="mysign_analytics", day=DAY):
    for report in Q.REPORTS:
        for qname, sql in Q.queries(report, db, day).items():
            yield report, qname, sql


# ---------------------------------------------------------------- the SQL

def _stack_constant(name):
    """Read without importing (that needs aws_cdk, and deploy/ on the path)."""
    tree = ast.parse((ROOT / "deploy" / "analytics_stack.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == name:
            return ast.literal_eval(node.value)
    raise KeyError(name)


def test_report_names_agree_everywhere():
    from app import reports
    assert Q.REPORTS == reports.REPORTS == _stack_constant("ROLLUP_REPORTS") == tuple(Q.QUERIES)
    assert [k for k, _, _ in Q.FUNNEL] == [k for k, _ in reports.FUNNEL_STEPS]
    assert [(k, label) for k, label, _ in Q.FUNNEL] == list(reports.FUNNEL_STEPS)
    assert [(k, label) for k, label, _ in Q.FEATURES] == list(reports.FEATURES)
    assert set(S.SHAPERS) == set(Q.REPORTS)


def test_windows():
    assert Q.window("visits", DAY) == (DAY, DAY)
    assert Q.window("active", DAY) == ("2026-09-30", DAY)              # 28 days
    assert Q.window("signups", DAY) == ("2026-09-27", DAY)             # 30 days back
    assert Q.window("retention", DAY) == ("2026-07-29", DAY)           # 12 weeks + 6 days
    active = Q.queries("active", "db", DAY)["main"]
    assert "dt BETWEEN '2026-09-30' AND '2026-10-27'" in active
    assert "dt >= '2026-10-21'" in active                              # WAU: 7 days


def test_stage_database_and_both_tables_in_every_query():
    for _, _, sql in all_queries("mysign_analytics_staging"):
        assert '"mysign_analytics_staging"."events"' in sql
        assert '"mysign_analytics_staging"."events_json"' in sql
        assert "mysign_analytics\"" not in sql.replace("mysign_analytics_staging", "")
    assert "INSERT" not in "".join(sql.upper() for _, _, sql in all_queries())


@pytest.mark.parametrize("db", ['x"; DROP', "Mysign", "", "a-b"])
def test_database_name_is_an_identifier_not_sql(db):
    with pytest.raises(ValueError):
        Q.queries("visits", db, DAY)


@pytest.mark.parametrize("day", ["2026-13-01", "2026-10-27' OR 1=1 --", "yesterday"])
def test_dates_are_parsed_before_they_reach_sql(day):
    with pytest.raises((ValueError, TypeError)):
        Q.queries("funnel", "db", day)


def _final_selects(tree, exp):
    """The SELECTs whose columns are the output: the outer one, each UNION branch."""
    node = tree.this if isinstance(tree, exp.Subquery) else tree
    if isinstance(node, exp.Union):
        return _final_selects(node.left, exp) + _final_selects(node.right, exp)
    return [node]


def test_no_ids_leave_any_query():
    sqlglot = pytest.importorskip("sqlglot")
    exp = sqlglot.exp
    for report, qname, sql in all_queries():
        tree = sqlglot.parse_one(sql, read="trino")
        for sel in _final_selects(tree, exp):
            assert isinstance(sel, exp.Select), (report, qname)
            for col in sel.expressions:
                name = col.alias_or_name
                assert name not in Q.IDS, (report, qname, name)
                for c in col.find_all(exp.Column):
                    if c.name in Q.IDS:
                        # Only inside an aggregate: count(DISTINCT uid) is a number.
                        assert c.find_ancestor(exp.AggFunc), (report, qname, col.sql())


def test_every_branch_prunes_partitions():
    sqlglot = pytest.importorskip("sqlglot")
    exp = sqlglot.exp
    for report, qname, sql in all_queries():
        tree = sqlglot.parse_one(sql, read="trino")
        for t in tree.find_all(exp.Table):
            if t.name in ("events", "events_json"):
                where = t.find_ancestor(exp.Select).args.get("where")
                assert where is not None and "dt" in {c.name for c in where.find_all(exp.Column)}, \
                    (report, qname, t.name)


# ---------------------------------------------------------------- the SQL, run

@pytest.fixture(scope="module")
def duck():
    duckdb = pytest.importorskip("duckdb")
    sqlglot = pytest.importorskip("sqlglot")
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    con.execute("CREATE SCHEMA db")
    ctx = ("STRUCT(utm_source VARCHAR, utm_medium VARCHAR, utm_campaign VARCHAR, "
           "referrer_domain VARCHAR, landing_path VARCHAR, country VARCHAR, "
           "device_class VARCHAR, os_family VARCHAR, browser_family VARCHAR)")
    for t in ("events", "events_json"):
        con.execute(f"CREATE TABLE db.{t} (event_id VARCHAR, ts BIGINT, anon_id VARCHAR, "
                    "uid VARCHAR, session_id VARCHAR, type VARCHAR, name VARCHAR, "
                    f"screen VARCHAR, props MAP(VARCHAR, VARCHAR), ctx {ctx}, dt VARCHAR, "
                    "source VARCHAR)")
    n = [0]

    def ev(day, hh, source, type_, name, anon=None, uid=None, sid=None, screen=None,
           props=None, utm=None, table="events"):
        n[0] += 1
        ts = int(dt.datetime.fromisoformat(f"{day}T{hh:02d}:00:00+00:00").timestamp() * 1000)
        c = {"utm_source": utm, "utm_medium": None, "utm_campaign": None,
             "referrer_domain": "google.com" if utm else None, "landing_path": "/",
             "country": "US", "device_class": "mobile", "os_family": None,
             "browser_family": None}
        props = props or {}
        con.execute(f"INSERT INTO db.{table} VALUES (?,?,?,?,?,?,?,?,"
                    "MAP(?::VARCHAR[], ?::VARCHAR[]),?,?,?)",
                    [f"e{n[0]}", ts + n[0], anon, uid, sid, type_, name, screen,
                     list(props), list(props.values()), c, day, source])

    week_ago = "2026-10-20"
    for i in range(8):
        a, u = f"a{i}", f"u{i}"
        ev(week_ago, 9, "site", "page", "home", anon=a, sid=f"s{i}", utm="tiktok")
        ev(DAY, 9, "site", "page", "home", anon=a, sid=f"t{i}", utm="tiktok")
        ev(DAY, 9, "site", "tap", "start_open", anon=a, sid=f"t{i}")
        ev(DAY, 10, "server", "server", "auth.code_requested", anon=a,
           props={"channel": "sms" if i % 2 else "email"})
        ev(DAY, 10, "server", "server", "auth.verified", anon=a,
           props={"channel": "sms", "new": "true"})
        ev(DAY, 10, "server", "server", "consented", anon=a, uid=u)
        ev(DAY, 10, "server", "server", "funnel.consented")
        ev(DAY, 10, "app", "open", "app", anon=a, uid=u, sid=f"x{i}", screen="home")
        for _ in range(i + 1):
            ev(DAY, 11, "app", "heartbeat", "app", anon=a, uid=u, sid=f"x{i}",
               screen="home")
        ev(DAY, 11, "app", "screen", "chart", anon=a, uid=u, sid=f"x{i}")
        ev(DAY, 12, "server", "server", "checked_in", uid=u, props={"mood": "good"})
        ev(DAY, 8, "server", "server", "push_sent", uid=u, props={"kind": "morning"})
        ev(DAY, 9, "app", "open", "push_opened", anon=a, uid=u, sid=f"x{i}",
           props={"kind": "morning"})
        ev(DAY, 9, "server", "server", "paywall_shown", uid=u, props={"source": "chat_cap"})
    ev(DAY, 13, "server", "server", "subscription_started", uid="u1",
       props={"tier": "plus", "source": "stripe"})
    # A pilot's hand grant from /ops: not revenue, not "paid", not activity.
    ev(DAY, 13, "server", "server", "subscription_started", uid="pilot",
       props={"tier": "plus", "source": "operator"})
    ev(DAY, 9, "site", "page", "home")                                   # GPC: no ids at all
    ev(DAY, 12, "backfill", "server", "funnel.onboarded", table="events_json")
    ev(week_ago, 10, "backfill", "server", "consented", uid="old", table="events_json")
    ev(DAY, 10, "backfill", "screen", "home", uid="old", table="events_json")

    def run(report, k=2):
        res = {}
        for q, sql in Q.queries(report, "db", DAY).items():
            cur = con.execute(sqlglot.transpile(sql, read="trino", write="duckdb")[0])
            cols = [c[0] for c in cur.description]
            res[q] = [{c: (str(v) if isinstance(v, dt.date) else
                           float(v) if type(v).__name__ == "Decimal" else v)
                       for c, v in zip(cols, row)} for row in cur.fetchall()]
        return S.shape(report, DAY, res, k)[0]

    return run


def test_sql_runs_visits_and_signups(duck):
    v = duck("visits")
    assert v["totals"] == {"visits": 9, "visitors": 8, "pageviews": 9, "private_visits": 1}
    assert v["by"]["utm_source"] == [{"key": "tiktok", "visits": 8, "visitors": 8,
                                      "pageviews": 8, "private_visits": 0}]
    s = duck("signups")
    assert s["code_requested"] == {"total": 8, "by_channel": {"sms": 4, "email": 4}}
    assert s["verified"]["new"] == 8 and s["conversion"] == 1.0
    assert s["attribution"]["by"]["utm_source"][0] == {"key": "tiktok", "signups": 8,
                                                       "from_site": 8}


def test_sql_runs_funnel_active_features(duck):
    f = {s["step"]: s["count"] for s in duck("funnel")["steps"]}
    assert f == {"visit": 9, "start_open": 8, "code_requested": 8, "verified": 8,
                 "consented": 8, "onboarded": 1, "first_message": 0, "returned_day2": 0,
                 "week_one": 0, "paywall_shown": 8, "paid": 1}
    a = duck("active")
    assert (a["dau"], a["new"], a["returning"]) == (9, 8, 1)          # "old" came back
    feats = {r["feature"]: r for r in duck("features")["features"]}
    assert feats["checkin"]["users"] == 8 and feats["reading"]["uses"] == 8
    assert feats["chat"]["uses"] == 0 and feats["chat"]["uses_per_user"] is None


def test_sql_runs_time_retention_notifications_money(duck):
    t = duck("time")
    assert (t["sessions"], t["users"], t["seconds"]) == (8, 8, 36 * 30)
    assert t["by_screen"] == [{"screen": "home", "seconds": 1080, "users": 8}]
    r = duck("retention")
    old = next(c for c in r["cohorts"] if c["cohort"] == "2026-10-19")
    assert old["users"] == 1 and old["d1"] == 0.0 and old["d7"] == 1.0
    assert old["w1"] is None and old["counts"]["w1_n"] == 0              # not matured yet
    n = duck("notifications")
    assert n["totals"]["sent"] == 8 and n["totals"]["checked_in"] == 8
    assert [h["hour"] for h in n["by_hour"]] == [8, 9]
    m = duck("monetization")
    assert m["totals"]["started"] == 1 and m["totals"]["conversion"] == 0.125
    assert m["totals"]["operator_grants"] == 1
    assert {r["key"]: r["started"] for r in m["by_source"]} == {
        "chat_cap": 0, "stripe": 1, "operator": 0}


def test_sql_runs_site_and_usage(duck):
    s = duck("site")
    assert {r["step"]: r["count"] for r in s["steps"]} == {
        "landed": 8, "hero": 0, "scrolled": 0, "opened": 8, "submitted": 0, "code_sent": 0,
        "verified": 0}                                  # the GPC page view is not a person
    ch = {r["key"]: r for r in s["channels"]}
    # The fixture's taps carry no referrer, so they land in "direct".
    assert ch["google"] == {"key": "google", "pageviews": 8, "events": 8, "scrolled": 0,
                            "opened": 0}
    assert ch["direct"]["pageviews"] == 1 and ch["direct"]["opened"] == 8
    assert s["totals"] == {"pageviews": 9, "countries": 1}
    assert s["taps"] == [{"key": "start_open", "uses": 8, "people": 8}] and s["sections"] == []
    assert sum(r["sessions"] for r in s["lengths"]) == 8 and s["lengths"][0]["key"] == "<2s"
    assert s["by"]["os_family"] == [{"key": "(none)", "pageviews": 9, "visitors": 8}]
    u = duck("usage")
    # 8 devices: page + tap; 8 each of open/push_opened/screen, 36 heartbeats.
    assert u["totals"]["site_events"] == 17 and u["totals"]["app_events"] == 60
    assert u["totals"]["app_opens"] == 8                              # push_opened isn't one
    assert u["totals"]["events"] == sum(r["events"] for r in u["by_hour"])
    assert next(r for r in u["by_hour"] if r["hour"] == 9)["pageviews"] == 9
    assert {"key": "chart", "uses": 8, "users": 8} in u["screens"]
    assert next(r for r in u["server"] if r["key"] == "checked_in")["uses"] == 8


def test_channels_in_order(duck):
    """The first rule that fits wins: a paid ad from Instagram is paid."""
    keys = [k for k, _ in Q.CHANNELS]
    assert keys.index("paid") < keys.index("instagram")
    sql = Q.queries("site", "db", DAY)["channels"]
    assert sql.index("THEN 'paid'") < sql.index("THEN 'instagram'") and "ELSE 'direct'" in sql


# ---------------------------------------------------------------- shaping

def test_site_names_are_capped_but_small_ones_kept():
    """Event names are the client's to send; nobody can grow a summary with them."""
    names = [{"dim": "tap", "key": f"t{i:03d}", "uses": 1, "people": 1} for i in range(120)]
    res = {"steps": [{}], "channels": [], "names": names, "lengths": [], "clients": [
        {"dim": "utm_medium", "key": "paid", "pageviews": 2, "visitors": 2, "countries": 1},
        {"dim": "os_family", "key": "ios", "pageviews": 2, "visitors": 2, "countries": 1}]}
    data, _ = S.shape("site", DAY, res)
    assert len(data["taps"]) == S.KEEP + 1 and data["taps"][-1]["key"] == "(other)"
    assert data["by"]["os_family"][0]["key"] == "ios" and data["by"]["utm_medium"] == []



def test_small_segments_fold_into_other_and_vanish_when_still_small():
    rows = [{"key": "big", "n": 9}, {"key": "a", "n": 3}, {"key": "b", "n": 2},
            {"key": "c", "n": 1}]
    assert S.fold(rows, "n", k=5) == [{"key": "big", "n": 9}, {"key": "(other)", "n": 6}]
    assert S.fold(rows[1:3], "n", k=5) == [{"key": "(other)", "n": 5}]
    assert S.fold(rows[2:], "n", k=5) == []


def test_visits_totals_are_never_suppressed():
    rows = [{"dim": "total", "key": None, "visits": 2, "visitors": 2, "pageviews": 3,
             "private_visits": 0},
            {"dim": "country", "key": "IS", "visits": 2, "visitors": 2, "pageviews": 3,
             "private_visits": 0}]
    data, agg = S.shape("visits", DAY, {"main": rows})
    assert data["totals"]["visitors"] == 2 and data["by"]["country"] == []
    assert not any(r.get("key") == "IS" for r in agg)


def test_a_row_with_an_id_is_refused():
    with pytest.raises(ValueError):
        S.check_no_ids([{"dt": DAY, "uid": "u1", "n": 1}])


def test_cap_keeps_the_top_and_folds_the_tail():
    rows = [{"key": f"k{i:05d}" + "x" * 40, "visitors": 10_000 - i} for i in range(9000)]
    data = {"totals": {"visitors": 1}, "by": {"landing_path": rows}}
    out = S.cap(data, limit=20_000)
    seg = out["by"]["landing_path"]
    assert out["truncated"] is True and len(json.dumps(out)) <= 20_000
    assert seg[0] == rows[0] and seg[-1]["key"] == "(other)"
    assert seg[-1]["truncated"] == 9000 - (len(seg) - 1)
    assert sum(r["visitors"] for r in seg) == sum(r["visitors"] for r in rows)
    assert S.cap({"a": 1}) == {"a": 1}


# ---------------------------------------------------------------- the plan

def at(hour, day=DAY):
    return dt.datetime.fromisoformat(f"{day}T{hour:02d}:05:00+00:00")


def test_hourly_redoes_yesterday_until_three():
    early = H.plan({"mode": "hourly"}, now=at(1))
    assert {d for _, d, _ in early} == {DAY, "2026-10-26"}
    late = H.plan({"mode": "hourly"}, now=at(5))
    assert {d for _, d, _ in late} == {DAY}
    assert all(not final for _, _, final in early + late)
    assert "retention" not in {r for r, _, _ in early}


def test_daily_is_final_for_yesterday_with_retention():
    work = H.plan({"mode": "daily"}, now=at(2))
    assert {d for _, d, _ in work} == {"2026-10-26"} and all(f for _, _, f in work)
    assert {r for r, _, _ in work} == set(Q.REPORTS)


def test_a_range_rerun():
    work = H.plan({"from": "2026-10-25", "to": DAY, "reports": ["funnel"]}, now=at(9))
    assert work == [("funnel", "2026-10-25", True), ("funnel", "2026-10-26", True),
                    ("funnel", DAY, False)]
    for bad in ({"mode": "weekly"}, {"from": DAY, "to": "2026-10-01"},
                {"from": "2025-01-01", "to": DAY}, {"mode": "daily", "reports": ["nope"]}):
        with pytest.raises(ValueError):
            H.plan(bad, now=at(9))


# ---------------------------------------------------------------- Athena, stubbed

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeAthena:
    """Each query takes `polls` polls; `fail` ones fail, `hang` ones never finish."""

    def __init__(self, rows=None, fail=(), hang=(), polls=2, page=2):
        self.rows, self.fail, self.hang, self.polls, self.page = rows or {}, fail, hang, polls, page
        self.started, self.stopped, self.seen, self.sql = [], [], {}, {}

    def _kind(self, sql):
        return next((k for k in list(self.fail) + list(self.hang) + list(self.rows) if k in sql),
                    None)

    def start_query_execution(self, QueryString, WorkGroup, QueryExecutionContext):
        assert WorkGroup == "wg" and QueryExecutionContext["Database"] == "db"
        qid = f"q{len(self.started)}"
        self.started.append(qid)
        self.sql[qid], self.seen[qid] = QueryString, 0
        return {"QueryExecutionId": qid}

    def batch_get_query_execution(self, QueryExecutionIds):
        out = []
        for qid in QueryExecutionIds:
            self.seen[qid] += 1
            kind = self._kind(self.sql[qid])
            if kind in self.hang or self.seen[qid] < self.polls:
                st = "RUNNING"
            else:
                st = "FAILED" if kind in self.fail else "SUCCEEDED"
            out.append({"QueryExecutionId": qid,
                        "Status": {"State": st, "StateChangeReason": "boom"},
                        "Statistics": {"DataScannedInBytes": 10}})
        return {"QueryExecutions": out}

    def get_query_results(self, QueryExecutionId, MaxResults, NextToken=None):
        cols, rows = self.rows.get(self._kind(self.sql[QueryExecutionId]), ([("n", "bigint")], []))
        start = int(NextToken or 0)
        data = rows[start:start + self.page]
        body = [{"Data": [{"VarCharValue": None if v is None else str(v)} for v in r]}
                for r in data]
        if start == 0:
            body = [{"Data": [{"VarCharValue": c} for c, _ in cols]}] + body
        page = {"ResultSet": {"ResultSetMetadata": {"ColumnInfo": [
            {"Name": c, "Type": t} for c, t in cols]}, "Rows": body}}
        if start + self.page < len(rows):
            page["NextToken"] = str(start + self.page)
        return page

    def stop_query_execution(self, QueryExecutionId):
        self.stopped.append(QueryExecutionId)


def test_runner_pages_and_types_results():
    rows = [("a", 1, 0.5, "true"), ("b", None, 1.25, "false"), ("c", 3, None, None)]
    fake = FakeAthena(rows={"SELECT": ([("k", "varchar"), ("n", "bigint"), ("x", "double"),
                                        ("ok", "boolean")], rows)})
    clock = Clock()
    a = H.Athena(fake, "wg", "db", clock=clock, sleep=clock.sleep)
    done, errors = a.run({"one": "SELECT 1"}, budget_s=60)
    assert errors == {}
    assert done["one"] == [{"k": "a", "n": 1, "x": 0.5, "ok": True},
                           {"k": "b", "n": None, "x": 1.25, "ok": False},
                           {"k": "c", "n": 3, "x": None, "ok": None}]
    assert a.scanned == 10


def test_runner_caps_inflight_and_reports_failures():
    fake = FakeAthena(fail=("BAD",))
    clock = Clock()
    a = H.Athena(fake, "wg", "db", max_inflight=3, clock=clock, sleep=clock.sleep)
    real = fake.batch_get_query_execution
    peak = []

    def spy(QueryExecutionIds):
        peak.append(len(QueryExecutionIds))
        return real(QueryExecutionIds)

    fake.batch_get_query_execution = spy
    jobs = {i: f"SELECT {i}" for i in range(7)} | {"x": "SELECT BAD"}
    done, errors = a.run(jobs, budget_s=60)
    assert max(peak) == 3 and len(done) == 7
    assert errors == {"x": "FAILED: boom"}


def test_runner_stops_what_outlives_the_budget():
    fake = FakeAthena(hang=("SLOW",))
    clock = Clock()
    a = H.Athena(fake, "wg", "db", max_inflight=1, clock=clock, sleep=clock.sleep)
    done, errors = a.run({"slow": "SELECT SLOW", "later": "SELECT 2"}, budget_s=10)
    assert errors == {"slow": "timeout", "later": "timeout (not started)"}
    assert fake.stopped == ["q0"] and done == {}


# ---------------------------------------------------------------- the run

class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body.decode()


class Conditional(Exception):
    def __init__(self):
        super().__init__("conditional")
        self.response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeDdb:
    def __init__(self):
        self.items = {}

    def put_item(self, TableName, Item, ConditionExpression=None, **kw):
        key = (Item["pk"]["S"], Item["sk"]["S"])
        old = self.items.get(key)
        if ConditionExpression and old and old["final"]["BOOL"]:
            raise Conditional()
        self.items[key] = Item


FUNNEL_COLS = [(k, "bigint") for k, _, _ in Q.FUNNEL] + [(k, "bigint") for k, _ in Q.FUNNEL_EXTRA]


def _funnel_run(final, ddb, s3, fail=()):
    fake = FakeAthena(rows={"SELECT": (FUNNEL_COLS, [tuple(range(len(FUNNEL_COLS)))])},
                      fail=fail)
    clock = Clock()
    return H.run([("funnel", DAY, final)], athena=H.Athena(fake, "wg", "db", clock=clock,
                                                          sleep=clock.sleep),
                 s3=s3, bucket="lake", ddb=ddb, table="t", budget_s=60)


def test_run_writes_agg_and_the_summary_item():
    s3, ddb = FakeS3(), FakeDdb()
    out = _funnel_run(True, ddb, s3)
    assert out["written"] == [f"funnel#{DAY}"] and not out["failed"]
    lines = s3.objects[f"agg/funnel/dt={DAY}/funnel.jsonl"].splitlines()
    assert json.loads(lines[1]) == {"dt": DAY, "step": "start_open", "count": 1}
    item = ddb.items[("_report", f"funnel#{DAY}")]
    body = json.loads(item["v"]["S"])
    assert item["final"] == {"BOOL": True} and body["final"] is True and body["version"] == 1
    assert body["data"]["steps"][0] == {"step": "visit", "label": "Site visit", "count": 0}
    assert "ttl" not in item


def test_a_partial_never_replaces_a_final():
    s3, ddb = FakeS3(), FakeDdb()
    _funnel_run(True, ddb, s3)
    before = dict(ddb.items)
    s3.objects.clear()
    out = _funnel_run(False, ddb, s3)
    assert out["skipped_final_exists"] == [f"funnel#{DAY}"] and ddb.items == before
    assert s3.objects == {}


def test_handler_raises_after_writing_what_it_could(monkeypatch):
    s3, ddb = FakeS3(), FakeDdb()
    fake = FakeAthena(rows={"SELECT": (FUNNEL_COLS, [tuple(range(len(FUNNEL_COLS)))])},
                      fail=("approx_percentile",))
    monkeypatch.setattr(H, "_clients", lambda: (fake, s3, ddb))
    clock, real = Clock(), H.Athena
    monkeypatch.setattr(H, "Athena", lambda c, wg, db: real(c, wg, db, clock=clock,
                                                           sleep=clock.sleep))
    monkeypatch.setenv("WORKGROUP", "wg")
    monkeypatch.setenv("DATABASE", "db")
    monkeypatch.setenv("LAKE_BUCKET", "lake")
    monkeypatch.setenv("APP_TABLE", "t")
    monkeypatch.setattr(H, "plan", lambda e: [("funnel", DAY, True), ("time", DAY, True)])
    with pytest.raises(RuntimeError, match="time#"):
        H.handler({"mode": "daily"})
    assert ("_report", f"funnel#{DAY}") in ddb.items
    assert f"agg/funnel/dt={DAY}/funnel.jsonl" in s3.objects
    assert not any("time" in k for k in s3.objects)


def test_without_a_table_only_s3_is_written():
    s3 = FakeS3()
    out = _funnel_run(True, None, s3)
    assert out["written"] == [f"funnel#{DAY}"] and s3.objects


def test_cli_dry_run_prints_sql(capsys):
    assert H.main(["--from", DAY, "--to", DAY, "--reports", "visits", "--dry-run"]) == \
        {"planned": 1}
    assert '"mysign_analytics"."events_json"' in capsys.readouterr().out


def test_backfill_tool_hands_its_range_to_the_rollups(monkeypatch):
    spec = importlib.util.spec_from_file_location("backfill_events_rollups",
                                                  ROOT / "tools" / "backfill_events.py")
    bf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bf)
    seen = {}
    fake = type("M", (), {"main": staticmethod(lambda a: seen.setdefault("a", a))})
    monkeypatch.setattr(bf, "rollups_module", lambda: fake)
    monkeypatch.setenv("MYSIGN_TABLE", "app-table")
    bf.main(["--bucket", "lake", "--rollup-from", "2026-10-01", "--rollup-to", DAY])
    assert seen["a"] == ["--from", "2026-10-01", "--to", DAY, "--workgroup", "mysign",
                         "--database", "mysign_analytics", "--bucket", "lake",
                         "--table", "app-table"]
