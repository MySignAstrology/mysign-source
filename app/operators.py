"""
Who may use the operator console, and what they did there.

An operator is an ordinary app account — signed in with the same email or phone
code as everyone else — whose uid is on this list with a role. No second
password store, no second sign-in flow to get wrong, and taking someone off the
list takes effect on their next request.

Roles are ordered; each includes the ones before it:

    analyst   aggregate numbers only — nobody in particular
    safety    + the roster, the flagged queue, one person's journey, and a
                conversation that has a safety flag or a report
    admin     + plan grants, managing operators, reading the audit trail

`social` is outside that order: the social media tool and nothing here. An
analyst or safety operator can be given the social tool too (`social: True` on
the record); an admin always has it, as its admin.

The audit trail exists because the console reads other people's lives. Every
data access writes one item *before* the data is returned; if the write fails,
the request fails. A view nobody can account for is worse than an outage.
"""
import datetime as dt
import re
import secrets
import time

from app import log
from engine import store
from engine.store import load, save

ROLES = ("analyst", "safety", "admin")
STAFF = ("social",) + ROLES             # everything an operator record may say
SET = "operators"
OPS_PK = "_ops"
AUDIT_PK = "_audit"
AUDIT_DAYS = 400
AUDIT_MAX_ROWS = 1000
UID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def rank(role):
    return ROLES.index(role) if role in ROLES else -1


# ---------------------------------------------------------------- operators

def get(uid):
    if not uid or not UID.match(uid):
        return None
    rec = load(OPS_PK, f"op-{uid}", None)
    return rec if rec and rec.get("role") in STAFF else None


def social(rec):
    """Their level in the social media tool: "admin", "editor" or None."""
    if rec["role"] == "admin":
        return "admin"
    return "editor" if rec["role"] == "social" or rec.get("social") else None


def put(uid, role, added_by, label=None, social=None):
    """`social` None keeps what they had."""
    if role not in STAFF:
        raise ValueError(f"unknown role {role!r}")
    if not UID.match(uid or ""):
        raise ValueError("invalid uid")
    prev = get(uid) or {}
    rec = {"role": role, "label": (label or prev.get("label") or "")[:80],
           "added_by": prev.get("added_by") or added_by,
           "added_at": prev.get("added_at") or int(time.time()),
           "social": bool(prev.get("social") if social is None else social),
           # What an admin session must match (app/admin.py). Random for a new
           # record, so one from before they were removed stays dead.
           "sv": prev.get("sv", "") if prev else secrets.token_hex(8)}
    if prev:
        rec["changed_by"], rec["changed_at"] = added_by, int(time.time())
    save(OPS_PK, f"op-{uid}", rec)
    store.add_member(SET, uid)
    return {"uid": uid, **rec}


def end_sessions(uid):
    """Every admin session this person has stops working."""
    rec = get(uid)
    if rec:
        rec["sv"] = secrets.token_hex(8)
        save(OPS_PK, f"op-{uid}", rec)


def remove(uid):
    store.delete(OPS_PK, f"op-{uid}")
    store.remove_member(SET, uid)


def uids():
    return store.members(SET)


def all_():
    return [{"uid": u, **r} for u in uids() if (r := get(u))]


def admin_count():
    # Read-check-write, so two admins removing each other at the same moment
    # could both succeed. MYSIGN_OPS_ADMINS and the break-glass token are the
    # way back from that; a transaction is not worth it at this headcount.
    return sum(1 for o in all_() if o["role"] == "admin")


# -------------------------------------------------------------------- audit

def _now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def audit(ctx, action, subject=None, **params):
    """
    Raises if the store does. Callers write this before returning anything, so
    an unrecorded view is a failed request rather than a silent one.
    """
    at = _now_iso()
    rec = {"at": at, "op": ctx.get("uid"), "role": ctx.get("role"),
           "via": ctx.get("via"), "action": action, "subject": subject,
           "params": params}
    save(AUDIT_PK, f"{at}#{secrets.token_hex(4)}", rec,
         ttl=int(time.time()) + AUDIT_DAYS * 86400)
    log.emit("ops.audit", op=rec["op"], role=rec["role"], via=rec["via"],
             action=action, subject=subject, params=params)
    return rec


def audit_trail(days=7, limit=AUDIT_MAX_ROWS):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
              ).strftime("%Y-%m-%dT%H:%M:%S")
    # Keys sort by time, so the cut and the newest-first order need no loads.
    sks = sorted((k for k in store.keys(AUDIT_PK) if k >= cutoff), reverse=True)
    out = []
    for sk in sks[:limit]:
        rec = load(AUDIT_PK, sk, None)
        if rec:
            out.append(rec)
    return {"entries": out, "days": days, "truncated": len(sks) > limit}
