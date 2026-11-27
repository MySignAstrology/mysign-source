# Sign-in by text (SMS)

Phone sign-in works the same way as email sign-in. She enters a number, gets a
six-digit code, and the code opens the same kind of session. Texts go out
through **AWS End User Messaging SMS** (`pinpoint-sms-voice-v2`) from a US
toll-free number in us-east-2. The feature is **off** until a human finishes the
setup below. Until then, the app answers a phone request with 503 "Text sign-in
isn't available yet — use your email", and the sign-in screen asks only for
an email.

## State of the account (checked read-only, 28 Oct 2026)

| Check | Result |
|---|---|
| `describe-account-attributes`, us-east-2 | works. `ACCOUNT_TIER = SANDBOX`: texts can go only to verified destination numbers |
| `describe-spend-limits` | TEXT/VOICE/MEDIA/NOTIFY: enforced **$1/month**, max $1. Raising it needs a support case |
| `describe-phone-numbers`, configuration sets, protect configurations, registrations | none |
| Opt-out lists | `Default` exists. AWS appears to have created it on first access; nothing else was created |
| us-east-1 | **AccessDenied: explicit deny from an org SCP** (`p-k87vce3a`). us-east-1 can't be used. us-east-2 is fine |

## How it fits together

- **The app** (`app/auth.py`) sends only when `MYSIGN_SMS_ORIGIN` is set.
  `app/bootstrap.py` fills it from the secret key `sms_origination_identity`.
  - `MYSIGN_SMS_CONFIG_SET` names the configuration set on every send.
  - `MYSIGN_DOMAIN` adds the WebOTP line `@mysignapp.com #123456`, so Safari
    and Chrome can offer the code on our own site only.
  - The message is "My Sign: your code is 123456. It works for 15 minutes.
    Reply STOP to opt out." plus that line: 101 GSM characters, one billed part.
- **The stack** (`deploy/mysign_stack.py`, `_sms`) does its part only with
  `-c smsEnabled=true`:
  - grants `sms-voice:SendTextMessage` on this account's numbers, pools and
    protect configurations in us-east-2, and on the `mysign-sms` configuration set
  - sets `MYSIGN_SMS_CONFIG_SET` and `MYSIGN_DOMAIN`
  - alarms on `auth.sms_cap`
- **By hand** (`deploy/sms/setup-sms.sh`): the configuration set, the protect
  configuration, the number and the carrier registration. A number is bought,
  not declared, and registration is a weeks-long review. Neither belongs in a
  deploy.

## Setup

1. `deploy/sms/setup-sms.sh` (profile `mysign`; pass the alarm topic ARN to send
   delivery failures there instead of to CloudWatch Logs `/mysign/sms`). It:
   - creates configuration set `mysign-sms` (default type TRANSACTIONAL) with a
     **failures-only** event destination. Delivery events carry the number, so
     successes are not kept.
   - creates a protect configuration that **allows SMS to the US only**, blocks
     every other country, and attaches it to the configuration set
   - after a y/N prompt, requests a US toll-free number (TRANSACTIONAL, SMS)
     on the `Default` opt-out list
   - prints the number's ARN, the registration commands and the support-case steps
2. **Toll-free verification.** Use `create-registration --registration-type
   US_TOLL_FREE_REGISTRATION` with the fields the script prints. The use case is
   ONE_TIME_PASSCODES: "one-time sign-in codes". The opt-in is DIGITAL_FORM:
   "user enters their own phone number on the sign-in screen and requests a
   code". The website is https://mysignapp.com. A **screenshot of the sign-in
   screen is a required attachment**. Then associate the number and submit.
   **Carriers take about 2–4 weeks.** SMS stays off until the registration is
   COMPLETE. An unverified toll-free number's texts are blocked.
3. **Support case:** move SMS out of the sandbox, and raise the monthly text
   spend limit from $1 (for example to $50). Then run
   `set-text-message-spend-limit-override`.
4. **Go live:** see the checklist below.

## Costs

