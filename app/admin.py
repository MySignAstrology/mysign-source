"""
One sign-in for the admin platform (admin.mysignapp.com).

Social media, the app console and analytics used to have three doors: a Cognito
password, the consumer app's 90-day session, and a shared password. This is the
one that replaces them. Same email codes as the app (app/auth.py: same limits,
no passwords), same operator list (app/operators.py), but its own session:

- a separate cookie, signed with a separate key, so neither kind of session can
  ever stand in for the other;
- seven days, not ninety;
- tied to the operator record, so removing someone ends it on their next
  request, and signing out ends every copy of it without touching their phone.

Nobody is signed up here: an address that is not on the list is told exactly
what one that is on it is told, and nothing is sent.
"""
import base64
import hashlib
import hmac
import json
import os
import time

from app import auth, log, operators
from engine import store
from engine.store import load

SESSION_DAYS = 7
REQUESTS_PER_IP_HOUR = 20
SENT = {"sent": True}
BAD_CODE = "That code isn't right, or it has expired."


def cookie():
    """`__Host-` pins it to this exact host over https; plain http has no such thing."""
    return "__Host-ms_admin" if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else "ms_admin"


def url():
    """https://admin.mysignapp.com once it exists; empty until then."""
    return os.environ.get("MYSIGN_ADMIN_URL", "").strip().rstrip("/")


# ----------------------------------------------------------------- sessions

def _key():
    return hmac.new(auth.secret(), b"admin-session", hashlib.sha256).digest()


def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def sign(uid, sv):
    payload = _b64(json.dumps({"u": uid, "v": sv,
                               "e": int(time.time()) + SESSION_DAYS * 86400}).encode())
    return f"{payload}.{_b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())}"


def current(request):
    """(uid, operator record) of a live admin session, or (None, None)."""
    try:
        payload, sig = request.cookies.get(cookie(), "").split(".", 1)
        if not hmac.compare_digest(sig.encode(), _b64(
                hmac.new(_key(), payload.encode(), hashlib.sha256).digest()).encode()):
            return None, None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        uid, sv, exp = data["u"], data["v"], data["e"]
    except (ValueError, KeyError, TypeError):
        return None, None
    if exp <= time.time():
        return None, None
    rec = operators.get(uid)
    acct = load(uid, "account", None) if rec else None
    if not rec or not acct or rec.get("sv", "") != sv:
        return None, None
    request.state.account = acct
    return uid, rec


# ------------------------------------------------------------------ sign-in

def _staff_uid(email, admin_emails):
    """The account behind an address that may come in, or None. Never creates one."""
    if not email or auth.is_review_account(email):
        return None              # App Review's fixed code must never open this door
    uid = auth.uid_for(email, create=False)
    if uid and (operators.get(uid) or email in admin_emails):
        return uid
    return None


def request_code(email, admin_emails, ip=None, device=None):
    """
    (payload, status). Past the per-network limit, which is spent before the
    address is looked at, every address gets the same answer.
    """
    email = (email or "").strip().lower()
    if not auth.valid_email(email):
        return {"error": "That doesn't look like an email address."}, 400
    h = auth.ip_hash(ip)
    if h and not auth.dev_auth_allowed() and store.incr(
            "_auth", f"admin-ip-{h}-{int(time.time() // 3600)}", "n",
            limit=REQUESTS_PER_IP_HOUR, ttl=int(time.time()) + 2 * 3600) is None:
        return {"error": "Too many codes requested. Try again in an hour."}, 429
    if not _staff_uid(email, admin_emails):
        log.emit("admin.signin.unknown")
        return SENT, 200
    ok, payload = auth.request_code_for("email", email, ip=ip, device=device)
    if not ok:
        # Logged, not shown: a limit hit only by listed addresses would say who is listed.
        log.emit("admin.signin.not_sent", level="warn",
                 reason=payload.get("log") or payload.get("error"))
        return SENT, 200
    return ({**SENT, "dev_code": payload["dev_code"]} if "dev_code" in payload else SENT), 200


def verify(email, code, admin_emails, ip=None, device=None):
    """(uid, token) or (None, None)."""
    email = (email or "").strip().lower()
    uid = _staff_uid(email, admin_emails)
    if not uid:
        return None, None
    # The code is spent through the app's own check, so its guess limits hold here too.
    got, err = auth.verify_code_for("email", email, code, ip=ip, device=device)
    if err or got != uid:
        log.emit("admin.signin.rejected", level="warn", uid=uid, reason=err)
        return None, None
    rec = operators.get(uid)
    if not rec:
        rec = operators.put(uid, "admin", added_by="MYSIGN_OPS_ADMINS", label="bootstrap admin")
        log.emit("ops.admin_bootstrapped", uid=uid)
    operators.audit({"uid": uid, "role": rec["role"], "via": "admin"}, "signin")
    return uid, sign(uid, rec.get("sv", ""))


def areas(rec):
    """What the person may open. The pages read this; the APIs check for themselves."""
    role = rec["role"]
    return {"analytics": operators.rank(role) >= operators.rank("analyst"),
            "manage": operators.rank(role) >= operators.rank("analyst"),
            "social": operators.social(rec)}
