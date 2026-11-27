"""
The analytics pipeline: one set of rules (app/events.py) on both paths, the
collector that takes anonymous events, the app's dual write to Firehose, and
the funnel's atomic counts.
"""
import base64
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import time

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-ev-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import activity, events, forward, server
from engine import funnel, store
from tests.test_app import last_uid, onboard, sign_in

COLLECT = ROOT / "deploy" / "collect"


class Firehose:
    """Stands in for the boto3 client: records batches, can refuse or fail."""

    def __init__(self, fail_first=0, raise_=None):
        self.calls, self.fail_first, self.raise_ = [], fail_first, raise_

    def put_record_batch(self, DeliveryStreamName, Records):
        if self.raise_:
            raise self.raise_
        self.calls.append((DeliveryStreamName, Records))
        n, self.fail_first = self.fail_first, 0
        return {"FailedPutCount": n,
                "RequestResponses": [{"ErrorCode": "ServiceUnavailable"} if i < n else {}
                                     for i in range(len(Records))]}

    def envelopes(self):
        return [json.loads(r["Data"]) for _, recs in self.calls for r in recs]


@pytest.fixture
def fh(monkeypatch):
    stub = Firehose()
    monkeypatch.setenv("MYSIGN_EVENTS_STREAM", "mysign-events-test")
    monkeypatch.setattr(forward, "_client", stub)
    return stub


def _ev(type_, name, screen="home", **kw):
    return {"type": type_, "name": name, "screen": screen, "at": time.time() * 1000, **kw}


# ------------------------------------------------------------- the rules

def test_envelope_carries_only_the_documented_fields():
    now = time.time()
    c = events.clean_event(_ev("tap", "Nav_People", props={"kind": "friend", "count": 3,
                                                          "ok": True, "text": "hi"}), now)
    env = events.envelope(c, source="app", received_at=now, uid="u1234abcd5678",
                          anon_id="a" * 22, session_id="sess-abc123", app_version="1.4.0",
                          ctx={"country": "GB", "device_class": "mobile", "ip": "1.2.3.4",
                               "user_agent": "Mozilla/5.0"})
    assert set(env) == {"event_id", "ts", "received_at", "source", "app_version", "anon_id",
                        "uid", "session_id", "type", "name", "screen", "props", "ctx"}
    assert env["name"] == "nav_people" and env["type"] == "tap"
    assert env["props"] == {"kind": "friend", "count": "3", "ok": "true"}
    assert env["ctx"] == {"country": "GB", "device_class": "mobile"}
    assert isinstance(env["ts"], int) and abs(env["ts"] - now * 1000) < 2000


def test_ids_that_fail_their_pattern_become_null():
    c = events.clean_event(_ev("screen", "home"), time.time())
    env = events.envelope(c, source="site", uid="me@example.com", anon_id="0207 946 0000",
                          session_id="x", app_version="v1 beta with spaces")
    assert env["uid"] is env["anon_id"] is env["session_id"] is env["app_version"] is None
    with pytest.raises(ValueError):
        events.envelope(c, source="somewhere")


@pytest.mark.parametrize("value", ["I think my husband is cheating", "me@example.com",
                                   "call 07700 900123"])
def test_free_text_props_are_dropped_not_trimmed(value):
    c = events.clean_event(_ev("change", "picker", props={"value": value}), time.time())
    assert "p" not in c


def test_clients_cannot_send_server_events_or_unknown_types():
    now = time.time()
    assert events.clean_event(_ev("server", "message_sent"), now) is None
    assert events.clean_event(_ev("message", "send"), now) is None
    assert events.clean_event(_ev("page", "landing"), now)["ty"] == "page"
    assert events.clean_event(_ev("heartbeat", "app"), now)["ty"] == "heartbeat"


