"""
The morning push pipeline: one bad user or device never costs anyone else their
morning, subscriptions are only ever sent to real push services, the day's stamp
is claimed before anything goes out, and "today" is the user's today.

Everything external is faked — webpush, the model client, the chart build — so
these run offline and assert the mechanics, which is what breaks at 07:30.
"""
import base64
import datetime as dt
import json
import os
import pathlib
import secrets
import sys
import tempfile
import threading
import time
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-push-"))
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("MYSIGN_TABLE", None)
os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)

import requests

import app.server as server
from app import auth, cron, push
from engine import context
from engine.store import load, save

UTC = dt.timezone.utc


def _b64(n):
    return base64.urlsafe_b64encode(secrets.token_bytes(n)).decode().rstrip("=")


def good_sub(host="fcm.googleapis.com"):
    return {"endpoint": f"https://{host}/fcm/send/{secrets.token_hex(8)}",
            "keys": {"p256dh": _b64(65), "auth": _b64(16)}}


@pytest.fixture(autouse=True)
def strict(monkeypatch):
    """Production rules: other test modules turn on dev mode for the session."""
    monkeypatch.delenv("MYSIGN_DEV_AUTH", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def roster(monkeypatch):
    """Users scoped to the test — the shared roster has other modules' people."""
    users = []
    monkeypatch.setattr(auth, "all_users", lambda: list(users))

    def add(tz="America/Los_Angeles", subs=None):
        uid = "u-" + secrets.token_hex(6)
        save(uid, "profile", {"tz": tz, "name": "Maya"})
        # These tests exercise the composed "sky note"; the daily line has its own.
        save(uid, "account", {"push_style": "sky"})
        save(uid, "push", [good_sub()] if subs is None else subs)
        users.append(uid)
        return uid
    return add


@pytest.fixture
def sky(monkeypatch):
    """Stand-ins for the chart work; records the date each user was built for."""
    built = {}
    monkeypatch.setattr(server, "_natal", lambda uid: {})
    monkeypatch.setattr(server, "_sky", lambda uid, day=None: {})

    def build(uid, natal, sky_, checkin, today=None, **kw):
        built[uid] = today
        return "block", {"band": "high",
                         "headlines": [{"transit": "Mars", "natal": "Venus"}]}
    monkeypatch.setattr(context, "build", build)
    return built


# 07:40 in Los Angeles (PDT, UTC-7) on 27 Oct.
LA_MORNING = dt.datetime(2026, 10, 27, 14, 40, tzinfo=UTC)


# ------------------------------------------------------------- validation

@pytest.mark.parametrize("host", ["fcm.googleapis.com", "updates.push.services.mozilla.com",
                                  "web.push.apple.com", "api.push.apple.com",
                                  "wns2-par02p.notify.windows.com"])
def test_real_push_services_are_accepted(host):
    assert push.validate_subscription(good_sub(host)) == (True, "ok")


@pytest.mark.parametrize("mutate", [
    lambda s: s.update(endpoint="http://fcm.googleapis.com/x"),
    lambda s: s.update(endpoint="https://push.example/x"),
    lambda s: s.update(endpoint="https://169.254.169.254/latest/meta-data"),
    lambda s: s.update(endpoint="https://fcm.googleapis.com.attacker.net/x"),
    lambda s: s.update(endpoint="https://evilfcm.googleapis.com/x"),
    lambda s: s.update(endpoint="https://fcm.googleapis.com:8443/x"),
    lambda s: s.update(endpoint="https://user:pw@fcm.googleapis.com/x"),
    lambda s: s.update(endpoint=None),
    lambda s: s.pop("keys"),
    lambda s: s["keys"].pop("auth"),
    lambda s: s["keys"].update(p256dh="not base64!"),
    lambda s: s["keys"].update(p256dh=_b64(20)),
    lambda s: s["keys"].update(auth=_b64(64)),
    lambda s: s.update(junk="x" * 5000),
])
def test_malformed_or_unknown_subscriptions_are_rejected(mutate):
    s = good_sub()
    mutate(s)
    ok, reason = push.validate_subscription(s)
    assert not ok and reason != "ok"


def test_subscribe_refuses_and_stores_nothing():
    uid = "u-sub-" + secrets.token_hex(4)
    assert push.subscribe(uid, {"endpoint": "https://169.254.169.254/", "keys": {}}) is False
    assert push.subscribe(uid, "garbage") is False
    assert (load(uid, "push", []) or []) == []
    # a good one stores only the fields web push uses
    s = {**good_sub(), "extra": "dropped"}
    assert push.subscribe(uid, s, "Europe/London") == 1
    assert set(load(uid, "push", [])[0]) == {"endpoint", "keys", "tz"}


# ---------------------------------------------------------------- isolation

def test_one_bad_user_or_device_never_stops_the_rest(roster, sky, monkeypatch, capsys):
    no_keys = roster(subs=[{"endpoint": good_sub()["endpoint"]}])        # malformed
    flaky = roster()                                                      # network error
    gone = roster()                                                       # 410
    broken = roster()                                                     # build raises
    bad_tz = roster(tz="Not/AZone")
    fine = roster()
    flaky_ep = load(flaky, "push", None)[0]["endpoint"]
    gone_ep = load(gone, "push", None)[0]["endpoint"]

    delivered = []

    def fake_webpush(subscription_info, **kw):
        ep = subscription_info["endpoint"]
        if ep == flaky_ep:
            raise requests.Timeout(f"timed out talking to {ep}")
        if ep == gone_ep:
            raise push.WebPushException("gone", response=types.SimpleNamespace(status_code=410))
        delivered.append(ep)
    monkeypatch.setattr(push, "webpush", fake_webpush)

    real_build = context.build
    def build(uid, *a, **kw):
        if uid == broken:
            raise RuntimeError("chart exploded")
        return real_build(uid, *a, **kw)
    monkeypatch.setattr(context, "build", build)

    out = cron.morning(LA_MORNING)

    assert delivered == [load(fine, "push", None)[0]["endpoint"]]
    assert out["failed"] == 1                       # `broken`; bad_tz never got queued
    assert load(no_keys, "push", None) == []              # malformed: pruned
    assert load(gone, "push", None) == []                 # 410: pruned
    assert len(load(flaky, "push", None)) == 1            # transient: kept for tomorrow

    logs = capsys.readouterr().out
    assert "cron.user.failed" in logs and "push.failed" in logs
    for ep in (flaky_ep, gone_ep):
        assert ep not in logs, "an endpoint reached the logs"
    assert bad_tz in logs


# --------------------------------------------------------------- idempotency

def test_stamp_is_claimed_before_sending_so_a_crash_mid_send_cannot_double_push(
        roster, sky, monkeypatch):
    uid = roster()
    stamp = "pushed-2026-10-27"
    calls = []

    def send(u, title, body, url="/", **_):
        calls.append(u)
        assert load(u, stamp, None), "the claim must exist before anything is sent"
        raise RuntimeError("lambda died mid-send")
    monkeypatch.setattr(push, "send", send)

    assert cron.morning(LA_MORNING)["failed"] == 1
    cron.morning(LA_MORNING + dt.timedelta(minutes=15))
    assert calls == [uid]
    assert load(uid, stamp, None)["state"] == "claimed"


def test_failure_before_sending_releases_the_claim(roster, sky, monkeypatch):
    uid = roster()
    sent = []
    monkeypatch.setattr(push, "send",
                        lambda u, t, b, url="/", **_: sent.append(u) or {"sent": 1, "pruned": 0})
    real_build = context.build
    fail = {"on": True}

    def build(*a, **kw):
        if fail["on"]:
            raise RuntimeError("transient")
        return real_build(*a, **kw)
    monkeypatch.setattr(context, "build", build)

    cron.morning(LA_MORNING)
    assert sent == [] and not load(uid, "pushed-2026-10-27", None)
    fail["on"] = False
    assert cron.morning(LA_MORNING + dt.timedelta(minutes=15))["pushed"] == 1
    assert sent == [uid]


# ---------------------------------------------------------------------- scale

def test_due_users_are_composed_in_parallel(roster, sky, monkeypatch):
    uids = [roster() for _ in range(8)]
    lock, sent = threading.Lock(), []

    def slow_compose(uid, verdict, block, name):
        time.sleep(0.3)
        return "My Sign", "Mars is on your Venus today."
    monkeypatch.setattr(cron, "_compose", slow_compose)

    def send(u, t, b, url="/", **_):
        with lock:
            sent.append(u)
        return {"sent": 1, "pruned": 0}
    monkeypatch.setattr(push, "send", send)

    t0 = time.monotonic()
    out = cron.morning(LA_MORNING, workers=8)
    assert out["pushed"] == 8 and sorted(sent) == sorted(uids)
    assert time.monotonic() - t0 < 1.5, "eight 0.3s composes ran serially"


def test_time_budget_defers_the_tail_to_the_next_run(roster, sky, monkeypatch, capsys):
    uids = [roster() for _ in range(6)]
    monkeypatch.setattr(cron, "_compose", lambda *a: ("My Sign", "Mars is on your Venus today."))
    sent = []
    monkeypatch.setattr(push, "send",
                        lambda u, t, b, url="/", **_: sent.append(u) or {"sent": 1, "pruned": 0})

    asked = {"n": 0}
    lock = threading.Lock()

    def remaining():          # plenty for the first four users, then nearly out
        with lock:
            asked["n"] += 1
            return 200 if asked["n"] <= 4 else 10

    out = cron.morning(LA_MORNING, remaining=remaining, workers=2)
    assert out["pushed"] == 4 and out["deferred"] == 2
    assert '"cron.deferred"' in capsys.readouterr().out

    # 07:55 — still inside the window, so the deferred two go now, and only them.
    out = cron.morning(LA_MORNING + dt.timedelta(minutes=15), remaining=lambda: 200)
    assert out["pushed"] == 2
    assert sorted(sent) == sorted(uids)


# --------------------------------------------------------------------- dates

def test_today_is_the_users_local_date(roster, sky, monkeypatch):
    sydney = roster(tz="Australia/Sydney")
    la = roster(tz="America/Los_Angeles")
    sent = []
    monkeypatch.setattr(push, "send",
                        lambda u, t, b, url="/", **_: sent.append(u) or {"sent": 1, "pruned": 0})

    # 20:45 UTC on 27 Oct is 07:45 on 28 Oct in Sydney (AEDT) and 13:45 on 27 Oct in LA.
    cron.morning(dt.datetime(2026, 10, 27, 20, 45, tzinfo=UTC))
    assert sent == [sydney]
    assert sky[sydney] == dt.date(2026, 10, 28)
    assert load(sydney, "pushed-2026-10-28", None)
    assert not load(sydney, "pushed-2026-10-27", None)

    # 14:40 UTC on 28 Oct: LA is 07:40 on the 28th, Sydney is already the 29th at 01:40.
    cron.morning(dt.datetime(2026, 10, 28, 14, 40, tzinfo=UTC))
    assert sent == [sydney, la]
    assert sky[la] == dt.date(2026, 10, 28)
    assert load(la, "pushed-2026-10-28", None)


@pytest.mark.parametrize("hhmm,due", [((7, 29), False), ((7, 30), True), ((9, 29), True),
                                      ((9, 30), False), ((3, 0), False)])
def test_due_window(hhmm, due):
    from zoneinfo import ZoneInfo
    local = dt.datetime(2026, 10, 27, *hhmm, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert cron._due(local) is due


# --------------------------------------------------------------------- model

VERDICT = {"band": "high", "headlines": [{"transit": "Mars", "natal": "Venus"}]}
FALLBACK = "Mars is on your Venus today."


def _fake_anthropic(monkeypatch, response=None, raises=None):
    class Messages:
        def create(self, **kw):
            if raises:
                raise raises
            return response

    class Client:
        def __init__(self, **kw):
            self.messages = Messages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))


