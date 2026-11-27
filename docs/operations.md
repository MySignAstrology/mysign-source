# Operations

Staging, the static-from-S3 edge, provisioned concurrency and GitHub Actions
deploys: see `docs/environments.md`.

## Incident runbook (item 93)

**Where to look first:** CloudWatch Insights, log group `/mysign/app` (the
scheduler logs to `/mysign/cron`, the support-mail forwarder to
`/mysign/mailforward`). The old `/aws/lambda/mysign-Fn…` groups stop receiving
lines after the first deploy of this stack version; they keep their history.

```
fields @timestamp, event, path, status, ms, error_type
| filter level = "error"
| sort @timestamp desc
```

Every error line carries a `request_id`. A user quoting one from a 500 page can
be matched exactly.

| Symptom | Likely cause | Do |
| --- | --- | --- |
| Header shows **demo** in production | Secret unreadable, or key missing | `secret.unreadable` in logs → check the Lambda's role; else add `anthropic_api_key` |
| Sign-in returns 503 | SES sender unverified, or sandbox | `auth.unavailable` in logs → verify the sender, request production access |
| Replies stream then stop | Model provider outage, or Lambda timeout | Check Anthropic status; confirm the function timeout ≥ 120s |
| Morning push silent for everyone | Scheduler not firing | `CronSilentAlarm` fires; check the EventBridge rule is enabled |
| Push failing for one person | Dead subscription | `push.failed` with 404/410 → pruned automatically; ask them to re-enable |
| iPhone app notifications failing for everyone | APNs key wrong or revoked, or `apns_env` pointing at the wrong service | `apns.failed` with `InvalidProviderToken` (key, key id or team id) or `BadDeviceToken` for everyone (`apns_env`); see `docs/ios-push.md` |
| **Red-tier page at 3am** | Someone in crisis | Open the thread. Confirm the canned reply sent. Decide on follow-up. Log it. This is the only page you cannot leave until morning. |
| DynamoDB item too large | A shard grew past 380KB | `store_dynamo` raises before writing; shard by week instead of month for that user |
| Costs spike | Quota bypassed, or a loop | `quota.refused` counts; check the `message` events per uid in the dashboard |
| `RedTierAlarm` | A `safety.red` line — someone in crisis | Same as the page above. Fires whether or not PagerDuty is configured. |
| `AlertUnroutedAlarm` | A page had no PagerDuty key | Add `pagerduty_routing_key`; meanwhile the alarm topic *is* the page |
| `ApiThrottleAlarm` | Account Lambda concurrency exhausted (limit 10 until the increase lands) | Check the Service Quotas request; then deploy with `-c reservedConcurrency=N` |
| `AppErrorLinesAlarm` | ≥5 `level=error` lines in 5 min | Run the Insights query above |
| `AuthUnavailableAlarm` / `SecretUnreadableAlarm` | Sign-in off / secret unreadable | Rows two and one of this table |
| `MailBounceRate` / `MailComplaintRate` | SES reputation near AWS's review line | Stop any bulk send; read the bounce/complaint notifications on the alarm topic |
| `MailForwarderErrorAlarm` | Support mail arrived but did not forward | Raw message is in the `Inbox` bucket under `inbound/`; check `/mysign/mailforward` |

**After every production deploy: publish the source.** My Sign is AGPL (`NOTICE.md`), so
the deployed commit must be public: `tools/publish_source.sh <deployed commit>` pushes a
snapshot of it to github.com/MySignAstrology/mysign-source.

**Rollback:** `cdk deploy` the previous commit. The table and secret are
`RETAIN`, so a stack rollback never touches data.

## Secrets (item 94)

