"""
The operator reports: reading the rollups' summaries back (app/reports.py),
the live fallback from the DynamoDB counters, and /api/ops/report/*.
"""
import csv
import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient

import app.server as server
from app import reports
from engine import store
from tests.test_ops_auth import _wipe_operators, person


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("MYSIGN_OPS_TOKEN", raising=False)
    monkeypatch.delenv("MYSIGN_OPS_ADMINS", raising=False)
    for k in store.keys(reports.PK):
        store.delete(reports.PK, k)
    _wipe_operators()
    yield
    _wipe_operators()


def visits(n, country="US", key_visitors=None):
    return {"totals": {"visits": n, "visitors": n, "pageviews": 2 * n, "private_visits": 0},
            "by": {"country": [{"key": country, "visits": n, "visitors": key_visitors or n,
                                "pageviews": 2 * n, "private_visits": 0}]}}


# ---------------------------------------------------------------- reading

def test_get_reads_one_report_in_range_oldest_first():
    reports.put("visits", "2026-10-02", visits(6), final=False, now=100)
    reports.put("visits", "2026-10-01", visits(5), now=99)
    reports.put("visits", "2026-10-05", visits(7))
    reports.put("active", "2026-10-02", {"dau": 1})
    days = reports.get("visits", "2026-10-01", "2026-10-03")
    assert [(d["date"], d["final"], d["generated_at"]) for d in days] == [
        ("2026-10-01", True, 99), ("2026-10-02", False, 100)]
    assert days[0]["totals"]["visits"] == 5
    with pytest.raises(reports.UnknownReport):
        reports.get("nope", "2026-10-01", "2026-10-03")


def test_summary_sums_counts_and_merges_segments():
    reports.put("visits", "2026-10-01", visits(5))
    reports.put("visits", "2026-10-02", visits(6, key_visitors=6))
    reports.put("visits", "2026-10-03", visits(9, country="FR"))
    r = reports.report("visits", dt.date(2026, 10, 1), dt.date(2026, 10, 3))
    assert r["source"] == "rollup" and len(r["days"]) == 3
    assert r["totals"]["totals"] == {"visits": 20, "visitors": 20, "pageviews": 40,
                                     "private_visits": 0}
    assert r["totals"]["by"]["country"] == [
        {"key": "US", "visits": 11, "visitors": 11, "pageviews": 22, "private_visits": 0},
        {"key": "FR", "visits": 9, "visitors": 9, "pageviews": 18, "private_visits": 0}]


def test_summing_days_keeps_numeric_row_keys():
    for day in ("2026-10-01", "2026-10-02"):
        reports.put("notifications", day, {
            "totals": {"sent": 4, "opened": 1},
            "by_hour": [{"hour": 13, "sent": 3, "opened": 1}, {"hour": 9, "sent": 1, "opened": 0}]})
    n = reports.range_summary("notifications", "2026-10-01", "2026-10-02")
    assert [(r["hour"], r["sent"], r["open_rate"]) for r in n["by_hour"]] == [(9, 2, 0.0), (13, 6, 0.333)]


def test_summary_recomputes_rates_and_keeps_snapshots():
    for day, dau, wau, mau, new in (("2026-10-01", 10, 30, 60, 2), ("2026-10-02", 20, 35, 80, 4)):
        reports.put("active", day, {"dau": dau, "wau": wau, "mau": mau, "stickiness": 0.9,
                                    "new": new, "returning": dau - new})
    a = reports.range_summary("active", "2026-10-01", "2026-10-02")
    assert a == {"dau": 15.0, "dau_max": 20, "wau": 35, "mau": 80, "stickiness": 0.188,
                 "new": 6, "returning": 24}
    for day, shown, started in (("2026-10-01", 10, 1), ("2026-10-02", 30, 3)):
        reports.put("monetization", day, {
            "totals": {"shown": shown, "shown_users": shown, "started": started, "renewed": 0,
                       "canceled": 0, "conversion": 0.5},
            "by_source": [{"key": "chat", "shown": shown, "shown_users": shown,
                           "started": started, "renewed": 0, "canceled": 0,
                           "conversion": 0.5}], "by_tier": []})
    m = reports.range_summary("monetization", "2026-10-01", "2026-10-02")
    assert m["totals"]["conversion"] == 0.1 and m["by_source"][0]["conversion"] == 0.1
    reports.put("retention", "2026-10-01", {"as_of": "2026-10-01", "cohorts": [{"cohort": "a"}]})
    reports.put("retention", "2026-10-02", {"as_of": "2026-10-02", "cohorts": [{"cohort": "b"}]})
    r = reports.range_summary("retention", "2026-10-01", "2026-10-02")
    assert r["as_of"] == "2026-10-02" and r["cohorts"] == [{"cohort": "b"}]


