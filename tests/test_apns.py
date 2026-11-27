"""
Notifications in the iPhone app (app/apns.py): off until the key is in and
switched on, a device token belongs to one account at a time and goes when she
signs out or deletes, the provider token is formed the way Apple reads it and
reused, a token Apple says is gone is dropped while a busy or failing Apple
costs nothing, and the morning job reaches a phone through APNs with her
settings exactly as it reaches a browser through Web Push.

Apple is never called. `apns._post` is the one request to Apple and is
replaced in every sending test; the tests that check the request itself hand
httpx a transport that answers from here.
"""
import base64
import datetime as dt
import importlib.util
import json
import os
import pathlib
import secrets
import subprocess
import sys
import tempfile

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-apns-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import apns, auth, bootstrap, cron, push, server, skypush
from engine import context
from engine.store import load, save
from tests.test_app import last_uid, onboard, sign_in

ROOT = pathlib.Path(__file__).resolve().parent.parent
UTC = dt.timezone.utc
IOS = {"X-Client": "ios-app"}
KEY = ec.generate_private_key(ec.SECP256R1())
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()
# 07:40 in Los Angeles (PDT, UTC-7) on 27 Oct.
LA_MORNING = dt.datetime(2026, 10, 27, 14, 40, tzinfo=UTC)


