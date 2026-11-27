# The iPhone app

A Capacitor shell around `app/static/index.html`. The web app stays the product.
`build-www.mjs` copies it into the bundle and adds `bridge.js`, which points its
`/api` calls at the server, carries the session as a bearer token, and sends
links to pages on the website to an in-app browser. The session token lives in
the Keychain (`ios/App/App/TokenStore.swift`).

## Run it

Needs Xcode. The packages come from Swift Package Manager, so there is no
CocoaPods step.

```bash
cd native && npm ci && npm run sync && npx cap open ios
```

`npm run sync` builds for mysignapp.com. To use a local server, run
`MYSIGN_API=http://localhost:8497 npm run sync`. A local server must have
`MYSIGN_DEV_AUTH=1` so sign-in codes appear without email.

Run `npm run sync` again after any change to `app/static/index.html` or
`bridge.js`, or the app keeps the old page.

## The App Review account

Reviewers can't receive a code, so one address signs in with a fixed one. Put
`review_email` and a 12-digit `review_code` in the stack's secret. The code box
takes digits only, and exactly 6 or 12 of them. Then fill the account:

```bash
MYSIGN_TABLE=<table> MYSIGN_SECRET_ARN=<arn> AWS_PROFILE=mysign AWS_REGION=us-east-2 python tools/seed_review_account.py
```

Run it again just before each submission, so the check-ins run up to yesterday.
The account has 100 days of made-up check-ins and memory, and 3 people, on a
free Plus plan. Each pattern on it passed `memory.verify`.

## Not built yet

These need the Apple Developer account:

- Notifications for real. They are built (`docs/ios-push.md`): the app asks
  iOS only when she turns them on in Settings, and the server sends through
  APNs. But there is no APNs key yet, so the server says it can't send and the
  app shows no notification rows.
- Selling Plus for real. In-app purchase is built (`docs/ios-iap.md`) and
  tested against Xcode's local StoreKit file (`ios/App/App/MySign.storekit`),
  but the products don't exist in App Store Connect yet, and the server sells
  nothing until `apple_iap` is set.
- Sign in with Apple, and universal links for invites and shared results.

Signed-out analytics are off in the app. The `/e` collector answers no preflight,
so `bridge.js` drops those events. The sign-in funnel won't count app users until
the collector handles CORS.

The server needs `NativeCors` (`app/edge.py`) live before a build can reach
production.