# ---------------------------------------------------------------- the fallback

def test_live_fallback_from_counters_when_there_are_no_rollups():
    day = "2020-01-02"
    store.incr("_activity", f"_act#{day}", "people", by=7)
    store.incr("_activity", f"_act#{day}", "server:checked_in", by=4)
    store.incr("_activity", f"_act#{day}", "screen:chart", by=2)
    store.incr("_activity", f"_act#{day}", "tap:sky_banner", by=1)
    store.incr("_activity", f"_act#{day}", "server:paywall_shown", by=3)
    store.incr("_activity", f"_act#{day}", "server:subscription_started", by=1)
    store.incr("_study", f"funnel-{day}", "consented", by=2)
    store.incr("_study", f"funnel-{day}", "paid", by=1)
    lo, hi = "2020-01-01", "2020-01-03"
    a = reports.report("active", lo, hi)
    assert a["source"] == "live" and [d["date"] for d in a["days"]] == [day]
    assert a["days"][0] == {"date": day, "final": False, "generated_at": None, "dau": 7,
                                 "wau": None, "mau": None, "stickiness": None, "new": 2,
                                 "returning": 5}
    f = {s["step"]: s["count"] for s in reports.report("funnel", lo, hi)["totals"]["steps"]}
    # funnel.paid includes operator hand grants: not "paid" without the lake.
    assert f["consented"] == 2 and f["paid"] is None and f["visit"] is None
    feats = {r["feature"]: r for r in reports.report("features", lo, hi)["totals"]["features"]}
    assert feats["checkin"]["uses"] == 4 and feats["reading"]["uses"] == 2
    assert feats["sky"]["uses"] == 1 and feats["checkin"]["users"] is None
    m = reports.report("monetization", lo, hi)["totals"]["totals"]
    assert (m["shown"], m["started"]) == (3, None)
    assert reports.report("notifications", lo, hi)["unavailable"] is True
    v = reports.report("visits", lo, hi)
    assert v == {"source": "live", "unavailable": True, "days": [], "totals": {}}


def test_a_rollup_anywhere_in_the_range_wins_over_live():
    store.incr("_activity", "_act#2020-02-01", "people", by=7)
    reports.put("active", "2020-02-02", {"dau": 3, "wau": 3, "mau": 3, "new": 0, "returning": 3})
    r = reports.report("active", "2020-02-01", "2020-02-02")
    assert r["source"] == "rollup" and [d["date"] for d in r["days"]] == ["2020-02-02"]


def test_live_reads_at_most_ninety_days():
    r = reports.report("active", "2019-01-01", "2019-12-31")
    assert r["clamped_to_days"] == reports.LIVE_DAYS


# ---------------------------------------------------------------- CSV

def test_csv_is_tidy_and_never_a_formula():
    reports.put("visits", "2026-10-01", visits(5, country="=HYPERLINK(\"x\")"))
    r = {"days": reports.get("visits", "2026-10-01", "2026-10-01")}
    r["totals"] = reports.summarize("visits", r["days"])
    rows = list(csv.reader(io.StringIO(reports.to_csv(r))))
    assert rows[0] == ["date", "section", "key", "metric", "value"]
    assert ["2026-10-01", "totals", "", "visits", "5"] in rows
    assert ["total", "by.country", "'=HYPERLINK(\"x\")", "visitors", "5"] in rows
    assert not any(cell.startswith("=") for row in rows for cell in row)


# ---------------------------------------------------------------- the API

def test_report_api_needs_an_analyst():
    assert TestClient(server.app).get("/api/ops/report/funnel").status_code == 404   # no ops yet
    person("op@ops.com", "analyst")
    assert TestClient(server.app).get("/api/ops/report/funnel").status_code == 401
    plain, _ = person("plain@x.com")
    assert plain.get("/api/ops/report/funnel").status_code == 403
    assert plain.get("/api/ops/report/nope").status_code == 403       # no probing for names


