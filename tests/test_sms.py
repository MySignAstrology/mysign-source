"""
Phone sign-in. Run with: python -m pytest tests/test_sms.py -q

Every text costs money and a sign-in form that texts any number is an SMS
pumping target, so most of this is about refusing: when SMS is off, past each
limit, and past the day's ceiling — always without leaking a code.
"""
import itertools
import os

import pytest

os.environ["MYSIGN_DEV_AUTH"] = "1"
os.environ.pop("MYSIGN_TABLE", None)
os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)

from fastapi.testclient import TestClient

import app.server as server
from app import auth
from engine import store

_n = itertools.count(2000)


def number():
    """A fresh valid number per use: per-number counters outlive a test."""
    return f"(312) 555-{next(_n):04d}"


def viewer():
    """A fresh caller address per test, so the per-IP limit is the test's own."""
    return {"cloudfront-viewer-address": f"203.0.113.{next(_n) % 250}:443"}


@pytest.fixture
def c(monkeypatch):
    monkeypatch.delenv("MYSIGN_SMS_ORIGIN", raising=False)
    monkeypatch.delenv("MYSIGN_FROM_EMAIL", raising=False)
    monkeypatch.setenv("MYSIGN_DEV_AUTH", "1")
    return TestClient(server.app)


def phone_sign_in(c, phone, headers=None):
    r = c.post("/api/auth/request", json={"phone": phone}, headers=headers or viewer())
    assert r.status_code == 200, r.text
    v = c.post("/api/auth/verify", json={"phone": phone, "code": r.json()["dev_code"]})
    assert v.status_code == 200, v.text
    return v.json()["uid"]


def email_sign_in(c, email):
    code = c.post("/api/auth/request", json={"email": email}).json()["dev_code"]
    uid = c.post("/api/auth/verify", json={"email": email, "code": code}).json()["uid"]
    c.post("/api/consent", json={"ai": True})
    return uid


@pytest.mark.parametrize("raw, want", [
    ("(312) 555-1234", "+13125551234"),
    ("312-555-1234", "+13125551234"),
    ("+1 312 555 1234", "+13125551234"),
    ("13125551234", "+13125551234"),
    ("312.555.1234", "+13125551234"),
    ("+44 20 7946 0958", None),        # not US
    ("555-1234", None),                # no area code
    ("112-555-1234", None),            # area code can't start with 1
    ("312-155-1234", None),            # nor the exchange
    ("911-555-1234", None),            # N11 is a service
    ("23125551234", None),             # 11 digits not starting 1
    ("312 555 1234 ext 9", None),
    ("", None), (None, None),
])
def test_phone_normalization(raw, want):
    assert auth.normalize_phone(raw) == want


@pytest.mark.parametrize("raw, kind", [
    ("a@b.com", "email"), ("(312) 555-1234", "phone"), ("+13125551234", "phone"), ("hello", None),
])
def test_single_field_is_sorted_by_shape(raw, kind):
    assert auth.detect(raw) == kind


def test_phone_signs_in_through_the_sender(c, monkeypatch):
    sent = []
    monkeypatch.setenv("MYSIGN_SMS_ORIGIN", "arn:aws:sms-voice:us-east-2:1:phone-number/x")
    monkeypatch.setattr(auth, "_send_sms", lambda to, code: sent.append((to, code)))
    phone = number()
    r = c.post("/api/auth/request", json={"id": phone}, headers=viewer())
    assert r.json() == {"sent": True, "channel": "sms"}
    to, code = sent[0]
    assert to == auth.normalize_phone(phone)
    v = c.post("/api/auth/verify", json={"id": phone, "code": code})
    assert v.status_code == 200
    uid = v.json()["uid"]
    assert store.load(uid, "account", {})["phone"] == to
    # The key namespace holds a hash, never the number.
    assert auth.uid_for_id("phone", phone, create=False) == uid
    assert not any(to.lstrip("+") in k for k in store.keys("_auth"))


def test_sms_off_refuses_cleanly_and_leaks_nothing(c, monkeypatch):
    monkeypatch.delenv("MYSIGN_DEV_AUTH", raising=False)
    assert c.get("/api/auth/channels").json() == {"email": True, "sms": False}
    phone = number()
    r = c.post("/api/auth/request", json={"phone": phone}, headers=viewer())
    assert r.status_code == 503
    assert "use your email" in r.json()["detail"] and "dev_code" not in r.text
    k, _ = auth._ikey("phone", phone)
    assert store.load("_auth", auth._code_kind("phone", k), None) is None


def test_a_refused_send_is_a_503_and_the_number_stays_out_of_logs(c, monkeypatch, capsys):
    monkeypatch.setenv("MYSIGN_SMS_ORIGIN", "arn:x")

    def boom(to, code):
        raise RuntimeError(f"ValidationException: {to} is blocked")
    monkeypatch.setattr(auth, "_send_sms", boom)
    phone = number()
    r = c.post("/api/auth/request", json={"phone": phone}, headers=viewer())
    assert r.status_code == 503 and "dev_code" not in r.text
    assert auth.normalize_phone(phone)[2:] not in capsys.readouterr().out


def test_resend_waits_half_a_minute(c):
    phone, h = number(), viewer()
    assert c.post("/api/auth/request", json={"phone": phone}, headers=h).status_code == 200
    r = c.post("/api/auth/request", json={"phone": phone}, headers=h)
    assert r.status_code == 429


