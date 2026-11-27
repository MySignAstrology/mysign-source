"""
Sign-in limits (security audit M1, M6, L1), from p2_auth.py and h_review.py.

Before: five guesses per code and every new code reset the count, so 25 real
guesses an hour (600 a day) against one address, forever; nothing limited
/api/auth/verify per network or service-wide; anyone could hold a victim at
"Too many codes requested" and void her code with each request; email sends
had no service-wide ceiling; the App Review account had no limit at all.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import app.server as server
from app import auth
from tests.test_app import sign_in  # noqa: F401  (sets the dev-auth env first)


@pytest.fixture(autouse=True)
def fresh_windows(monkeypatch):
    """Every counter window unique to the test, so limits never leak between
    tests sharing one store within the same hour."""
    tag = uuid.uuid4().hex[:6]
    real = auth._windows
    monkeypatch.setattr(auth, "_windows", lambda now: tuple(f"{w}{tag}" for w in real(now)))


@pytest.fixture
def edge(monkeypatch):
    monkeypatch.setenv("MYSIGN_AUTH_LIMITS", "1")


def email():
    return f"lim-{uuid.uuid4().hex[:8]}@x.com"


def ask(e, ip="198.51.100.7", device=None):
    ok, p = auth.request_code_for("email", e, ip=ip, device=device)
    return p.get("dev_code") if ok else p


def wrong(code):
    return f"{(int(code) + 1) % 1000000:06d}"


def test_new_codes_do_not_reset_the_guess_budget():
    e, evaluated = email(), 0
    for i in range(7):                                   # the old run: 7 sends x 7 guesses
        code = ask(e, ip=f"198.51.100.{60 + i}")
        for _ in range(7):
            _, err = auth.verify_code_for("email", e, wrong(code))
            evaluated += err == "That code isn't right."
    assert evaluated <= auth.VERIFY_FOREIGN_PER_ID_DAY   # was 25 in an hour


def test_guesses_from_elsewhere_do_not_lock_out_the_device_that_asked():
    e = email()
    mine = ask(e, device="her-phone-cookie-0001")
    for _ in range(auth.VERIFY_FOREIGN_PER_ID_DAY + 5):  # a stranger, no cookie
        auth.verify_code_for("email", e, "000000", ip="203.0.113.9")
    _, err = auth.verify_code_for("email", e, "000000", ip="203.0.113.9")
    assert "other devices" in err
    uid, err = auth.verify_code_for("email", e, mine, device="her-phone-cookie-0001")
    assert err is None and uid


def test_a_code_she_did_not_ask_for_does_not_void_the_one_she_has():
    e = email()
    hers = ask(e, ip="198.51.100.20", device="her-laptop-cookie-01")
    ask(e, ip="203.0.113.50", device="someone-else-cookie1")    # an attacker's request
    uid, err = auth.verify_code_for("email", e, hers, device="her-laptop-cookie-01")
    assert err is None and uid
    _, err = auth.verify_code_for("email", e, hers)               # and it's spent
    assert err


def test_requests_from_one_network_cannot_use_up_her_sends():
    e = email()
    for _ in range(auth.SENDS_PER_ID_IP_HOUR):
        assert isinstance(ask(e, ip="203.0.113.77"), str)
    refused = ask(e, ip="203.0.113.77")
    assert "Too many codes" in refused["error"]
    assert isinstance(ask(e, ip="198.51.100.30"), str)            # she still can


def test_a_code_still_works_after_a_cross_device_typo_or_two():
    e = email()
    code = ask(e)
    auth.verify_code_for("email", e, wrong(code))
    uid, err = auth.verify_code_for("email", e, code)
    assert err is None and uid


def test_verify_is_limited_per_network(edge):
    ip, refused = "203.0.113.200", None
    for i in range(auth.VERIFY_PER_IP_HOUR + 1):
        e = email()
        ask(e, ip=f"198.51.100.{i % 200}")
        _, refused = auth.verify_code_for("email", e, "000000", ip=ip)
    assert "this network" in refused


def test_verify_has_a_service_wide_ceiling(edge, monkeypatch, capsys):
    monkeypatch.setenv("MYSIGN_VERIFY_HOURLY_CAP", "3")
    errs = []
    for i in range(4):
        e = email()
        ask(e, ip=f"198.51.100.{100 + i}")
        errs.append(auth.verify_code_for("email", e, "000000", ip=f"192.0.2.{i}")[1])
    assert errs[:3] == ["That code isn't right."] * 3 and "busy" in errs[3]
    assert '"auth.cap"' in capsys.readouterr().out


def test_email_sends_have_a_service_wide_circuit_breaker(edge, monkeypatch, capsys):
    monkeypatch.setenv("MYSIGN_EMAIL_HOURLY_CAP", "2")
    got = [ask(email(), ip=f"198.51.100.{150 + i}") for i in range(3)]
    assert all(isinstance(g, str) for g in got[:2])
    assert got[2]["status"] == 503 and "paused" in got[2]["error"]
    assert '"auth.cap"' in capsys.readouterr().out


def test_email_sends_are_limited_per_network_and_per_domain(edge, monkeypatch):
    for _ in range(auth.EMAIL_PER_IP_HOUR):
        assert isinstance(ask(email(), ip="203.0.113.123"), str)
    assert ask(email(), ip="203.0.113.123")["status"] == 429
    monkeypatch.setattr(auth, "EMAIL_PER_DOMAIN_HOUR", 2)
    dom = f"bounce-{uuid.uuid4().hex[:6]}.test"
    got = [ask(f"u{i}@{dom}", ip=f"192.0.2.{50 + i}") for i in range(3)]
    assert got[2]["status"] == 503


def test_the_review_account_has_an_attempt_limit(monkeypatch):
    monkeypatch.setenv("MYSIGN_REVIEW_EMAIL", "review-lim@example.com")
    monkeypatch.setenv("MYSIGN_REVIEW_CODE", "482913-long-review-code")
    st = {}
    for g in range(auth.REVIEW_PER_HOUR + 5):
        err = auth.verify_code_for("email", "review-lim@example.com", f"{g:06d}")[1]
        st[err] = st.get(err, 0) + 1
    assert st["That code isn't right."] == auth.REVIEW_PER_HOUR
    _, err = auth.verify_code_for("email", "review-lim@example.com", "482913-long-review-code")
    assert "Too many tries" in err                         # even the right one, this hour


def test_the_browser_that_asked_gets_a_device_cookie_and_signs_in():
    c = TestClient(server.app)
    e = email()
    r = c.post("/api/auth/request", json={"email": e})
    assert "ms_dev=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    code = r.json()["dev_code"]
    stranger = TestClient(server.app)
    for _ in range(auth.VERIFY_FOREIGN_PER_ID_DAY + 2):
        stranger.post("/api/auth/verify", json={"email": e, "code": "000000"})
    assert c.post("/api/auth/verify", json={"email": e, "code": code}).status_code == 200


# ------------------------------------------- remembered devices (audit, Nov 2026)

def _signed_in_once(e, device):
    code = ask(e, device=device)
    uid, err = auth.verify_code_for("email", e, code, device=device)
    assert uid and not err
    return uid


def test_strangers_cannot_evict_the_code_on_her_remembered_device():
    e, phone = email(), "her-phone-cookie-remembered-01"
    _signed_in_once(e, phone)
    hers = ask(e, device=phone)
    for i in range(4):                                     # four requests from elsewhere
        ask(e, ip=f"203.0.113.{i + 1}", device=f"stranger-{i}-cookie-xxxxxxxx")
    uid, err = auth.verify_code_for("email", e, hers, device=phone)
    assert uid and not err


def test_strangers_cannot_spend_her_day_of_codes():
    e, phone = email(), "her-phone-cookie-remembered-02"
    _signed_in_once(e, phone)
    n = 0
    for i in range(10):                                    # many networks, many fake devices
        for _ in range(auth.SENDS_PER_ID_IP_HOUR):
            ask(e, ip=f"198.18.{i}.9", device=f"bot-{i}-{n}-cookie-xxxxxxx")
            n += 1
    assert "Too many codes" in ask(e, ip="198.18.99.9", device="new-bot-cookie-xxxxxxxx")["error"]
    code = ask(e, ip="198.51.100.7", device=phone)          # she still gets one
    assert isinstance(code, str)
    uid, err = auth.verify_code_for("email", e, code, device=phone)
    assert uid and not err


def test_a_new_device_is_still_held_to_the_address_limits():
    e = email()
    for i in range(auth.SENDS_PER_ID_HOUR):
        ask(e, ip=f"192.0.2.{i + 1}", device=f"dev-{i}-cookie-xxxxxxxxxx")
    assert "Too many codes" in ask(e, ip="192.0.2.200", device="another-cookie-xxxxxxxx")["error"]


def test_review_account_guesses_are_limited_per_network(edge, monkeypatch):
    monkeypatch.setenv("MYSIGN_REVIEW_EMAIL", "review@mysignapp.com")
    monkeypatch.setenv("MYSIGN_REVIEW_CODE", "a-long-review-code-123")
    if not auth.is_review_account("review@mysignapp.com"):
        pytest.skip("review account configured differently")
    for _ in range(auth.REVIEW_PER_HOUR):
        auth.verify_code_for("email", "review@mysignapp.com", "wrong", ip="203.0.113.50")
    _uid, err = auth.verify_code_for("email", "review@mysignapp.com", "a-long-review-code-123",
                                    ip="198.51.100.20")
    assert not err                                         # another network isn't locked out
