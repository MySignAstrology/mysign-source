"""
What people do in the app — screens and taps, never what they type.

The operator dashboard (/ops) is built from this. It answers "where do people
get stuck, what do they use, what did this person do this week" without
reading anyone's words: events carry a screen, a named control and a few
allowlisted properties. Message text, check-in notes, names, birth details and
anything typed are not accepted here and are not derivable from it.

Disclosed on the consent screen and in the privacy policy, switchable off in
Settings (account.analytics_off), kept 90 days.

Storage, per user: one item per day, "act-YYYY-MM-DD", a capped list of events
with a TTL. Globally: a counter item per day for totals, screens and taps
(atomic, so concurrent writers never lose counts), and a per-day member set of
who was active.

The rules for what an event may carry live in app/events.py, shared with the
anonymous collector. Every accepted event is also forwarded to the analytics
lake (app/forward.py) when MYSIGN_EVENTS_STREAM is set: the same cleaned
event under the same rules, and the same switch-off stops both.
"""
import datetime as dt
import os
import re
import time

from app import events, forward, log
from engine import store
from engine.store import load, save

RETAIN_DAYS = 90
MAX_EVENTS_PER_DAY = 800          # a real day is ~50-200; past this is a loop
MAX_BATCH = events.MAX_BATCH
TYPES = events.TYPES
NAME_RE = events.NAME_RE
SESSION_RE = events.SESSION_RE
PROPS = events.PROPS
# What DynamoDB keeps for /ops. Page views and heartbeats go to the lake only:
# they'd fill the 800-a-day cap without telling the dashboard anything new.
STORED = frozenset({"screen", "tap", "change", "open", "hide", "error", "server"})
SEEN_EVERY_S = 300                 # last_seen is written at most this often


def _day(ts=None):
    return dt.datetime.fromtimestamp(ts or time.time(), dt.timezone.utc).date().isoformat()


def _ttl():
    return int(time.time()) + RETAIN_DAYS * 86400


def _clean(e, now):
    """One event from the app, normalised, or None if it doesn't meet the rules."""
    return events.clean_event(e, now, events.CLIENT_TYPES)


def enabled(acct):
    return not (acct or {}).get("analytics_off")


def record(uid, session, evs, acct=None, anon_id=None):
    """Store a batch from the app. Returns how many were kept.

    `anon_id` is the device's random pre-sign-in id, if the app sent one; it
    rides along to the lake only, so a funnel can run from first visit to
    sign-in. The caller drops it when the browser asks not to be tracked."""
    acct = acct if acct is not None else (load(uid, "account", {}) or {})
    if not enabled(acct):
        return 0
    if not isinstance(session, str) or not SESSION_RE.match(session):
        session = "unknown"
    now = time.time()
    clean = [c for c in (_clean(e, now) for e in (evs or [])[:MAX_BATCH]) if c]
    for c in clean:
        c["s"] = session
    try:
        kept = _store(uid, [c for c in clean if c["ty"] in STORED], acct, now)
    except Exception as e:
        # A store failure costs this batch, never the request: track() was
        # already wrapped, record() wasn't, and a full counter item made
        # /api/activity a 500 for everyone (audit L2).
        log.error("activity.store_failed", exc=e, uid=uid)
        kept = 0
    if forward.stream():
        _forward([events.envelope(c, source="app", received_at=now, uid=uid, anon_id=anon_id,
                                  session_id=None if session == "unknown" else session)
                  for c in clean])
    return kept


def server_event(name, screen, props):
    ev = {"at": round(time.time(), 3), "ty": "server", "n": name, "s": "server"}
    if screen:
        ev["sc"] = screen
    p = events.clean_props(props)
    if p:
        ev["p"] = p
    return ev


def track(uid, name, screen=None, **props):
    """A server-side event: the things the server knows happened (a message
    was sent, a person was added) whether or not the app reported them."""
    acct = load(uid, "account", {}) or {}
    if not enabled(acct) or not NAME_RE.match(name):
        return 0
    ev = server_event(name, screen, props)
    try:
        kept = _store(uid, [ev], acct, time.time())
    except Exception as e:       # analytics must never break the thing it watches
        log.emit("activity.track_failed", uid=uid, name=name, error=type(e).__name__)
        return 0
    _forward([events.envelope(ev, source="server", uid=uid)])
    return kept


def track_anon(name, screen=None, props=None, anon_id=None):
    """
    A server event with no uid, for the lake only — nothing is written to
    DynamoDB. For what happens before anyone is signed in (a sign-in code was
    asked for), when there is no account to keep it on. `anon_id` is the
    device's random id from the request; the caller passes None under GPC/DNT.
    """
    if not forward.stream() or not NAME_RE.match(name):
        return 0
    ev = server_event(name, screen, props or {})
    _forward([events.envelope(ev, source="server", anon_id=anon_id)])
    return 1


