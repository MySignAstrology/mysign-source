# Notifications in the iPhone app (APNs)

Web Push does not fire inside the App Store shell, so there the morning note
and every other notification go out through Apple's push service. It is a
second way of delivering the same notifications, with the same settings.
Nothing about what is sent, when, or to whom is decided differently for the
app: `app/push.py send` is still the one door every notification leaves by.

Everything here is built and tested without an Apple Developer membership.
What has never happened is a real request to Apple, because there is no key
yet. The steps that need the membership are in "When enrollment clears".

**Until those steps are done the feature is off**: the routes answer 404, the
app shows no notification rows in Settings, and nothing is sent.

## How it works

```
page (app/static/index.html)        Settings → Morning notification
  │  window.MySignPush
native/bridge.js                    passes the device token to the server
  │  Capacitor plugin "NativePush"
ios/App/App/PushPlugin.swift        JSON in and out; the only place iOS is asked for permission
ios/App/App/PushCenter.swift        the device token, foreground banners, taps
                                                │ device token (hex)
app/apns.py   ◀── POST /api/push/apns ──────────┘
  ▲   │
  │   └── HTTP/2 POST https://api.push.apple.com/3/device/<token>   (ES256 JWT, .p8 key)
app/push.py send()                   one notification per device: APNs, then Web Push
  ▲
app/cron.py (morning) · app/skypush.py · the routes that already called push.send
```

1. `/api/state` tells the app (and only the app: `X-Client: ios-app`) whether
   the server can send: `native_push`. False hides the rows.
2. She taps **Turn on**, in Settings or in the one-time invitation (below).
   Only then does iOS show its permission sheet. Nothing asks at launch, at
   sign-in or anywhere else (`tests/test_apns.py` checks there is exactly one
   `requestAuthorization`, and both buttons go through it).
3. iOS returns a device token. The app posts it to `/api/push/apns` with her
   time zone, then asks for one test notification (`/api/push/test`, the same
   route the web uses) so turning it on has visible proof.
4. The morning job, the sky heads-up, a person's "your turn" and the test all
   call `push.send`. It sends to her phones through APNs and to her browsers
   through Web Push.
5. A tap opens the app at the notification's `url`, the same link the web push
   carries: `/app?from=push&pk=morning` lands on today's line, `?p=<id>` on
   that person, `?sky=events` on the events list. `push_opened` is counted the
   same way as on the web. It works when the app was closed: the tap is held
   until the page has started.
6. A notification that arrives while the app is open is shown as a banner.

### The invitation

After the reply to her first check-in the app shows one sheet, once: "Get
your morning note as a notification. One note each morning at 7:30am, your
time. You can change the time or turn it off in Settings." with **Turn on**
and **Not now**. It is the web's "add to Home Screen" sheet
(`installNudge` in `app/static/index.html`) with the app's words, and it is
remembered the same way (`ms_install_seen` on the device).

- **Turn on** does what the Settings switch does: iOS's permission sheet,
  then the token, then the test notification.
- **Not now** closes it for good. iOS is not asked. Settings stays the way in.
- It is shown only to someone the iPhone has never asked. Not when
  notifications are on, not when iOS permission is denied (Settings shows
  Blocked; this would be nagging), not when she allowed them and then turned
  them off herself.
- While the server can't send (no APNs key) it is not shown and not marked as
  seen, so it is still there for the day the feature is switched on.

### Rules worth knowing

- **Her settings are the web's.** Time (`push_time`), style (`push_style`), the
  off switch (`push_off`), her time zone, "mention my people", the sky opt-in,
  the once-a-day stamp, the crisis hold and the run's time budget are read by
  the same code as before. The only change in the scheduler is the question
  "does she have a device?", which now counts a phone (`push.has_device`).
- **One notification per device, never two on one phone.** A laptop browser
  and the iPhone app each get theirs, as two browsers always have. When the
  app took the notification, her Safari subscriptions (`*.push.apple.com`) are
  skipped for that one: on an iPhone that is the Home Screen copy of My Sign,
  and the same phone would buzz twice. They are skipped, not removed. If APNs
  delivered to nobody, Web Push goes out exactly as before. (This also skips
  Safari on a Mac for someone who has the app. See "Decisions".)