def test_per_number_limit(c, monkeypatch):
    monkeypatch.setattr(auth, "SMS_RESEND_SECONDS", 0)
    phone = number()
    codes = [c.post("/api/auth/request", json={"phone": phone}, headers=viewer()).status_code
             for _ in range(auth.SMS_PER_NUMBER_HOUR + 1)]
    assert codes == [200] * auth.SMS_PER_NUMBER_HOUR + [429]


def test_per_caller_limit(c):
    h = viewer()
    codes = [c.post("/api/auth/request", json={"phone": number()}, headers=h).status_code
             for _ in range(auth.SMS_PER_IP_HOUR + 1)]
    assert codes == [200] * auth.SMS_PER_IP_HOUR + [429]
    # Another caller is unaffected.
    assert c.post("/api/auth/request", json={"phone": number()}, headers=viewer()).status_code == 200


def test_daily_cap_points_to_email_and_is_alarmable(c, monkeypatch, capsys):
    used = store.counter("_auth", f"sms-day-{auth._sms_day()}", "n")
    monkeypatch.setenv("MYSIGN_SMS_DAILY_CAP", str(used + 1))
    assert c.post("/api/auth/request", json={"phone": number()}, headers=viewer()).status_code == 200
    r = c.post("/api/auth/request", json={"phone": number()}, headers=viewer())
    assert r.status_code == 503 and "email" in r.json()["detail"] and "dev_code" not in r.text
    assert '"event": "auth.sms_cap"' in capsys.readouterr().out
    assert c.get("/api/auth/channels").json()["sms"] is False
    # Email is untouched by the SMS ceiling.
    assert c.post("/api/auth/request", json={"email": "cap@x.com"}).status_code == 200


def test_email_and_phone_codes_never_share_a_record(c):
    phone = number()
    pc = c.post("/api/auth/request", json={"phone": phone}, headers=viewer()).json()["dev_code"]
    ec = c.post("/api/auth/request", json={"email": "both@x.com"}).json()["dev_code"]
    k, _ = auth._ikey("phone", phone)
    assert store.load("_auth", auth._code_kind("phone", k), None)["codes"][0]["hash"] != \
        store.load("_auth", auth._code_kind("email", auth._ekey("both@x.com")[0]), None)["codes"][0]["hash"]
    if pc != ec:
        assert c.post("/api/auth/verify", json={"phone": phone, "code": ec}).status_code == 401
    assert c.post("/api/auth/verify", json={"email": "both@x.com", "code": ec}).status_code == 200
    assert c.post("/api/auth/verify", json={"phone": phone, "code": pc}).status_code == 200


def test_adding_a_phone_to_an_account_then_signing_in_with_it(c, monkeypatch):
    monkeypatch.setattr(auth, "SMS_RESEND_SECONDS", 0)
    uid = email_sign_in(c, "link@x.com")
    phone = number()
    code = c.post("/api/account/identifier/request", json={"phone": phone},
                  headers=viewer()).json()["dev_code"]
    r = c.post("/api/account/identifier/verify", json={"phone": phone, "code": code})
    assert r.status_code == 200 and r.json()["phone"] == auth.normalize_phone(phone)
    assert c.get("/api/account/identifiers").json()["phone"] == auth.normalize_phone(phone)
    c.post("/api/auth/signout")
    assert phone_sign_in(TestClient(server.app), phone) == uid


def test_a_number_on_another_account_is_refused_after_proof(c, monkeypatch):
    monkeypatch.setattr(auth, "SMS_RESEND_SECONDS", 0)
    phone = number()
    other = phone_sign_in(TestClient(server.app), phone)
    uid = email_sign_in(c, "taken@x.com")
    code = c.post("/api/account/identifier/request", json={"phone": phone},
                  headers=viewer()).json()["dev_code"]
    r = c.post("/api/account/identifier/verify", json={"phone": phone, "code": code})
    assert r.status_code == 409 and "different account" in r.json()["detail"]
    assert auth.uid_for_id("phone", phone, create=False) == other != uid


def test_linking_requires_a_session(c):
    assert c.post("/api/account/identifier/request", json={"phone": number()}).status_code == 401


def test_deletion_removes_the_phone_and_export_includes_it(c):
    email_sign_in(c, "gone@x.com")
    phone = number()
    code = c.post("/api/account/identifier/request", json={"phone": phone},
                  headers=viewer()).json()["dev_code"]
    c.post("/api/account/identifier/verify", json={"phone": phone, "code": code})
    assert c.get("/api/export").json()["account"]["phone"] == auth.normalize_phone(phone)
    assert c.delete("/api/account").json() == {"deleted": True}
    assert auth.uid_for_id("phone", phone, create=False) is None
    assert auth.uid_for("gone@x.com", create=False) is None
    k, _ = auth._ikey("phone", phone)
    assert store.load("_auth", auth._code_kind("phone", k), None) is None


def test_the_text_is_one_part_and_domain_bound(monkeypatch):
    monkeypatch.setenv("MYSIGN_DOMAIN", "mysignapp.com")
    body = auth.sms_body("123456")
    assert len(body) <= 160 and body.isascii()
    assert body.endswith("\n@mysignapp.com #123456")
    monkeypatch.delenv("MYSIGN_DOMAIN")
    assert "@" not in auth.sms_body("123456")
