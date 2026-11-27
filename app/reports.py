"""
The operator reports: what the rollups (deploy/rollups) computed, read back.

Contract — what the rollup Lambda writes and this module reads (one item per
report per UTC day, in engine/store.py's item shape):

    pk  "_report"
    sk  "<report>#<YYYY-MM-DD>"            e.g. "visits#2026-10-27"
    v   JSON {"data": {...}, "generated_at": <epoch s>, "final": <bool>, "version": 1}
    generated_at (N), final (BOOL)         copies, so a partial hourly write can
                                           be conditioned never to replace a final one
    no ttl                                 aggregates carry no ids; kept for good

`data` is the report's summary (shapes in docs/analytics.md, "Rollups"),
at most 350 KB: long segment lists keep their top rows and fold the tail into
a "(other)" row (`"truncated": n`), and the summary gets `"truncated": true`.
`final` is false for the hourly partials of today (and of yesterday until
03:00 UTC), true once the 02:30 UTC daily run has rewritten the day.

`get` reads one Query on the `_report` partition (sk between name#from and
name#to). `report` adds the totals over the range. When there are no rollups
in the range at all — locally, or before the analytics stack is deployed — it
answers from what the app already keeps in DynamoDB (the `_activity` day
counters and the funnel's day buckets): a few GetItems per day, never a scan
of people. Then `source` is "live", and what the counters can't say is null.
"""
import copy
import datetime as dt
import time

from engine import funnel, store

PK = "_report"
VERSION = 1
MAX_DAYS = 400          # longest range the API serves
LIVE_DAYS = 90          # the counters expire after 90 days anyway

# Order is display order. deploy/rollups/rollup_sql.py REPORTS must match (tested).
REPORTS = ("visits", "signups", "funnel", "active", "features", "time",
           "retention", "notifications", "monetization", "site", "usage")
LABELS = {
    "visits": "Site visits", "signups": "Sign-ups", "funnel": "Funnel",
    "active": "Active people", "features": "Features", "time": "Time in app",
    "retention": "Retention", "notifications": "Notifications",
    "monetization": "Monetization", "site": "Landing page", "usage": "Activity",
}
# What the live fallback can say at all.
# What the DynamoDB counters can stand in for. Not notifications: push_sent is
# lake only and push_opened is an `open` event, which the counters skip.
LIVE = ("signups", "funnel", "active", "features", "monetization")

# (key, label) as in rollup_sql.FUNNEL and FEATURES (tested to match).
FUNNEL_STEPS = (
    ("visit", "Site visit"), ("start_open", "Opened the start sheet"),
    ("code_requested", "Asked for a code"), ("verified", "Verified a code"),
    ("consented", "Agreed to the terms"), ("onboarded", "Built a chart"),
    ("first_message", "Sent a first message"), ("returned_day2", "Came back on day 2"),
    ("week_one", "Active in week one"), ("paywall_shown", "Saw the paywall"),
    ("paid", "Subscribed"),
)
FEATURES = (
    ("checkin", "Check-in"), ("chat", "Chat"), ("reading", "Chart reading"),
    ("people", "People added"), ("memory", "Memory"), ("sky", "Sky events"),
    ("invites", "Invites"), ("push", "Push opened"),
)

OTHER = "(other)"
_IDENT = ("cohort", "key", "step", "feature", "screen", "hour")


class UnknownReport(KeyError):
    pass


def _check(name):
    if name not in REPORTS:
        raise UnknownReport(name)


def _date(d):
    return d if isinstance(d, dt.date) else dt.date.fromisoformat(d)


def _span(start, end):
    a, b = _date(start), _date(end)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def sk(name, day):
    return f"{name}#{_date(day).isoformat()}"


def available():
    """The report index: name, label, and whether the live fallback covers it."""
    return [{"name": n, "label": LABELS[n], "live": n in LIVE} for n in REPORTS]


# ---------------------------------------------------------------- rollups

