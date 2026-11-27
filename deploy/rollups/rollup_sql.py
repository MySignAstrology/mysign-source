"""
The rollups' SQL: one builder per report, Athena (Trino) dialect.

Every query reads the live Parquet table `events` and the backfill's JSON
table `events_json` through one inline UNION ALL (`ev`), with the `dt` (and,
where it helps, `source`) predicate repeated inside each branch so partition
projection prunes both. No view and no INSERT INTO: the rollup needs nothing
but SELECT on the two tables (docs/analytics.md §6 flags INSERT INTO with
partition projection as unverified).

Rules every query here keeps, and tests/test_rollups.py checks by parsing:
  - the final SELECT list never carries `uid`, `anon_id`, `session_id` or
    `event_id` except inside an aggregate (count(DISTINCT …), count_if …).
  - dates are inlined only after `_day()` has parsed them, so a query can't
    be steered by its input.
  - `props` is read with element_at(): Trino's props['k'] fails on a missing
    key, element_at returns null.

Days are UTC (the `dt` partition). Report names here and in app/reports.py
REPORTS must match; a test holds them together.
"""
import datetime as dt
import re

# The order is the order /ops shows them in.
REPORTS = ("visits", "signups", "funnel", "active", "features", "time",
           "retention", "notifications", "monetization", "site", "usage")
# Recomputed by the daily run only: a snapshot of every cohort as of a day.
SNAPSHOT = ("retention",)
IDS = ("uid", "anon_id", "session_id", "event_id")

# Server events nobody did: they don't make a person "active". subscription_*
# come from billing webhooks or an operator's hand grant (plan.grant), not the app.
PASSIVE = ("push_sent", "subscription_started", "subscription_renewed",
           "subscription_canceled")
# plan.grant(source="operator"): a pilot's free Plus, never revenue.
OPERATOR = "operator"
_PAID = f"coalesce(element_at(props, 'source'), '') <> '{OPERATOR}'"
ATTRIBUTION_DAYS = 30        # how far back a signup's first site visit is looked for
ACTIVE_DAYS = 28             # MAU window
RETENTION_WEEKS = 12         # cohorts recomputed by each daily run (inside the 90-day raw window)
HEARTBEAT_S = 30             # the client's heartbeat interval while the app is visible

# Feature → which events count as a use. Event names are the ones the app
# and the server emit (app/server.py activity.track, engine/funnel.py, and the
# Track IIFE in app/static/index.html, whose tap names are slugged element ids:
# skyBanner → sky_banner).
FEATURES = (
    ("checkin", "Check-in", "type = 'server' AND name = 'checked_in'"),
    ("chat", "Chat", "type = 'server' AND name = 'message_sent'"),
    ("reading", "Chart reading", "type = 'screen' AND name IN ('chart', 'place')"),
    ("people", "People added", "type = 'server' AND name = 'person_added'"),
    ("memory", "Memory", "type = 'screen' AND name = 'memory'"),
    ("sky", "Sky events", "type IN ('tap', 'screen') AND regexp_like(name, '^sky[_.]')"),
    # funnel.* events carry no uid, so `users` is 0 for invites: uses only.
    ("invites", "Invites",
     "type = 'server' AND name IN ('funnel.invite_created', 'funnel.invite_accepted')"),
    ("push", "Push opened", "name = 'push_opened'"),
)