def track_lake(uid, name, screen=None, **props):
    """
    A server event with the uid, for the lake only: no per-user DynamoDB item,
    no counters. For events the cron emits for many people at once
    (`push_sent`), where track()'s three or four writes per person would cost
    more than the event is worth. Switched off still means nothing at all.
    """
    if not forward.stream() or not NAME_RE.match(name):
        return 0
    try:
        if not enabled(load(uid, "account", {}) or {}):
            return 0
    except Exception as e:
        log.emit("activity.track_failed", uid=uid, name=name, error=type(e).__name__)
        return 0
    _forward([events.envelope(server_event(name, screen, props), source="server", uid=uid)])
    return 1


PAID = "plus"


def track_plan(uid, before, after, source):
    """
    `subscription_started` / `_renewed` / `_canceled` from a plan change
    (engine/plan.grant). `before` and `after` are plan.get() records; `source`
    is who changed it — apple, google, stripe or web from billing, operator for
    a hand grant. Nothing when the change isn't one of the three.
    """
    was = (before or {}).get("tier") == PAID
    now = (after or {}).get("tier") == PAID
    if now:
        name = "subscription_renewed" if was else "subscription_started"
    elif was:
        name = "subscription_canceled"
    else:
        return 0
    return track(uid, name, None, tier=PAID, source=source)


def track_auth(uid, channel, new_user, anon_id=None):
    """
    `auth.verified`: someone proved they hold the address (or the Apple /
    Google account) and is now signed in.

    Before she has agreed to the consent screen — every new account, at this
    point — this is a count and nothing more: no uid, no device id, and nothing
    kept on her record. After, it's an ordinary server event, and `anon_id`
    (her device's pre-sign-in id; pass None when the request carried Sec-GPC
    or DNT) joins the anonymous visit to the account in the lake.
    """
    try:
        acct = load(uid, "account", {}) or {}
        if not enabled(acct):
            return 0                 # switched off means not even counted
        ev = server_event("auth.verified", "sign_in",
                          {"channel": channel, "new": bool(new_user)})
        if not acct.get("consented_at"):
            _forward([events.envelope(ev, source="server")])
            return 0
        kept = _store(uid, [ev], acct, time.time())
        _forward([events.envelope(ev, source="server", uid=uid, anon_id=anon_id)])
        return kept
    except Exception as e:
        log.emit("activity.track_failed", uid=uid, what="auth.verified",
                 error=type(e).__name__)
        return 0


def _forward(envs):
    try:
        forward.send(envs)
    except Exception as e:       # send() doesn't raise; this is the belt to its braces
        log.emit("activity.forward_failed", level="warn", error_type=type(e).__name__)


# Distinct names on one day's shared counter item (audit L2). Every name is a
# new attribute on `_activity / _act#<day>`, and names come from the client:
# about seven accounts sending 800 made-up names a day would take the item
# past DynamoDB's 400 KB limit, after which every write to it fails — for
# everyone, all day. The app's real vocabulary is a few hundred names; past
# the cap a new name is counted under "<type>:other" instead.
MAX_AGG_FIELDS = 600


def _bounded(agg, counts):
    known = set(store.counters("_activity", agg) or {})
    out = {}
    for f, n in counts.items():
        if f not in known and ":" in f:
            if len(known) >= MAX_AGG_FIELDS:
                f = f.split(":", 1)[0] + ":other"
            known.add(f)
        out[f] = out.get(f, 0) + n
    return out


def _store(uid, evs, acct, now):
    if not evs:
        return 0
    by_day = {}
    for e in evs:
        by_day.setdefault(_day(e["at"]), []).append(e)
    kept = 0
    for day, batch in by_day.items():
        key = f"act-{day}"
        cur = load(uid, key, []) or []
        room = MAX_EVENTS_PER_DAY - len(cur)
        if room <= 0:
            continue
        batch = batch[:room]
        save(uid, key, sorted(cur + batch, key=lambda e: e["at"]), ttl=_ttl())
        kept += len(batch)
        agg, counts = f"_act#{day}", {"events": len(batch)}
        if not cur:
            counts["people"] = 1          # her first events today: one more active person
        for e in batch:
            if e["ty"] == "screen":
                f = f"screen:{e['n']}"
            elif e["ty"] in ("tap", "change", "server", "error"):
                f = f"{e['ty']}:{e['n']}"
            else:
                continue
            counts[f] = counts.get(f, 0) + 1
        for f, n in _bounded(agg, counts).items():
            store.incr("_activity", agg, f, by=n, ttl=_ttl())
    # last_seen, at most every few minutes — not a write per tap. The ops
    # console's index row moves with it: new row, account, then the old row
    # goes (app/opsindex.py).
    last = acct.get("last_seen") or 0
    if now - last > SEEN_EVERY_S and (acct.get("email") or acct.get("phone")):
        from app import opsindex
        was = opsindex.when(acct)
        acct["last_seen"] = now
        opsindex.put(uid, now)
        save(uid, "account", acct)
        opsindex.moved(uid, was, now)
    return kept