def _resp(text, stop="end_turn"):
    blocks = [types.SimpleNamespace(type="thinking", thinking="...")]
    if text is not None:
        blocks.append(types.SimpleNamespace(type="text", text=text))
    return types.SimpleNamespace(content=blocks, stop_reason=stop)


@pytest.mark.parametrize("response,raises", [
    (_resp("I can't help with that.", stop="refusal"), None),
    (_resp("Mars squares your Venus and the", stop="max_tokens"), None),
    (_resp("   "), None),
    (_resp(None), None),
    (None, RuntimeError("overloaded")),
])
def test_bad_model_output_falls_back(monkeypatch, response, raises):
    _fake_anthropic(monkeypatch, response, raises)
    assert cron._compose("u", VERDICT, "block", "Maya") == ("My Sign", FALLBACK)


def test_clean_model_output_is_used(monkeypatch):
    _fake_anthropic(monkeypatch, _resp("Mars on your Venus turns friction into pull."))
    assert cron._compose("u", VERDICT, "block", "Maya")[1] == \
        "Mars on your Venus turns friction into pull."


# ------------------------------------------------------------ the daily line

def test_the_default_push_is_the_line_of_the_day(roster, sky, monkeypatch):
    """The lock screen and Today show the same line (engine/lines.py), with the
    transit that chose it as the title."""
    uid = roster()
    save(uid, "account", {})                              # no style chosen
    sent = []
    monkeypatch.setattr(push, "send", lambda u, t, b, **_: sent.append((t, b)) or
                        {"sent": 1, "pruned": 0, "failed": 0})
    from engine import context, lines
    monkeypatch.setattr(context, "build", lambda *a, **k: ("block", {
        "band": "high", "headlines": [{"transit": "Mars", "aspect": "square", "natal": "Venus"}]}))
    cron.morning(LA_MORNING)
    expect = lines.line([{"transit": "Mars", "aspect": "square", "natal": "Venus"}],
                        "2026-10-27", uid)
    title = lines.sky_of(expect["why"])           # the planets, short enough for a title
    assert sent == [(title[0].upper() + title[1:], expect["text"])]
    assert title.startswith("Mars") and "Venus" in title