# Funnel steps, in order: (key, label, expression counted DISTINCT).
FUNNEL = (
    ("visit", "Site visit",
     "CASE WHEN source = 'site' AND type = 'page' THEN coalesce(session_id, event_id) END"),
    ("start_open", "Opened the start sheet",
     "CASE WHEN source = 'site' AND type = 'tap' AND name IN ('start_open', 'signin_open') "
     "THEN coalesce(anon_id, session_id, event_id) END"),
    ("code_requested", "Asked for a code",
     "CASE WHEN type = 'server' AND name = 'auth.code_requested' "
     "THEN coalesce(anon_id, event_id) END"),
    ("verified", "Verified a code",
     "CASE WHEN type = 'server' AND name = 'auth.verified' "
     "THEN coalesce(anon_id, uid, event_id) END"),
    ("consented", "Agreed to the terms",
     "CASE WHEN name = 'funnel.consented' THEN event_id END"),
    ("onboarded", "Built a chart", "CASE WHEN name = 'funnel.onboarded' THEN event_id END"),
    ("first_message", "Sent a first message",
     "CASE WHEN name = 'funnel.first_message' THEN event_id END"),
    ("returned_day2", "Came back on day 2",
     "CASE WHEN name = 'funnel.returned_day2' THEN event_id END"),
    ("week_one", "Active in week one", "CASE WHEN name = 'funnel.week_one' THEN event_id END"),
    ("paywall_shown", "Saw the paywall",
     "CASE WHEN name = 'paywall_shown' THEN coalesce(uid, event_id) END"),
    ("paid", "Subscribed",
     f"CASE WHEN name = 'subscription_started' AND {_PAID} THEN coalesce(uid, event_id) END"),
)
# Shown beside the funnel, not as steps.
FUNNEL_EXTRA = (
    ("code_requested_sms",
     "CASE WHEN type = 'server' AND name = 'auth.code_requested' "
     "AND element_at(props, 'channel') = 'sms' THEN coalesce(anon_id, event_id) END"),
    ("code_requested_email",
     "CASE WHEN type = 'server' AND name = 'auth.code_requested' "
     "AND element_at(props, 'channel') = 'email' THEN coalesce(anon_id, event_id) END"),
    ("verified_new",
     "CASE WHEN type = 'server' AND name = 'auth.verified' "
     "AND element_at(props, 'new') = 'true' THEN coalesce(anon_id, uid, event_id) END"),
    ("signed_up", "CASE WHEN name = 'funnel.signed_up' THEN event_id END"),
    ("paywall_intent", "CASE WHEN name = 'funnel.paywall_intent' THEN event_id END"),
    # funnel.paid also counts operator hand grants (POST /api/ops/plan).
    ("funnel_paid", "CASE WHEN name = 'funnel.paid' THEN event_id END"),
)

# (key, first day after signup, last day): d1/d7/d30 are that exact day,
# wK is any day of the K-th week after the signup day.
RETENTION_POINTS = (("d1", 1, 1), ("d7", 7, 7), ("d30", 30, 30)) + tuple(
    (f"w{k}", 7 * k, 7 * k + 6) for k in range(1, 9))

USER_SOURCES = ("app", "native", "server", "backfill")
# Things that happened when they say they did: the backfill's funnel rows all
# sit at noon UTC, so hours of the day leave it out.
LIVE_SOURCES = ("site", "app", "native", "server")

# The landing page, in order: (key, label, predicate on a site event). People
# are devices (or sessions); a page view with neither (GPC/DNT) isn't a person.
SCROLLED = "name LIKE 'section_view:%' AND name <> 'section_view:hero'"
OPENED = "name IN ('start_open', 'signin_open')"
SITE_STEPS = (
    ("landed", "Landing page view", "name IN ('home', 'ig', 'check')"),
    ("hero", "Saw the hero", "name IN ('hero_view', 'section_view:hero')"),
    ("scrolled", "Scrolled past the hero", SCROLLED),
    ("opened", "Opened sign-up", OPENED),
    ("submitted", "Submitted sign-up", "name IN ('start_submit', 'email_submit')"),
    ("code_sent", "Code sent", "name = 'start_code_sent'"),
    ("verified", "Code verified", "name IN ('start_verified', 'code_verified')"),
)
# Where a site event came from, as (key, predicate); the first that fits wins.
CHANNELS = (
    ("paid", "ctx.utm_medium = 'paid'"),
    ("ig_probe", "regexp_like(ctx.utm_source, '^ig_probe')"),
    ("instagram", "ctx.utm_medium = 'social' OR ctx.referrer_domain LIKE '%instagram%'"),
    ("facebook", "ctx.referrer_domain LIKE '%facebook%'"),
    ("google", "ctx.referrer_domain LIKE '%google%'"),
)
DIRECT = "direct"
# How long a site session lasted: (key, from second, below second).
VISIT_LENGTHS = (("<2s", 0, 2), ("2-10s", 2, 10), ("10-60s", 10, 60), ("1-5m", 60, 300),
                 ("5m+", 300, None))
CLIENT_DIMS = ("os_family", "browser_family", "utm_medium")
NAMES = 200                  # event names are the visitor's to send: keep the top ones
_COLS = "event_id, ts, anon_id, uid, session_id, type, name, screen, props, ctx, dt, source"
_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