Check current pricing at <https://aws.amazon.com/end-user-messaging/pricing/>.
At the time of writing, a US toll-free text costs **about $0.0083+ per message
part** (the AWS fee plus a carrier fee of roughly $0.0025), and the number costs
about **$2/month**. A sign-in is one part, so it costs about 100× an email
sign-in (SES is $0.10 per 1,000).

| Volume | Per month |
|---|---|
| number only | ~$2 |
| 1,000 text sign-ins | ~$10–11 |
| worst day at the cap (500) | ~$5/day, bounded by the monthly spend limit |

## Fraud and cost controls

SMS pumping is the real risk. A script asks for codes to premium-rate numbers
and earns a cut of each text. The limits are layered so the cheapest check
fails first:

| Control | Where | Limit |
|---|---|---|
| Resend interval | per number (`sends[]` on the code record) | 30 s |
| Per number | atomic counters `sms-n-{hash}-{hour/day}` | 3/hour, 10/day |
| Per IP | `sms-ip-{hmac(ip)}-{hour}`. The IP is `CloudFront-Viewer-Address`, else the rightmost X-Forwarded-For entry, else the socket address. Only a keyed hash is stored, with a 2 h TTL | 5/hour |
| Global | `sms-day-{date}`; `MYSIGN_SMS_DAILY_CAP` | 500/day. Above it, texts are refused with "use your email instead", `auth.sms_cap` is logged at error level, and `SmsCapAlarm` fires to the alarm topic |
| Code attempts | same machinery as email | 5 tries, 15 minutes |
| Countries | the app accepts US numbers only (10-digit NANP, area code and exchange not starting 0/1, no N11). The protect configuration blocks everything but the US at AWS, which also catches Canada and the Caribbean, since they share +1 | US |
| Spend | AWS monthly text spend limit | whatever the case grants |
| Enumeration | whether a number belongs to another account is revealed only after the code is proven (`/api/account/identifier/verify` → 409) | — |

Logs never carry the number. `phone` is in `app/log.py` `REDACT`;
`auth.sms_limited` carries an 8-character hash prefix; API failures log only
the exception type and the AWS error code.

When `SmsCapAlarm` fires, look in `/mysign/app` for `auth.sms_limited` by
`scope` and hash. One IP hash or a run of number hashes means pumping. Email
sign-in keeps working the whole time.

## API

- `GET /api/auth/channels` returns `{"email": true, "sms": bool}`. The sign-in
  screen asks for "Phone or email" only when `sms` is true.
- `POST /api/auth/request` accepts `{email}`, `{phone}` or `{id}` (auto-detected)
  and returns `{"sent": true, "channel": "sms"|"email"}`.
- `POST /api/auth/verify` accepts the same identifier plus `{code}`. On success
  it logs `auth.verify.ok` with `channel` and `new_user`.
- Signed in: `GET /api/account/identifiers`, then
  `POST /api/account/identifier/request` and `/verify` add a phone (or an
  email) to the current account. Settings → "Phone number" uses them. The
  account holds one phone; adding a new one replaces the old.
- `DELETE /api/account` removes `_auth/phone-*` if it still points at her, along
  with any phone code in flight. `GET /api/export` includes `phone`.

## Go-live checklist

- [ ] Registration status **COMPLETE** (`describe-registrations`)
- [ ] Out of the sandbox; the spend limit is raised and the override is set
- [ ] Protect configuration: US = ALLOW, every other country BLOCK, attached to `mysign-sms`
- [ ] Secret: `"sms_origination_identity": "<phone-number ARN>"`
- [ ] Deploy with `-c smsEnabled=true` (and `domainName` set, for the WebOTP line)
- [ ] `GET /api/auth/channels` → `"sms": true`
- [ ] Sign in with your own phone:
  - the text arrives
  - the code autofills on iOS Safari / Android Chrome
  - STOP opts you out, after which a request fails cleanly with the email hint
- [ ] Settings → add a phone to an email account, then sign in with the phone →
      same account
- [ ] `SmsCapAlarm` exists and points at the alarm topic. For a test, set
      `MYSIGN_SMS_DAILY_CAP=1` on a stage, not production
- [ ] Privacy and terms pages mention texts (done on 28 Oct 2026)