# ================================================================ reading

def _all_counter_fields(day):
    return store.counters("_activity", f"_act#{day}")


def _days(n):
    today = dt.datetime.now(dt.timezone.utc).date()
    return [(today - dt.timedelta(days=i)).isoformat() for i in range(n)][::-1]


def mask_email(email):
    if not email or "@" not in email:
        return None
    local, dom = email.split("@", 1)
    return f"{local[:1]}{'•' * max(2, min(len(local) - 1, 6))}@{dom}"


def overview(days=7):
    """
    The aggregate picture. Day counters for the series, screens and actions;
    the 7-day KPIs from app/reports.py (rollups, or the live counters) and the
    ops index — no request here reads every account.
    """
    from app import opsindex, reports
    from engine import funnel
    span = _days(days)
    series, screens, taps = [], {}, {}
    for d in span:
        c = _all_counter_fields(d)
        series.append({"date": d, "active": c.get("people", 0), "events": c.get("events", 0)})
        for k, v in c.items():
            if k.startswith("screen:"):
                screens[k[7:]] = screens.get(k[7:], 0) + v
            elif k.startswith(("tap:", "change:", "server:", "error:")):
                taps[k] = taps.get(k, 0) + v
    opsindex.ensure()
    today = dt.datetime.now(dt.timezone.utc).date()
    week = (today - dt.timedelta(days=6), today)
    act = reports.report("active", *week)
    active_7 = (act["totals"].get("wau") if act["source"] == "rollup"
                else opsindex.active_since(time.time() - 7 * 86400))
    signups = reports.report("signups", *week)["totals"]
    return {
        "range": {"from": span[0], "to": span[-1], "days": days},
        "kpis": {"users_total": opsindex.users_total(), "active_today": series[-1]["active"],
                 "active_7d": active_7,
                 "new_7d": ((signups or {}).get("verified") or {}).get("new"),
                 "events_today": series[-1]["events"]},
        "series": series,
        "funnel": funnel.report(days),
        "top_screens": sorted(({"name": k, "count": v} for k, v in screens.items()),
                              key=lambda x: -x["count"])[:12],
        "top_actions": sorted(({"name": k.split(":", 1)[1], "kind": k.split(":", 1)[0],
                                "count": v} for k, v in taps.items()),
                              key=lambda x: -x["count"])[:15],
    }


STAGES = [("signed_up", "Signed up"), ("consented", "Agreed"), ("onboarded", "Built chart"),
          ("first_message", "Chatted"), ("returned_day2", "Came back"), ("paid", "Paid")]


def _stage(uid, acct, prof):
    """The furthest step she's reached, from the funnel's own once-only record."""
    from engine import funnel
    seen = funnel.seen(uid)
    if acct.get("consented_at"):
        seen.add("consented")
    if prof:
        seen.add("onboarded")
    stage = "signed_up"
    for s, _ in STAGES:
        if s in seen:
            stage = s
    return stage


def _lookup(q):
    """An exact person for a full address, number or id — or None."""
    from app import auth
    q = q.strip()
    try:
        if "@" in q:
            return auth.uid_for(q, create=False)
        if re.fullmatch(r"\+?[\d\s().-]{7,20}", q):
            return auth.uid_for_id("phone", q, create=False)
    except Exception:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{4,64}", q) and load(q, "account", None):
        return q
    return None


def _is_team(email):
    """Ours or a test account: MYSIGN_TEAM_MARKERS, comma-separated parts of an address."""
    marks = [m.strip().lower() for m in os.environ.get("MYSIGN_TEAM_MARKERS", "").split(",")]
    email = (email or "").lower()
    return any(m and m in email for m in marks)


def _row(uid, acct, span):
    from engine import plan
    prof = load(uid, "profile", None) or {}
    return {
        "uid": uid, "email": mask_email(acct.get("email")),
        "created_at": acct.get("created_at"), "last_seen": acct.get("last_seen"),
        "country": prof.get("country"), "onboarded": bool(prof),
        "team": _is_team(acct.get("email")), "tz": acct.get("tz"),
        "plan": plan.get(uid)["tier"],
        "stage": _stage(uid, acct, prof),
        "active_days": len(span & set(store.keys(uid))),
        "people": len(load(uid, "connections", []) or []),
        "analytics_off": bool(acct.get("analytics_off")),
    }