def test_referrer_is_a_domain_and_landing_keeps_only_utm():
    assert events.referrer_domain("https://www.google.com/search?q=my+ex+birthday") == "google.com"
    assert events.referrer_domain("https://mysignapp.com/today", own=("mysignapp.com",)) is None
    assert events.referrer_domain("not a url at all") is None
    path, utm = events.landing("https://mysignapp.com/invite/k3x9Qa7Zp?utm_source=TikTok"
                               "&utm_campaign=Spring%20Launch&email=me@x.com&fbclid=abc#top")
    assert path == "/invite/id"                  # the invite token is an id, not a page
    assert utm == {"utm_source": "tiktok", "utm_campaign": "spring_launch"}
    _, utm = events.landing("/?utm_source=me@example.com&utm_medium=07700900123")
    assert utm == {}                             # a person pasted into a campaign link


def test_device_from_cloudfront_headers_and_ua_family_only():
    ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    ctx = events.request_ctx({"user-agent": ua, "cloudfront-viewer-country": "de",
                              "cloudfront-is-mobile-viewer": "true",
                              "referer": "https://mysignapp.com/?utm_medium=social"},
                             {"referrer": "https://t.co/abc"})
    assert ctx == {"device_class": "mobile", "os_family": "ios", "browser_family": "safari",
                   "country": "DE", "referrer_domain": "t.co", "landing_path": "/",
                   "utm_medium": "social"}
    assert ua not in json.dumps(ctx)
    assert events.no_ids({"sec-gpc": "1"}) and events.no_ids({"dnt": "1"})
    assert not events.no_ids({"dnt": "0"})


def test_put_retries_only_what_firehose_refused():
    stub = Firehose(fail_first=2)
    envs = [events.envelope(events.clean_event(_ev("tap", f"b{i}"), time.time()), source="app")
            for i in range(5)]
    assert events.put(stub, "s", envs) == 0
    assert [len(r) for _, r in stub.calls] == [5, 2]
    assert all(r["Data"].endswith(b"\n") for _, recs in stub.calls for r in recs)


# ------------------------------------------------------------- collector