| Secret | Where | Rotate | If lost |
| --- | --- | --- | --- |
| `anthropic_api_key` | Secrets Manager | on suspicion, or yearly | new key, redeploy; nothing else changes |
| `session_secret` | Secrets Manager | yearly | **signs everyone out** — rotate at a quiet hour and say so |
| `vapid_private` | Secrets Manager | **never, unless compromised** | **every push subscription dies** and each person must re-enable notifications. Back it up before the first deploy. |
| `apns_key`, `apns_key_id`, `apns_team_id`, `apns` | Secrets Manager (`docs/ios-push.md`) | when Apple says, or on suspicion: make a new key in the developer portal, swap all three, then revoke the old one | iPhone notifications stop (`apns.failed` on every send) until replaced. No tokens are lost; nobody has to re-enable anything |
| `pagerduty_routing_key` | Secrets Manager | when PagerDuty says | pages go to `alert.unrouted` in logs (and `AlertUnroutedAlarm`) until replaced |
| `from_email` | Secrets Manager — **only without a domain**. With `-c domainName`, CDK sets `MYSIGN_FROM_EMAIL="My Sign <hello@domain>"` on the function and that wins (env beats secret in `app/bootstrap.py`) | n/a | sign-in fails closed (`auth.unavailable`) |
| `support_email` | same rule: secret without a domain, CDK sets `support@domain` with one | n/a | support link falls back |
| `ops_admins` | Secrets Manager | n/a — a list, not a credential | comma-separated sign-in emails treated as console admins (see below). Removing it locks nobody out: an admin is persisted on first use |
| `team_markers` | Secrets Manager | n/a — not a credential | comma-separated parts of an address (`@ours.example,+qa`) that mark our own and test accounts as "team" in `/ops` Users. Unset: nobody is marked |
| `ops_token` | Secrets Manager | **remove** once an admin has signed in; until then on suspicion | break-glass only (role admin, `ops.breakglass` logged on every use). Sent **only** as the `x-ops-token` header — the `?token=` query form was removed so it never lands in URL logs |
| `origin_token` | Secrets Manager only. Both stacks resolve it from the secret at deploy time (`{{resolve:secretsmanager}}`), so it never appears in a template, a flag or a shell history; the synth refuses `-c originToken=` | at a quiet hour: write a new value (`python3 -c 'import secrets; print(secrets.token_hex(32))'` piped straight into `put-secret-value`, never echoed), deploy `mysign` then `mysign-analytics` so CloudFront and the collector send it, then force a cold start (`aws lambda update-function-configuration --function-name <Fn> --description rotated-$(date +%F)`). Requests 403 in between | with the secret set and CloudFront sending a different value, every request is 403 `forbidden`. Remove `origin_token` from the secret to fail open |
| SES domain identity | CDK (`MailDomain`), verified by DKIM CNAMEs | n/a | sign-in fails closed |
| Deploy credential | your machine | every 90 days | nothing runs until replaced; nothing breaks |

**Backup the VAPID key now.** It's the one secret whose loss is user-visible and
unrecoverable. Print it, put it somewhere that isn't AWS.

## Admin platform (`admin.mysignapp.com`)

One sign-in, three areas. `/` signs in (email + 6-digit code, `app/admin.py`)
and lists what you may open; `/social/` is the social media tool
(social-media-automation repo), `/manage` runs the app (Users, Safety, System
health, Admin) and `/analytics` is the numbers (Dashboard, Acquisition, Funnel,
Engagement, Retention, Notifications). `/manage` and `/analytics` are one page,
`app/static/ops.html`, showing the area its path names; `mysignapp.com/ops`
redirects there.

- **Session.** Its own cookie (`__Host-ms_admin`) and signing key, seven days,
  tied to the operator record: removal takes effect on the next request here
  and within a minute in the social tool; **Sign out** ends every copy without
  touching the person's app session. An app session still works on
  `mysignapp.com/api/ops/*` but cannot reach the admin host.
- **Sign-in says nothing.** An address not on the list gets the same answer and
  no email. `admin.signin.not_sent` in the log means a listed address hit a limit.
- **Edge.** `_admin` in `deploy/mysign_stack.py`: its own CloudFront, same WAF.
  `deploy/edge/admin.js` lets through only the pages, `/api/ops/*`, the two
  sign-in calls and `/social`. Off until `adminCertificateArn` is set.
- **Break-glass.** `/manage#breakglass` shows the token form when nobody can
  sign in.