def test_report_api_answers_with_totals_and_a_comparison():
    c, _ = person("analyst@ops.com", "analyst")
    reports.put("visits", "2026-10-01", visits(5))
    reports.put("visits", "2026-10-03", visits(6))
    reports.put("visits", "2026-10-04", visits(9))
    r = c.get("/api/ops/report/visits?from=2026-10-03&to=2026-10-04&compare=1")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    j = r.json()
    assert (j["name"], j["from"], j["to"], j["source"]) == ("visits", "2026-10-03",
                                                            "2026-10-04", "rollup")
    assert [d["date"] for d in j["days"]] == ["2026-10-03", "2026-10-04"]
    assert j["totals"]["totals"]["visits"] == 15
    assert j["previous"]["from"] == "2026-10-01" and j["previous"]["to"] == "2026-10-02"
    assert j["previous"]["totals"]["totals"]["visits"] == 5
    assert c.get("/api/ops/report").json()["reports"][0] == {
        "name": "visits", "label": "Site visits", "live": False}


def test_report_api_validates():
    c, _ = person("analyst2@ops.com", "analyst")
    assert c.get("/api/ops/report/nope").status_code == 404
    assert c.get("/api/ops/report/nope.csv").status_code == 404
    assert c.get("/api/ops/report/visits?from=2026-10-05&to=2026-10-01").status_code == 400
    assert c.get("/api/ops/report/visits?from=2026-1-5").status_code == 400
    assert c.get("/api/ops/report/visits?from=2025-01-01&to=2026-10-01").status_code == 400
    assert c.get("/api/ops/report/visits?from=2025-09-01&to=2026-10-04").status_code == 200
    j = c.get("/api/ops/report/funnel").json()
    to = dt.date.fromisoformat(j["to"])
    assert (to - dt.date.fromisoformat(j["from"])).days == 29 and "previous" not in j


def test_report_api_exports_csv():
    c, _ = person("analyst3@ops.com", "analyst")
    reports.put("visits", "2026-10-01", visits(5))
    for url in ("/api/ops/report/visits.csv?from=2026-10-01&to=2026-10-01",
                "/api/ops/report/visits?from=2026-10-01&to=2026-10-01&format=csv"):
        r = c.get(url)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert r.headers["cache-control"] == "no-store"
        assert 'filename="mysign-visits-2026-10-01-2026-10-01.csv"' in \
            r.headers["content-disposition"]
        assert r.text.startswith("date,section,key,metric,value\n")


# ------------------------------------------------------- site and usage ranges

def test_site_and_usage_ranges_add_up():
    def site(c, n):
        return {"totals": {"pageviews": n, "countries": c},
                "steps": [{"step": "landed", "label": "Landing page view", "count": n}],
                "channels": [{"key": "paid", "pageviews": n, "events": n, "scrolled": 0, "opened": 0}],
                "sections": [], "taps": [], "lengths": [{"key": "<2s", "sessions": 1}],
                "by": {"os_family": [{"key": "ios", "pageviews": n, "visitors": n}]}}
    t = reports.summarize("site", [site(3, 5), site(2, 7)])
    assert t["totals"] == {"pageviews": 12, "countries": 3}          # countries: the busiest day
    assert t["steps"][0] == {"step": "landed", "label": "Landing page view", "count": 12}
    assert t["channels"][0]["pageviews"] == 12 and t["lengths"][0]["sessions"] == 2

    def usage(n):
        return {"totals": {"events": n}, "by_hour": [{"hour": 13, "events": n, "pageviews": 0}],
                "screens": [{"key": "home", "uses": n, "users": 1}], "taps": [], "changes": [],
                "server": [], "hour_basis": "utc"}
    u = reports.summarize("usage", [usage(1), usage(2)])
    assert u["by_hour"] == [{"hour": 13, "events": 3, "pageviews": 0}]
    assert u["totals"]["events"] == 3 and u["screens"][0]["uses"] == 3
    assert reports.summarize("site", []) == {} and reports.summarize("usage", []) == {}


def test_site_and_usage_need_the_lake():
    assert "site" not in reports.LIVE and "usage" not in reports.LIVE
