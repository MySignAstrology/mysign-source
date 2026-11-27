"""
The admin platform's one sign-in (app/admin.py): who gets a code, what the
session opens, what ends it, and that it and the app's session never mix.
"""
import os
import pathlib
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-admin-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"

import app.server as server
from app import admin, auth, operators
from tests.test_ops_auth import MUT, _wipe_operators, fresh, person

REQ, VER = "/api/auth/request-admin", "/api/auth/verify-admin"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in ("MYSIGN_OPS_TOKEN", "MYSIGN_OPS_ADMINS", "MYSIGN_ADMIN_URL"):
        monkeypatch.delenv(k, raising=False)
    _wipe_operators()
    yield
    _wipe_operators()


def staff(role, **kw):
    """(email, uid) of an operator with an app account."""
    email = fresh(f"{role}@staff.com")
    uid = auth.uid_for(email)
    operators.put(uid, role, added_by="test", **kw)
    return email, uid


def enter(email):
    c = TestClient(server.app)
    code = c.post(REQ, json={"email": email}).json()["dev_code"]
    assert c.post(VER, json={"email": email, "code": code}).status_code == 200
    return c


def test_a_listed_address_signs_in_and_it_is_audited():
    email, uid = staff("admin")
    c = enter(email.upper())
    s = c.get("/api/ops/session").json()
    assert (s["uid"], s["email"], s["role"]) == (uid, email, "admin")
    assert s["areas"] == {"analytics": True, "manage": True, "social": "admin"}
    assert c.get("/api/ops/operators").status_code == 200
    assert ("signin", uid) in [(e["action"], e["op"]) for e in operators.audit_trail(1)["entries"]]


def test_an_unlisted_address_gets_the_same_answer_and_nothing_is_made():
    c = TestClient(server.app)
    nobody, someone = fresh("nobody@x.com"), fresh("someone@x.com")
    uid = auth.uid_for(someone)                       # a person using the app, not staff
    for email in (nobody, someone):
        r = c.post(REQ, json={"email": email})
        assert (r.status_code, r.json()) == (200, {"sent": True})
        assert c.post(VER, json={"email": email, "code": "000000"}).status_code == 401
    assert auth.uid_for(nobody, create=False) is None
    # Not even with a real code from the app's own sign-in.
    code = c.post("/api/auth/request", json={"email": someone}).json()["dev_code"]
    assert c.post(VER, json={"email": someone, "code": code}).status_code == 401
    assert operators.get(uid) is None


def test_a_limited_send_is_not_told_apart(monkeypatch):
    email, _ = staff("analyst")
    monkeypatch.setattr(auth, "request_code_for",
                        lambda *a, **k: (False, {"error": "Too many codes", "status": 429}))
    r = TestClient(server.app).post(REQ, json={"email": email})
    assert (r.status_code, r.json()) == (200, {"sent": True})


def test_the_review_accounts_fixed_code_never_opens_this_door(monkeypatch):
    email, _ = staff("admin")
    monkeypatch.setenv("MYSIGN_REVIEW_EMAIL", email)
    monkeypatch.setenv("MYSIGN_REVIEW_CODE", "reviewcode12")
    c = TestClient(server.app)
    assert c.post(VER, json={"email": email, "code": "reviewcode12"}).status_code == 401


def test_wrong_code():
    email, _ = staff("admin")
    c = TestClient(server.app)
    code = c.post(REQ, json={"email": email}).json()["dev_code"]
    wrong = f"{(int(code) + 1) % 1000000:06d}"
    r = c.post(VER, json={"email": email, "code": wrong})
    assert r.status_code == 401 and not c.cookies.get(admin.cookie())


def test_bootstrap_admin(monkeypatch):
    email = fresh("boss@x.com")
    uid = auth.uid_for(email)
    monkeypatch.setenv("MYSIGN_OPS_ADMINS", email)
    enter(email)
    assert operators.get(uid)["role"] == "admin"


def test_the_two_sessions_never_stand_in_for_each_other():
    email, uid = staff("admin")
    adm = enter(email).cookies.get(admin.cookie())
    c = TestClient(server.app)
    c.cookies.set(auth.COOKIE, adm)                                   # admin token as app session
    assert c.get("/api/account/identifiers").status_code == 401
    assert c.get("/api/ops/me").status_code == 401
    c = TestClient(server.app)
    c.cookies.set(admin.cookie(), auth.sign(uid, auth.session_version(uid)))   # and the reverse
    assert c.get("/api/ops/session").status_code == 401
    # An admin session is not a sign-in to the app either.
    assert enter(email).get("/api/account/identifiers").status_code == 401