def get(name, start, end):
    """
    The rollup's daily summaries in [start, end], oldest first, as
    {"date", "final", "generated_at", **data}. Days with no rollup are absent.
    """
    _check(name)
    out = []
    for key, item in store.load_range(PK, sk(name, start), sk(name, end)):
        if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
            continue
        day = key.split("#", 1)[1]
        out.append({"date": day, "final": bool(item.get("final")),
                    "generated_at": item.get("generated_at"), **item["data"]})
    return out


def put(name, day, data, final=True, now=None):
    """Write one summary the way the rollup Lambda does (tests, local seeding)."""
    _check(name)
    store.save(PK, sk(name, day), {"data": data, "generated_at": int(now or time.time()),
                                   "final": bool(final), "version": VERSION})


# ---------------------------------------------------------------- summing days

def _add(a, b):
    """Deep sum: numbers add, dicts merge, lists of rows merge by their key."""
    if a is None:
        return copy.deepcopy(b)
    if b is None:
        return a
    if isinstance(a, bool) or isinstance(b, bool):
        return a
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + b
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _add(a.get(k), v)
        return out
    if isinstance(a, list) and isinstance(b, list) and all(isinstance(r, dict) for r in a + b):
        idents = [f for f in _IDENT if any(f in r for r in a + b)]
        if not idents:
            return a
        merged = {tuple(r.get(f) for f in idents): dict(r) for r in a}
        for r in b:
            k = tuple(r.get(f) for f in idents)
            # The identifying fields name the row; an hour of 13 stays 13, not 26.
            merged[k] = ({**_add(merged[k], r), **dict(zip(idents, k))} if k in merged
                         else dict(r))
        return list(merged.values())
    return a


def _rate(a, b, nd=3):
    return round(a / b, nd) if a is not None and b else None


def _desc(rows, metric):
    return sorted(rows, key=lambda r: (r.get("key") == OTHER, -(r.get(metric) or 0)))


def _sum_days(days):
    total = None
    for d in days:
        total = _add(total, {k: v for k, v in d.items()
                             if k not in ("date", "final", "generated_at", "truncated")})
    return total or {}


def _visits(days):
    t = _sum_days(days)
    for dim, rows in (t.get("by") or {}).items():
        t["by"][dim] = _desc(rows, "visitors")
    return t


def _signups(days):
    t = _sum_days(days)
    if t:
        t["conversion"] = _rate((t.get("verified") or {}).get("new"), t.get("visitors"), 4)
        for dim, rows in ((t.get("attribution") or {}).get("by") or {}).items():
            t["attribution"]["by"][dim] = _desc(rows, "signups")
    return t


def _funnel(days):
    return _sum_days(days)


def _active(days):
    if not days:
        return {}
    dau = [d.get("dau") for d in days if d.get("dau") is not None]
    last = days[-1]
    avg = round(sum(dau) / len(dau), 1) if dau else None
    news = [d.get("new") for d in days if d.get("new") is not None]
    rets = [d.get("returning") for d in days if d.get("returning") is not None]
    return {"dau": avg, "dau_max": max(dau) if dau else None,
            "wau": last.get("wau"), "mau": last.get("mau"),
            "stickiness": _rate(avg, last.get("mau")),
            "new": sum(news) if news else None,
            "returning": sum(rets) if rets else None}


def _features(days):
    t = _sum_days(days)
    for r in t.get("features") or []:
        r["uses_per_user"] = _rate(r.get("uses"), r.get("users"), 2)
        r["share"] = _rate(r.get("users"), t.get("active_users"))
    return t


def _time(days):
    t = _sum_days(days)
    if not t:
        return t
    # Session-weighted mean of the daily medians: an approximation, said so.
    for f in ("median_session_s", "p75_session_s"):
        w = [(d.get(f), d.get("sessions") or 0) for d in days if d.get(f) is not None]
        n = sum(s for _, s in w)
        t[f] = round(sum(v * s for v, s in w) / n) if n else None
    t["approximate"] = ["median_session_s", "p75_session_s"]
    t["sessions_per_user"] = _rate(t.get("sessions"), t.get("users"), 2)
    t["seconds_per_user"] = _rate(t.get("seconds"), t.get("users"), 0)
    t["by_screen"] = sorted(t.get("by_screen") or [], key=lambda r: -(r.get("seconds") or 0))
    return t


