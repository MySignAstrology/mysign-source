"""
The operator view for the pilot.

The SMS rig held every outbound message for a human to approve before it sent.
That does not survive contact with a streaming chat — you cannot gate a reply
that is already appearing word by word without destroying the thing being
tested. So the in-app pilot is **monitored, not gated**, and that is a real
difference from `rig/operator.py` rather than a rename.

What monitoring has to buy, then, is speed: every red-tier event visible
immediately, every flagged or reported thread readable, and enough shape per
participant to notice someone going quiet. Paging still fires from `app/alerts.py`; this is where you
go once it has.

Off unless something turns it on: an operator in the table, an admin email in
MYSIGN_OPS_ADMINS, or the legacy MYSIGN_OPS_TOKEN. It reads other people's
conversations — that is the entire point, and the reason it does not exist by
default.

Who may do what lives in `app/operators.py`; this file decides who is asking.
"""
import datetime as dt
import hmac
import os
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response

from app import log, operators, opsindex
from engine import quota, thread
from engine.store import load

NO_STORE = {"Cache-Control": "no-store"}
_SAFE = {"GET", "HEAD", "OPTIONS"}


def _deny(status, detail=None):
    return HTTPException(status, detail, headers=NO_STORE)


def _admin_emails():
    raw = os.environ.get("MYSIGN_OPS_ADMINS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def enabled():
    """Cheap checks first: the table is only read when the env says nothing."""
    return bool(os.environ.get("MYSIGN_OPS_TOKEN") or _admin_emails() or operators.uids())


def _cross_site(request):
    """
    Fetch Metadata is authoritative when the browser sends it. Origin is the
    fallback for browsers that don't; behind CloudFront the Host header is the
    Function URL's, so an Origin check alone would refuse the real console.
    """
    site = request.headers.get("sec-fetch-site")
    if site:
        return site not in ("same-origin", "none")
    origin = request.headers.get("origin")
    if not origin:
        return False
    host = urlsplit(origin).netloc.lower()
    allowed = {h.lower() for h in (request.headers.get("host"),
                                   request.headers.get("x-forwarded-host")) if h}
    return origin == "null" or host not in allowed


def _session(request):
    """(uid, role) of whoever the admin session, app session or Bearer token names."""
    from app import admin, auth
    uid, rec = admin.current(request)
    if rec:
        return uid, rec["role"]
    uid = auth.current_user(request)
    if not uid:
        return None, None
    rec = operators.get(uid)
    if rec:
        return uid, rec["role"]
    # First sign-in of a bootstrap admin: persisted, so the env list can be
    # emptied later without locking anyone out. A code-verified address is
    # the only thing trusted here; a phone-only account never matches.
    email = ((getattr(request.state, "account", None) or {}).get("email") or "").lower()
    if email and email in _admin_emails():
        operators.put(uid, "admin", added_by="MYSIGN_OPS_ADMINS", label="bootstrap admin")
        log.emit("ops.admin_bootstrapped", uid=uid)
        return uid, "admin"
    return uid, None


def authorize(request: Request, role="analyst"):
    """
    The operator asking, as {uid, role, via}. A signed-in operator first; the
    shared token only as break-glass. 404 while nothing is configured, so the
    console's existence is not advertised; 401/403 once it is.
    """
    if role not in operators.ROLES:
        raise ValueError(f"unknown role {role!r}")
    if not enabled():
        raise _deny(404)
    if _cross_site(request):
        log.emit("ops.cross_site.refused", path=request.url.path)
        raise _deny(403, "Cross-site requests are refused.")

    uid, have = _session(request)
    if have:
        ctx = {"uid": uid, "role": have, "via": "session"}
    elif request.headers.get("x-ops-token") is not None:
        token = os.environ.get("MYSIGN_OPS_TOKEN") or ""
        # Header only. A query-string token lands in CloudFront and browser
        # history, and in any log that records a URL.
        given = request.headers.get("x-ops-token") or ""
        if not token or not hmac.compare_digest(given.encode(), token.encode()):
            raise _deny(401, "bad operator token")
        ctx = {"uid": None, "role": "admin", "via": "token"}
        # Loud on purpose: once admins exist this should be removed from the
        # secret, and every use until then is worth asking about.
        log.emit("ops.breakglass", level="warn", path=request.url.path,
                 method=request.method)
    elif uid:
        log.emit("ops.denied", uid=uid, reason="not an operator")
        raise _deny(403, "This account is not an operator.")
    else:
        raise _deny(401, "Sign in to the app first.")

    if operators.rank(ctx["role"]) < operators.rank(role):
        operators.audit(ctx, "denied", need=role, path=request.url.path)
        raise _deny(403, f"This needs the {role} role.")

    # A cookie rides along on a cross-site form post; a custom header can't
    # without a CORS preflight this app never grants. Bearer and token
    # callers set headers deliberately, but one rule is simpler to trust.
    if request.method not in _SAFE and ctx["via"] == "session" \
            and request.headers.get("x-ops-request") != "1":
        log.emit("ops.csrf.refused", uid=uid, path=request.url.path)
        raise _deny(403, "Missing x-ops-request header.")

    request.state.operator = ctx
    return ctx


def need(role):
    """A route dependency: authorize, and keep the answer out of every cache."""
    def dep(request: Request, response: Response):
        response.headers.update(NO_STORE)
        return authorize(request, role)
    return dep


def _roster_row(uid):
    prof = load(uid, "profile", None) or {}
    acct = load(uid, "account", {}) or {}
    msgs = thread.recent(uid, 0)
    tiers = [m.get("tier") for m in msgs if m.get("role") == "assistant"]
    last = msgs[-1]["at"] if msgs else None
    quiet_days = None
    if last:
        # Her stamps are in her time zone, ahead of the server's date for half
        # the planet; a message "tomorrow" by the server's clock is today, not -1.
        quiet_days = max(0, (dt.date.today() - dt.date.fromisoformat(last[:10])).days)
    return {
        "uid": uid,
        "name": prof.get("name"),
        "consented": bool(acct.get("consented_at")),
        "onboarded": bool(prof),
        "messages": len(msgs),
        "episodes": len(load(uid, "episodes", []) or []),
        "red": sum(1 for t in tiers if t == "red"),
        "amber": sum(1 for t in tiers if t == "amber"),
        "last_activity": last,
        "quiet_days": quiet_days,
        "quota": quota.usage(uid),
    }


def roster(cursor=None, limit=50):
    """
    One page of the pilot, most recently active first, with enough shape per
    person to spot trouble — and the cursor for the next page. Paged from the
    ops index (app/opsindex.py), so a request reads its page, not everyone.
    Red and amber across everyone are the flagged queue's job.
    """
    opsindex.ensure()
    pairs, nxt = opsindex.page(cursor, max(1, min(limit, 200)))
    return opsindex.pmap(lambda p: _roster_row(p[0]), pairs), nxt


def conversation(uid, limit=200):
    prof = load(uid, "profile", None) or {}
    return {
        "uid": uid, "name": prof.get("name"),
        "birth": prof.get("birth_local"),
        "messages": thread.recent(uid, limit),
        "episodes": load(uid, "episodes", [])[-10:],
        "patterns": load(uid, "patterns", []),
    }


def flagged(days=7, cursor=None, limit=200):
    """
    Every red and amber reply, and every reported one, newest first — from the
    flag index (app/opsindex.py), so it costs the flags in range, not a read of
    every thread. ([rows], next_cursor).
    """
    opsindex.ensure()
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    rows, nxt = opsindex.flagged(cutoff, limit=max(1, min(limit, 500)), cursor=cursor)
    names = {}
    for r in rows:
        if r["uid"] not in names:
            names[r["uid"]] = (load(r["uid"], "profile", None) or {}).get("name")
    return [{"uid": r["uid"], "name": names[r["uid"]], "at": r["at"], "tier": r["tier"],
             "id": r.get("id"), "kind": r.get("kind", "tier")} for r in rows], nxt