@pytest.mark.parametrize("bad", ["", "x", "a.b", "é.é", "e30.e30", "." * 5])
def test_garbage_cookies(bad):
    staff("admin")
    r = TestClient(server.app).get("/api/ops/session",
                                   headers={"cookie": f"{admin.cookie()}={bad}".encode()})
    assert r.status_code == 401


def test_expiry(monkeypatch):
    email, _ = staff("admin")
    c = enter(email)
    now = time.time()
    monkeypatch.setattr(admin.time, "time", lambda: now + admin.SESSION_DAYS * 86400 + 1)
    assert c.get("/api/ops/session").status_code == 401


def test_signing_out_ends_every_copy_but_not_the_app():
    email, uid = staff("admin")
    a, b = enter(email), enter(email)
    ver = auth.session_version(uid)
    assert a.post("/api/ops/signout").status_code == 403              # no x-ops-request
    assert a.post("/api/ops/signout", headers=MUT).status_code == 200
    assert b.get("/api/ops/session").status_code == 401
    assert auth.session_version(uid) == ver
    enter(email)


def test_removal_is_immediate_and_being_added_back_revives_nothing():
    email, uid = staff("safety")
    c = enter(email)
    operators.remove(uid)
    assert c.get("/api/ops/session").status_code == 401
    operators.put(uid, "safety", added_by="test")
    assert c.get("/api/ops/session").status_code == 401


def test_a_role_change_keeps_the_session_and_applies_at_once():
    email, uid = staff("admin")
    c = enter(email)
    operators.put(uid, "analyst", added_by="test")
    assert c.get("/api/ops/session").json()["role"] == "analyst"
    assert c.get("/api/ops/operators").status_code == 403


def test_a_deleted_account_is_out():
    from engine import store
    email, uid = staff("admin")
    c = enter(email)
    store.delete(uid, "account")
    assert c.get("/api/ops/session").status_code == 401


# ------------------------------------------------------------------- areas

@pytest.mark.parametrize("role,social,want", [
    ("social", None, {"analytics": False, "manage": False, "social": "editor"}),
    ("analyst", None, {"analytics": True, "manage": True, "social": None}),
    ("analyst", True, {"analytics": True, "manage": True, "social": "editor"}),
    ("safety", True, {"analytics": True, "manage": True, "social": "editor"}),
    ("admin", False, {"analytics": True, "manage": True, "social": "admin"}),
])
def test_areas(role, social, want):
    email, _ = staff(role, social=social)
    assert enter(email).get("/api/ops/session").json()["areas"] == want


@pytest.mark.parametrize("path", ["me", "activity/overview", "report/funnel", "health", "roster",
                                  "flagged", "activity/users", "operators", "audit"])
def test_social_only_reads_nothing_of_the_app(path):
    email, _ = staff("social")
    assert enter(email).get(f"/api/ops/{path}").status_code == 403


def test_admins_hand_out_social_and_no_session_secret_is_shown():
    email, _ = staff("admin")
    c = enter(email)
    _, uid = person("writer@x.com")
    r = c.post("/api/ops/operators", json={"uid": uid, "role": "social"}, headers=MUT).json()
    assert r["role"] == "social" and r["social"] == "editor"
    r = c.post("/api/ops/operators", json={"uid": uid, "role": "analyst", "social": True}, headers=MUT).json()
    assert r["social"] == "editor"
    # A later role change that doesn't mention it keeps it.
    r = c.post("/api/ops/operators", json={"uid": uid, "role": "safety"}, headers=MUT).json()
    assert r["social"] == "editor"
    body = c.get("/api/ops/operators").json()
    assert body["roles"] == ["social", "analyst", "safety", "admin"]
    assert all("sv" not in o for o in body["operators"])


def test_changes_still_need_the_header():
    email, _ = staff("admin")
    c = enter(email)
    _, uid = person("writer@x.com")
    assert c.post("/api/ops/operators", json={"uid": uid, "role": "social"}).status_code == 403


@pytest.mark.parametrize("path", [REQ, VER])
def test_no_cross_site_sign_in(path):
    r = TestClient(server.app).post(path, json={"email": "a@b.com", "code": "1"},
                                    headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


# ------------------------------------------------------------------- pages

def test_pages_and_the_old_address(monkeypatch):
    c = TestClient(server.app, follow_redirects=False)
    for p in ("/admin", "/manage", "/analytics", "/ops"):
        r = c.get(p)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store", p
    monkeypatch.setenv("MYSIGN_ADMIN_URL", "https://admin.mysignapp.com")
    r = c.get("/ops")
    assert (r.status_code, r.headers["location"]) == (302, "https://admin.mysignapp.com/analytics")
