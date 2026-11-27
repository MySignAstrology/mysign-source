# Data inventory

What My Sign stores, where it goes, how big it gets, and what it costs.
Written from the code on branch `deep-compatibility-v2` as of 27 October 2026
(including `engine/daily.py`, added that day) and from read-only checks of the
live AWS account (889002527942, us-east-2). The code is moving. When a new
`save(` call lands, add a row here.

Companion docs: `docs/retention.md` (the operator's retention table),
`/privacy` (the user-facing version).

---

## 1. Where the data is stored

**Everything durable is in one DynamoDB table** (`mysign-TableCD117FA1-…`, on-demand,
us-east-2). Every user record has `pk = <uid>` (`u` + 12 hex characters) and
`sk = <kind>`. The value is a JSON string in attribute `v`, with an optional
numeric `ttl`. Counters live under `sk = <kind>#ctr` as native numbers, and
sets use `pk = _set#<name>`, one item per member (`engine/store_dynamo.py`).

Live table state on 27 Oct 2026 (keys only, values not read):

- Item count: 3. These are `_study/funnel-2026-10-27`, `_auth/code-<hash>` and `_auth/users`.
  `_auth/users` is the **legacy single-item roster**, and it is still present in
  production. `auth.all_users()` and `auth.forget()` both handle it.
- PITR: enabled, 35 days. TTL: enabled on attribute `ttl`. Deletion protection: on.
- SSE: default (AWS-owned key). No customer-managed KMS key.
- AWS Backup plan `Backup`: daily at 05:00 UTC, kept 35 days, in vault `mysignBackupVault…`.
- No DynamoDB stream, so nothing else gets a copy.

Other places data lives:

| Where | What | Retention | Removed on account deletion? |
|---|---|---|---|
| DynamoDB (above) | everything in §2 | until account deletion, except TTL'd kinds | yes, all `pk=<uid>` items, plus `_auth/email-*`, `_auth/phone-*`, `code-*` |
| DynamoDB PITR | whole-table continuous backup | 35 days | no, ages out |
| AWS Backup vault | daily full copy of the table | 35 days | no, ages out |
| CloudWatch Logs `/mysign/app`, `/mysign/cron` | structured JSON lines: event, uid, lengths, status | 90 days | no (keyed by uid, no content by design; see §5) |
| CloudWatch Logs `/aws/lambda/mysign-Fn…`, `/aws/lambda/mysign-Cron…` | legacy groups from before the explicit groups existed | **never expires** (0 bytes stored today) | no |
| CloudWatch `/mysign/mailforward` | forwarder logs (only once a domain is live) | 90 days | no |
| X-Ray (app Lambda, `Tracing.ACTIVE`) | request traces: paths, timings | 30 days (X-Ray default) | no |
| CloudWatch custom metrics (EMF) | counts, latency by `path`, `status`, `tier` | 15 months (CloudWatch default) | n/a. But `path` includes ids (see §5) |
| S3 `Inbox` bucket (not yet created, it needs a domain) | raw inbound support mail | 90 days lifecycle | no |
| Forward target `som@xii-studio.io` (CDK `forwardTo` default) | every support email, re-sent | Gmail, indefinite | no |
| SES | sign-in emails only (address + 6-digit code). Account in **sandbox**; only verified identity is `som@xii-studio.io` | SES keeps no message bodies. Bounce/complaint events go to the SNS alarm topic and include the recipient and the subject, which contains the code | n/a |
| AWS End User Messaging SMS (only once `sms_origination_identity` is set; see `docs/sms.md`) | sign-in texts only (phone number + 6-digit code), from a US toll-free number. Only *failed* deliveries are logged (configuration set `mysign-sms` → CloudWatch `/mysign/sms`, or the alarm topic), and those events include the destination number | `/mysign/sms`: 30 days. AWS's own send records: per its terms | no, ages out |
| SMS opt-out list `Default` | numbers that replied STOP | indefinite (carrier/legal requirement) | no |
| SES account suppression list | addresses that bounced or complained | indefinite | no |
| Anthropic API | see §4 | 30 days, or up to 2 years if flagged under the Usage Policy | no |
| PagerDuty | red-tier page: uid + first 120 chars of the crisis message | PagerDuty account retention (not configured by us) | no |
| Web push services (Apple, Google FCM, Mozilla, Microsoft) | encrypted notification payloads (RFC 8291). They see endpoint, timing and size, not content | theirs | subscription deleted with the account |
| Apple Push Notification service (only once `apns` is set; see `docs/ios-push.md`) | the iPhone app's device token and each notification's title, body, link and kind, over TLS. Not end-to-end encrypted: Apple can read the text | theirs | token deleted with the account, on sign-out, and when Apple reports it gone (410) |
| Browser | `ms_session` cookie: httpOnly, HMAC-signed `{uid, ver, exp}`, 90 days. No localStorage use | 90 days | invalidated (the account it points at is gone) |
| SQS `CronDLQ` | failed EventBridge events (no user data) | 14 days | n/a |

### 1a. The analytics lake (`mysign-analytics` stack)

Usage events also go to S3 through Kinesis Data Firehose, so reports read
Athena instead of walking every user. The full design is in `docs/analytics.md`;
the rules every event passes are `app/events.py`.

- **Signed-in app events**: `/api/activity` and server-side `activity.track()`
  write to DynamoDB as before *and* forward the same cleaned events, with the
  uid, to the stream `mysign-events` (when `MYSIGN_EVENTS_STREAM` is set).
  Nothing goes for an account with analytics switched off, or before consent
  (except the uid-less `auth.verified` count).
- **Anonymous events**: the collector Lambda (`deploy/collect/`, CloudFront
  `/e`) takes website visits and pre-sign-in app screens. It never accepts a
  uid. It stores a random `anon_id` the browser generated (16–40 url-safe
  characters), country from `CloudFront-Viewer-Country`, device class, OS and
  browser family (the User-Agent is parsed and dropped), referrer domain,
  landing path (ids and every query parameter but `utm_*` stripped) and UTM
  tags. **No IP, no full URL, no UA string, no free text.** `Sec-GPC: 1` or
  `DNT: 1` drops `anon_id` and `session_id`; bots are dropped.
- **Funnel steps** are also sent as uid-less `server` events `funnel.<step>`,
  and a sign-in code being sent as `auth.code_requested` (channel only, no
  uid; the `anon_id` the page sent, unless GPC/DNT).
- **Lake only, never in DynamoDB**: `heartbeat` (every 30 s while the app is
  open, in front, and touched in the last five minutes — time in app, with the
  screen she is on) and `push_sent` (the push kind: morning, person, sky, …;
  sent for each push a device accepted, from the cron too).
- **Also forwarded**: `push_opened` (the app was opened from a push, with its
  kind — the push link carries `from=push&pk=<kind>`, which the app strips on
  load), `paywall_shown` (which limit), `subscription_started` / `_renewed` /
  `_canceled` (tier and where it came from). The full list is
  `docs/analytics.md` §2a.
- **The device id** (`anon_id`) lives in the browser's `localStorage`
  (`ms_anon`), shared by the website and the app on the same origin, and is
  sent with the sign-in calls so a first visit can be joined to the sign-up
  (after consent). Not made or sent under GPC/DNT, or on a device where
  analytics was switched off.

| Prefix (bucket `mysign-analytics-lake…`, SSE-S3, public access blocked, TLS only) | Contains | Retention |
|---|---|---|
| `raw/events/dt=/source=/` | Parquet, one row per event: the envelope, incl. uid / anon_id | **90 days** (lifecycle) |
| `raw/events-json/dt=/source=backfill/` | one-off copy of pre-lake `act-*` (`tools/backfill_events.py`) | ≤ 90 days *from the event* (expiry tag) |
| `agg/` | daily totals. **No uid, no anon_id** | none |
| `errors/` | records Firehose couldn't convert (same fields as raw) | 14 days |
| `athena-results/` | query output | 30 days |

Firehose and collector logs: `/mysign/analytics-firehose`, `/mysign/collect`, 90 days.

---

## 2. Every kind stored

Sizes are compact JSON as `engine/store_dynamo.py` writes it. They were measured
by running the real app locally on the file backend: one onboarding, one
check-in, four messages, one connection, one rollover. **Note that
`json.dumps` escapes non-ASCII characters**, so Cyrillic, CJK and emoji take
3–6× the bytes shown below. See §3.

### Per user (`pk = <uid>`)

| sk (kind) | Contains | Sensitivity | Size / growth | TTL | Export | Deleted |
|---|---|---|---|---|---|---|
| `account` | email, phone (if added), created_at, consented_at, consent_version, ai_consent, tz, session_ver, push_style | **email, phone** | ~200 B fixed | none | partial: email, phone, created_at, consented_at. `tz`, `push_style`, `consent_version`, `ai_consent` omitted | yes |
| `profile` | name, birth_date, birth_time, birth_city, birth_local, birth_time_known, birth_ut `[y,m,d,hour,lat,lon]`, tz, country, houses, rulerships, `people[]` (`{name, relation, birth:null}`) | **birth data**, third-party names | ~350 B + ~50 B per person. `people` only grows (`memory.learn_people`) | none | yes | yes |
| `about` | hobbies, interests, skills, passions (catalog ids from `engine/about.py`, or up to 3 of her own words per list, ≤32 chars each, `<>` and control characters stripped), `dream` (≤120 chars), `nudge_off`, `updated_at`. Written by `PUT /api/about` (sign-up step and Settings) | personal interests, free text | ≤~1.5 KB fixed (lists capped at 12/8/5/3) | none | yes (`about`) | yes |
| `salience_bands` | per-chart thresholds | low (derived from chart) | ~300 B fixed | none | yes (`other`) | yes |
| `reading-{houses}-{rulers}` | natal reading with AI opening paragraph, name, birth_local, chart facts | birth data | ~4.5 KB per tradition used | none | **only the first one**. Others are popped and dropped (`server.py` `readings`) | yes |
| `sky-{date}-{houses}` | computed transits to her chart for one day | low (derived) | ~2.1 KB per active day. **Never pruned** | **none** | excluded (derived) | yes |
| `thread_shards` | ordered list of shard keys | low | ~25 B per shard | none | via `thread` | yes |
| `thread-{YYYY-MM}[.n]` | messages `{id, role, text, at, band, tier, tools, chips}` incl. crisis messages | **highest: the conversation** | ~600 B per exchange (user + reply). New shard at 150 KB or month end | none | yes, as `thread` (every shard) | yes |
| `thread`, `thread_months` | legacy pre-shard layout, migrated on first touch | as above | — | none | — | yes |
| `checkin-{date}` | mood 1–10, text ≤500 chars, time | **mood** | 60–600 B per check-in day | none | yes | yes |
| `episodes` | one per active day: date, mood, summary, `her_words` (verbatim quotes), people, topics, decision, safety tier, sky_state | **high**: AI-written memory, quotes, third-party names, crisis tier | ~0.9–1.5 KB per active day, **one item, unbounded** | none | yes | yes |
| `loops` | ≤30 things she said were coming: `what`, `when`, suggested `ask`, kind, said_on, days raised, status. Written by memory extraction, never from a red-tier day | **high** (her plans, third-party names) | ~250 B each, capped at 30 | none | `other` | yes |
| `patterns` | verified patterns (claim, support, p-value) | medium | <5 KB, overwritten weekly | none | yes | yes |
| `patterns_run`, `rolled_through` | ISO dates | low | 12 B | none | `patterns_run` in `other`. `rolled_through` excluded | yes |
| `questions` | last 500 messages: `text[:300]`, topics, people, is_question, moon_sign | **high** (her words) | ~450 B each ASCII, ~1.8 KB Cyrillic/CJK. Capped at 500 **by count** | none | yes | yes |
| `mentions` | `{date: [aspect keys surfaced]}` | low | ~170 B per active day, **never pruned** (only 21 days are read, `salience.py:68`) | none | `other` | yes |
| `act-{YYYY-MM-DD}` | app usage: `{at, ty, n, sc, s, p}` — event type (screen/tap/change/open/hide/error/server; heartbeats and `push_sent` are lake only), control or screen id, session id, allowlisted enumerable props (`app/events.py` `PROPS`). **No typed text**. Also copied to the analytics lake (§1a) | low (behavioural) | ~80 B/event, capped 800/day | **90 days** | yes, as `app_usage` | yes; also on Settings opt-out (the lake copy is not: §6) |
| `actrate-{0..9}#ctr` | per-minute ingestion counter for `/api/activity` | none | 10 B | 2 min | excluded | yes |
| `metrics` | last 500 outbound tags: msg_id, sent_at, band, score, tier, has_callback, chars, reply `{latency_s, words, chars}` | low–medium (behavioural) | ~200 B each. Capped at 500, about 100 KB | none | `other` | yes |
| `last_outbound` | last assistant message id | low | 10 B | none | `other` | yes |
| `connections` | ≤100 people: name, relation, kind, born, birth_ut, birth_date/time/city, time_precision, scores, drivers, frictions, overlays, contacts, themes | **other people's birth data** + relationship | ~1.9 KB per row. Capped at 100 rows, about 190–300 KB | none | yes | yes |
| `compat-{cid}` | full compatibility report + AI reading | third-party derived | ~25 KB each, ≤100 per user | none | `other` | yes. Also on removing that person |
| `life_events` | ≤50 `{kind, date, note≤200}` | sensitive-ish (dated life events) | ≤ ~15 KB | none | yes | yes |
| `reports` | ≤200 flagged replies `{id, reason≤300, at}` | medium (her words about a reply) | ~400 B each ASCII. 200 × non-ASCII reasons (~2 KB) ≈ 400 KB, **can cross the ceiling** | none | `other` | yes |
| `rectify` | birth-time rectification result | low | <2 KB | none | `other` | yes |
| `push` | device subscriptions: endpoint, p256dh, auth, tz | **device bearer capability** | ~500 B per device, **no device cap** | none | count only (deliberate) | yes |
| `apns` | iPhone app device tokens: token, tz, at (when registered). Up to 5, newest kept | **device identifier** (Apple's address for this app on her phone) | ~150 B per device, ≤5 | none. Removed on sign-out and when Apple answers 410 | count, plus `iphone_app_devices` (`registered_at`, `tz`). Never the token | yes |
| `pushed-{date}` | `{at, state, band, body, sent, pruned, failed}`. Includes the notification text | low–medium | ~250 B per push day, **never pruned** | **none** | `other` | yes |
| `daily-{date}` | the day's line, theme, why | low | ~200 B | 3 days | `other` | yes |
| `daily_seen` | last 90 lines shown | low | ≤ ~5 KB | none | `other` | yes |
| `plan` | tier, source, until, granted_by, changed_at | billing state | ~150 B | none | `other` | yes |
| `entitlements` | per billing provider (`stripe`, `apple`): until, product | billing state | ~120 B | none | `other` | yes |
| `apple` | appAccountToken (a random UUID), original transaction id, product, expiry, status, auto-renew, environment | billing state; no card or Apple Account details | ~300 B | none | `other` | yes |
| `funnel_seen` | which funnel steps were counted (legacy list, still written and read) | low | ~60 B | none | `other` | yes |
| `funnel_once#ctr` | one counter per funnel step, capped at 1: the atomic "counted already" | low | ~60 B | none | excluded | yes |
| `msgcount-{date}#ctr` | free-tier daily count | low | ~40 B | 3 days | excluded | yes |
| `quota-m-{n}#ctr`, `quota-d-{n}#ctr` | burst / daily counters | low | ~40 B | 2 min / 2 days | excluded | yes |

### Global

| pk / sk | Contains | Retention | Deleted with account |
|---|---|---|---|
| `_auth / email-{sha256(email)[:24]}` | the uid | until deletion | yes. Unsalted hash: anyone with table read can confirm whether a given address has an account |
| `_auth / phone-{sha256(e164)[:24]}` | the uid. Phone sign-in, US numbers in E.164. The plaintext number is only on `account.phone`, as email is on `account.email` | until deletion | yes. Same unsalted-hash caveat as email; the US number space is small enough to enumerate |
| `_auth / code-{hash}` | sha256(code+secret), expires (15 min), `sends[]` timestamps. Phone codes are `code-phone-{hash}`, so the two kinds never share a record | `ttl` 1 h (+ up to 48 h sweep lag; reads treat it as gone) | yes |
| `_auth / code-{hash}#ctr` | attempt counter | 1 h | yes |
| `_auth / sms-n-{hash}-{hour\|day}#ctr` | texts sent to one number (limit 3/h, 10/day) | 2 h / 2 days TTL | no, ages out. Key is the phone hash |
| `_auth / sms-ip-{hmac(ip)}-{hour}#ctr` | texts requested from one IP (limit 5/h). Keyed HMAC, never the address | 2 h TTL | no, ages out. Not linked to a uid |
| `_auth / sms-day-{date}#ctr` | all texts today vs `MYSIGN_SMS_DAILY_CAP` (500). No identifiers | 3 days TTL | n/a (anonymous) |
| `_auth / users` | **legacy** roster list (present in prod) | until emptied | uid removed |
| `_set#users / {uid}` | roster, one item per user | until deletion | yes |
| `_study / funnel-{date}` | integer counts per funnel step (legacy, read-modify-write; read, no longer written). No uids | forever, ~150 B/day | n/a (anonymous) |
| `_study / funnel-{date}#ctr` | the same counts, atomic. `funnel.report()` sums both | forever, ~150 B/day | n/a (anonymous) |
| `_activity / _act#{date}#ctr` | per-day counters: total events, `people` (distinct active users), `screen:<id>`, `tap:<id>`, `server:<id>`. No uids | 90-day TTL, ~2 KB/day | n/a (anonymous) |
| `_report / {report}#{date}` | daily rollup summaries written by `deploy/rollups` (analytics.md §7): counts and rates, segments under 5 people folded into "(other)". No uids or anon_ids | forever, 9 items/day, usually a few KB each (capped at 350 KB) | n/a (anonymous) |
| `_ops / op-{uid}` + `_set#operators / {uid}` | console operators: `role`, `label` (free text an admin typed, ≤80 chars), `added_by`, `added_at`, `changed_by/at` | until removed by an admin | **no** — an operator's own account deletion leaves the row; it grants nothing without a live session |
| `_audit / {iso-ts}#{rand}` | one console access: `at`, `op` (operator uid; null for the break-glass token), `role`, `via` (`session`/`token`), `action`, `subject` (the uid looked at, if any), `params` (days, tier, role, flagged/reported, whether a search ran — never the search text). No content, no names, no emails | `ttl` **400 days** | **no** — it is the record that her data was looked at, kept for her protection as much as ours |
| `_apns / t-{sha256 of device token}` | `{uid}`: which account holds an iPhone device token, so a phone that changes hands moves with it. The key is a hash, never the token | until the token is removed | yes (`apns.forget`) |
| `_auth / dev_secret`, `_push / vapid` | local development only (Lambda fails closed / reads Secrets Manager) | — | — |

**Verified against `app/server.py`.** `delete_account` calls `store.delete_all(uid)`,
which deletes every item under `pk=uid` (paged Query, so thread shards and
`compat-*` are all included). It then calls `auth.forget` (roster + legacy roster)
and `auth.forget_identities`, which deletes `email-`/`phone-` (only if still
mapped to this uid), `code-`/`code-phone-` and their `#ctr`. `export_everything` walks
`store.keys(uid)`. It skips only `sky-*`, `thread*` (exported separately and in
full), `quota*`, `rolled_through`, `#ctr`, and `push` and `apns` (counts only; `apns`
also as `iphone_app_devices`, without tokens). The gaps:

- only one `reading-*` is exported
- `account.tz`, `push_style`, `consent_version` and `ai_consent` are not exported
- writes that race the delete can recreate items with no account (§6)

---

## 3. The 400 KB item limit

`store_dynamo._check_size` refuses any write over 380,000 characters of JSON.
Because `json.dumps` defaults to `ensure_ascii=True`, every non-ASCII character
is stored as `\uXXXX`: 6 bytes, or 12 for an emoji. Measured on a 300-character
message:

| Text | Characters | JSON bytes | UTF-8 bytes |
|---|---|---|---|
| Russian | 300 | 1,532 | 546 |
| Chinese | 300 | 1,802 | 900 |
| English + emoji | 300 | 1,127 | 525 |

| Kind | Bounded? | When it hits 380 KB, for a heavy user | What happens then |
|---|---|---|---|
| `questions` | 500 by count, **not by bytes** | English: never (~225 KB). **Russian/Chinese/Arabic: ~200 questions, about a week at 30 msgs/day** | `remember_question` runs unguarded in `/api/message` **before** the reply. Every message after that is a 500, and the quota/plan claim is already spent. **Permanent lockout.** |
| `episodes` | **no** | ~1.2 KB/day ASCII, so ~300 active days (~10 months daily). Non-ASCII quotes: ~4–5 months | `upsert_episode` raises inside `rollover`. The error is caught and logged, the model call is already paid, and memory silently stops forever. Up to 3 wasted extraction calls per day |
| `connections` | 100 rows | ~1.9–3 KB/row puts 100 people at 190–300 KB. Non-ASCII names and notes push it toward the limit | add/edit/rescore of people fails |
| `mentions` | **no** | ~170 B/day, so ~6 years | turn building fails (it is loaded every turn). Only 21 days are needed |
| `profile.people` | **no** | ~50 B per name, so thousands of names. In practice it only grows big through the heuristic extractor (any capitalised word) | onboarding edits and birth time fail. Every name also goes into every chat context block |
| `push` | **no device cap** | ~500 B/device, so ~700 devices | subscribe fails. Low risk |
| `metrics` | 500 | ~100 KB | fine |
| `daily_seen` | 90 | ~5 KB | fine |
| `life_events` | 50 | ~15 KB | fine |
| `reports` | 200 | ~80 KB ASCII. ~400 KB if the reasons are non-ASCII | the 200th report fails. Low |
| `thread-*` shards | spill at 150 KB (measured on the same escaped JSON) | never | fine. This is solved |
| `compat-{cid}` | one item each | ~25 KB | fine |
| `sky-*`, `pushed-*`, `checkin-*` | one item per day | fine per item, but the **item count** grows without end | storage only (§7) |

---

## 4. What goes to Anthropic

Every call goes over the commercial API, with the key from Secrets Manager.
Which model answers which call is set in one place, `engine/models.py`: Plus
chat on `claude-sonnet-5-5` (with the server-side refusal fallback), free chat
and the background calls (safety classifier, memory extraction, suggested
replies, sky-style push) on `claude-haiku-5-5`, one-off readings and the weekly
pattern proposals on `claude-sonnet-5-5`. Each is overridable by environment
variable (`MYSIGN_MODEL_CHAT_PLUS`, …). The retention notes below were written
for Opus 5.5; confirm them for Sonnet 5.5 and Haiku 5.5 before relying on them. No call sets `inference_geo`. Under Anthropic's commercial
terms, API inputs and outputs are **not used for training** by default.
Anthropic deletes them **within 30 days**, or keeps them up to 2 years (and
classification scores up to 7 years) if its automated systems flag them under
the Usage Policy. Opus 5.5 is not a "covered model" for the separate
covered-model retention rule. Sources:
privacy.claude.com articles 7996866 ("How long do you store my organization's data?",
updated 1 July 2026), 7996868 ("Is my data used for model training?") and 15425996.

| Call | Code | What is sent | When |
|---|---|---|---|
| Chat reply (agent loop, streamed, up to 6 rounds) | `engine/agent.py`, `server.py /api/message` | system prompt (cached); context block: date, safety tier, today's check-in (mood + text), sky facts, names + relations of people she's mentioned, up to 6 saved people with scores, her "about you" lists and `dream` (labels only, no placements; `engine/about.py context_lines`), last 7 days of episode summaries + one verbatim quote each, up to 4 retrieved older episodes, patterns, recurring topics/people counts; **last ~9 messages verbatim**; the new message. Tool results as needed: her natal placements, sky for a date, a saved person's compatibility summary (name, relation, scores, themes; not their raw birth date), memory search results (summaries + quotes), ranking of saved people | every non-crisis message |
| Safety classifier | `engine/safety.py classify` | the message text only | every message the regex pre-filter didn't already mark red |
| Memory extraction | `engine/memory.py extract_day` | **the whole day's transcript, both sides**, plus the check-in JSON | once per finished day, on her next app open |
| Weekly pattern proposal | `memory.propose_patterns` | **every episode she has ever had**, as indented JSON (summaries, quotes, people, topics, decisions, moods, tiers, sky states) | weekly, once she has ≥10 episodes |
| Natal reading | `engine/reading.py` | **her first name** + chart facts (Sun/Moon/rising, houses, aspects) | once per tradition, at first reading |
| Compatibility reading | `engine/compat.py narrate` | the other person's **name** + kind + computed scores, themes, contacts | once per person, when she opens the written reading |
| Suggested replies | `engine/chips.py suggest` | **her latest message and the reply to it**, nothing else | after every non-crisis reply |
| Push (only if `push_style == "sky"`) | `app/cron.py _compose` | the push context block (as chat, with top 3 aspects) | daily at 07:30 local. The default `line` style uses a written library and **makes no model call** |

**Never sent:** email address, phone number, uid, raw birth date/time/place (only computed chart
facts), push endpoints. **Never sent to the model at all:** red-tier crisis
messages after the regex pre-filter fires. Those get a canned reply. A message
that only the model classifier marks red has already been sent to Anthropic
once, for classification.

---

## 5. Logs: the "shape not content" rule

`app/log.py` replaces any field whose **name** is in `REDACT` (text, message,
body, reply, summary, note, email, name, birth, code, token, …) with
`<N chars redacted>`. Lines carry `uid`, event names, lengths, status and timings.
`tests/test_app.py::test_a_message_body_never_reaches_a_log` enforces it for
the message path.

Gaps found:

1. **`error=str(exc)[:300]` is not scrubbed** (`log.error`). `memory.extract.failed`
   logs the exception from `messages.parse(..., output_format=Extraction)`. A
   pydantic `ValidationError` prints `input_value=…`, which is the model's
   extraction: her summary and her quotes. The same pattern is in
   `agent.failed`, `rollover.day.failed`, `compat.narrate.failed`,
   `reading.prose.failed` and `cron.compose.failed`. Those carry Anthropic SDK
   errors, which normally describe the request shape and not its content, but
   nothing guarantees it.
2. **`log.exception` tracebacks** (`request.failed`) include the final exception
   message, same exposure.
3. **EMF metric dimension `path=request.url.path`** includes ids:
   `/api/connections/{cid}/deep|reading`, `PATCH/DELETE /api/connections/{cid}`,
   `/api/ops/user/{uid}`. That creates one custom metric per person per route,
   which is a cost and turns a uid into a metric name. The code comment says
   this must not happen.
4. **PagerDuty (`app/alerts.py red_tier`)** sends `uid` and the **first 120
   characters of the crisis message** to a third party that the privacy page did
   not list. It is deliberate for triage. It is now disclosed on `/privacy`, but
   PagerDuty's retention is not set by us.
5. SES bounce/complaint notifications go to the alarm SNS topic and carry the
   recipient address and a subject that contains the sign-in code. Low risk,
   since codes last 15 minutes.
6. The `print(f"[auth] code for {norm}: {code}")` dev path is local-only
   (`dev_auth_allowed` is false on Lambda). OK.

Clean: `push.failed` (error type only), `auth.unavailable` (SES / SMS error code only),
`auth.sms_limited` (8-char hash prefix only; `phone` is in `REDACT`),
`onboarded` (country, time_known), `compat.narrate.ungrounded` (planet/sign
names), all `ops.*` events.

---

## 6. Deletion and export gaps

- **Race with deletion.** `delete_all` is a Query then a batch delete. A chat
  stream that is still running persists the assistant turn, `metrics` and
  `mentions` *after* the response started, and the cron writes `pushed-{date}`.
  Either can land after the delete and leave orphan items under a uid with no
  account. Nothing ever lists them, so nothing ever removes them.
- **Removing a person** deletes their `connections` row and `compat-{cid}`. Their name
  stays in `profile.people`, in episodes, in questions and in the thread.
  There is no per-person scrub, and no way to delete one message or one
  episode. The privacy page now says this.
- **Export** drops all but one `reading-*`, omits `account.tz`/`push_style`,
  and is a single buffered JSON body. A Function URL streaming response is
  capped (20 MB by default), so a multi-year heavy user (thread ~6.6 MB/yr,
  and `indent=1` adds ~20%) eventually cannot export.
- **Not reachable by deletion** (disclosed): logs (90 d, uid only), X-Ray (30 d),
  PITR + Backup (35 d), Anthropic (30 d / 2 y if flagged), PagerDuty, support
  mail (S3 90 d + the forward inbox), SES suppression list.
- **The analytics lake isn't reached by deletion or switch-off.** Parquet rows
  can't be deleted one at a time cheaply, so `raw/` ages out at 90 days instead.
  After an account is deleted its uid maps to nothing (the `_auth/email-` item
  and roster entry are gone); after switch-off nothing new is sent. The privacy
  page says both. `agg/` has no ids, so it isn't personal data.
- **No dormant-account policy.** An account nobody opens is kept forever.

---

## 6a. The consent screen and the code disagree

The consent screen (`app/static/index.html` `#vConsent`, edited 27 Oct) now says
"**Nobody on our team reads your conversations** — except if something you write
suggests you could be in danger … or if you ask us to look at something".
`/api/ops/user/{uid}` now refuses any thread without a red flag or a report,
for every role, and the console is per-person (`safety` role or above to open a
thread) with every view written to `_audit` (§2 Global). The shared
`MYSIGN_OPS_TOKEN` remains as break-glass until removed from the secret.

The new AI-consent checkbox (`#aiOk`, stored as `account.ai_consent` +
`consent_version`) satisfies Apple 5.1.2(i)'s in-app consent. It says the
birth details of people you add are sent to Anthropic. The code sends only
their name and computed compatibility, never raw birth data, so the screen
over-discloses. That is safe, but it is inconsistent with `/privacy`.

---

## 7. How much data, and what it costs

### Per user per year

"Active day" means she opened the app. Thread bytes ≈ 600 B per exchange.

| Profile | Assumption | Thread | sky + pushed + checkin + mentions + episode | Fixed (reading, profile, people, questions, metrics) | **≈ per year** |
|---|---|---|---|---|---|
| Light / free | 3 msgs/day, 240 active days | 0.43 MB | 240 × ~4 KB = 0.96 MB | ~0.15 MB | **~1.5 MB** |
| Typical Plus | 10 msgs/day, 300 days, 10 people | 1.8 MB | 300 × ~4 KB = 1.2 MB | ~0.6 MB (10 × 26 KB people + 225 KB questions + 100 KB metrics) | **~3.6 MB** |
| Heavy | 30 msgs/day, 365 days, 30 people | 6.6 MB | 1.5 MB | ~1.1 MB | **~9 MB** |

The `sky-*` cache alone is ~0.75 MB/user-year of pure derived data with no TTL.
Non-ASCII users store 2–3× this until `ensure_ascii=False`.

### At scale (after one year, blended average ~3 MB/user)

On-demand us-east-2 pricing assumed: storage $0.25/GB-month, PITR $0.20/GB-month,
backup storage $0.10/GB-month. DynamoDB backups are full copies, so 35 dailies
hold about 35× the table. <!-- verify against the Backup bill: if AWS Backup
deduplicates DynamoDB backups the vault cost drops ~35×. -->

| Users | Table | Table $/mo | PITR $/mo | 35 daily backups $/mo | **Storage total $/mo** |
|---|---|---|---|---|---|
| 1,000 | 3 GB | $0.75 | $0.60 | ~$10.50 | **~$12** |
| 10,000 | 30 GB | $7.50 | $6 | ~$105 | **~$120** |
| 100,000 | 300 GB | $75 | $60 | ~$1,050 | **~$1,185** |

The 35-day backup vault on top of 35-day PITR is ~90% of the storage bill.

### Per active user per month (compute + model)

Model assumptions, from the code:

- System prompt (8.8 KB) + tool definitions (3.8 KB) ≈ 3.2k tokens. That is
  cached, and it is over Opus 5.5's 512-token minimum, so the cache works.
- Context block ≈ 1.3k tokens, history (≤9 msgs) ≈ 0.7k, message ≈ 50.
- Tool rounds average 1.3 calls per turn. Output (medium-effort adaptive
  thinking + a ≤4-sentence reply) ≈ 800 tokens per call.
- Prices: $4/M in, $20/M out, $0.20/M cache read, $5/M cache write (5-minute TTL).

| Per unit | Calculation | Cost |
|---|---|---|
| Chat message (main loop) | 3.0k uncached in ($0.012) + 4.2k cache read ($0.001) + 1.0k out ($0.020) | ~$0.033 |
| Safety classifier | ~250 in + ~150 out | ~$0.004 |
| **Per message** | | **~$0.037** |
| Cache write per session (>5 min gap) | 3.2k × $5/M | ~$0.016 |
| Memory extraction per active day | ~1.5k in + ~1.5k out | ~$0.036 |
| Weekly patterns | all episodes: 90 eps ≈ 30k tokens → ~$0.18/week. 365 eps ≈ 120k tokens → ~$0.54/week | grows without bound |
| One-offs | natal reading ~$0.04. Each compatibility reading ~$0.05 | — |
| Sky-style push | ~1.5k in + 400 out | ~$0.014/day (default `line` style: $0) |

| Profile | Anthropic | Lambda (1 GB; chat ≈ 15 s billed while streaming) | DynamoDB requests | **Total ≈** |
|---|---|---|---|---|
| Free: 3 msgs × 20 days | 60 × .037 + 20 × .016 + 20 × .036 + ~$0.4 patterns ≈ **$3.7** | ~$0.03 | ~$0.03 | **~$3.8** |
| Plus: 10 msgs × 25 days | 250 × .037 + 50 × .016 + 25 × .036 + ~$0.4–2 ≈ **$11–13** | ~$0.10 | ~$0.10 | **~$11–13** |
| Heavy: 30 msgs × 30 days | 900 × .037 + ~$1 + $1.1 + ~$2 ≈ **$37** | ~$0.30 | ~$0.35 | **~$38** |
| Abuse ceiling: 120/day (`quota.DAILY_MAX`) | 3,600 × .037 ≈ **$135+** | ~$1 | ~$1.4 | **~$140** |

DynamoDB requests are small but not free. Every message rewrites, in whole:

- the current thread shard twice (average ~75 KB, so ~75 WRU each)
- `questions` (up to ~225 KB)
- `metrics` twice (~100 KB)

That is ~400–600 WRU per message (~$0.0003). Logs (~2–3 KB per message) are
negligible. SES is $0.10 per 1,000 sign-ins. A text sign-in costs roughly
100× that (~$0.01 per US toll-free message with carrier fees, plus ~$2/month
for the number); the daily cap bounds the worst day at ~$5. See `docs/sms.md`.

Two things scale badly with the user count, not with any one user:

- **The cron scans the whole roster every 15 minutes, serially**
  (`profile` + `push` GetItem per user, 96× a day). At ~5 ms a read, the 300 s
  Lambda runs out at ~25–30k users, before anyone is pushed.
- **The weekly pattern job** sends a user's whole history every week.