def tok():
    return secrets.token_hex(32)


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Off, with nothing cached and no connection, unless a test turns it on."""
    for name in ("MYSIGN_APNS", "MYSIGN_APNS_KEY", "MYSIGN_APNS_KEY_ID",
                 "MYSIGN_APNS_TEAM_ID", "MYSIGN_APNS_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(apns, "_jwt", {})
    monkeypatch.setattr(apns, "_client", None)
    monkeypatch.setattr(apns, "RETRY_WAIT", 0)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("MYSIGN_APNS", "1")
    monkeypatch.setenv("MYSIGN_APNS_KEY", PEM)
    monkeypatch.setenv("MYSIGN_APNS_KEY_ID", "KEYID12345")
    monkeypatch.setenv("MYSIGN_APNS_TEAM_ID", "TEAMID6789")


@pytest.fixture
def c():
    return TestClient(server.app)


@pytest.fixture
def apple(monkeypatch):
    """A stand-in for Apple: answers per token, records every call."""
    class Apple:
        calls, answers = [], {}

        def post(self, token, data):
            self.calls.append((token, json.loads(data)))
            answer = self.answers.get(token, 200)
            if isinstance(answer, list):                 # one answer per attempt
                answer = answer.pop(0) if len(answer) > 1 else answer[0]
            if isinstance(answer, Exception):
                raise answer
            return answer if isinstance(answer, tuple) else (answer, None, None)
    a = Apple()
    a.calls, a.answers = [], {}
    monkeypatch.setattr(apns, "_post", a.post)
    return a


def _user(c, email):
    sign_in(c, email)
    return last_uid()


def _register(c, token, **kw):
    return c.post("/api/push/apns", json={"token": token, **kw})


# ------------------------------------------------------------------ the switch

def test_off_until_the_key_and_the_switch_are_both_there(monkeypatch):
    assert not apns.enabled()
    monkeypatch.setenv("MYSIGN_APNS", "1")
    assert not apns.enabled()                            # a switch without a key
    monkeypatch.setenv("MYSIGN_APNS_KEY", PEM)
    monkeypatch.setenv("MYSIGN_APNS_KEY_ID", "K")
    assert not apns.enabled()
    monkeypatch.setenv("MYSIGN_APNS_TEAM_ID", "T")
    assert apns.enabled()
    monkeypatch.setenv("MYSIGN_APNS", "0")
    assert not apns.enabled()                            # a key without the switch


def test_off_means_not_available_and_nothing_is_sent(c, apple):
    uid = _user(c, "apns-off@x.test")
    for path in ("/api/push/apns", "/api/push/apns/remove", "/api/push/apns/status"):
        r = c.post(path, json={"token": tok()})
        assert r.status_code == 404 and "aren't available" in r.json()["detail"], path
    assert load(uid, apns.KIND, None) is None
    onboard(c)
    assert c.get("/api/state", headers=IOS).json()["native_push"] is False
    assert "native_push" not in c.get("/api/state").json()        # a browser isn't told
    # Tokens left from a time it was on are not sent to, and don't count as a device.
    save(uid, apns.KIND, [{"token": tok(), "at": "2026-01-01T00:00:00+00:00"}])
    assert push.send(uid, "My Sign", "hello", kind="test") == {"sent": 0, "pruned": 0, "failed": 0}
    assert not push.has_device(uid) and apple.calls == []


def test_the_app_is_told_when_it_can_offer_notifications(c, on):
    _user(c, "apns-state@x.test")
    onboard(c)
    assert c.get("/api/state", headers=IOS).json()["native_push"] is True
    assert "native_push" not in c.get("/api/state").json()


def test_the_secret_carries_the_key_the_way_other_secrets_are_read():
    for name, env in {"apns": "MYSIGN_APNS", "apns_key": "MYSIGN_APNS_KEY",
                      "apns_key_id": "MYSIGN_APNS_KEY_ID", "apns_team_id": "MYSIGN_APNS_TEAM_ID",
                      "apns_env": "MYSIGN_APNS_ENV"}.items():
        assert bootstrap.KEYS[name] == env


# --------------------------------------------------------------------- devices

def test_register_unregister_and_status(c, on):
    uid = _user(c, "apns-reg@x.test")
    t = tok()
    assert c.post("/api/push/apns/status", json={"token": t}).json()["registered"] is False
    r = _register(c, t.upper(), tz="America/Chicago")              # hex in either case
    assert r.status_code == 200 and r.json() == {"devices": 1, "registered": True}
    held = load(uid, apns.KIND, None)
    assert [d["token"] for d in held] == [t] and held[0]["tz"] == "America/Chicago"
    assert set(held[0]) == {"token", "tz", "at"}
    st = c.post("/api/push/apns/status", json={"token": t}).json()
    assert st["registered"] is True and st["devices"] == 1
    assert c.post("/api/push/apns/remove", json={"token": t}).json() == {"devices": 0, "registered": False}
    assert load(uid, apns.KIND, None) is None
    assert load(apns.INDEX, apns._slot(t), None) is None
    assert c.post("/api/push/apns/status", json={"token": t}).json()["registered"] is False


def test_registering_twice_is_one_device_and_a_second_phone_is_two(c, on):
    uid = _user(c, "apns-dup@x.test")
    a, b = tok(), tok()
    assert _register(c, a).json()["devices"] == 1
    assert _register(c, a).json()["devices"] == 1
    assert _register(c, b).json()["devices"] == 2
    assert [d["token"] for d in load(uid, apns.KIND, None)] == [a, b]


@pytest.mark.parametrize("bad", ["", "abc", "z" * 64, "a" * 63, "a" * 201,
                                 "https://api.push.apple.com/" + "a" * 40, "a" * 32 + " " + "a" * 31])
def test_what_is_not_a_device_token_is_refused_and_not_stored(c, on, bad):
    uid = _user(c, f"apns-bad-{secrets.token_hex(3)}@x.test")
    assert _register(c, bad).status_code in (400, 422)
    assert load(uid, apns.KIND, None) is None


def test_a_refreshed_token_replaces_the_old_one(c, on):
    uid = _user(c, "apns-refresh@x.test")
    old, new, other = tok(), tok(), tok()
    _register(c, old)
    _register(c, other)
    assert _register(c, new, replaces=old).json()["devices"] == 2
    assert [d["token"] for d in load(uid, apns.KIND, None)] == [other, new]
    assert load(apns.INDEX, apns._slot(old), None) is None
    assert load(apns.INDEX, apns._slot(new), None) == {"uid": uid}


def test_devices_are_capped_and_the_newest_win(on):
    uid = "u-apns-cap-" + secrets.token_hex(4)
    tokens = [tok() for _ in range(apns.MAX_DEVICES + 2)]
    for t in tokens:
        assert apns.register(uid, t) <= apns.MAX_DEVICES
    assert [d["token"] for d in load(uid, apns.KIND, None)] == tokens[-apns.MAX_DEVICES:]
    assert load(apns.INDEX, apns._slot(tokens[0]), None) is None      # no line left for the dropped
    assert load(apns.INDEX, apns._slot(tokens[-1]), None) == {"uid": uid}


def test_another_accounts_token_cannot_be_removed_or_read(c, on):
    hers = _user(c, "apns-hers@x.test")
    t = tok()
    _register(c, t)
    other = TestClient(server.app)
    _user(other, "apns-other@x.test")
    assert other.post("/api/push/apns/status", json={"token": t}).json()["registered"] is False
    assert other.post("/api/push/apns/remove", json={"token": t}).json()["devices"] == 0
    assert [d["token"] for d in load(hers, apns.KIND, None)] == [t]
    assert load(apns.INDEX, apns._slot(t), None) == {"uid": hers}


def test_a_phone_that_changes_hands_stops_getting_the_last_accounts_notifications(c, on, apple):
    first = _user(c, "apns-first@x.test")
    t = tok()
    _register(c, t)
    other = TestClient(server.app)
    second = _user(other, "apns-second@x.test")
    assert _register(other, t).json()["devices"] == 1              # same phone, next account
    assert load(first, apns.KIND, None) is None
    assert load(apns.INDEX, apns._slot(t), None) == {"uid": second}
    push.send(first, "My Sign", "for the first account", kind="test")
    assert apple.calls == []
    push.send(second, "My Sign", "for the second", kind="test")
    assert [call[0] for call in apple.calls] == [t]


def test_routes_need_a_signed_in_account(c, on):
    assert _register(c, tok()).status_code == 401


def test_signing_out_takes_the_phone_off_the_account(c, on, apple):
    uid = _user(c, "apns-out@x.test")
    t = tok()
    _register(c, t)
    assert c.post("/api/auth/signout").status_code == 200
    assert load(uid, apns.KIND, None) is None
    assert load(apns.INDEX, apns._slot(t), None) is None
    push.send(uid, "My Sign", "nobody is signed in", kind="test")
    assert apple.calls == []


def test_deleting_the_account_deletes_the_tokens_and_the_index(c, on):
    uid = _user(c, "apns-delete@x.test")
    a, b = tok(), tok()
    _register(c, a)
    _register(c, b)
    assert c.delete("/api/account").json() == {"deleted": True}
    assert load(uid, apns.KIND, None) is None
    assert load(apns.INDEX, apns._slot(a), None) is None
    assert load(apns.INDEX, apns._slot(b), None) is None


def test_the_export_counts_the_phone_and_never_holds_the_token(c, on):
    _user(c, "apns-export@x.test")
    onboard(c)
    t = tok()
    _register(c, t, tz="Europe/London")
    raw = c.get("/api/export").text
    ex = json.loads(raw)
    assert ex["notification_devices"] == 1
    assert len(ex["iphone_app_devices"]) == 1
    assert ex["iphone_app_devices"][0]["tz"] == "Europe/London"
    assert ex["iphone_app_devices"][0]["registered_at"]
    assert t not in raw and apns.KIND not in ex["other"]


# ------------------------------------------------------------ the provider token

def test_the_provider_token_is_an_es256_jwt_apple_can_check(on):
    jwt = apns.provider_token(now=1_800_000_000)
    head, claims, sig = jwt.split(".")
    assert json.loads(_unb64(head)) == {"alg": "ES256", "kid": "KEYID12345"}
    assert json.loads(_unb64(claims)) == {"iss": "TEAMID6789", "iat": 1_800_000_000}
    raw = _unb64(sig)
    assert len(raw) == 64                                 # r and s, bare: not DER
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    KEY.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert "=" not in jwt


def test_the_provider_token_is_reused_then_renewed_before_the_hour(on, monkeypatch):
    t0 = 1_800_000_000
    first = apns.provider_token(now=t0)
    assert apns.provider_token(now=t0 + 60) == first
    assert apns.provider_token(now=t0 + apns.TOKEN_LIFE - 1) == first
    later = apns.provider_token(now=t0 + apns.TOKEN_LIFE)
    assert later != first
    assert json.loads(_unb64(later.split(".")[1]))["iat"] == t0 + apns.TOKEN_LIFE
    assert apns.TOKEN_LIFE < 3600 and apns.TOKEN_LIFE >= 20 * 60   # Apple's two limits
    # A different key is a different token at once, not after the cache runs out.
    monkeypatch.setenv("MYSIGN_APNS_KEY_ID", "ROTATED001")
    rotated = apns.provider_token(now=t0 + apns.TOKEN_LIFE + 1)
    assert json.loads(_unb64(rotated.split(".")[0]))["kid"] == "ROTATED001"


@pytest.mark.parametrize("shape", [
    lambda pem: pem.replace("\n", "\\n"),                               # line breaks written out
    lambda pem: "".join(pem.strip().splitlines()[1:-1]),                # the bare base64 body
    lambda pem: "  " + pem + "\n\n",
])
def test_the_key_is_read_however_the_secret_holds_it(on, monkeypatch, shape):
    monkeypatch.setenv("MYSIGN_APNS_KEY", shape(PEM))
    assert apns.provider_token(now=1_800_000_000).count(".") == 2


# ------------------------------------------------------------------ the request

def _transport(monkeypatch, status=200, body=None):
    seen = []

    def answer(request):
        seen.append(request)
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)
    monkeypatch.setattr(apns, "_client", httpx.Client(transport=httpx.MockTransport(answer)))
    return seen


def test_the_request_apple_gets(on, monkeypatch):
    seen = _transport(monkeypatch)
    t = tok()
    data = json.dumps(apns.payload("Mars on your Venus", "A line.", "/app?from=push&pk=morning", "morning")).encode()
    assert apns._post(t, data) == (200, None, None)
    r = seen[0]
    assert str(r.url) == f"https://api.push.apple.com/3/device/{t}" and r.method == "POST"
    assert r.headers["apns-topic"] == "app.mysign.ios"
    assert r.headers["apns-push-type"] == "alert" and r.headers["apns-priority"] == "10"
    assert r.headers["authorization"] == "bearer " + apns.provider_token()
    assert int(r.headers["apns-expiration"]) > 0
    assert json.loads(r.content) == {
        "aps": {"alert": {"title": "Mars on your Venus", "body": "A line."}, "sound": "default"},
        "url": "/app?from=push&pk=morning", "kind": "morning"}


def test_sandbox_or_production_is_chosen_by_config(on, monkeypatch):
    assert apns.host() == "https://api.push.apple.com"
    monkeypatch.setenv("MYSIGN_APNS_ENV", "sandbox")
    assert apns.host() == "https://api.sandbox.push.apple.com"
    seen = _transport(monkeypatch, 410, {"reason": "Unregistered", "timestamp": 1_700_000_000_000})
    assert apns._post(tok(), b"{}") == (410, "Unregistered", 1_700_000_000_000)
    assert seen[0].url.host == "api.sandbox.push.apple.com"
    monkeypatch.setenv("MYSIGN_APNS_ENV", "production")
    assert apns.host() == "https://api.push.apple.com"


def test_the_client_speaks_http2_and_is_kept(on, monkeypatch):
    made = []
    monkeypatch.setattr(httpx, "Client", lambda **kw: made.append(kw) or object())
    assert apns._http() is apns._http()                   # one connection, reused
    assert made == [{"http2": True, "timeout": apns.TIMEOUT}]
    assert importlib.util.find_spec("h2")                 # httpx needs it for http2=True


def test_the_apps_name_is_not_said_twice_and_nothing_extra_rides_along():
    plain = apns.payload("My Sign", "It's a quiet day in your chart. How are you doing?", "/app", None)
    assert plain == {"aps": {"alert": {"body": "It's a quiet day in your chart. How are you doing?"},
                             "sound": "default"}, "url": "/app"}
    titled = apns.payload("Mars is on your Venus", "Say the thing.", "/app?from=push&pk=morning", "morning")
    assert set(titled) == {"aps", "url", "kind"}          # no uid, no email, no mood, no name


# ---------------------------------------------------------------------- sending

def _device(uid, token=None, at="2026-11-01T00:00:00+00:00"):
    token = token or tok()
    held = load(uid, apns.KIND, []) or []
    save(uid, apns.KIND, [*held, {"token": token, "tz": None, "at": at}])
    save(apns.INDEX, apns._slot(token), {"uid": uid})
    return token


def test_success_and_a_token_apple_says_is_gone(on, apple, capsys):
    uid = "u-apns-" + secrets.token_hex(4)
    good, gone = _device(uid), _device(uid)
    apple.answers[gone] = (410, "Unregistered", None)
    out = push.send(uid, "My Sign", "hello", kind="test")
    assert out == {"sent": 1, "pruned": 1, "failed": 0}
    assert [d["token"] for d in load(uid, apns.KIND, None)] == [good]
    assert load(apns.INDEX, apns._slot(gone), None) is None
    assert [call[0] for call in apple.calls] == [good, gone]       # 410 is not retried
    logs = capsys.readouterr().out
    assert "apns.failed" in logs and "Unregistered" in logs
    assert good not in logs and gone not in logs, "a device token reached the logs"


def test_a_410_from_before_she_registered_again_keeps_the_token(on, apple):
    uid = "u-apns-" + secrets.token_hex(4)
    t = _device(uid, at="2026-11-10T00:00:00+00:00")
    before = dt.datetime(2026, 11, 1, tzinfo=UTC).timestamp() * 1000
    apple.answers[t] = (410, "Unregistered", before)
    assert push.send(uid, "My Sign", "hello", kind="test") == {"sent": 0, "pruned": 0, "failed": 1}
    assert [d["token"] for d in load(uid, apns.KIND, None)] == [t]
    after = dt.datetime(2026, 11, 11, tzinfo=UTC).timestamp() * 1000
    apple.answers[t] = (410, "Unregistered", after)
    assert push.send(uid, "My Sign", "hello", kind="test")["pruned"] == 1
    assert load(uid, apns.KIND, None) is None


def test_429_is_not_retried_and_the_token_is_kept(on, apple):
    uid = "u-apns-" + secrets.token_hex(4)
    t = _device(uid)
    apple.answers[t] = (429, "TooManyRequests", None)
    assert push.send(uid, "My Sign", "hello", kind="test") == {"sent": 0, "pruned": 0, "failed": 1}
    assert len(apple.calls) == 1
    assert [d["token"] for d in load(uid, apns.KIND, None)] == [t]


@pytest.mark.parametrize("first", [(503, "ServiceUnavailable", None), (500, "InternalServerError", None),
                                   httpx.ConnectError("connection reset")])
def test_a_5xx_or_a_dropped_connection_is_retried_once(on, apple, first):
    uid = "u-apns-" + secrets.token_hex(4)
    t = _device(uid)
    apple.answers[t] = [first, 200]
    assert push.send(uid, "My Sign", "hello", kind="test") == {"sent": 1, "pruned": 0, "failed": 0}
    assert len(apple.calls) == 2


def test_apple_down_costs_two_tries_and_no_token(on, apple):
    uid = "u-apns-" + secrets.token_hex(4)
    t = _device(uid)
    apple.answers[t] = (503, "ServiceUnavailable", None)
    assert push.send(uid, "My Sign", "hello", kind="test") == {"sent": 0, "pruned": 0, "failed": 1}
    assert len(apple.calls) == 2                          # one retry, then tomorrow
    assert [d["token"] for d in load(uid, apns.KIND, None)] == [t]


@pytest.mark.parametrize("status,reason", [(400, "BadDeviceToken"), (400, "DeviceTokenNotForTopic"),
                                           (403, "InvalidProviderToken"), (413, "PayloadTooLarge")])
def test_other_refusals_fail_without_a_retry_and_without_losing_the_token(on, apple, status, reason):
    """A wrong `apns_env` answers BadDeviceToken for everyone: that must not empty the roster."""
    uid = "u-apns-" + secrets.token_hex(4)
    t = _device(uid)
    apple.answers[t] = (status, reason, None)
    assert push.send(uid, "My Sign", "hello", kind="test") == {"sent": 0, "pruned": 0, "failed": 1}
    assert len(apple.calls) == 1 and load(uid, apns.KIND, None)[0]["token"] == t


def test_an_expired_provider_token_is_made_again_and_the_send_retried(on, apple):
    uid = "u-apns-" + secrets.token_hex(4)
    t = _device(uid)
    apns._jwt.update({"token": "stale", "at": 9e18, "for": ("KEYID12345", "TEAMID6789")})
    apple.answers[t] = [(403, "ExpiredProviderToken", None), 200]
    assert push.send(uid, "My Sign", "hello", kind="test")["sent"] == 1
    assert apns._jwt == {}                                # dropped; the real _post would mint a new one


def test_a_broken_key_fails_quietly_for_her_and_loudly_in_the_log(on, monkeypatch, capsys):
    monkeypatch.setenv("MYSIGN_APNS_KEY", "not a key")
    monkeypatch.setattr(apns, "_client", httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200))))
    uid = "u-apns-" + secrets.token_hex(4)
    _device(uid)
    assert push.send(uid, "My Sign", "hello", kind="test") == {"sent": 0, "pruned": 0, "failed": 1}
    assert "apns.failed" in capsys.readouterr().out


# ------------------------------------------------------ one notification a device

def _web_sub(host):
    b64 = lambda n: base64.urlsafe_b64encode(secrets.token_bytes(n)).decode().rstrip("=")  # noqa: E731
    return {"endpoint": f"https://{host}/send/{secrets.token_hex(8)}",
            "keys": {"p256dh": b64(65), "auth": b64(16)}}


@pytest.fixture
def web(monkeypatch):
    got = []
    monkeypatch.setattr(push, "webpush", lambda subscription_info, **kw: got.append(
        (subscription_info["endpoint"], json.loads(kw["data"]))))
    return got


def test_the_app_and_a_laptop_each_get_one_and_the_home_screen_copy_is_skipped(on, apple, web, monkeypatch):
    counted = []
    monkeypatch.setattr("app.activity.track_lake", lambda uid, name, *a, **kw: counted.append((name, kw)))
    uid = "u-apns-" + secrets.token_hex(4)
    phone = _device(uid)
    laptop, home_screen = _web_sub("fcm.googleapis.com"), _web_sub("web.push.apple.com")
    save(uid, "push", [laptop, home_screen])
    out = push.send(uid, "Mars is on your Venus", "Say the thing.", url="/app", kind="morning")
    assert out == {"sent": 2, "pruned": 0, "failed": 0}
    assert [call[0] for call in apple.calls] == [phone]
    assert [ep for ep, _ in web] == [laptop["endpoint"]]
    # The same words and the same link on both.
    assert apple.calls[0][1]["url"] == web[0][1]["url"] == "/app?from=push&pk=morning"
    assert apple.calls[0][1]["aps"]["alert"] == {"title": web[0][1]["title"], "body": web[0][1]["body"]}
    assert counted == [("push_sent", {"kind": "morning"})]         # one event, however many devices
    assert len(load(uid, "push", None)) == 2                       # skipped, not removed


def test_if_the_app_did_not_take_it_the_web_goes_out_as_before(on, apple, web):
    uid = "u-apns-" + secrets.token_hex(4)
    phone = _device(uid)
    home_screen = _web_sub("web.push.apple.com")
    save(uid, "push", [home_screen])
    apple.answers[phone] = (503, "ServiceUnavailable", None)
    out = push.send(uid, "My Sign", "hello", kind="morning")
    assert out == {"sent": 1, "pruned": 0, "failed": 1}
    assert [ep for ep, _ in web] == [home_screen["endpoint"]]


def test_web_only_accounts_are_untouched(on, apple, web):
    uid = "u-apns-" + secrets.token_hex(4)
    home_screen = _web_sub("web.push.apple.com")
    save(uid, "push", [home_screen])
    assert push.send(uid, "My Sign", "hello", kind="morning") == {"sent": 1, "pruned": 0, "failed": 0}
    assert apple.calls == [] and [ep for ep, _ in web] == [home_screen["endpoint"]]


def test_the_test_notification_reaches_the_phone(c, on, apple):
    _user(c, "apns-test@x.test")
    t = tok()
    _register(c, t)
    assert c.post("/api/push/test").json() == {"sent": 1, "pruned": 0, "failed": 0}
    assert apple.calls[0][0] == t
    assert apple.calls[0][1]["aps"]["alert"] == {
        "body": "That worked. This is how your morning notifications will look."}


# ---------------------------------------------------------------- the scheduler

@pytest.fixture
def roster(monkeypatch):
    users = []
    monkeypatch.setattr(auth, "all_users", lambda: list(users))
    monkeypatch.setattr(server, "_natal", lambda uid: {})
    monkeypatch.setattr(server, "_sky", lambda uid, day=None: {})
    monkeypatch.setattr(context, "build", lambda uid, *a, **kw: (
        "block", {"band": "high", "headlines": [{"transit": "Mars", "natal": "Venus"}]}))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def add(native=True, subs=(), account=None):
        uid = "u-apns-cron-" + secrets.token_hex(5)
        save(uid, "profile", {"tz": "America/Los_Angeles", "name": "Maya"})
        save(uid, "account", {"push_style": "sky", **(account or {})})
        if subs:
            save(uid, "push", list(subs))
        users.append(uid)
        return (uid, _device(uid)) if native else (uid, None)
    return add


def test_the_morning_job_reaches_a_phone_with_no_browser_subscription(on, apple, web, roster):
    uid, phone = roster()
    out = cron.morning(LA_MORNING)
    assert out["pushed"] == 1
    assert [call[0] for call in apple.calls] == [phone] and web == []
    sent = apple.calls[0][1]
    assert sent["url"] == "/app?from=push&pk=morning" and sent["kind"] == "morning"
    assert sent["aps"]["alert"] == {"body": "Mars is on your Venus today."}
    stamp = load(uid, "pushed-2026-10-27", None)
    assert stamp["state"] == "sent" and stamp["sent"] == 1
    # Once a day, whichever way it travels.
    assert cron.morning(LA_MORNING + dt.timedelta(minutes=15))["pushed"] == 0
    assert len(apple.calls) == 1


def test_the_morning_job_picks_the_channel_per_device(on, apple, web, roster):
    browser_only, _ = roster(native=False, subs=[_web_sub("fcm.googleapis.com")])
    phone_only, phone = roster()
    both, both_phone = roster(subs=[_web_sub("web.push.apple.com")])
    nothing, _ = roster(native=False)
    assert cron.morning(LA_MORNING)["pushed"] == 3
    assert sorted(call[0] for call in apple.calls) == sorted([phone, both_phone])
    assert [ep for ep, _ in web] == [load(browser_only, "push", None)[0]["endpoint"]]
    assert load(nothing, "pushed-2026-10-27", None) is None
    assert load(phone_only, "pushed-2026-10-27", None)["sent"] == 1
    assert load(both, "pushed-2026-10-27", None)["sent"] == 1      # the phone, once


def test_her_settings_hold_for_the_phone_too(on, apple, web, roster):
    roster(account={"push_off": True})
    roster(account={"push_time": "09:00"})                         # 07:40 now: not yet
    roster(account={"tz": "Asia/Tokyo"})                           # 23:40 where she is
    _, due = roster(account={"push_time": "07:30"})
    assert cron.morning(LA_MORNING)["pushed"] == 1
    assert [call[0] for call in apple.calls] == [due]


def test_with_apns_off_a_phone_only_account_is_not_even_composed_for(apple, web, roster, monkeypatch):
    uid, _ = roster()
    built = []
    monkeypatch.setattr(context, "build", lambda u, *a, **kw: built.append(u))
    assert cron.morning(LA_MORNING)["pushed"] == 0
    assert built == [] and apple.calls == [] and load(uid, "pushed-2026-10-27", None) is None


def test_no_morning_note_on_a_phone_during_a_crisis_episode(on, apple, roster, monkeypatch):
    from engine import episode
    roster()
    monkeypatch.setattr(episode, "active", lambda uid: True)
    assert cron.morning(LA_MORNING)["pushed"] == 0 and apple.calls == []


def test_the_sky_heads_up_counts_a_phone_as_a_device(on, roster):
    uid, _ = roster()
    assert not skypush._candidate(uid)                             # not opted in
    save(uid, "sky-prefs", {"event_push": True})
    assert skypush._candidate(uid)
    apns.forget(uid)
    assert not skypush._candidate(uid)


# ------------------------------------------------------------------- the shell

NATIVE = ROOT / "native"


def test_the_shell_declares_push_and_asks_only_when_she_turns_it_on():
    import plistlib
    ent = plistlib.loads((NATIVE / "ios/App/App/App.entitlements").read_bytes())
    assert ent == {"aps-environment": "development"}      # Xcode writes "production" into a store build
    project = (NATIVE / "ios/App/App.xcodeproj/project.pbxproj").read_text()
    assert project.count("CODE_SIGN_ENTITLEMENTS = App/App.entitlements;") == 2
    for name in ("PushCenter.swift", "PushPlugin.swift", "PushCenterTests.swift"):
        assert f"{name} in Sources" in project, name
    assert "PushPlugin()" in (NATIVE / "ios/App/App/MySignViewController.swift").read_text()
    # iOS is asked for permission in exactly one place: the plugin's `enable`,
    # which the page calls from the Settings switch. Nothing asks at launch.
    swift = {p.name: p.read_text() for p in (NATIVE / "ios/App/App").glob("*.swift")}
    asking = [name for name, text in swift.items() if "requestAuthorization" in text]
    assert asking == ["PushPlugin.swift"]
    assert swift["PushPlugin.swift"].count("requestAuthorization") == 1
    assert "registerForRemoteNotifications" not in swift["AppDelegate.swift"].split("didRegister")[0]
    # No third-party push SDK.
    deps = json.loads((NATIVE / "package.json").read_text())["dependencies"]
    assert not [d for d in deps if any(x in d.lower() for x in ("firebase", "onesignal", "push"))]


def test_nothing_in_the_app_says_notifications_are_coming_later():
    page = (ROOT / "app/static/index.html").read_text()
    for text in (page, (NATIVE / "bridge.js").read_text(), (NATIVE / "README.md").read_text()):
        assert "arrive in an update" not in text and "arrive in an app update" not in text


def test_the_bridge_registers_the_phone_only_after_she_allows_it():
    """native/bridge.js run outside the app, with iOS and the server faked
    (tests/bridge_push.js prints what happened in each case)."""
    import shutil
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    out = subprocess.run(["node", str(ROOT / "tests/bridge_push.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)

    # Not offered until the server says it can send.
    assert r["unavailable"]["available"] is False
    # Turning on: iOS first, then the server, with the token in the body.
    assert r["enable"]["result"] == {"ok": True, "permission": "granted"}
    assert r["enable"]["log"] == [["native", "enable"], ["POST", "/api/push/apns", {"token": "aa" * 32, "tz": "UTC"}]]
    assert r["enable"]["state"] == {"permission": "granted", "on": True, "today": None}
    # She said no to iOS: the server hears nothing.
    assert r["denied"]["result"] == {"ok": False, "permission": "denied"}
    assert r["denied"]["log"] == [["native", "enable"]]
    assert r["denied"]["state"] == {"permission": "denied", "on": False}
    # iOS allowed it but gave no token (no network): said, not registered.
    assert r["no_token"]["result"]["ok"] is False and r["no_token"]["result"]["message"]
    assert [x for x in r["no_token"]["log"] if x[0] == "POST"] == []
    # Turning off removes this phone, by its token.
    assert r["disable"]["log"][-1] == ["POST", "/api/push/apns/remove", {"token": "aa" * 32}]
    assert r["disable"]["state"]["on"] is False
    # A token iOS changed is swapped: at launch, and while the app is open.
    assert ["POST", "/api/push/apns", {"token": "bb" * 32, "tz": "UTC", "replaces": "aa" * 32}] in r["refresh_at_launch"]["log"]
    assert r["refresh_at_launch"]["kept"] == "bb" * 32
    assert ["POST", "/api/push/apns", {"token": "cc" * 32, "tz": "UTC", "replaces": "aa" * 32}] in r["refresh_live"]["log"]
    # Never asked at launch when she hasn't turned them on.
    assert r["launch_untouched"]["log"] == []
    # Signing out forgets the phone had them on.
    assert r["signout"]["kept"] == ""
    # A tap waits for the page, and can only ever open the app's own screens.
    assert r["taps"]["early"] == ["/app?p=c1&from=push&pk=person"]
    assert r["taps"]["late"] == ["/app?sky=events&from=push&pk=sky", "/app", "/app"]