**Turning it on** (once):
1. `aws acm request-certificate --region us-east-1 --domain-name admin.mysignapp.com --validation-method DNS`, add its CNAME to the zone, wait for ISSUED.
2. Put the ARN in `deploy/cdk.json` as `adminCertificateArn`; build and deploy `mysign`.
3. Deploy `mysign-social` (it asks the admin host who is signed in, so it goes second).

**Operators.** Ordinary app accounts with a role.
`app/operators.py` holds the list (`_ops / op-<uid>`, `_set#operators`) and
`app/ops.py` decides who is asking. `social` is outside the order: the social
media tool only. Analyst and safety can be given it too; admins always have it.

| Role | Can use |
| --- | --- |
| `social` | the social media tool, as an editor; nothing of the app |
| `analyst` | `GET /api/ops/me`, `/activity/overview`, `/funnel`, `/report`, `/report/{name}` (`?from=&to=&compare=1`, `.csv`; see analytics.md §7), `/health` — totals only, nobody in particular |
| `safety` | analyst + `/roster`, `/flagged`, `/activity/users`, `/activity/user/{uid}` (journey), `/user/{uid}` (conversation — **only** with a red flag or a report, for every role) |
| `admin` | safety + `POST /plan`, `GET/POST /operators`, `DELETE /operators/{uid}`, `GET /audit` |

**First admin.**
1. Put `ops_admins` (e.g. `"you@example.com"`) in the secret and force a cold
   start (same command as for `origin_token` below the table).
2. Sign up in the app with that email, as any user would.
3. Sign in at the admin platform. You are recorded as `admin` (`ops.admin_bootstrapped`) and
   from then on the table, not the env, is what counts.
4. Add everyone else from the **Operators** tab. They must have signed up in
   the app first; the console cannot create accounts.
5. **Remove `ops_token` from the secret.** It is break-glass: it still works,
   as admin, and logs `ops.breakglass` (warn) on every request, so any line
   with that event after this step is worth asking about.

Removing someone takes effect on their next request. An address still in
`ops_admins` comes back as admin on its next visit, so take it out there too.
The last admin can be neither removed nor demoted (409).

With no token, no `ops_admins` and no operators, every `/api/ops/*` route is a
404 — its existence is not advertised. Once any is configured: 401 signed out,
403 signed in without the role.

**Audit.** Every console read and change writes `_audit / <iso-ts>#<rand>`
(TTL 400 days) *before* returning data — if the write fails, the request fails
— plus an `ops.audit` log line. The **Audit** tab (`GET /api/ops/audit?days=`,
admin) shows it. Refused role checks are recorded as `denied`.

**Tabs.** The console is one page (`app/static/ops.html`, no build step);
each tab is a hash route (`#/overview`, `#/users/<uid>`, `#/admin/audit`…) and
only appears for roles that can use it.

| Tab | Role | Shows |
| --- | --- | --- |
| Overview | analyst | Visitors, sign-ups, visitor → sign-up, daily/weekly/monthly active, stickiness, new vs returning, accounts, app opens, events, paid starts and plan mix, daily charts, and **What stands out**: plain sentences from the other tabs' numbers |
| Acquisition | analyst | Visits by source, campaign, referrer, landing page, country, device; landing → verified mini-funnel; SMS vs email codes; sign-ups by source. Landing page: step-by-step funnel, channel quality, sections, actions, visit length, views by hour, OS, browser, medium |
| Funnel | analyst | Visit → paid, with step and overall conversion and the biggest drop-off marked |
| Engagement | analyst | Feature use (check-in, chat, reading…), time in app, time per screen; events and opens per day, by hour and weekday; screens, taps, settings changed, server events |
| Retention | analyst | D1/D7/D30 and W1–W8 heatmap by sign-up cohort, channel or utm_source |
| Notifications | analyst | Push sent → opened → checked in, by kind and by hour (UTC) |
| Users | safety | Everyone, most recently seen first, 50 at a time (**Load more**); search; a person's journey |
| Safety | safety | Flagged replies (newest first) and the pilot roster; opening a conversation |
| System health | analyst | CloudWatch: Lambda, app metrics, SES, edge traffic, cold starts (below) |
| Admin | admin | Operators, the audit trail, Plus grants |

