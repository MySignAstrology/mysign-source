"""
Operator console access: per-person operators with roles, the break-glass
token, the admin bootstrap, CSRF, and the audit trail every view must leave.
"""
import os
import pathlib
import sys
import tempfile
import time
import uuid

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-opsauth-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)

import app.server as server
from app import operators
from engine.store import save
from tests.test_app import last_uid, onboard, sign_in

MUT = {"x-ops-request": "1"}


def _wipe_operators():
    for uid in operators.uids():
        operators.remove(uid)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Operators live in the shared table; a leftover one would switch the
    console on for every later test that expects it absent."""
    monkeypatch.delenv("MYSIGN_OPS_TOKEN", raising=False)
    monkeypatch.delenv("MYSIGN_OPS_ADMINS", raising=False)
    _wipe_operators()
    yield
    _wipe_operators()


def fresh(email):
    """Codes are capped at five an hour per address, and parametrized tests
    sign the same people in again and again."""
    local, dom = email.split("@")
    return f"{local}.{uuid.uuid4().hex[:6]}@{dom}"


def person(email, role=None):
    c = sign_in(TestClient(server.app), fresh(email))
    uid = last_uid()
    if role:
        operators.put(uid, role, added_by="test")
    return c, uid


@pytest.fixture
def subject():
    """Someone whose conversation may be opened: she reported a reply."""
    c, uid = person("subject@x.com")
    onboard(c)
    save(uid, "reports", [{"id": "x", "reason": "", "at": "2026-10-27T10:00:00"}])
    return uid


def _actions():
    return [(e["action"], e["subject"]) for e in operators.audit_trail(1)["entries"]]


# --------------------------------------------------------------- the matrix

ANALYST = ["/api/ops/activity/overview", "/api/ops/funnel", "/api/ops/me",
           "/api/ops/report/funnel", "/api/ops/health"]
SAFETY = ["/api/ops/roster", "/api/ops/flagged", "/api/ops/activity/users",
          "/api/ops/activity/user/{s}", "/api/ops/user/{s}"]
ADMIN = ["/api/ops/operators", "/api/ops/audit"]


@pytest.mark.parametrize("role,allowed", [
    ("analyst", ANALYST),
    ("safety", ANALYST + SAFETY),
    ("admin", ANALYST + SAFETY + ADMIN),
])
def test_each_role_sees_exactly_its_routes(role, allowed, subject):
    c, _ = person(f"{role}@ops.com", role)
    for path in ANALYST + SAFETY + ADMIN:
        r = c.get(path.format(s=subject))
        want = 200 if path in allowed else 403
        assert r.status_code == want, (role, path, r.status_code, r.text[:120])
        assert r.headers["cache-control"] == "no-store", path
    grant = c.post("/api/ops/plan", json={"uid": subject, "tier": "plus"}, headers=MUT)
    assert grant.status_code == (200 if role == "admin" else 403)


def test_me_reports_the_role_and_a_masked_address():
    c, uid = person("someone.long@ops.com", "safety")
    me = c.get("/api/ops/me").json()
    assert me["uid"] == uid and me["role"] == "safety" and me["via"] == "session"
    assert me["email_masked"].endswith("@ops.com") and "someone.long" not in me["email_masked"]


def test_unflagged_conversation_stays_closed_even_to_an_admin():
    c, _ = person("boss@ops.com", "admin")
    _, quiet = person("quiet@x.com")
    assert c.get(f"/api/ops/user/{quiet}").status_code == 403
    assert ("conversation.refused", quiet) in _actions()


def test_a_denied_role_is_audited():
    c, uid = person("nosy@ops.com", "analyst")
    assert c.get("/api/ops/roster").status_code == 403
    e = operators.audit_trail(1)["entries"][0]
    assert e["action"] == "denied" and e["op"] == uid and e["params"]["need"] == "safety"


# ------------------------------------------------------- who gets in at all

def test_absent_when_nothing_is_configured():
    c, _ = person("plain@x.com")
    for client in (c, TestClient(server.app)):
        for path in ("/api/ops/me", "/api/ops/roster", "/api/ops/activity/overview"):
            assert client.get(path).status_code == 404, path


def test_configured_401_when_signed_out_403_when_not_an_operator():
    person("anyop@ops.com", "analyst")
    assert TestClient(server.app).get("/api/ops/me").status_code == 401
    c, _ = person("plain2@x.com")
    r = c.get("/api/ops/activity/overview")
    assert r.status_code == 403 and r.headers["cache-control"] == "no-store"


def test_break_glass_token_is_admin_and_logged(monkeypatch, capsys, subject):
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "t0k")
    c = TestClient(server.app)
    capsys.readouterr()
    me = c.get("/api/ops/me", headers={"x-ops-token": "t0k"}).json()
    assert me == {"uid": None, "role": "admin", "via": "token",
                  "email_masked": None, "roles": list(operators.ROLES),
                  "areas": {"analytics": True, "manage": True, "social": None}}
    assert '"event": "ops.breakglass"' in capsys.readouterr().out
    # No x-ops-request needed: a custom token header is already unforgeable.
    assert c.post("/api/ops/plan", json={"uid": subject, "tier": "plus"},
                  headers={"x-ops-token": "t0k"}).status_code == 200
    assert c.get("/api/ops/me", headers={"x-ops-token": "nope"}).status_code == 401


def test_a_signed_in_operator_wins_over_the_token(monkeypatch):
    monkeypatch.setenv("MYSIGN_OPS_TOKEN", "t0k")
    c, uid = person("ana@ops.com", "analyst")
    me = c.get("/api/ops/me", headers={"x-ops-token": "t0k"}).json()
    assert me["via"] == "session" and me["uid"] == uid


def test_admin_bootstrap_from_env(monkeypatch):
    first = fresh("first@ops.com")
    monkeypatch.setenv("MYSIGN_OPS_ADMINS", f" {first.upper()} , second@ops.com")
    c = sign_in(TestClient(server.app), first)
    uid = last_uid()
    me = c.get("/api/ops/me").json()
    assert me["role"] == "admin" and me["uid"] == uid
    assert operators.get(uid)["role"] == "admin"          # persisted, not just implied
    # Once persisted the env can go and the admin stays.
    monkeypatch.delenv("MYSIGN_OPS_ADMINS")
    assert c.get("/api/ops/operators").status_code == 200
    other, _ = person("third@ops.com")
    assert other.get("/api/ops/me").status_code == 403


# ---------------------------------------------------------- managing people

def test_operator_crud_and_the_last_admin():
    admin, me = person("root@ops.com", "admin")
    email = fresh("newbie@ops.com")
    newc, new = sign_in(TestClient(server.app), email), last_uid()

    r = admin.post("/api/ops/operators", headers=MUT,
                   json={"email": email.title(), "role": "analyst", "label": "Sam"})
    assert r.status_code == 200 and r.json()["uid"] == new and r.json()["role"] == "analyst"
    assert admin.post("/api/ops/operators", headers=MUT,
                      json={"email": "nobody@ops.com", "role": "analyst"}).status_code == 404
    assert admin.post("/api/ops/operators", headers=MUT,
                      json={"email": email, "role": "god"}).status_code == 400

    rows = {o["uid"]: o for o in admin.get("/api/ops/operators").json()["operators"]}
    assert rows[new]["role"] == "analyst" and rows[new]["label"] == "Sam"
    assert rows[new]["added_by"] == me and "newbie" not in rows[new]["email_masked"]

    # Sole admin: can neither leave nor step down.
    assert admin.delete(f"/api/ops/operators/{me}", headers=MUT).status_code == 409
    assert admin.post("/api/ops/operators", headers=MUT,
                      json={"uid": me, "role": "safety"}).status_code == 409
    # With a second admin, either can go.
    assert admin.post("/api/ops/operators", headers=MUT,
                      json={"uid": new, "role": "admin"}).status_code == 200
    assert admin.delete(f"/api/ops/operators/{me}", headers=MUT).status_code == 200
    assert operators.get(me) is None
    assert admin.get("/api/ops/me").status_code == 403    # took effect at once
    assert newc.delete("/api/ops/operators/unobody00", headers=MUT).status_code == 404

    acts = [a for a, _ in _actions()]
    assert {"operator.set", "operator.remove"} <= set(acts)


# ------------------------------------------------------------------- audit

def test_conversation_and_journey_views_are_audited(subject):
    c, op = person("safe@ops.com", "safety")
    assert c.get(f"/api/ops/user/{subject}").status_code == 200
    assert c.get(f"/api/ops/activity/user/{subject}?days=7").status_code == 200
    rows = operators.audit_trail(1)["entries"]
    seen = {(e["action"], e["subject"]): e for e in rows}
    convo = seen[("conversation.open", subject)]
    assert convo["op"] == op and convo["role"] == "safety" and convo["via"] == "session"
    assert convo["params"]["reported"] is True
    assert seen[("journey.view", subject)]["params"]["days"] == 7

    admin, _ = person("auditor@ops.com", "admin")
    trail = admin.get("/api/ops/audit?days=1").json()["entries"]
    assert trail[0]["action"] == "audit.view"                   # newest first, self-recorded
    assert any(e["action"] == "conversation.open" and e["subject"] == subject for e in trail)


def test_no_audit_no_data(monkeypatch, subject):
    """If the trail can't be written, the conversation isn't shown."""
    c, _ = person("safe2@ops.com", "safety")

    def broken(*a, **k):
        raise RuntimeError("table unavailable")
    monkeypatch.setattr(operators, "save", broken)
    r = c.get(f"/api/ops/user/{subject}")
    assert r.status_code == 500 and "messages" not in r.text


# --------------------------------------------------------------- CSRF/CORS

def test_cookie_mutations_need_the_custom_header(subject):
    c, _ = person("csrf@ops.com", "admin")
    body = {"uid": subject, "tier": "plus"}
    assert c.post("/api/ops/plan", json=body).status_code == 403
    assert c.post("/api/ops/plan", json=body, headers=MUT).status_code == 200


def test_cross_site_is_refused(subject):
    c, _ = person("xsite@ops.com", "admin")
    assert c.get("/api/ops/me", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert c.get("/api/ops/me", headers={"sec-fetch-site": "same-site"}).status_code == 403
    assert c.get("/api/ops/me", headers={"sec-fetch-site": "same-origin"}).status_code == 200
    body = {"uid": subject, "tier": "plus"}
    assert c.post("/api/ops/plan", json=body,
                  headers={**MUT, "origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/ops/plan", json=body,
                  headers={**MUT, "origin": "http://testserver"}).status_code == 200


# ------------------------------------------------------------- on DynamoDB

def test_operators_and_audit_work_on_dynamodb(monkeypatch):
    """Tests run on files; production is DynamoDB. Same calls, both backends."""
    moto = pytest.importorskip("moto")
    import boto3
    from boto3.dynamodb.conditions import Key

    import engine.store_dynamo as sd
    for k, v in {"AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "testing",
                 "AWS_SECRET_ACCESS_KEY": "testing", "MYSIGN_TABLE": "mysign-test"}.items():
        monkeypatch.setenv(k, v)
    # Patches undone inside the mock: the autouse wipe must hit files again.
    with moto.mock_aws(), monkeypatch.context() as mp:
        boto3.client("dynamodb").create_table(
            TableName="mysign-test", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}])
        mp.setattr(sd, "_table", None)
        mp.setattr(operators, "store", sd)
        mp.setattr(operators, "load", sd.load)
        mp.setattr(operators, "save", sd.save)

        operators.put("uadmin0001", "admin", added_by="test", label="A")
        operators.put("uanaly0001", "analyst", added_by="test")
        assert operators.uids() == ["uadmin0001", "uanaly0001"]
        assert operators.get("uanaly0001")["role"] == "analyst" and operators.admin_count() == 1
        ctx = {"uid": "uadmin0001", "role": "admin", "via": "session"}
        operators.audit(ctx, "journey.view", subject="usubject01", days=7)
        operators.audit(ctx, "audit.view", days=1)
        trail = operators.audit_trail(1)["entries"]
        assert [e["action"] for e in trail] == ["audit.view", "journey.view"]
        item = sd.table().query(KeyConditionExpression=Key("pk").eq("_audit"))["Items"][0]
        assert int(item["ttl"]) > time.time() + 399 * 86400
        operators.remove("uanaly0001")
        assert operators.uids() == ["uadmin0001"] and operators.get("uanaly0001") is None