def _day(d):
    """A date, as the only kind of value ever inlined into SQL."""
    if isinstance(d, str):
        d = dt.date.fromisoformat(d)
    if not isinstance(d, dt.date):
        raise TypeError(d)
    return d


def _ident(name):
    if not _IDENT.match(name or ""):
        raise ValueError(f"bad identifier {name!r}")
    return f'"{name}"'


def _quote_list(xs):
    return ", ".join(f"'{x}'" for x in xs)


def _minus(d, n):
    return (_day(d) - dt.timedelta(days=n)).isoformat()


def window(report, day):
    """(first, last) `dt` a report reads for `day`, for cost estimates and tests."""
    d = _day(day).isoformat()
    return {"signups": (_minus(d, ATTRIBUTION_DAYS), d),
            "active": (_minus(d, ACTIVE_DAYS - 1), d),
            "retention": (_minus(d, RETENTION_WEEKS * 7 + 6), d)}.get(report, (d, d))


def _events(db, lo, hi, sources=None):
    """`ev`: both tables, one shape, pruned by day (and source) in each branch."""
    lo, hi = _day(lo).isoformat(), _day(hi).isoformat()
    cond = f"dt BETWEEN '{lo}' AND '{hi}'" if lo != hi else f"dt = '{lo}'"
    if sources:
        cond += f" AND source IN ({_quote_list(sources)})"
    return (f"ev AS (\n"
            f"  SELECT {_COLS} FROM {_ident(db)}.\"events\" WHERE {cond}\n"
            f"  UNION ALL\n"
            f"  SELECT {_COLS} FROM {_ident(db)}.\"events_json\" WHERE {cond}\n)")


def _grouping(dims):
    which = "\n    ".join(f"WHEN grouping({c}) = 0 THEN '{c}'" for c in dims)
    sets = ", ".join(["()"] + [f"({c})" for c in dims])
    return which, sets


# ---------------------------------------------------------------- reports

VISIT_DIMS = ("utm_source", "utm_campaign", "referrer_domain", "landing_path", "country",
              "device_class")
ATTRIBUTION_DIMS = ("utm_source", "referrer_domain", "landing_path")


def visits(db, day):
    d = _day(day).isoformat()
    firsts = ",\n    ".join(f"min_by(ctx.{c}, ts) AS {c}" for c in VISIT_DIMS)
    which, sets = _grouping(VISIT_DIMS)
    return f"""WITH {_events(db, d, d, ("site",))},
s AS (
  -- A visit is a session; with GPC/DNT there is no session id, so each page
  -- view stands alone. Its dimensions are those of its first event.
  SELECT coalesce(session_id, event_id) AS sid,
    max(anon_id) AS anon_id,
    count_if(type = 'page') AS pageviews,
    {firsts}
  FROM ev
  WHERE session_id IS NOT NULL OR type = 'page'
  GROUP BY 1
)
SELECT
  CASE
    {which}
    ELSE 'total' END AS dim,
  coalesce({", ".join(VISIT_DIMS)}) AS key,
  count(*) AS visits,
  count(DISTINCT anon_id) AS visitors,
  sum(pageviews) AS pageviews,
  count_if(anon_id IS NULL) AS private_visits
FROM s
GROUP BY GROUPING SETS ({sets})"""


def signups_counts(db, day):
    d = _day(day).isoformat()
    return f"""WITH {_events(db, d, d, ("server", "site", "backfill"))}
SELECT name AS metric,
  coalesce(element_at(props, 'channel'), 'unknown') AS channel,
  coalesce(element_at(props, 'new'), '') AS is_new,
  count(DISTINCT coalesce(anon_id, uid, event_id)) AS people,
  count(DISTINCT event_id) AS events
FROM ev
WHERE type = 'server' AND name IN ('auth.code_requested', 'auth.verified')
GROUP BY 1, 2, 3
UNION ALL
SELECT 'site.visitors', '', '', count(DISTINCT anon_id), count(DISTINCT event_id)
FROM ev WHERE source = 'site'"""