Every report tab shares one date range (today, 7, 28, 90 days or custom; UTC
days, kept for the browser tab's session), a **compare to previous period**
toggle (▲▼ against the same number of days just before) and a **CSV** link to
`/api/ops/report/{name}.csv` for the same range. With the session cookie the
link is a plain download; with the break-glass token the page fetches it with
the header and saves the result. Until the analytics stack is deployed the
reports run on live counters: a **Live counters** badge says so, and visits,
time in app, retention and notifications show *Not available until analytics
is deployed* instead of numbers.

**No full scans.** Lists come from two index partitions in the main table
(`app/opsindex.py`; no GSI, no table change): `_uidx`, one small row per
person ordered by last seen, and `_flags`, one row per red/amber reply or
report (uid, time, tier, message id — no text, TTL 400 days). `/roster`,
`/flagged` and `/activity/users` take `?cursor=&limit=` and answer with
`next_cursor` (null at the end; a cursor that isn't ours is a 400). Overview
numbers come from the reports (`app/reports.py`), not from walking accounts.
The roster is now most-recently-seen first rather than sorted by red count.
Searching by an exact email, phone or uid is one lookup; a partial search
looks through a bounded number of index rows per request.

*Backfill, once per environment after the first deploy with the index:*

```
AWS_PROFILE=mysign AWS_REGION=us-east-2 MYSIGN_TABLE=<table> \
  python tools/backfill_ops_index.py --batch 500
```

It is idempotent and safe while serving. If nobody runs it, each `/ops` list
request backfills a hundred people until the `_uidx / meta` marker says done —
it gets there, just slowly, and the account total reads "—" until then.
`--again` clears the marker and redoes it.

**System health.** `GET /api/ops/health?from=&to=` (analyst, at most 90 days)
reads CloudWatch: `AWS/Lambda` Invocations, Errors, Throttles, Duration p95
and ConcurrentExecutions for the app and the scheduler; the app's own
namespace (`MySign`, or `MySign/<stage>`: Messages, QuotaRefused, RedTier,
SmsCap, AppErrorLines, AlertUnrouted, AuthUnavailable); SES Send, Delivery,
Bounce, Complaint and reputation rates; CloudFront requests, bytes and error
rates (from us-east-1, for `MYSIGN_SITE_DISTRIBUTION`: `siteDistributionId` in
`deploy/cdk.json`, since the stack can't refer to its own distribution without
a cycle); and cold starts from a Logs Insights
query over the app's log group (`@type = "REPORT"` with `@initDuration`).
Hourly points for ranges under two days, daily otherwise. Answers are cached
in the process for five minutes per range (a minute after a failure); a Logs
query still running is picked up by the next request, and the page asks again
on its own. The stack sets `MYSIGN_CRON_FN_NAME`, `MYSIGN_METRIC_NS` and
`MYSIGN_APP_LOG_GROUP` and grants `cloudwatch:GetMetricData`,
`logs:GetQueryResults` (no resource-level permissions exist for either) and
`logs:StartQuery` on the app's log group only — see the `ops health` block in
`deploy/mysign_stack.py`. Run locally, or with CloudWatch unreachable, it
answers `{unavailable: true, reason}` within seconds and the tab says so. A
metric nobody has emitted yet (e.g. QuotaRefused before the first refusal)
reads 0. Each Logs Insights query scans the range's log data and is billed per
GB — cheap at pilot volume, worth a look if the logs grow.

**Tests.** `npx playwright test -c tests/e2e/ops.config.js` boots two servers
on spare ports — one with no rollups (live fallback), one seeded by
`tests/e2e/seed_reports.py` — signs in a dev operator and walks every tab.
`OPS_SHOTS=<dir>` also saves screenshots, including mobile and dark.

**CSRF.** All `/api/ops/*` answers are `Cache-Control: no-store`. Cross-site
requests (`Sec-Fetch-Site` other than `same-origin`/`none`, or a foreign
`Origin` when Fetch Metadata is absent) are refused. Changes made with the
session cookie also need the `x-ops-request: 1` header, which a cross-site form
cannot send.

## Backups

DynamoDB point-in-time recovery, 35 days, plus an AWS Backup daily plan
(`Backup`, 35-day retention) into its own vault. The table has deletion
protection and the stack has termination protection — both must be turned off
by hand before anything can be torn down. **Restore drill (item 90):** once,
before the pilot — restore the table to a point in time under a new name,
confirm a known account reads back, delete the copy. A backup never restored is
a hope.

## Retention

See `docs/retention.md`.

## Cold starts

Measure (Logs Insights, `/mysign/app`, last 7 days):

```
filter @type="REPORT"
| stats count(*) as inv, count(@initDuration) as cold, avg(@initDuration) as avgInit, pct(@duration,95) as p95
```

**Before (Oct 2026):** 3,696 invocations, 252 cold (7%), init avg 5.3s (p50
5.5s, p95 6.1s). Warm requests p95 74ms — the cold start was the latency.
Timeline of one init: `INIT_START` → `secret.loaded` +1.8s → `boot` +2.3s →
uvicorn listening +0.25s. Almost all of it was Python importing `app.server`.

**Why:** the zip carried no bytecode and `/var/task` is read-only, so every
cold start compiled ~2,000 modules from source and threw the result away. On
top of that, `import app.server` pulled in the Anthropic SDK (~0.3s alone) and
the web-push chain (pywebpush → aiohttp, requests, cryptography), which most
requests never touch — `/`, `/api/state`, static files and city lookups are
the bulk of traffic.

**Done:**
- `deploy/build.sh` compiles everything with 3.11 (`unchecked-hash`, so mtimes
  don't matter). Dependencies ship as bytecode only; `app/` and `engine/` keep
  their source for tracebacks.
- `anthropic` (engine/memory.py) and the push libraries (app/push.py) load on
  first use. `tests/test_coldstart.py` fails if either comes back at import.
- The zip ships `cities.sqlite` without `cities.tsv.gz`, so the runtime can't
  decide the index is stale and spend seconds rebuilding it in /tmp.
- uvicorn runs with `--no-access-log`: app/log.py already logs every request,
  and the access log was writing client IPs and raw query strings.
- The Web Adapter's readiness probe on `/` is already cheap (403 from the
  origin guard, or a static file), so no readiness env var is needed.

| | before | after |
| --- | --- | --- |
| `import app.server`, Lambda python:3.11 image (x86 emulated), best of 5 | 6.8s | 2.5s |
| same, local M-series, compiled from source / cached bytecode | 1.24s / 0.54s | — / 0.15s |
| zip | 47.1MB | 44.9MB |

`python tools/import_profile.py [--no-pyc] [--top 25]` reproduces the local
numbers. Re-run the query above after a deploy for the real ones.

**Next:**
1. Provisioned concurrency on the `live` alias (in progress) removes cold
   starts for the steady state; the lazy imports then cost their ~0.3s on the
   first model call per instance instead.
2. Graviton + Python 3.13 + SnapStart. Blocked only by pyswisseph, which
   publishes cp311 x86_64 wheels and nothing newer:
   - build wheels from the pyswisseph sdist with cibuildwheel in a GitHub
     workflow (`CIBW_BUILD="cp313-manylinux_x86_64 cp313-manylinux_aarch64"`,
     on `ubuntu-24.04-arm` runners or QEMU) and keep them in `deploy/wheels/`
     or a private index;
   - `build.sh`: `--find-links deploy/wheels`, platform
     `manylinux_2_28_aarch64`, `--python-version 3.13 --abi cp313`; bytecode
     compiled with 3.13;
   - stack: `PYTHON_3_13`, `ARM_64`, the `LambdaAdapterLayerArm64` layer;
   - re-run `tools/engine_check.py` and the accuracy suite against the NASA
     reference on the new build before switching;
   - then SnapStart on published versions (Python 3.12+). It snapshots after
     init, so secrets loaded by app/bootstrap.py are in the snapshot, and it
     cannot be combined with provisioned concurrency — pick one.
3. The runtime's boto3 is used (build.sh strips ours): the python:3.11 image
   carries 1.42.97 while requirements.txt asks for ≥1.43.118. Either ship our
   own (a few MB more zip) or relax the pin to what the runtime has.