def _retention(days):
    # Each day is a snapshot of every cohort as of that day: the latest wins.
    if not days:
        return {}
    last = days[-1]
    return {**{k: v for k, v in last.items() if k not in ("date", "final", "generated_at")},
            "as_of": last.get("as_of") or last["date"]}


def _notifications(days):
    t = _sum_days(days)
    if not t:
        return t
    rows = [t.get("totals") or {}] + (t.get("by_kind") or []) + (t.get("by_hour") or [])
    for r in rows:
        r["open_rate"] = _rate(r.get("opened"), r.get("sent"))
    t["by_kind"] = _desc(t.get("by_kind") or [], "sent")
    t["by_hour"] = sorted(t.get("by_hour") or [], key=lambda r: str(r.get("hour")).zfill(2))
    return t


def _monetization(days):
    t = _sum_days(days)
    if not t:
        return t
    for r in [t.get("totals") or {}] + (t.get("by_source") or []) + (t.get("by_tier") or []):
        r["conversion"] = _rate(r.get("started"), r.get("shown_users"))
    return t


def _site(days):
    t = _sum_days(days)
    if not t:
        return t
    # Distinct countries don't add up over days; the busiest day's do for a range.
    t["totals"]["countries"] = max((d.get("totals") or {}).get("countries") or 0 for d in days)
    for name, metric in (("channels", "pageviews"), ("sections", "uses"), ("taps", "uses")):
        t[name] = _desc(t.get(name) or [], metric)
    for dim, rows in (t.get("by") or {}).items():
        t["by"][dim] = _desc(rows, "pageviews")
    return t


def _usage(days):
    t = _sum_days(days)
    if not t:
        return t
    for name in ("screens", "taps", "changes", "server"):
        t[name] = _desc(t.get(name) or [], "uses")
    t["by_hour"] = sorted(t.get("by_hour") or [], key=lambda r: r.get("hour", 0))
    return t


_SUMMARY = {"visits": _visits, "signups": _signups, "funnel": _funnel, "active": _active,
            "features": _features, "time": _time, "retention": _retention,
            "notifications": _notifications, "monetization": _monetization,
            "site": _site, "usage": _usage}


def summarize(name, days):
    """
    The range's totals from its days. Counts add up; distinct counts become
    person-days (a visitor on two days counts twice) — except `active`, whose
    wau/mau are the last day's and dau the mean; and `retention`, which is the
    latest snapshot. Rates are recomputed from the summed counts.
    """
    _check(name)
    return _SUMMARY[name](days)


def range_summary(name, start, end):
    return report(name, start, end)["totals"]


# ---------------------------------------------------------------- live fallback

def _counters(day):
    return store.counters("_activity", f"_act#{day}")