def signups_attribution(db, day):
    d = _day(day).isoformat()
    lo = _minus(d, ATTRIBUTION_DAYS)
    firsts = ",\n    ".join(f"min_by(ctx.{c}, ts) AS {c}" for c in ATTRIBUTION_DIMS)
    which, sets = _grouping(ATTRIBUTION_DIMS)
    return f"""WITH {_events(db, lo, d, ("site", "app", "native", "server", "backfill"))},
consented AS (
  SELECT DISTINCT uid FROM ev
  WHERE dt = '{d}' AND type = 'server' AND name = 'consented' AND uid IS NOT NULL
),
signup AS (
  -- The devices of the day's new accounts: a new-account verification that
  -- carried the device id, or any event of a person who agreed today.
  SELECT DISTINCT anon_id FROM ev
  WHERE dt = '{d}' AND anon_id IS NOT NULL AND (
    (type = 'server' AND name = 'auth.verified' AND element_at(props, 'new') = 'true')
    OR uid IN (SELECT uid FROM consented))
),
touch AS (
  -- First touch: the earliest site event of that device in the window.
  SELECT anon_id,
    {firsts}
  FROM ev
  WHERE source = 'site' AND anon_id IN (SELECT anon_id FROM signup)
  GROUP BY anon_id
),
j AS (
  SELECT s.anon_id, t.anon_id IS NOT NULL AS touched,
    {", ".join(f"t.{c}" for c in ATTRIBUTION_DIMS)}
  FROM signup s LEFT JOIN touch t ON s.anon_id = t.anon_id
)
SELECT
  CASE
    {which}
    ELSE 'total' END AS dim,
  coalesce({", ".join(ATTRIBUTION_DIMS)}) AS key,
  count(DISTINCT anon_id) AS signups,
  count(DISTINCT CASE WHEN touched THEN anon_id END) AS from_site
FROM j
GROUP BY GROUPING SETS ({sets})"""


def funnel(db, day):
    d = _day(day).isoformat()
    cols = ",\n  ".join(f"count(DISTINCT {expr}) AS {k}" for k, _, expr in FUNNEL)
    extra = ",\n  ".join(f"count(DISTINCT {expr}) AS {k}" for k, expr in FUNNEL_EXTRA)
    return f"""WITH {_events(db, d, d, ("site", "server", "backfill"))}
SELECT
  {cols},
  {extra}
FROM ev"""


def active(db, day):
    d = _day(day).isoformat()
    wk, lo = _minus(d, 6), _minus(d, ACTIVE_DAYS - 1)
    return f"""WITH {_events(db, lo, d, USER_SOURCES)}
SELECT
  count(DISTINCT CASE WHEN dt = '{d}' THEN uid END) AS dau,
  count(DISTINCT CASE WHEN dt >= '{wk}' THEN uid END) AS wau,
  count(DISTINCT uid) AS mau,
  count(DISTINCT CASE WHEN dt = '{d}' AND name = 'consented' THEN uid END) AS new_users
FROM ev
WHERE uid IS NOT NULL AND name NOT IN ({_quote_list(PASSIVE)})"""


def features(db, day):
    d = _day(day).isoformat()
    cases = "\n      ".join(f"WHEN {pred} THEN '{k}'" for k, _, pred in FEATURES)
    return f"""WITH {_events(db, d, d, USER_SOURCES)},
f AS (
  SELECT
    CASE
      {cases}
    END AS feature,
    uid, event_id
  FROM ev
)
SELECT feature, count(DISTINCT uid) AS users, count(DISTINCT event_id) AS uses
FROM f WHERE feature IS NOT NULL GROUP BY 1
UNION ALL
SELECT '_active', count(DISTINCT uid), 0
FROM ev WHERE uid IS NOT NULL AND name NOT IN ({_quote_list(PASSIVE)})"""


def time_sessions(db, day):
    d = _day(day).isoformat()
    return f"""WITH {_events(db, d, d, ("app", "native"))},
s AS (
  SELECT session_id,
    max(uid) AS uid,
    count_if(type = 'heartbeat') AS beats,
    (max(ts) - min(ts)) / 1000.0 AS span_s
  FROM ev WHERE session_id IS NOT NULL
  GROUP BY session_id
),
l AS (
  -- Each heartbeat is {HEARTBEAT_S} s in the foreground, bounded by the
  -- session's own first and last event (open … hide) plus one interval.
  SELECT uid,
    CASE WHEN beats > 0 THEN least(beats * {HEARTBEAT_S}, span_s + {HEARTBEAT_S})
         ELSE span_s END AS secs
  FROM s
)
SELECT
  count(*) AS sessions,
  count(uid) AS signed_in_sessions,
  count(DISTINCT uid) AS users,
  coalesce(sum(secs), 0) AS seconds,
  coalesce(sum(CASE WHEN uid IS NOT NULL THEN secs END), 0) AS signed_in_seconds,
  approx_percentile(secs, 0.5) AS median_s,
  approx_percentile(secs, 0.75) AS p75_s
FROM l"""