def _collector(monkeypatch):
    # The symlink is the mechanism: the collector's rules are the app's file.
    assert (COLLECT / "events.py").is_symlink()
    assert os.path.samefile(COLLECT / "events.py", ROOT / "app" / "events.py")
    monkeypatch.syspath_prepend(str(COLLECT))
    monkeypatch.delitem(sys.modules, "events", raising=False)
    spec = importlib.util.spec_from_file_location("collect_handler", COLLECT / "handler.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stub = Firehose()
    monkeypatch.setattr(mod, "_client", stub)
    monkeypatch.setattr(mod, "STREAM", "mysign-events-test")
    monkeypatch.setattr(mod, "OWN", ("mysignapp.com",))
    monkeypatch.delenv("MYSIGN_ORIGIN_TOKEN", raising=False)
    monkeypatch.delenv("MYSIGN_SECRET_ARN", raising=False)
    monkeypatch.setenv("MYSIGN_COLLECT_OPEN", "1")     # local dev: no CloudFront in front
    return mod, stub


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"


def _req(body, method="POST", b64=False, **headers):
    raw = json.dumps(body) if not isinstance(body, str) else body
    if b64:
        raw = base64.b64encode(raw.encode()).decode()
    return {"requestContext": {"http": {"method": method}}, "isBase64Encoded": b64,
            "headers": {"user-agent": UA, "content-type": "text/plain", **headers},
            "body": raw}


VISIT = {"anon_id": "anonAnonAnon1234567890", "session": "sess-xyz789", "source": "site",
         "uid": "u000000000001", "ctx": {"referrer": "https://www.instagram.com/someone/"},
         "events": [_ev("page", "landing", "landing"), _ev("tap", "cta_download", "landing")]}


def test_collector_forwards_an_anonymous_batch(monkeypatch):
    mod, stub = _collector(monkeypatch)
    r = mod.handler(_req(VISIT, b64=True, **{"cloudfront-viewer-country": "US",
                                             "cloudfront-is-desktop-viewer": "true",
                                             "referer": "https://mysignapp.com/?utm_source=ig"}))
    assert r["statusCode"] == 204
    assert len(stub.calls) == 1 and stub.calls[0][0] == "mysign-events-test"
    envs = stub.envelopes()
    assert [e["name"] for e in envs] == ["landing", "cta_download"]
    e = envs[0]
    assert e["uid"] is None                      # never taken from the client
    assert e["source"] == "site" and e["anon_id"] == VISIT["anon_id"]
    assert e["ctx"] == {"country": "US", "device_class": "desktop", "os_family": "windows",
                        "browser_family": "chrome", "referrer_domain": "instagram.com",
                        "landing_path": "/", "utm_source": "ig"}
    assert "someone" not in json.dumps(envs) and UA not in json.dumps(envs)


def test_collector_honours_gpc_and_dnt(monkeypatch):
    mod, stub = _collector(monkeypatch)
    for h in ({"sec-gpc": "1"}, {"dnt": "1"}):
        assert mod.handler(_req(VISIT, **h))["statusCode"] == 204
    envs = stub.envelopes()
    assert len(envs) == 4
    assert all(e["anon_id"] is None and e["session_id"] is None for e in envs)


def test_collector_refuses_without_the_origin_header(monkeypatch):
    mod, stub = _collector(monkeypatch)
    monkeypatch.setenv("MYSIGN_ORIGIN_TOKEN", "s3cret")
    assert mod.handler(_req(VISIT))["statusCode"] == 403
    assert mod.handler(_req(VISIT, **{"x-origin-auth": "wrong"}))["statusCode"] == 403
    assert mod.handler(_req(VISIT, **{"x-origin-auth": "s3cret"}))["statusCode"] == 204
    assert len(stub.calls) == 1


def test_collector_without_any_token_fails_closed(monkeypatch):
    """Unconfigured used to mean open; a deploy that forgot the token left the
    public Function URL accepting writes past CloudFront and the WAF."""
    mod, stub = _collector(monkeypatch)
    monkeypatch.delenv("MYSIGN_COLLECT_OPEN")
    assert mod.handler(_req(VISIT))["statusCode"] == 403
    assert mod.handler(_req(VISIT, **{"x-origin-auth": "anything"}))["statusCode"] == 403
    assert not stub.calls


def test_collector_unreadable_secret_fails_closed(monkeypatch):
    mod, stub = _collector(monkeypatch)
    monkeypatch.setenv("MYSIGN_SECRET_ARN", "arn:aws:secretsmanager:us-east-2:0:secret:x")
    monkeypatch.setitem(sys.modules, "boto3", None)       # import fails like a bad read
    assert mod.handler(_req(VISIT, **{"x-origin-auth": "anything"}))["statusCode"] == 403
    assert not stub.calls


def test_collector_limits_and_shapes(monkeypatch):
    mod, stub = _collector(monkeypatch)
    assert mod.handler(_req(VISIT, method="GET"))["statusCode"] == 405
    assert mod.handler(_req("{not json"))["statusCode"] == 400
    assert mod.handler(_req("[1,2]"))["statusCode"] == 400
    big = {**VISIT, "events": [_ev("tap", "x", props={"value": "y" * 16})] * 2000}
    assert mod.handler(_req(big))["statusCode"] == 413
    many = {**VISIT, "events": [_ev("tap", f"b{i}") for i in range(80)]}
    assert mod.handler(_req(many))["statusCode"] == 204
    assert len(stub.envelopes()) == events.MAX_BATCH
    stub.calls.clear()
    assert mod.handler(_req(VISIT, **{"user-agent": "Googlebot/2.1"}))["statusCode"] == 204
    assert not stub.calls                        # bots aren't visitors
    bad_source = {**VISIT, "source": "server", "events": [_ev("server", "funnel.paid")]}
    assert mod.handler(_req(bad_source))["statusCode"] == 204
    assert not stub.calls                        # can't forge server events


# ------------------------------------------------------------- app dual-write

@pytest.fixture
def c():
    return TestClient(server.app)


def test_activity_batch_is_forwarded_in_one_put(c, fh):
    sign_in(c, "ev1@x.com"); onboard(c)
    uid = last_uid()
    fh.calls.clear()
    r = c.post("/api/activity", json={
        "session": "sess-abc123", "anon_id": "anonAnonAnon1234567890",
        "events": [_ev("screen", "home"), _ev("heartbeat", "app"), _ev("tap", "nav_ppl"),
                   _ev("tap", "I think my husband is cheating")]})
    assert r.json()["kept"] == 2                 # heartbeats go to the lake only
    assert len(fh.calls) == 1
    envs = fh.envelopes()
    assert [e["name"] for e in envs] == ["home", "app", "nav_ppl"]
    assert {e["uid"] for e in envs} == {uid}
    assert {e["anon_id"] for e in envs} == {"anonAnonAnon1234567890"}
    assert {e["session_id"] for e in envs} == {"sess-abc123"}
    assert {e["source"] for e in envs} == {"app"}


def test_gpc_drops_anon_id_on_the_app_path(c, fh):
    sign_in(c, "ev2@x.com"); onboard(c)
    fh.calls.clear()
    c.post("/api/activity", headers={"Sec-GPC": "1"},
           json={"session": "sess-abc123", "anon_id": "anonAnonAnon1234567890",
                 "events": [_ev("screen", "home")]})
    assert [e["anon_id"] for e in fh.envelopes()] == [None]


def test_switched_off_forwards_nothing(c, fh):
    sign_in(c, "ev3@x.com"); onboard(c)
    c.post("/api/privacy/analytics", json={"off": True})
    fh.calls.clear()
    c.post("/api/activity", json={"session": "sess-abc123", "events": [_ev("screen", "home")]})
    activity.track(last_uid(), "message_sent", screen="chat")
    activity.track_auth(last_uid(), "email", False, "anonAnonAnon1234567890")
    assert fh.calls == []


def test_forwarding_failure_never_reaches_the_request(c, fh, monkeypatch, capsys):
    sign_in(c, "ev4@x.com"); onboard(c)
    monkeypatch.setattr(forward, "_client", Firehose(raise_=RuntimeError("firehose down")))
    r = c.post("/api/activity", json={"session": "sess-abc123", "events": [_ev("screen", "home")]})
    assert r.status_code == 200 and r.json()["kept"] == 1
    assert activity.track(last_uid(), "message_sent", screen="chat") == 1
    out = capsys.readouterr().out
    assert '"event": "activity.forward_failed"' in out and "firehose down" not in out


def test_nothing_forwarded_without_a_stream(c, monkeypatch):
    stub = Firehose()
    monkeypatch.delenv("MYSIGN_EVENTS_STREAM", raising=False)
    monkeypatch.setattr(forward, "_client", stub)
    sign_in(c, "ev5@x.com"); onboard(c)
    c.post("/api/activity", json={"session": "sess-abc123", "events": [_ev("screen", "home")]})
    assert stub.calls == []


def test_track_auth_before_consent_is_a_bare_count(fh):
    from app import auth
    uid = auth.uid_for("ev6@x.com")
    activity.track_auth(uid, "email", True, "anonAnonAnon1234567890")
    (e,) = fh.envelopes()
    assert e["name"] == "auth.verified" and e["source"] == "server"
    assert e["uid"] is None and e["anon_id"] is None
    assert e["props"] == {"channel": "email", "new": "true"}
    assert not [k for k in store.keys(uid) if k.startswith("act-")]


def test_track_auth_after_consent_links_the_device(c, fh):
    sign_in(c, "ev7@x.com")
    uid = last_uid()
    fh.calls.clear()
    assert activity.track_auth(uid, "apple", False, "anonAnonAnon1234567890") == 1
    (e,) = fh.envelopes()
    assert e["uid"] == uid and e["anon_id"] == "anonAnonAnon1234567890"
    assert e["props"] == {"channel": "apple", "new": "false"}


# ------------------------------------------------------------- the funnel

def test_funnel_counts_concurrent_signups_without_losing_any(fh):
    import datetime as dt
    day = dt.date(2026, 3, 1)
    uids = [f"race-{i}" for i in range(16)]
    start = threading.Barrier(len(uids))

    def go(u):
        start.wait()
        funnel.hit(u, "signed_up", day=day)

    ts = [threading.Thread(target=go, args=(u,)) for u in uids]
    for t in ts: t.start()
    for t in ts: t.join()
    assert funnel.report(1, today=day)["totals"]["signed_up"] == 16
    envs = fh.envelopes()
    assert [e["name"] for e in envs] == ["funnel.signed_up"] * 16
    assert all(e["uid"] is None and e["anon_id"] is None for e in envs)


def test_funnel_once_holds_under_a_race():
    import datetime as dt
    day = dt.date(2026, 3, 2)
    start = threading.Barrier(8)
    got = []

    def go():
        start.wait()
        got.append(funnel.hit("race-same", "paid", day=day))

    ts = [threading.Thread(target=go) for _ in range(8)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert got.count(True) == 1
    assert funnel.report(1, today=day)["totals"]["paid"] == 1
    assert "paid" in funnel.seen("race-same")


def test_funnel_report_reads_legacy_buckets_too():
    import datetime as dt
    day = dt.date(2026, 3, 3)
    store.save("_study", f"funnel-{day.isoformat()}", {"signed_up": 3, "paid": 1})
    funnel.hit("legacy-mix", "signed_up", day=day)
    r = funnel.report(1, today=day)
    assert r["totals"]["signed_up"] == 4 and r["totals"]["paid"] == 1
    assert r["by_day"] == [{"date": day.isoformat(), **dict.fromkeys(funnel.STEPS, 0),
                            "signed_up": 4, "paid": 1}]
    # Someone counted under the old list-only record isn't counted again.
    store.save("old-timer", "funnel_seen", ["signed_up"])
    assert funnel.hit("old-timer", "signed_up", day=day) is False


# ------------------------------------------------------------- the backfill

def _backfill():
    spec = importlib.util.spec_from_file_location("backfill_events",
                                                  ROOT / "tools" / "backfill_events.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_expiry_keeps_history_inside_ninety_days():
    import datetime as dt
    bf, today = _backfill(), dt.date(2026, 6, 1)
    assert bf.expiry_days(today, today) is None                       # the bucket rule does it
    assert bf.expiry_days(today - dt.timedelta(days=20), today) == 60
    assert bf.expiry_days(today - dt.timedelta(days=60), today) == 30
    assert bf.expiry_days(today - dt.timedelta(days=88), today) == 1
    assert bf.expiry_days(today - dt.timedelta(days=90), today) == 0  # already past: skip


def test_backfill_converts_stored_activity_and_skips_the_switched_off(c, fh):
    sign_in(c, "bf1@x.com"); onboard(c)
    on = last_uid()
    c.post("/api/activity", json={"session": "sess-bf0001", "events": [
        _ev("screen", "home"), _ev("tap", "nav_people", props={"kind": "friend"})]})
    sign_in(c, "bf2@x.com"); onboard(c)
    off = last_uid()
    c.post("/api/activity", json={"session": "sess-bf0002", "events": [_ev("screen", "home")]})
    c.post("/api/privacy/analytics", json={"off": True})
    bf = _backfill()
    objects, counts = bf.plan()
    lines = [json.loads(ln) for key, (ls, _) in objects.items()
             if key.startswith(bf.JSON_PREFIX) for ln in ls]
    mine = [e for e in lines if e["uid"] == on]
    assert {(e["type"], e["name"]) for e in mine} >= {("screen", "home"), ("tap", "nav_people")}
    assert all(e["source"] == "backfill" for e in mine)
    assert {e["session_id"] for e in mine if e["type"] != "server"} == {"sess-bf0001"}
    assert all(e["session_id"] is None for e in mine if e["type"] == "server")
    assert not [e for e in lines if e["uid"] == off]
    funnel_evs = [e for e in lines if e["name"].startswith("funnel.")]
    assert funnel_evs and all(e["uid"] is None and e["anon_id"] is None for e in funnel_evs)
    assert any(k.startswith("agg/funnel_daily/") for k in objects)
    assert counts["events"] == len(lines)


def test_backfill_dry_run_writes_nothing_and_write_tags_old_days(monkeypatch, capsys):
    bf = _backfill()

    class S3:
        def __init__(self):
            self.puts = []

        def put_object(self, **kw):
            self.puts.append(kw)

    s3 = S3()
    monkeypatch.setitem(sys.modules, "boto3", None)       # a dry run never needs AWS
    bf.main([])
    assert "dry run" in capsys.readouterr().out
    bf.write({"raw/events-json/dt=2026-01-01/source=backfill/x.jsonl": (["{}"], 7),
              "agg/funnel_daily/dt=2026-01-01/x.jsonl": (["{}"], None)}, "lake", s3)
    tagged, agg = s3.puts
    assert tagged["Tagging"] == "mysign-expire-days=7" and "Tagging" not in agg


# ------------------------------------------------- sign-in, push, paywall, plan

ANON = "01JABCDEFGHJKMNPQRSTVWXYZ0"


def _named(fh, name):
    return [e for e in fh.envelopes() if e["name"] == name]


def test_verify_takes_the_anon_id_from_the_body(c, fh, monkeypatch):
    sign_in(c, "ev8@x.com")                           # consented: the device may be linked
    seen = []
    monkeypatch.setattr(activity, "track_auth", lambda *a: seen.append(a))
    code = c.post("/api/auth/request", json={"email": "ev8@x.com"}).json()["dev_code"]
    c.post("/api/auth/verify", json={"email": "ev8@x.com", "code": code, "anon_id": ANON})
    assert seen[-1][3] == ANON

    code = c.post("/api/auth/request", json={"email": "ev8@x.com"}).json()["dev_code"]
    c.post("/api/auth/verify", headers={"x-anon-id": ANON},
           json={"email": "ev8@x.com", "code": code})
    assert seen[-1][3] == ANON                        # the header still works

    code = c.post("/api/auth/request", json={"email": "ev8@x.com"}).json()["dev_code"]
    c.post("/api/auth/verify", headers={"Sec-GPC": "1"},
           json={"email": "ev8@x.com", "code": code, "anon_id": ANON})
    assert seen[-1][3] is None


def test_verify_links_the_body_anon_id_in_the_lake(c, fh):
    sign_in(c, "ev9@x.com")
    fh.calls.clear()
    code = c.post("/api/auth/request", json={"email": "ev9@x.com"}).json()["dev_code"]
    r = c.post("/api/auth/verify", json={"email": "ev9@x.com", "code": code, "anon_id": ANON})
    assert r.status_code == 200
    (e,) = _named(fh, "auth.verified")
    assert e["uid"] == r.json()["uid"] and e["anon_id"] == ANON


def test_a_code_request_is_counted_without_a_uid(c, fh):
    c.post("/api/auth/request", json={"email": "ev10@x.com", "anon_id": ANON})
    c.post("/api/auth/request", json={"email": "ev10@x.com", "anon_id": "not an id!"})
    c.post("/api/auth/request", headers={"DNT": "1"},
           json={"email": "ev10@x.com", "anon_id": ANON})
    got = _named(fh, "auth.code_requested")
    assert [e["anon_id"] for e in got] == [ANON, None, None]
    assert {e["uid"] for e in got} == {None}
    assert {e["source"] for e in got} == {"server"} and {e["type"] for e in got} == {"server"}
    assert got[0]["props"] == {"channel": "email"} and got[0]["screen"] == "sign_in"


def test_a_refused_code_request_is_not_counted(c, fh):
    assert c.post("/api/auth/request", json={"email": "not-an-address"}).status_code >= 400
    assert _named(fh, "auth.code_requested") == []


def test_push_mark_appends_to_any_url():
    from app import push
    assert push.mark("/", "morning") == "/?from=push&pk=morning"
    assert push.mark("/app?p=abc#x", "person") == "/app?p=abc&from=push&pk=person#x"
    assert push.mark("/app?from=push&pk=old", "sky") == "/app?from=push&pk=sky"


def test_push_send_marks_the_url_and_counts_push_sent(c, fh, monkeypatch):
    from app import push
    sign_in(c, "ev11@x.com"); onboard(c)
    uid = last_uid()
    store.save(uid, "push", [{"endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
                              "keys": {"p256dh": "a" * 87, "auth": "b" * 22}}])
    sent = []
    monkeypatch.setattr(push, "webpush", lambda subscription_info, data, **kw: sent.append(
        json.loads(data)))
    fh.calls.clear()
    assert push.send(uid, "t", "b", url="/app?p=1", kind="person")["sent"] == 1
    assert sent[-1]["url"] == "/app?p=1&from=push&pk=person" and sent[-1]["kind"] == "person"
    (e,) = fh.envelopes()
    assert (e["name"], e["type"], e["uid"], e["props"]) == (
        "push_sent", "server", uid, {"kind": "person"})
    acts = {k: store.load(uid, k, None) for k in store.keys(uid) if k.startswith("act-")}
    assert "push_sent" not in json.dumps(acts)        # lake only: no per-user write

    fh.calls.clear()
    push.send(uid, "t", "b", url="/")                 # no kind: unmarked, uncounted
    assert sent[-1]["url"] == "/" and "kind" not in sent[-1] and fh.calls == []

    c.post("/api/privacy/analytics", json={"off": True})
    push.send(uid, "t", "b", kind="morning")
    assert sent[-1]["url"] == "/app?from=push&pk=morning" and fh.calls == []


def test_paywall_shown_when_chat_is_capped(c, fh, monkeypatch):
    from engine import plan
    sign_in(c, "ev12@x.com"); onboard(c)
    monkeypatch.setattr(plan, "claim_message", lambda uid, d: (
        False, {"used": 5, "message": "That's today's five."}))
    fh.calls.clear()
    with c.stream("POST", "/api/message", json={"text": "hi"}) as r:
        assert r.status_code == 402
    (e,) = _named(fh, "paywall_shown")
    assert e["screen"] == "chat" and e["props"] == {"source": "chat_cap"}


def test_plan_changes_are_subscription_events(c, fh):
    from engine import plan
    sign_in(c, "ev13@x.com"); onboard(c)
    uid = last_uid()
    fh.calls.clear()
    plan.grant(uid, "plus", "apple")
    plan.grant(uid, "plus", "apple")
    plan.grant(uid, "free", "apple")
    plan.grant(uid, "free", "apple")                  # free to free: nothing
    got = [(e["name"], e["props"]) for e in fh.envelopes()]
    assert got == [("subscription_started", {"tier": "plus", "source": "apple"}),
                   ("subscription_renewed", {"tier": "plus", "source": "apple"}),
                   ("subscription_canceled", {"tier": "plus", "source": "apple"})]


def test_the_app_sign_in_screen_batch_reaches_the_lake_as_app(monkeypatch):
    """The shape index.html's Track sends to /e before sign-in."""
    mod, stub = _collector(monkeypatch)
    batch = {"source": "app", "anon_id": ANON, "session": "01JABCDEFGHJKMNPQRSTVWXYZ1",
             "ctx": {"referrer": None, "landing": "/app"},
             "events": [_ev("screen", "sign_in", "sign_in"), _ev("tap", "auth_go", "sign_in"),
                        _ev("heartbeat", "app", "sign_in")]}
    assert mod.handler(_req(batch))["statusCode"] == 204
    envs = stub.envelopes()
    assert [(e["source"], e["name"]) for e in envs] == [
        ("app", "sign_in"), ("app", "auth_go"), ("app", "app")]
    assert {e["anon_id"] for e in envs} == {ANON} and envs[0]["ctx"]["landing_path"] == "/app"


def test_state_says_when_the_collector_is_wired(c, monkeypatch):
    c2 = TestClient(server.app)
    monkeypatch.delenv("MYSIGN_COLLECTOR", raising=False)
    assert c2.get("/api/state").json()["collect"] is False
    monkeypatch.setenv("MYSIGN_COLLECTOR", "1")
    assert c2.get("/api/state").json()["collect"] is True


def test_collector_caps_forged_events_per_device_and_per_container(monkeypatch):
    """Anonymous, so anyone could inflate visits at the WAF's rate (audit L4)."""
    mod, stub = _collector(monkeypatch)
    monkeypatch.setattr(mod, "ANON_PER_MIN", 3)
    monkeypatch.setattr(mod, "CONTAINER_PER_MIN", 5)
    for _ in range(3):                                   # 6 events from one device
        assert mod.handler(_req(VISIT))["statusCode"] == 204
    assert len(stub.envelopes()) == 3
    other = {**VISIT, "anon_id": "anonOtherOther12345678"}
    for _ in range(3):
        mod.handler(_req(other))
    assert len(stub.envelopes()) == 5                    # the container's minute is spent
    later = mod.admit([{"anon_id": "anonOtherOther12345678"}], now=time.time() + 61)
    assert len(later) == 1                               # a new minute, a new budget