def _live_day(name, day):
    """One day of `name` from the DynamoDB counters, or None if there's nothing."""
    c = _counters(day)
    f = funnel._counts(_date(day))
    if not c and not f:
        return None

    def srv(n):
        return c.get(f"server:{n}")

    if name == "funnel":
        live = {"consented": f.get("consented"), "onboarded": f.get("onboarded"),
                "first_message": f.get("first_message"),
                "returned_day2": f.get("returned_day2"), "week_one": f.get("week_one"),
                # The funnel's `paid` bucket also counts operator hand grants.
                "paywall_shown": srv("paywall_shown"), "paid": None}
        return {"steps": [{"step": k, "label": label, "count": live.get(k)}
                          for k, label in FUNNEL_STEPS],
                "extra": {"signed_up": f.get("signed_up", 0),
                          "paywall_intent": f.get("paywall_intent", 0),
                          "funnel_paid": f.get("paid", 0)}}
    if name == "active":
        dau, new = c.get("people", 0), f.get("consented", 0)
        return {"dau": dau, "wau": None, "mau": None, "stickiness": None,
                "new": new, "returning": max(dau - new, 0)}
    if name == "features":
        uses = {
            "checkin": srv("checked_in") or 0,
            "chat": srv("message_sent") or 0,
            "reading": c.get("screen:chart", 0) + c.get("screen:place", 0),
            "people": srv("person_added") or 0,
            "memory": c.get("screen:memory", 0),
            "sky": sum(v for k, v in c.items()
                       if k.startswith(("tap:sky_", "tap:sky.", "screen:sky"))),
            "invites": f.get("invite_created", 0) + f.get("invite_accepted", 0),
            "push": None,
        }
        return {"active_users": c.get("people", 0),
                "features": [{"feature": k, "label": label, "users": None, "uses": uses[k],
                              "uses_per_user": None, "share": None} for k, label in FEATURES]}
    if name == "signups":
        return {"code_requested": {"total": None, "by_channel": {}},
                "verified": {"total": None, "new": f.get("signed_up", 0), "returning": None,
                             "by_channel": {}},
                "visitors": None, "conversion": None, "attribution": None}
    if name == "monetization":
        # The subscription_* counters can't tell billing from operator grants.
        t = {"shown": srv("paywall_shown") or 0, "shown_users": None, "started": None,
             "renewed": None, "canceled": None, "operator_grants": None,
             "conversion": None}
        return {"totals": t, "by_source": [], "by_tier": []}
    return None


def live(name, start, end):
    """The fallback's days: only the last LIVE_DAYS of the range, cheap reads only."""
    _check(name)
    if name not in LIVE:
        return []
    span = _span(start, end)[-LIVE_DAYS:]
    out = []
    for day in span:
        data = _live_day(name, day)
        if data is not None:
            out.append({"date": day, "final": False, "generated_at": None, **data})
    return out


# ---------------------------------------------------------------- the API's answer

def report(name, start, end):
    """
    {"source": "rollup"|"live", "days": [...], "totals": {...}} for [start, end].
    Rollups win whenever any day of the range has one; the two are never mixed.
    """
    _check(name)
    days = get(name, start, end)
    out = {"source": "rollup"}
    if not days:
        days = live(name, start, end)
        out = {"source": "live"}
        if name not in LIVE:
            out["unavailable"] = True     # needs the event lake: nothing to fall back on
        elif len(_span(start, end)) > LIVE_DAYS:
            out["clamped_to_days"] = LIVE_DAYS
    out.update(days=days, totals=summarize(name, days))
    return out


# ---------------------------------------------------------------- CSV

_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _cell(v):
    if v is None:
        return ""
    s = str(v)
    # A segment key is visitor-supplied (a utm tag): never let a sheet run it.
    return "'" + s if isinstance(v, str) and s.startswith(_FORMULA) else s


def _flatten(obj, path=()):
    """(section, key, metric, value) for every number in a summary."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                yield from _flatten(v, path + (k,))
            elif v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)):
                yield ".".join(path), "", k, v
    elif isinstance(obj, list):
        for r in obj:
            if not isinstance(r, dict):
                continue
            ids = [f for f in _IDENT if f in r]
            key = "/".join(str(r[f]) for f in ids)
            for f, v in r.items():
                if f in ids:
                    continue
                if isinstance(v, dict):
                    for sec, _, m, x in _flatten(v):
                        yield ".".join(path + (f,) + ((sec,) if sec else ())), key, m, x
                elif v is None or (isinstance(v, (int, float)) and not isinstance(v, bool)):
                    yield ".".join(path), key, f, v


def to_csv(resp):
    """Tidy CSV: date,section,key,metric,value — one row per number, days then totals."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["date", "section", "key", "metric", "value"])
    for d in resp.get("days") or []:
        data = {k: v for k, v in d.items() if k not in ("date", "final", "generated_at")}
        for sec, key, m, v in _flatten(data):
            w.writerow([d["date"], _cell(sec), _cell(key), _cell(m), _cell(v)])
    for sec, key, m, v in _flatten(resp.get("totals") or {}):
        w.writerow(["total", _cell(sec), _cell(key), _cell(m), _cell(v)])
    return buf.getvalue()