def time_screens(db, day):
    d = _day(day).isoformat()
    return f"""WITH {_events(db, d, d, ("app", "native"))}
SELECT coalesce(screen, '(none)') AS screen,
  count(DISTINCT event_id) * {HEARTBEAT_S} AS seconds,
  count(DISTINCT uid) AS users
FROM ev WHERE type = 'heartbeat'
GROUP BY 1"""


def retention(db, day):
    d = _day(day).isoformat()
    lo = window("retention", d)[0]
    flags = ",\n    ".join(
        f"max(CASE WHEN date_diff('day', su.d0, a.d) BETWEEN {a} AND {b} THEN 1 ELSE 0 END) AS {k}"
        for k, a, b in RETENTION_POINTS)
    # Both sides of a rate count only people whose point has fully passed by
    # `day`, so a cohort still maturing never shows more than 100%.
    points = ",\n  ".join(
        f"count_if({k} = 1 AND date_add('day', {b}, d0) <= DATE '{d}') AS {k}_n, "
        f"count_if(date_add('day', {b}, d0) <= DATE '{d}') AS {k}_base"
        for k, _, b in RETENTION_POINTS)
    return f"""WITH {_events(db, lo, d)},
su AS (
  -- A signup is someone's first `consented` in the window.
  SELECT uid, min(date(dt)) AS d0 FROM ev
  WHERE type = 'server' AND name = 'consented' AND uid IS NOT NULL
  GROUP BY uid
),
dev AS (
  SELECT DISTINCT uid, anon_id FROM ev
  WHERE uid IS NOT NULL AND anon_id IS NOT NULL AND uid IN (SELECT uid FROM su)
),
ch AS (
  SELECT dev.uid, max_by(element_at(e.props, 'channel'), e.ts) AS channel
  FROM dev JOIN ev e ON e.anon_id = dev.anon_id
  WHERE e.type = 'server' AND e.name = 'auth.code_requested'
  GROUP BY dev.uid
),
ut AS (
  SELECT dev.uid, min_by(e.ctx.utm_source, e.ts) AS utm_source
  FROM dev JOIN ev e ON e.anon_id = dev.anon_id
  WHERE e.source = 'site'
  GROUP BY dev.uid
),
act AS (
  SELECT DISTINCT uid, date(dt) AS d FROM ev
  WHERE uid IS NOT NULL AND source <> 'site' AND name NOT IN ({_quote_list(PASSIVE)})
),
c AS (
  SELECT su.uid, su.d0, cast(date_trunc('week', su.d0) AS date) AS wk,
    coalesce(ch.channel, 'unknown') AS channel,
    coalesce(ut.utm_source, '(none)') AS utm_source,
    {flags}
  FROM su
  LEFT JOIN ch ON ch.uid = su.uid
  LEFT JOIN ut ON ut.uid = su.uid
  LEFT JOIN act a ON a.uid = su.uid
  GROUP BY su.uid, su.d0, ch.channel, ut.utm_source
)
SELECT
  CASE WHEN grouping(channel) = 0 THEN 'channel'
       WHEN grouping(utm_source) = 0 THEN 'utm_source'
       ELSE 'all' END AS dim,
  coalesce(channel, utm_source) AS key,
  cast(wk AS varchar) AS cohort,
  count(*) AS users,
  {points}
FROM c
GROUP BY GROUPING SETS ((wk), (wk, channel), (wk, utm_source))"""