- **A device token belongs to one account at a time.** Registering it for a
  second account takes it off the first (`_apns / t-<sha256>` is the index), so
  the last person signed in on a phone never gets someone else's morning.
- **Signing out removes her phones.** Sign-out ends every session, so no
  device is left signed in. Deleting the account removes the tokens and the
  index lines.
- **Apple's answers.** 200: sent. 410: the token is dead, and is removed
  (unless she registered again after the moment Apple names). 5xx, or a
  dropped connection: one retry at once, then left for the next notification.
  429: not retried. An expired provider token: made again, one retry. Anything
  else (400 `BadDeviceToken` included): logged as `apns.failed`, token kept. A
  wrong `apns_env` answers `BadDeviceToken` for everyone, which is why that
  does not remove tokens.
- **The lock screen shows what the web push shows**, word for word: the title
  and body `push.send` was given. The title "My Sign" is left out, because iOS
  prints the app's name above every notification already. The payload carries
  the alert, a sound, the link and the kind. No uid, email, mood or name.
- **Nothing identifying in logs.** `apns.failed` has the uid, Apple's status
  and reason word, never the token.
- **Up to 5 phones an account**, newest kept, like the web.

### Settings

All in the stack's secret, read by `app/bootstrap.py` like every other secret.
No new environment variable, permission or resource in the stack.

| Secret key | Environment | Meaning |
|---|---|---|
| `apns` = `1` | `MYSIGN_APNS` | The switch. Nothing is sent without it. |
| `apns_key` | `MYSIGN_APNS_KEY` | The text of the `.p8` file. With real line breaks, with `\n`, or just the base64 body: all are read. |
| `apns_key_id` | `MYSIGN_APNS_KEY_ID` | The key's 10-character ID. |
| `apns_team_id` | `MYSIGN_APNS_TEAM_ID` | The Apple Developer team ID. |
| `apns_env` | `MYSIGN_APNS_ENV` | `sandbox` to use `api.sandbox.push.apple.com`. Anything else, or unset, is production. |

The feature is on only when the first four are all present. The bundle ID
(`app.mysign.ios`) is the topic and is fixed in `app/apns.py`.

**Sandbox or production.** A token is only good on the service it came from.
A build run from Xcode on a phone gets a sandbox token. TestFlight and App
Store builds get production tokens. So: production stays on production; set
`apns_env: "sandbox"` on staging only while Xcode-run builds are what talk to
it, and remove it when TestFlight builds do.

### Where things are kept

| Item | What | Export | Deleted with the account |
|---|---|---|---|
| `{uid} / apns` | up to 5 `{token, tz, at}` | the count, and `iphone_app_devices` (`registered_at`, `tz`); never the token | yes |
| `_apns / t-<sha256 of token>` | `{uid}`: which account holds a token | n/a | yes (`apns.forget`) |

## Testing locally

No Apple account, key, signing team or device is needed for any of this.

**Server** (the switch, tokens, the JWT, Apple's answers, the scheduler, the
bridge):

```bash
python -m pytest tests/test_apns.py tests/test_push_cron.py -q
```

Apple is never called. `apns._post` is the single request and is replaced.

**iOS** (token format, where a tap lands, a tap before the page is ready):

```bash
cd native && npm ci && npm run sync
cd ios/App && xcodebuild test -project App.xcodeproj -scheme App \
  -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO
```

**The page** (the Settings switch in each state), in a browser with a faked shell:

```bash
npx playwright test tests/e2e/push.spec.js --project=mobile
```

**A notification on screen, in the Simulator.** `simctl` can deliver a payload
file to a simulator without Apple. With the app installed on a booted
simulator:

```bash
cat > /tmp/morning.apns <<'EOF'
{"aps": {"alert": {"title": "Neptune is meeting your Mercury", "body": "Write it down. You'll want it later."}, "sound": "default"},
 "url": "/app?from=push&pk=morning", "kind": "morning"}
EOF
xcrun simctl push booted app.mysign.ios /tmp/morning.apns
```

That is exactly the JSON `app/apns.py payload()` builds. Tap the banner and the
app opens on today's line. Notification permission must have been given first
(Settings → Turn on, below).

**The whole loop by hand.** The Settings switch only appears when the server
says it can send, so a local server needs the switch and *some* key, and must
not reach Apple. The simplest way is a few lines that start the server with
`apns._post` replaced by a function that writes the payload to a file and runs
`xcrun simctl push` with it. Then:

1. `cd native && MYSIGN_API=http://localhost:<port> npm run sync`
2. Build without `CODE_SIGNING_ALLOWED=NO`, so Xcode signs it locally and the
   entitlement is in the build:
   `xcodebuild build -project App.xcodeproj -scheme App -destination 'platform=iOS Simulator,name=iPhone 17'`
   (With `CODE_SIGNING_ALLOWED=NO` the app builds and the tests pass, but the
   entitlement is not embedded in the build, so don't expect a token from it.)
3. Install, sign in, Settings → Morning notification → Turn on → Allow.

This was done on 2026-11-12 on the iPhone 17 simulator (iOS 26), signed in to
a local test account. What was seen: no permission sheet at launch; the
Settings row with **Turn on**; iOS's sheet only after tapping it; after Allow,
the simulator gave a device token, the server stored it, and "That worked.
This is how your morning notifications will look." arrived as a banner over
the open app, with the switch reading **Turn off**. With the app quit, a
sky payload showed as a banner on the home screen, and tapping it launched
the app on the Chart tab at the events list. With the app in the background,
a morning payload (title and body) tapped from the banner opened Today on the
line. `push_opened` was recorded for both. Not done there: Turn off, and the
Blocked state (both are covered by `tests/e2e/push.spec.js`).

## When enrollment clears

These are the steps left. None of them is done.

1. **App ID.** developer.apple.com → Certificates, Identifiers & Profiles →
   Identifiers → `app.mysign.ios` → tick **Push Notifications** → Save.
   (Nothing to configure under it: certificates are not used.)
2. **APNs key.** Same site → Keys → **+** → name it "My Sign APNs" → tick
   **Apple Push Notifications service (APNs)** → Continue → Register →
   **Download**. The `.p8` file can be downloaded **once**. Note the **Key ID**
   shown on that page, and the **Team ID** (Membership details). If Apple asks
   for an environment for the key, choose Sandbox & Production.
3. **Keep the key.** Put the `.p8` in the password manager. It never goes in
   the repo, in chat, or in a ticket.
4. **Secret.** In Secrets Manager, in the stack's secret (staging first), add
   `apns_key` (the whole text of the `.p8` file), `apns_key_id`, `apns_team_id`
   and `apns: "1"`. For staging, while testing from Xcode, also
   `apns_env: "sandbox"`.
5. **Deploy** (or wait for the functions to restart: secrets are read at cold
   start). Check `secret.loaded` in the log lists the four `MYSIGN_APNS*` names.
6. **Xcode.** Set the signing team on the App target. Signing & Capabilities
   should already show **Push Notifications** (from `App/App.entitlements`,
   `aps-environment = development`). Leave the value alone: Xcode writes
   `production` into TestFlight and App Store builds by itself.
7. **Real-device test**, on an iPhone, against staging:
   - [ ] Fresh install: no permission sheet at launch or at sign-in.
   - [ ] After the reply to the first check-in, the invitation sheet appears. **Not now**: no iOS sheet, and it never comes back (check in again tomorrow).
   - [ ] On a second fresh install, **Turn on** in the invitation: iOS sheet → Allow → "That worked…" arrives.
   - [ ] Settings shows Morning notification with **Turn on**.
   - [ ] Turn on → iOS sheet → Allow → "That worked…" arrives; the switch says Turn off.
   - [ ] `push.apns.registered` in the log; no `apns.failed`.
   - [ ] Lock the phone, `POST /api/push/test` again from the app: it shows on the lock screen with only the expected words.
   - [ ] Tap it from the lock screen: the app opens on Today.
   - [ ] Set the time to the next quarter hour: the morning note arrives within 15 minutes of it, once. `cron.pushed` shows `devices: 1`.
   - [ ] Tap the morning note with the app closed: Today, on the line, and `push_opened` (`kind: morning`) is recorded.
   - [ ] A person notification (`?p=`) opens that person, app closed and app in the background.
   - [ ] Turn off: nothing arrives at the next test; `{uid}/apns` is gone.
   - [ ] Turn on, then Sign out: nothing arrives; sign in as a second account on the same phone and turn on: only the second account's notifications arrive.
   - [ ] iOS Settings → Notifications → My Sign → off: the app's Settings shows **Blocked**, and its sheet opens the iPhone's Settings. Allow again and come back: the switch recovers by itself.
   - [ ] With My Sign also on the Home Screen from Safari with web notifications on: one notification, not two.
   - [ ] Delete the account: `_apns` has no line for the phone.
   - [ ] A TestFlight build against a server **without** `apns_env: "sandbox"` receives the test notification (production APNs).
8. **Production.** The same four keys in the production secret, no `apns_env`.
9. **App Privacy and policy.** See "Privacy" below before submitting.

## For App Review

Where a reviewer sees notifications, for the review notes (the founder's
`appstore/review-notes.md` is the source of truth; this is the paragraph to
add there). On a new account: sign in, finish the birth details, and on
Today tap a mood and **Check in**. After the reply, a sheet offers the
morning notification; **Turn on** shows iOS's permission prompt, and a test
notification arrives at once. The same control is always at **Settings →
Morning notification** (Turn on / Turn off, the time, and what it says). The
seeded review account already has check-ins, so there the invitation appears
after that day's check-in if the account has not checked in yet; otherwise
use Settings. The app never asks for notification permission at launch.

## Privacy

- The app sends the device token to our server, and our server sends it to
  Apple with each notification. Apple also sees the notification's content
  (unlike Web Push, APNs payloads are not end-to-end encrypted).
- `PrivacyInfo.xcprivacy` already declares **Device ID**, linked, not used for
  tracking, for app functionality (PR #152). That covers the token. No change.
- `/privacy` has not been changed in this work. It needs a sentence before
  the feature is switched on. Proposed, not yet reviewed:

  > **Notifications in the iPhone app.** If you turn on notifications in the
  > iPhone app, the app sends us a device token: an identifier Apple gives
  > your phone for this app. We keep it with your account and send it to
  > Apple's push notification service, together with the text of each
  > notification, so that Apple can deliver it. We use it for nothing else.
  > Turning notifications off in the app, signing out or deleting your
  > account removes the token from our servers.

  And in the list of companies that process data for us: "Apple (Apple Push
  Notification service): device token and notification text, for delivery."

## Not done, and why

- **No request has ever gone to Apple.** There is no key. The request's shape
  is checked against Apple's documentation by tests, not by Apple.
- **No real device.** Token registration, banners, cold-start taps and
  foreground banners were seen on the iOS Simulator only.
- **No background mode.** `UIBackgroundModes` → `remote-notification` is only
  for silent pushes that wake the app, and none are sent. Declaring a mode the
  app does not use is its own review question.
- **No badge count, no notification actions, no rich media.**
- **`apns.failed` has no alarm.** It is logged like `push.failed`. A broken key
  would show there on every send.

## Decisions for the founder

1. **Safari on a Mac.** Someone with the iPhone app *and* web notifications in
   Safari on a Mac stops getting them on the Mac while the phone is receiving,
   because a Mac's Safari subscription looks the same as an iPhone Home Screen
   one. The alternative is to accept two notifications on one iPhone for
   people who have both the Home Screen copy and the app.
2. **When to switch it on in production**: with the 1.0 build, or earlier on
   staging only.
