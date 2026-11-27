# Analytics pipeline

How usage events get from a screen to an Athena query, and what they may
carry on the way. The rules themselves are code, in one place:
`app/events.py`. This doc explains them; if the two disagree, the code is
right and this doc is the bug.

Companion docs: `docs/data-inventory.md` §1a (what's stored), `docs/retention.md`,
`/privacy` (the user-facing version).

---

## 1. Shape

```
 app (signed in) ──POST /api/activity──► app Lambda ──► DynamoDB act-* (90 d, /ops reads these)
                                              └──PutRecordBatch──┐
 website / pre-sign-in ──POST /e (CloudFront)──► collector Lambda ┤
                                                                  ▼
                                            Firehose mysign-events (Direct PUT)
                                              JSON → Parquet (Glue schema),
                                              partitioned dt= / source= by JQ
                                                                  ▼
                       S3 lake  raw/events/dt=YYYY-MM-DD/source=<source>/   (90 d)
                                agg/                                         (no ids, kept)
                                                                  ▼
                       Glue mysign_analytics.events (partition projection) ◄── Athena workgroup mysign
```

- **Signed-in events** keep going through `/api/activity`, where the server
  knows the uid. `activity.record()` stores them in DynamoDB as before and
  forwards the same cleaned events to Firehose (`app/forward.py`). This is a dual
  write: the DynamoDB path stays so `/ops` keeps working until the rollups read
  Athena. Page and heartbeat events go to the lake only.
- **Anonymous events** (website visitors, app screens before sign-in) go to
  the collector, a separate Lambda (`deploy/collect/handler.py`) behind its own
  Function URL, mounted at CloudFront `/e`. It **never accepts a uid**: if a
  body contains one, it is ignored.
- **Server events** come from `activity.track()` (with the uid, after consent),
  `funnel.hit()` (`funnel.<step>`, no uid) and `activity.track_auth()`
  (`auth.verified`; uid and anon_id only once she has consented, otherwise a
  bare count). Two lake-only variants write nothing to DynamoDB:
  `activity.track_anon()` (no uid at all, for `auth.code_requested`) and
  `activity.track_lake()` (with the uid, for `push_sent`, which the cron emits
  for everyone at once). The full list is §2a.

Forwarding is cheap and can't hurt a request:
- one lazily created boto3 client per container
- one `PutRecordBatch` per request
- a 1 s connect / 2 s read timeout
- refused records retried once

A failure is logged as `activity.forward_failed`, together with the EMF metric
`EventsForwardFailed`. It is logged at `warn`, not `error`, so a lost analytics
batch doesn't page anyone through `AppErrorLines`.

## 2. The envelope

One JSON object per event (`events.envelope`), one row in `events`.

| Field | Type | What | Rule |
|---|---|---|---|
| `event_id` | string | uuid4, made server-side | dedupe key |
| `ts` | bigint | when it happened, ms since epoch (the client's clock) | dropped if over a day old or 5 min in the future |
| `received_at` | bigint | when we got it, ms | server clock |
| `source` | partition | `site` \| `app` \| `server` \| `native` \| `backfill` | the collector accepts only site/app/native |
| `app_version` | string | build tag | `VERSION_RE`, else null |
| `anon_id` | string | random first-party id from browser storage | `ANON_RE` (16–40 url-safe chars), else null. **Never PII.** Null under GPC/DNT |
| `uid` | string | pseudonymous account id | app path only, only when signed in and analytics isn't off. Never from the collector |
| `session_id` | string | client session | `SESSION_RE`, else null. Null under GPC/DNT |
| `type` | string | `page` `screen` `tap` `change` `open` `hide` `heartbeat` `server` `error` | `TYPES`; clients can't send `server` |
| `name` | string | screen or control id, e.g. `nav_people` | `NAME_RE`: lower-case id, ≤60 chars. Not free text |
| `screen` | string | screen it happened on | `NAME_RE` |
| `props` | map<string,string> | a few enumerable values | keys from `PROPS` only, with per-key length caps; values must match `VALUE_RE` (no spaces, no `@`) or are dropped; booleans become `"true"`/`"false"` |
| `ctx` | struct | see below | every field optional |
| `dt` | partition | `YYYY-MM-DD` (UTC) of `ts` | from Firehose's JQ, not a column in the JSON |

`ctx`:

| Field | From | Rule |
|---|---|---|
| `utm_source`, `utm_medium`, `utm_campaign` | landing URL query | short tokens; anything with `@` or 7+ digits is dropped |
| `referrer_domain` | body `ctx.referrer` | host only, `www.` stripped, our own hosts dropped. **Never the full URL** |
| `landing_path` | body `ctx.landing`, else the `Referer` header | path only. Segments with a digit or over 20 chars become `id`. Only `utm_*` survive from the query |
| `country` | `CloudFront-Viewer-Country` | 2 letters. **The IP is never read or stored** |
| `device_class` | `CloudFront-Is-{Mobile,Tablet,Desktop,SmartTV}-Viewer`, else a UA guess | `mobile` `tablet` `desktop` `tv` `other` |
| `os_family`, `browser_family` | User-Agent | a fixed list (`ios`, `android`, `safari`, `chrome`, …). **The UA string is not kept** |

What never goes in: typed text, emails, phone numbers, full URLs, IP addresses,
UA strings, birth data, names.

## 2a. Event catalog

Client events (`/api/activity` signed in, `/e` signed out) are named after the
screen or control (`Track` in `app/static/index.html`, `site/track.js`); these
are the ones with fixed names. Server events are `type = 'server'`,
`source = 'server'`.

| name | type | source | props | uid | when |
|---|---|---|---|---|---|
| `app` | `heartbeat` | app | — | yes | every 30 s while the app is visible and was touched in the last 5 min (time in app). `screen` is where she is. Lake only, never in DynamoDB |
| `app` | `open` / `hide` | app | — | yes | the app comes to the front / is put away |
| `push_opened` | `open` | app | `kind` | yes | the app was opened from a push (the `from=push&pk=<kind>` marker, stripped on load) |
| `<screen>` / `<control>` | `screen` / `tap` / `change` / `error` | app | allowlisted | before sign-in: no (to `/e`, `anon_id`) | as today; before sign-in only when the stack routes `/e` (`/api/state` → `collect`) |
| `auth.code_requested` | `server` | server | `channel`: `email`\|`sms` | never | a sign-in code was sent. `anon_id` from the request when allowed |
| `auth.verified` | `server` | server | `channel`, `new` | after consent | sign-in succeeded (`track_auth`; a bare count before consent) |
| `push_sent` | `server` | server | `kind`: `morning`\|`person`\|`sky`\|`notice`\|`reveal`\|`test` | yes | at least one of her devices took the push (`push.send(kind=…)`). Lake only |
| `paywall_shown` | `server` | server | `source`: `chat_cap`\|`people_cap` | yes | the plan refused a message / a sixth person (the 402 the paywall sheet opens on) |
| `subscription_started` / `subscription_renewed` / `subscription_canceled` | `server` | server | `tier` (`plus`), `source`: `apple`\|`google`\|`stripe`\|`web`\|`operator` | yes | `engine/plan.grant()`: free→plus, plus→plus, plus→free. `operator` is a hand grant from `/ops`; leave it out of revenue |
| `message_sent`, `person_added`, … | `server` | server | as before | yes | existing `activity.track()` calls |
| `funnel.<step>` | `server` | server | — | never | existing `funnel.hit()` |

Push kinds come from `app/push.py` `KINDS`; the marker is added in one place
(`push.mark()`), whatever path the push opens, so the URLs themselves can change.

## 3. The collector (`POST /e`)

```json
{"anon_id": "k2V9…(16-40 url-safe chars)", "session": "s-abc123", "source": "site",
 "app_version": "2026.10.3",
 "ctx": {"referrer": "https://www.google.com/", "landing": "/?utm_source=ig&utm_campaign=launch"},
 "events": [{"type": "page", "name": "home", "screen": "home", "at": 1793241609483,
             "props": {"kind": "hero"}}]}
```

- Up to `MAX_BATCH` (50) events and `MAX_BODY` (64 KB). A body that is too big
  gets 413, bad JSON 400, a method other than POST 405, and a missing or wrong
  `x-origin-auth` 403. Everything else gets **204**, whether it was kept or not.
- Bots, and requests with no User-Agent, are dropped.
- `Sec-GPC: 1` or `DNT: 1`: `anon_id` and `session_id` are dropped before
  anything is stored. The visit is still counted, but it can't be linked to
  any other. `/api/activity` does the same with `anon_id`. The uid stays,
  because she has agreed to it and can switch it off in Settings.
- `x-origin-auth` follows `app/edge.py`. It is checked against
  `MYSIGN_ORIGIN_TOKEN`, or the app secret's `origin_token`
  (`MYSIGN_SECRET_ARN`), which is read once and retried every 60 s. A
  configured secret that can't be read refuses everything.
- One set of rules: `deploy/collect/events.py` is a **symlink** to
  `app/events.py`. CDK follows it when it packages the asset
  (`SymlinkFollowMode.ALWAYS`), so the Lambda gets a real copy of the same file.
  `tests/test_events.py` asserts that the link exists and runs the collector
  through it.
- A Firehose failure raises, deliberately. The error becomes a Lambda error,
  which the `CollectorErrorAlarm` counts.

## 4. Infrastructure (`deploy/analytics_stack.py`)

The stack is `mysign-analytics`, or `mysign-analytics-staging` with `-c stage=staging`.

| Resource | Prod name | Notes |
|---|---|---|
| S3 lake | generated (`LakeBucket` output) | SSE-S3, all public access blocked, TLS enforced, versioning off, RETAIN (staging: DESTROY + auto-delete). Lifecycle: `raw/` 90 d, `athena-results/` 30 d, `errors/` 14 d, incomplete multipart 2 d. Tag `mysign-expire-days` = 1/7/30/60 shortens `raw/` objects for the backfill |
| Glue database | `mysign_analytics` (`_staging`) | |
| Glue table | `events` | Parquet, `raw/events/`. Partition projection on `dt` (date, from 2026-01-01 to NOW) and `source` (enum), so no crawler |
| Glue table | `events_json` | the same columns as JSON lines, `raw/events-json/`, for the backfill |
| Firehose | `mysign-events` (`-staging`) | Direct PUT. JSON is converted to Snappy Parquet using the `events` schema. Dynamic partitioning by JQ: `dt` = day of `.ts` and `source`. Buffer 128 MB / 300 s. Errors go to `errors/!{firehose:error-output-type}/dt=…/`. Its role is limited to the bucket and that one table |
| Athena workgroup | `mysign` (`-staging`) | the result location `athena-results/` is enforced with SSE-S3. `bytes_scanned_cutoff_per_query` = 1 GiB (`-c athenaCutoffBytes=`) |
| Collector | Lambda python3.13 arm64, 256 MB, 5 s | Function URL with auth NONE (it checks `x-origin-auth` itself). Reserved concurrency 10 (`-c collectConcurrency=`). Log group `/mysign/collect`, 90 d. IAM: `firehose:PutRecordBatch` on the stream only |
| Alarm | `CollectorErrorAlarm` | any Lambda error in a 5-minute period. The action is `-c alarmTopicArn=` (the main stack's SNS topic) if given |
| Rollups | Lambda python3.13 arm64, 512 MB, 5 min (`deploy/rollups/`) | EventBridge `cron(5 * * * ? *)` `{"mode":"hourly"}` and `cron(30 2 * * ? *)` `{"mode":"daily"}`, no retries. Log group `/mysign/rollups`, 90 d. IAM: Athena on the workgroup only; Glue read on the db and both tables; S3 read on `raw/events/*` and `raw/events-json/*`, read/write on `athena-results/*`, write on `agg/<report>/*` only; `dynamodb:PutItem` on the app table restricted to the `_report` partition (`dynamodb:LeadingKeys`). `RollupErrorAlarm` like the collector's |

The stack's outputs are `StreamName`, `StreamArn`, `LakeBucket`, `Workgroup`,
`GlueDatabase`, `CollectorUrl`, `CollectorDomain` and `RollupFunction`.

These context values are optional:
- `domainName`: own hosts, so self-referrals are dropped
- `appSecretArn`: the app's secret, whose `origin_token` (the `x-origin-auth` token) is
  resolved at deploy time. There is no `originToken` flag; the synth refuses one, because a
  token passed as context is written into the template in plain text
- `alarmTopicArn`
- `athenaCutoffBytes`
- `collectConcurrency`
- `appTableName` (staging: `stagingAppTableName`, or `stages.staging.appTableName`):
  the main stack's `TableName` output. With it, the rollup Lambda also writes
  the `/ops` summaries to DynamoDB. Without it, the rollups write to S3 only (§7)

```
cd deploy
MYSIGN_ACCOUNT=889002527942 cdk synth mysign-analytics
MYSIGN_ACCOUNT=889002527942 cdk deploy mysign-analytics \
    -c appSecretArn=<the app secret ARN> -c alarmTopicArn=<main stack alarm topic> \
    -c appTableName=<main stack TableName output> \
    -c domainName=mysignapp.com
```

### Wiring the main stack (`deploy/mysign_stack.py`)

Done; tests in `tests/test_infra.py`.

1. **Env and IAM, on the app and the cron function.**
   `MYSIGN_EVENTS_STREAM` = `mysign-events` (prod) / `mysign-events-<stage>`,
   the analytics stack's name for the stream, and `firehose:PutRecordBatch`
   on that stream's ARN. By name, not by export, so neither stack pins the
   other. On by default; `-c analyticsEvents=false` (or `-c
   stagingAnalyticsEvents=false`) leaves it out. Until the stream exists,
   each forward fails fast, is logged (`activity.forward_failed`) and the
   request carries on.

2. **CloudFront `/e`**, only when the collector's domain is known:
   `-c collectorDomain=<CollectorDomain output>` for prod,
   `-c stagingCollectorDomain=…` (or `collectorDomain` in the `stages` map)
   for staging — staging never inherits prod's. A `https://` prefix or a
   trailing path is tolerated. The behavior: the Function URL as an HTTPS-only
   `HttpOrigin` with the same `x-origin-auth` as the app, `ALLOW_ALL`
   methods (POST needs it; the collector 405s the rest), `CACHING_DISABLED`,
   and an origin request policy (`CollectHeaders`) forwarding only
   `CloudFront-Viewer-Country`, `CloudFront-Is-{Mobile,Tablet,Desktop,SmartTV}-Viewer`,
   `User-Agent`, `Referer`, `Sec-GPC` and `DNT` — no cookies, no query
   string. It works with `staticFromS3` on (it replaces the `/e` → app entry
   in `_edge_routes`; no router function) and off (the one extra behavior).
   With no domain, nothing changes: `/e` stays the app's 404, and prod's
   distribution keeps its single behavior. The app also gets
   `MYSIGN_COLLECTOR=1`, which `/api/state` passes on as `collect: true`, so
   the sign-in screen only beacons when something will receive it.

   Context rather than an SSM parameter or a stack export: no CloudFormation
   dependency between the stacks, and synth works when the analytics stack
   has never been deployed.

**Deploy order** (each stage):

```
cd deploy
# 1. the analytics stack (stream, lake, collector)
MYSIGN_ACCOUNT=889002527942 cdk deploy mysign-analytics -c appSecretArn=<app secret ARN> ...
# 2. read its CollectorDomain output
aws cloudformation describe-stacks --stack-name mysign-analytics \
    --query "Stacks[0].Outputs[?OutputKey=='CollectorDomain'].OutputValue" --output text
# 3. the main stack, with it (staging: -c stage=staging -c stagingCollectorDomain=...)
MYSIGN_ACCOUNT=889002527942 cdk deploy mysign -c collectorDomain=<domain>
```

Deploying the main stack first is harmless (the forwards fail and are
logged); deploying it without `collectorDomain` leaves `/e` unrouted. Put the
domain in `cdk.json` (`collectorDomain`, or the staging entry of `stages`)
once known, so a later deploy doesn't drop the behavior by omission.

## 5. Querying

The workgroup enforces its own result location, so no output location is needed:

```sql
-- Daily visitors to the site by country (anon_id is null under GPC/DNT: count those separately)
SELECT dt, ctx.country, count(DISTINCT anon_id) AS visitors, count_if(anon_id IS NULL) AS private_hits
FROM mysign_analytics.events
WHERE source = 'site' AND type = 'page' AND dt >= cast(current_date - interval '7' day AS varchar)
GROUP BY 1, 2 ORDER BY 1, 3 DESC;

-- Funnel, from the server's own events
SELECT dt, name, count(*) FROM mysign_analytics.events
WHERE source = 'server' AND name LIKE 'funnel.%' AND dt >= '2026-10-01'
GROUP BY 1, 2 ORDER BY 1, 2;

-- Pre-sign-in to signed-in: devices that later verified
SELECT count(DISTINCT anon_id) FROM mysign_analytics.events
WHERE name = 'auth.verified' AND anon_id IS NOT NULL;
```

Always filter on `dt`. Partition projection prunes partitions only through
predicates on `dt` and `source`.

Live and backfilled rows can be read together through one view (create it once;
the rollups don't need it, they inline the same UNION ALL):

```sql
CREATE OR REPLACE VIEW mysign_analytics.events_all AS
SELECT event_id, ts, received_at, app_version, anon_id, uid, session_id, type, name, screen,
       props, ctx, dt, source FROM mysign_analytics.events
UNION ALL
SELECT event_id, ts, received_at, app_version, anon_id, uid, session_id, type, name, screen,
       props, ctx, dt, source FROM mysign_analytics.events_json;
```

## 6. Backfill (`tools/backfill_events.py`)

This is a one-off copy of what DynamoDB already holds into the lake. The data
is tiny (68 items in the table at the time of writing).

```
AWS_PROFILE=mysign AWS_REGION=us-east-2 MYSIGN_TABLE=<table> python tools/backfill_events.py            # dry run: counts only
AWS_PROFILE=mysign AWS_REGION=us-east-2 MYSIGN_TABLE=<table> python tools/backfill_events.py --write --bucket <LakeBucket>
```

`MYSIGN_TABLE` switches `engine/store.py` to DynamoDB. Without it, the script
reads the local `./data` files. Even a dry run reads the table.

- **Every user's `act-*`** (from `auth.all_users()`) becomes envelopes with
  `source=backfill`, the uid, and the session. A user who has switched off is
  skipped. Their items were deleted when they switched off anyway.
- **Funnel day buckets**, both the legacy and the atomic ones, become that
  many uid-less `funnel.<step>` server events at noon UTC, in the same shape
  the live pipeline writes.
- **Global counters as they are.** `_study/funnel-*` goes to
  `agg/funnel_daily/` and `_activity/_act#*` goes to `agg/activity_daily/`,
  as `{dt, step|field, count}` JSON lines. They contain no ids.
- The raw rows are JSON lines in `raw/events-json/dt=…/source=backfill/`,
  where the `events_json` table and the `events_all` view read them. Files
  are written as JSON because the venv has no pyarrow, and one table can't mix
  JSON and Parquet.
- **Retention follows the event, not the copy.** Each raw object is tagged
  `mysign-expire-days` with the largest of 60/30/7/1 that still ends before
  the event's 90th day. Days already past 90 are skipped.
- **It is not idempotent across runs.** Before a second run, delete the first
  run's objects; they share a `backfill-<run>` suffix.

To fold the backfill into the Parquet table instead, so that one table holds
everything:

```sql
INSERT INTO mysign_analytics.events
SELECT event_id, ts, received_at, app_version, anon_id, uid, session_id, type, name, screen,
       props, ctx, dt, source FROM mysign_analytics.events_json;
```

This writes Parquet under the `events` location. Athena's INSERT INTO with
partition projection and a `storage.location.template` hasn't been verified
here, so try it on staging first. Files written this way don't carry the
expiry tag, so they fall back to the 90-day rule from the day of the INSERT.

## 7. Rollups (`deploy/rollups/`)

A Lambda runs Athena queries in the stack's workgroup and keeps one small,
id-free summary per report and day. `/ops` reads the summaries, never the lake.

- `rollup_sql.py`: one SQL template per report (Athena engine v3 / Trino:
  `min_by`, `count_if`, `approx_percentile`, `element_at`, `GROUPING SETS`).
  Each query reads `events` **and** `events_json` through an inline
  `UNION ALL`, pruned by `dt` (and `source`) in both branches, so a backfill
  counts without `INSERT INTO`.
- `rollup_shape.py`: Athena rows → the JSON below, small-count suppression,
  the size cap, and a check that no id column ever leaves.
- `handler.py`: the Lambda and a CLI. Up to 8 queries run at once. Every query
  shares one time budget, which is the Lambda's remaining time minus 20 s.
  Queries still running at the deadline are stopped. A report with a failed
  query is skipped, its neighbours are still written, and the invocation then
  raises, which fires `RollupErrorAlarm`.

### Schedule

| Run | When | Days | Reports | `final` |
|---|---|---|---|---|
| hourly | `:05` every hour | today so far; also yesterday until 03:00 UTC (late events) | all but `retention` | false |
| daily | 02:30 UTC | yesterday | all, including `retention` (re-scores the last 12 weekly cohorts, so D1/D7/D30 and W1..W8 fill in as they mature) | true |

A partial (hourly) write never replaces a final one: the DynamoDB put is
conditional (`attribute_not_exists(final) OR final = false`), and when it is
refused the `agg/` write is skipped too.

### Outputs

- **S3:** `agg/<report>/dt=YYYY-MM-DD/<report>.jsonl`, with one JSON line
  holding that day's summary. These are kept long-term, like the rest of `agg/`.
- **DynamoDB** (only with `appTableName`): one item per report and day.

| Attribute | Value |
|---|---|
| `pk` | `"_report"` (global items start with `_`, like `_activity`, `_study`, `_ops`) |
| `sk` | `"<report>#YYYY-MM-DD"` |
| `v` | JSON `{"data": {...}, "generated_at": <epoch s>, "final": <bool>, "version": 1}` (the store's usual value attribute) |
| `generated_at`, `final` | copies of the above, readable without parsing |
| `ttl` | absent: summaries have no ids and are kept |

`data` is at most 350 KB. Past that, the longest segment lists are cut and
their tail is summed into a `"(other)"` row (`truncated: <rows>`), and `data`
gets `truncated: true`. `app/reports.py` is the reader. It does one `Query`
on `_report` with `sk BETWEEN "<r>#from" AND "<r>#to"` (`store.load_range`).

### Reports

Every day is `{date, final, generated_at, ...data}`. Counts are distinct
people (or `event_id`s for "uses"), per UTC day `dt`. Backfilled rows
(`source=backfill`) keep their original `type` and `name`, so server events
are matched by `type='server'`, not by source.

| Report | `data` | Definitions |
|---|---|---|
| `visits` | `{totals:{visits,visitors,pageviews,private_visits}, by:{utm_source,utm_campaign,referrer_domain,landing_path,country,device_class:[{key,visits,visitors,pageviews,private_visits}]}}` | Site events only. A visit is a `session_id`. A GPC/DNT page view has no session and no `anon_id`: it is a visit of its own, counted in `private_visits`, but not a visitor. A visit's dimensions are those of its first event |
| `signups` | `{code_requested:{total,by_channel:{sms,email}}, verified:{total,new,returning,by_channel:{<ch>:{new,returning}}}, visitors, conversion, attribution:{signups,from_site,by:{utm_source,referrer_domain,landing_path:[{key,signups,from_site}]}}}` | Server `auth.code_requested` {channel} and `auth.verified` {channel,new}, as distinct devices (`anon_id`, else `uid`, else the event). `conversion` = verified new ÷ site visitors that day. Attribution covers the day's new accounts' devices (`auth.verified` new=true with `anon_id`, or any device of someone who `consented` that day), joined to that device's **first** site event in the previous 30 days. `from_site` = signups with any site touch |
| `funnel` | `{steps:[{step,label,count}], extra:{code_requested_sms,code_requested_email,verified_new,signed_up,paywall_intent,funnel_paid}}` | Daily counts per step, each cohorted by the day of its own event, so this is not one cohort walking down. Steps: `visit` (site sessions with a page) → `start_open` (site tap `start_open`/`signin_open`) → `code_requested` → `verified` → `consented` → `onboarded` → `first_message` → `returned_day2` → `week_one` (these five are the `funnel.<step>` server events of `engine/funnel.py`) → `paywall_shown` → `paid` (`subscription_started`, not `source=operator`). `extra.funnel_paid` is the `funnel.paid` bucket, which also counts operator hand grants |
| `active` | `{dau,wau,mau,stickiness,new,returning}` | Distinct `uid` with any non-site, non-passive event (`push_sent` and `subscription_*` don't count: nobody did them in the app) over the last 1/7/28 days. `stickiness` = dau/mau. `new` = `consented` that day. `returning` = dau − new |
| `features` | `{active_users, features:[{feature,label,users,uses,uses_per_user,share}]}` | `checkin` = `checked_in`; `chat` = `message_sent`; `reading` = screen `chart`/`place`; `people` = `person_added`; `memory` = screen `memory`; `sky` = taps/screens named `sky_*`/`sky.*`; `invites` = `funnel.invite_created`/`invite_accepted` (no uid: uses only); `push` = `push_opened` (an app `open` event). `share` = users ÷ active_users |
| `time` | `{sessions,users,seconds,median_session_s,p75_session_s,sessions_per_user,seconds_per_user,by_screen:[{screen,seconds,users}]}` | App/native sessions. Heartbeats (`type=heartbeat`, name `app`) stop after 5 idle minutes, so this is engaged time. Session length = heartbeats × 30 s, capped at the session's own first-to-last event span + 30 s. Without heartbeats it is that span. Per screen = heartbeats × 30 s. Per-user values use signed-in sessions only. Medians come from `approx_percentile` |
| `retention` | `{as_of, points:[d1,d7,d30,w1..w8], cohorts:[…], by_channel:[…], by_utm_source:[…]}`, each row `{cohort,key,users,d1..w8,counts:{<p>_n,<p>_base}}` | Cohort = ISO week (Monday) of someone's first `consented`. `dN` = active exactly N days later. `wK` = active on any day 7K..7K+6 later. The rate is `<p>_n ÷ <p>_base`, counting only members whose point has fully passed, so it is `null` until anyone has matured. The split by signup channel (last `auth.code_requested` of their devices) and by first-touch `utm_source` |
| `notifications` | `{totals:{sent,opened,users_sent,users_opened,checked_in,open_rate}, by_kind:[{key,…}], by_hour:[{hour,…}], hour_basis:"utc"}` | server `push_sent` {kind} → app `open` `push_opened` {kind}. `checked_in` = openers with a `checked_in` later that same day. Hours are UTC: events carry no local time |
| `monetization` | `{totals:{shown,shown_users,started,renewed,canceled,operator_grants,conversion}, by_source:[{key,…}], by_tier:[{key,…}]}` | `paywall_shown` {source: `chat_cap`\|`people_cap`} → `subscription_*` {tier,source}. `started`/`renewed`/`canceled` leave out `source=operator` (hand grants), which are counted in `operator_grants` instead. `conversion` = started ÷ shown_users. `by_source` mixes paywall sources and purchase sources, one row per key |
| `site` | `{totals:{pageviews,countries}, steps:[{step,label,count}], channels:[{key,pageviews,events,scrolled,opened}], sections:[{key,uses,people}], taps:[…], lengths:[{key,sessions}], by:{os_family,browser_family,utm_medium:[{key,pageviews,visitors}]}}` | The landing page. `steps` are people with an id (`anon_id`, else session). `channels` buckets every site event (`rollup_sql.CHANNELS`, first match wins), and there a private visitor's actions each count on their own. `lengths` are session spans. Event names come from the client, so `sections`/`taps` keep their top 50 |
| `usage` | `{totals:{events,site_events,app_events,server_events,app_opens}, by_hour:[{hour,events,pageviews}], hour_basis:"utc", screens:[{key,uses,users}], taps:[…], changes:[…], server:[…]}` | Everything recorded, by source, hour and name. Totals and hours leave the backfill out (its rows sit at noon UTC). `/ops` shifts hours to the viewer's clock and derives weekdays from the days |

**Ranges** (`app/reports.range_summary`, the API's `totals`) add the days up.
The exceptions are rates, which are recomputed from the summed parts; `active`
(average `dau`, plus `dau_max`, the last day's `wau`/`mau`, and summed
`new`/`returning`); `retention` (the latest snapshot); and `time` medians
(their session-weighted mean, flagged in `approximate`). Rows are matched on
their key (`key`, `step`, `feature`, `screen`, `hour`, `cohort`), which is kept
as it is, never added (`by_hour` hour 13 stays 13 across days).

**Small counts.** An open-ended segment of fewer than 5 people (`MIN_COUNT`)
is folded into `"(other)"`. That covers the visits dimensions, the attribution
dimensions, and the retention channel/utm_source cohorts. Totals and closed
sets are never folded: screens, push kinds, hours, tiers, paywall sources,
features, channels, visit lengths, OS and browser families, event names. They can't single anyone out on their own. At launch, expect most
segments to be "(other)".

### API

`GET /api/ops/report` lists the reports. `GET /api/ops/report/{name}?from=YYYY-MM-DD&to=YYYY-MM-DD&compare=1`
needs the **analyst** role and is audited as `report`. The default range is the
last 30 days. More than 400 days, a bad date or `from > to` is a 400. An
unknown report is a 404. The response:

```json
{"name": "visits", "from": "2026-10-01", "to": "2026-10-30", "source": "rollup",
 "days": [{"date": "2026-10-01", "final": true, "generated_at": 1790000000, "totals": {}, "by": {}}],
 "totals": {},
 "previous": {"from": "2026-09-01", "to": "2026-09-30", "source": "rollup", "totals": {}}}
```

`previous` appears only with `compare=1`: the same number of days just before
`from`. With `.csv` (`/api/ops/report/visits.csv`) or `format=csv`, the
response is a tidy CSV, `date,section,key,metric,value`, where totals rows have
`date=total`.

**Live fallback.** When no day in the range has a rollup (locally, or before
the analytics stack is deployed), `source` is `"live"`. The days are then
rebuilt from the existing DynamoDB counters: `_activity/_act#<day>` and the
`engine/funnel` buckets. That's a few reads per day, with no walking of users,
clamped to the last 90 days (`clamped_to_days`). It covers `signups`, `funnel`,
`active` (dau, new, returning), `features` and `monetization` (paywall views
only). Whatever those counters can't say is `null`, including `paid` and
`started`, because the counters can't tell billing from operator grants.
`visits`, `time`, `retention` and `notifications` come back with
`unavailable: true`: heartbeats and `push_sent` are lake-only, and the
counters skip `open` events. Rollup and live data are never mixed in one answer.

### Cost

A day's rollup is 18 queries (`signups` and `time` are two each, `site` five, `usage` three), plus
`retention` in the daily run. Hourly runs do one day, or two before 03:00 UTC,
so a day comes to ~500 queries. Athena bills at least 10 MB per query, which makes ~4.8 GB/day ≈ **$0.024/day (~$0.75/month)** while the lake is small. With traffic, the per-day reports scan one day's
Parquet columns. `active` scans 28 days, `signups` 31, and the daily
`retention` 90. At ~1M events/day (~50 MB of Parquet a day), that's still under
~10 GB a day, about $1.5/month. The workgroup's 1 GiB per-query cutoff bounds
any single mistake.

### Rerun, backfill

```
export AWS_PROFILE=mysign AWS_REGION=us-east-2
# any range (final for days before today), S3 + DynamoDB:
python deploy/rollups/handler.py --from 2026-09-01 --to 2026-09-30 \
    --workgroup mysign --database mysign_analytics --bucket <LakeBucket> --table <TableName>
python deploy/rollups/handler.py --from 2026-09-01 --to 2026-09-01 --reports funnel --dry-run  # print SQL
# after a backfill, in one go:
MYSIGN_TABLE=<table> python tools/backfill_events.py --write --bucket <LakeBucket> \
    --rollup-from 2026-06-01 --rollup-to 2026-09-30
```

Or invoke the Lambda with `{"from": "…", "to": "…", "reports": ["funnel"]}`
(at most 400 days; mind the 5-minute timeout, so do about 10 days per call).
Reruns overwrite: summaries are keyed by day, and `agg/` keeps one object per
report and day. If live and backfilled rows overlap on a day, counts made with
`DISTINCT event_id` are unaffected. People counts are by id, so they don't
change either.

## 8. Retention and deletion

- `raw/` (live): **90 days**, by a lifecycle rule. This matches the privacy
  policy's "90 days, then deleted automatically". If one changes, change the
  other (`RAW_DAYS` in the stack).
- `agg/`: kept. Rollups must aggregate away `uid`, `anon_id` and
  `session_id`. A table here with an id in it is a bug.
- **Switch-off** (`analytics_off`): nothing more is stored or forwarded from
  that moment, and the DynamoDB copy is deleted. The lake copy is not; it
  ages out within 90 days. The privacy page says so.
- **Account deletion**: same. The lake rows carry only the uid, and after
  deletion nothing maps that uid to a person. They age out within 90 days.
  `docs/data-inventory.md` §6 lists this as a disclosed gap.
- Raw rows can't be deleted cheaply one at a time; that would need rewriting
  the Parquet files. If that's ever needed, run a CTAS without that uid's
  rows, swap the files, and delete the old ones.

## 9. Not done yet

- [x] **The client side.** `Track` in `index.html` reuses the website's
  `ms_anon` (ULID, made if absent; never under GPC/DNT or once analytics was
  switched off on the device) and sends it with `/api/activity` and the
  sign-in calls; heartbeats for time in app; `push_opened`; the sign-in
  screen beacons to `/e` with `source: "app"`. `site/track.js` now puts
  `anon_id`, `session`, `ctx` at the top of the batch and `at` on each event,
  where the collector reads them.
- [x] **The main-stack wiring** in §4: env, IAM, and the `/e` behavior.
- [x] **The auth routes**: `anon_id` from the body (or `x-anon-id`) on
  `/api/auth/request` and `/api/auth/verify`; `auth.code_requested` and
  `auth.verified`.
- [x] **Server events**: `push_sent`, `paywall_shown`, `subscription_*` (§2a).
- [x] **Rollups** into `agg/` and DynamoDB, and `/api/ops/report/*` reading them (§7).
- [x] **`/ops` UI** for `/api/ops/report/*`: the report tabs in `ops.html`, with range,
  compare and CSV (docs/operations.md, *Operator console*).
- [ ] **Rollups verified on real Athena.** The SQL is tested on DuckDB (transpiled
  by sqlglot) but has never run on Athena engine v3.
- [ ] **Billing webhooks.** `subscription_*` fire from `plan.grant()`; until a
  webhook calls it with `apple`/`google`/`stripe`/`web`, only `operator`
  grants show up.
- [ ] `site/site.js` sends its sign-in steps as `type: 'auth'`, which isn't a
  client type, so the collector drops them; the server's
  `auth.code_requested` / `auth.verified` count the same steps.