def notifications(db, day):
    d = _day(day).isoformat()
    return f"""WITH {_events(db, d, d, USER_SOURCES)},
ci AS (
  SELECT uid, max(ts) AS last_ts FROM ev
  WHERE name = 'checked_in' AND uid IS NOT NULL GROUP BY uid
),
x AS (
  SELECT 'sent' AS stage, element_at(props, 'kind') AS kind,
    hour(from_unixtime(ts / 1000)) AS hr, uid, event_id, false AS checked_in
  FROM ev WHERE name = 'push_sent'
  UNION ALL
  SELECT 'opened', element_at(e.props, 'kind'), hour(from_unixtime(e.ts / 1000)),
    e.uid, e.event_id, coalesce(ci.last_ts >= e.ts, false)
  FROM ev e LEFT JOIN ci ON ci.uid = e.uid
  WHERE e.name = 'push_opened'
)
SELECT
  CASE WHEN grouping(kind) = 0 THEN 'kind'
       WHEN grouping(hr) = 0 THEN 'hour'
       ELSE 'total' END AS dim,
  coalesce(kind, cast(hr AS varchar)) AS key,
  count(DISTINCT CASE WHEN stage = 'sent' THEN event_id END) AS sent,
  count(DISTINCT CASE WHEN stage = 'opened' THEN event_id END) AS opened,
  count(DISTINCT CASE WHEN stage = 'sent' THEN uid END) AS users_sent,
  count(DISTINCT CASE WHEN stage = 'opened' THEN uid END) AS users_opened,
  count(DISTINCT CASE WHEN stage = 'opened' AND checked_in THEN uid END) AS checked_in
FROM x
GROUP BY GROUPING SETS ((), (kind), (hr))"""


def monetization(db, day):
    d = _day(day).isoformat()
    return f"""WITH {_events(db, d, d, USER_SOURCES)},
x AS (
  SELECT name, element_at(props, 'source') AS src, element_at(props, 'tier') AS tier,
    {_PAID} AS paid, uid, event_id
  FROM ev
  WHERE name IN ('paywall_shown', 'subscription_started', 'subscription_renewed',
                 'subscription_canceled')
)
SELECT
  CASE WHEN grouping(src) = 0 THEN 'source'
       WHEN grouping(tier) = 0 THEN 'tier'
       ELSE 'total' END AS dim,
  coalesce(src, tier) AS key,
  count(DISTINCT CASE WHEN name = 'paywall_shown' THEN event_id END) AS shown,
  count(DISTINCT CASE WHEN name = 'paywall_shown' THEN coalesce(uid, event_id) END) AS shown_users,
  count(DISTINCT CASE WHEN name = 'subscription_started' AND paid
                      THEN coalesce(uid, event_id) END) AS started,
  count(DISTINCT CASE WHEN name = 'subscription_renewed' AND paid THEN event_id END) AS renewed,
  count(DISTINCT CASE WHEN name = 'subscription_canceled' AND paid THEN event_id END) AS canceled,
  count(DISTINCT CASE WHEN name = 'subscription_started' AND NOT paid
                      THEN coalesce(uid, event_id) END) AS operator_grants
FROM x
GROUP BY GROUPING SETS ((), (src), (tier))"""


def site_steps(db, day):
    d = _day(day).isoformat()
    cols = ",\n  ".join(f"count(DISTINCT CASE WHEN {pred} THEN coalesce(anon_id, session_id) END) "
                        f"AS {k}" for k, _, pred in SITE_STEPS)
    return f"""WITH {_events(db, d, d, ("site",))}
SELECT
  {cols}
FROM ev"""


def site_channels(db, day):
    d = _day(day).isoformat()
    cases = "\n      ".join(f"WHEN {pred} THEN '{k}'" for k, pred in CHANNELS)
    return f"""WITH {_events(db, d, d, ("site",))},
c AS (
  SELECT
    CASE
      {cases}
      ELSE '{DIRECT}' END AS channel,
    -- A private visitor has no id: each of their actions counts on its own,
    -- rather than a channel of private browsers looking like nobody acted.
    type, name, event_id, coalesce(anon_id, session_id, event_id) AS who
  FROM ev
)
SELECT channel AS key,
  count(DISTINCT CASE WHEN type = 'page' THEN event_id END) AS pageviews,
  count(DISTINCT event_id) AS events,
  count(DISTINCT CASE WHEN {SCROLLED} THEN who END) AS scrolled,
  count(DISTINCT CASE WHEN {OPENED} THEN who END) AS opened
FROM c
GROUP BY 1"""