def test_her_chosen_time_and_off_switch_are_respected(roster, sky, monkeypatch):
    early, late, off = roster(), roster(), roster()
    save(early, "account", {"push_time": "07:30"})
    save(late, "account", {"push_time": "09:00"})
    save(off, "account", {"push_off": True})
    sent = []
    monkeypatch.setattr(push, "send", lambda u, t, b, **_: sent.append(u) or
                        {"sent": 1, "pruned": 0, "failed": 0})
    cron.morning(LA_MORNING)                              # 07:40 in LA
    assert sent == [early]


def test_the_push_follows_where_she_is_not_where_she_was_born(roster, sky, monkeypatch):
    uid = roster(tz="Australia/Sydney")                   # born there
    save(uid, "account", {"tz": "America/Los_Angeles"})   # lives here now
    sent = []
    monkeypatch.setattr(push, "send", lambda u, t, b, **_: sent.append(u) or
                        {"sent": 1, "pruned": 0, "failed": 0})
    cron.morning(LA_MORNING)
    assert sent == [uid]


def test_the_morning_push_opens_the_app_not_the_website(roster, sky, monkeypatch):
    """/ is the website now; a tap on the plain morning push lands in /app."""
    roster()
    payloads = []
    monkeypatch.setattr(push, "webpush", lambda data, **kw: payloads.append(json.loads(data)))
    cron.morning(LA_MORNING)
    assert [p["url"] for p in payloads] == ["/app?from=push&pk=morning"]


