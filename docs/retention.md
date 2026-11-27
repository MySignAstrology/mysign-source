# Data retention

The user-facing version is on `/privacy`. This is the operator's version.

| What | Where | Kept | Removed by |
| --- | --- | --- | --- |
| Profile, chart, people | DynamoDB, one item per kind | while the account exists | `DELETE /api/account` |
| Conversation | DynamoDB, one item per month | while the account exists | `DELETE /api/account` — every shard |
| Check-ins, episodes, patterns, questions | DynamoDB | while the account exists | `DELETE /api/account` |
| Push subscription, VAPID | DynamoDB | until turned off or deleted | Settings, or `DELETE /api/account` |
| Sign-in code | DynamoDB | 15 minutes, single use; the item carries a `ttl` and DynamoDB deletes it (usually within 48h of expiry) | expiry, then TTL |
| Session cookie | browser | 90 days (`SESSION_DAYS` in `app/auth.py`) | sign-out, or expiry |
| Quota window | DynamoDB | rolling 24h | itself |
| Operational logs | CloudWatch `/mysign/app`, `/mysign/cron`, `/mysign/mailforward` | **90 days** (`THREE_MONTHS`, set in CDK) | retention policy |
| Legacy logs | CloudWatch `/aws/lambda/mysign-…` (pre-hardening) | forever until someone sets a policy — set 90 days by hand once | retention policy |
| Support mail | S3 `Inbox` bucket, `inbound/` | **90 days** | lifecycle rule |
| Database backups | DynamoDB PITR + AWS Backup vault | **35 days** each | rolling |
| Usage events (lake) | S3 `mysign-analytics` lake, `raw/` | **90 days** from arrival; backfilled rows 90 days from the event (expiry tag) | lifecycle rule — *not* account deletion or switch-off (see `docs/analytics.md`) |
| Daily usage totals | S3 lake, `agg/` | no expiry — no uid or anon_id | — |
| Firehose errors, Athena results | S3 lake, `errors/`, `athena-results/` | 14 / 30 days | lifecycle rule |

## What deletion does and does not do

`DELETE /api/account` removes every DynamoDB item for the user, immediately, and
drops them from the roster the scheduler iterates. The privacy page says
"immediate" and this is what makes it true.

Point-in-time recovery and the AWS Backup vault keep encrypted backups for 35
days after that. **A
restore is only ever a whole-table restore to recover from a failure affecting
everyone.** It is never used to recover an individual account someone asked us
to delete, and there is no tooling to do so. The privacy page says exactly this.

## Logs

Logs record shape, never content — enforced in `app/log.py`, tested in
`tests/test_app.py::test_a_message_body_never_reaches_a_log`. Nothing in a log
line identifies a person beyond their opaque `uid`. Log retention is therefore
an operational choice, not a privacy one; 90 days is enough to investigate a
month-old incident.

## The pilot

While a human operator can read conversations (`/api/ops/*`), that access is
disclosed on the consent screen and the privacy page. Remove the disclosure and
the routes together when the pilot ends — one without the other is a lie in
either direction.