def site_names(db, day):
    d = _day(day).isoformat()
    return f"""WITH {_events(db, d, d, ("site",))},
n AS (
  SELECT
    CASE WHEN name LIKE 'section_view:%' THEN 'section' ELSE 'tap' END AS dim,
    CASE WHEN name LIKE 'section_view:%' THEN substr(name, 14) ELSE name END AS key,
    event_id, coalesce(anon_id, session_id) AS who
  FROM ev WHERE type = 'tap' AND name <> 'hero_view'
)
SELECT dim, key, count(DISTINCT event_id) AS uses, count(DISTINCT who) AS people
FROM n
GROUP BY dim, key
ORDER BY uses DESC, key
LIMIT {NAMES}"""


def site_lengths(db, day):
    d = _day(day).isoformat()
    whens = "\n    ".join(f"WHEN secs < {hi} THEN '{k}'" for k, _, hi in VISIT_LENGTHS if hi)
    last = VISIT_LENGTHS[-1][0]
    return f"""WITH {_events(db, d, d, ("site",))},
s AS (
  SELECT session_id, (max(ts) - min(ts)) / 1000.0 AS secs
  FROM ev WHERE session_id IS NOT NULL
  GROUP BY session_id
),
b AS (
  SELECT CASE
    {whens}
    ELSE '{last}' END AS length
  FROM s
)
SELECT length AS key, count(*) AS sessions
FROM b
GROUP BY 1"""


def site_clients(db, day):
    d = _day(day).isoformat()
    cols = ", ".join(f"coalesce(ctx.{c}, '(none)') AS {c}" for c in CLIENT_DIMS)
    which, sets = _grouping(CLIENT_DIMS)
    return f"""WITH {_events(db, d, d, ("site",))},
p AS (
  SELECT {cols}, ctx.country AS country, anon_id, event_id
  FROM ev WHERE type = 'page'
)
SELECT
  CASE
    {which}
    ELSE 'total' END AS dim,
  coalesce({", ".join(CLIENT_DIMS)}) AS key,
  count(DISTINCT event_id) AS pageviews,
  count(DISTINCT anon_id) AS visitors,
  count(DISTINCT country) AS countries
FROM p
GROUP BY GROUPING SETS ({sets})"""


def usage_names(db, day):
    d = _day(day).isoformat()
    return f"""WITH {_events(db, d, d, USER_SOURCES)}
SELECT type AS dim, name AS key,
  count(DISTINCT event_id) AS uses, count(DISTINCT uid) AS users
FROM ev WHERE type IN ('screen', 'tap', 'change', 'server')
GROUP BY type, name
ORDER BY uses DESC, name
LIMIT {NAMES}"""


def usage_hours(db, day):
    d = _day(day).isoformat()
    return f"""WITH {_events(db, d, d, LIVE_SOURCES)}
SELECT hour(from_unixtime(ts / 1000)) AS hour,
  count(DISTINCT event_id) AS events,
  count(DISTINCT CASE WHEN source = 'site' AND type = 'page' THEN event_id END) AS pageviews
FROM ev
GROUP BY 1"""


def usage_sources(db, day):
    d = _day(day).isoformat()
    return f"""WITH {_events(db, d, d, LIVE_SOURCES)}
SELECT source AS key,
  count(DISTINCT event_id) AS events,
  count(DISTINCT CASE WHEN type = 'open' AND name <> 'push_opened' THEN event_id END) AS opens
FROM ev
GROUP BY source"""


# report → {query name: builder(db, day)}
QUERIES = {
    "visits": {"main": visits},
    "signups": {"counts": signups_counts, "attribution": signups_attribution},
    "funnel": {"main": funnel},
    "active": {"main": active},
    "features": {"main": features},
    "time": {"sessions": time_sessions, "screens": time_screens},
    "retention": {"main": retention},
    "notifications": {"main": notifications},
    "monetization": {"main": monetization},
    "site": {"steps": site_steps, "channels": site_channels, "names": site_names,
             "lengths": site_lengths, "clients": site_clients},
    "usage": {"names": usage_names, "hours": usage_hours, "sources": usage_sources},
}


def queries(report, db, day):
    """{query name: SQL} for one report and one day."""
    if report not in QUERIES:
        raise KeyError(report)
    return {q: build(db, day) for q, build in QUERIES[report].items()}