def test_devices_are_capped_and_the_newest_win():
    """p4_push.py registered 150 fake devices on one account (audit M5)."""
    uid = "u-cap-" + secrets.token_hex(4)
    subs = [good_sub() for _ in range(push.MAX_DEVICES + 3)]
    for s in subs:
        n = push.subscribe(uid, s)
    assert n == push.MAX_DEVICES
    kept = [s["endpoint"] for s in load(uid, "push", [])]
    assert kept == [s["endpoint"] for s in subs[-push.MAX_DEVICES:]]


def test_the_morning_after_a_low_check_in_the_title_remembers_it(roster, sky, monkeypatch):
    uid = roster()
    save(uid, "account", {"push_style": "line"})
    save(uid, "checkin-2026-10-26", {"mood": 3, "text": ""})
    sent = []
    monkeypatch.setattr(push, "send",
                        lambda u, t, b, url="/", **_: sent.append((t, b)) or {"sent": 1, "pruned": 0})
    cron.morning(LA_MORNING)
    assert sent and sent[0][0] == "Yesterday was hard. Gently, then."   # no number on a lock screen
    assert sent[0][1]                                    # the line of the day is still the body


def test_after_low_title_only_after_a_low_day():
    from engine import mood
    assert mood.after_low_title(None) is None
    assert mood.after_low_title({"mood": 5}) is None
    assert mood.after_low_title({"mood": 4}) == "Yesterday was hard. Gently, then."
    assert mood.after_low_title({"mood": 2.0}) == "Yesterday was hard. Gently, then."   # legacy floats


def test_no_morning_push_during_a_crisis_episode(roster, sky, monkeypatch):
    from engine import episode
    uid = roster()
    save(uid, "account", {"push_style": "line"})
    sent = []
    monkeypatch.setattr(push, "send",
                        lambda u, t, b, url="/", **_: sent.append(u) or {"sent": 1, "pruned": 0})
    monkeypatch.setattr(episode, "active", lambda u: {"kind": "self_harm"} if u == uid else None)
    cron.morning(LA_MORNING)
    assert sent == []
    assert not load(uid, "pushed-2026-10-27", None)          # nothing claimed; tomorrow can send