PAGE = 50
MAX_PAGE = 200


def users(days=14, q=None, limit=PAGE, cursor=None, stage=None):
    """
    One page of people, most recently seen first, from the ops index
    (app/opsindex.py): `limit` rows and a `next_cursor` to ask for the next.
    `q` is an exact address, number or id when it is one; otherwise a
    substring of the id or masked address, looked for a bounded number of
    rows at a time. `stage` keeps only people at that step.
    """
    from app import opsindex
    opsindex.ensure()
    limit = max(1, min(int(limit or PAGE), MAX_PAGE))
    span = {f"act-{d}" for d in _days(days)}
    exact = _lookup(q) if q else None
    if stage and stage not in dict(STAGES):
        stage = None

    def at_stage(uid, acct):
        return _stage(uid, acct, load(uid, "profile", None) or {}) == stage

    if exact:
        acct = load(exact, "account", None)
        pairs = [(exact, acct)] if acct and (not stage or at_stage(exact, acct)) else []
        nxt = None
    else:
        ql = (q or "").lower()

        def accept(uid, acct):
            if ql and ql not in (uid + " " + (mask_email(acct.get("email")) or "")).lower():
                return False
            return not stage or at_stage(uid, acct)
        pairs, nxt = opsindex.page(cursor, limit, accept=accept if (ql or stage) else None)
    rows = opsindex.pmap(lambda p: _row(p[0], p[1], span), pairs)
    return {"users": rows, "count": len(rows), "total": opsindex.users_total(),
            "next_cursor": nxt, "exact": bool(exact),
            "stages": [s for s, _ in STAGES], "stage_labels": dict(STAGES),
            "index": {"done": opsindex.status().get("done", False)}}


SESSION_GAP_S = 30 * 60


def journey(uid, days=14):
    """Her events, grouped into sessions, newest first."""
    acct = load(uid, "account", {}) or {}
    prof = load(uid, "profile", None) or {}
    evs = []
    for d in _days(days):
        evs += load(uid, f"act-{d}", []) or []
    evs.sort(key=lambda e: e["at"])
    sessions, cur = [], None
    for e in evs:
        if e["ty"] == "server":
            # Server events join the session they happened in, if any.
            if cur and e["at"] - cur["end"] <= SESSION_GAP_S:
                cur["events"].append(e)
                cur["end"] = max(cur["end"], e["at"])
            else:
                sessions.append({"id": "server", "start": e["at"], "end": e["at"], "events": [e]})
            continue
        if not cur or e.get("s") != cur["id"] or e["at"] - cur["end"] > SESSION_GAP_S:
            cur = {"id": e.get("s") or "unknown", "start": e["at"], "end": e["at"], "events": []}
            sessions.append(cur)
        cur["events"].append(e)
        cur["end"] = e["at"]
    shaped = []
    screens, taps = {}, {}
    for s in sessions:
        path = []
        for e in s["events"]:
            if e["ty"] == "screen" and (not path or path[-1] != e["n"]):
                path.append(e["n"])
            if e["ty"] == "screen":
                screens[e["n"]] = screens.get(e["n"], 0) + 1
            elif e["ty"] in ("tap", "change", "server"):
                taps[e["n"]] = taps.get(e["n"], 0) + 1
        shaped.append({
            "id": s["id"], "start": s["start"], "end": s["end"],
            "duration_s": round(s["end"] - s["start"]),
            "path": path,
            "events": [{"at": e["at"], "type": e["ty"], "name": e["n"],
                        "screen": e.get("sc"), "props": e.get("p") or {}} for e in s["events"]],
        })
    shaped.reverse()
    return {
        "user": {"uid": uid, "email": mask_email(acct.get("email")),
                 "created_at": acct.get("created_at"), "last_seen": acct.get("last_seen"),
                 "country": prof.get("country"), "onboarded": bool(prof),
                 "stage": _stage(uid, acct, prof),
                 "people": len(load(uid, "connections", []) or []),
                 "analytics_off": bool(acct.get("analytics_off"))},
        "summary": {"sessions": len(shaped), "events": len(evs),
                    "time_s": sum(s["duration_s"] for s in shaped),
                    "top_screens": sorted(screens.items(), key=lambda x: -x[1])[:8],
                    "top_actions": sorted(taps.items(), key=lambda x: -x[1])[:10]},
        "sessions": shaped,
        "stages": [k for k, _ in STAGES], "stage_labels": dict(STAGES),
        "days": days,
    }
