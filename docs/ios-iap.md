# In-App Purchase (iPhone app)

Plus is sold inside the iPhone app through Apple's StoreKit 2 and nowhere else
(App Review 3.1.1). On the web it is still Stripe (`docs/billing.md`). Both
write the same plan through `engine/plan.py`.

Everything here runs today without an Apple Developer membership: Xcode's local
StoreKit file stands in for App Store Connect. The steps that need the
membership are in "When enrollment clears" at the end.

## What is sold

One subscription group, **My Sign Plus**. Family Sharing is off.

| Product ID | Level | Price (US) | Trial |
|---|---|---|---|
| `mysign_plus_annual` | 1 | $59.99 a year | 7 days free |
| `mysign_plus_monthly` | 2 | $9.99 a month | 7 days free |

The product IDs are the Stripe lookup keys, so one table maps a purchase to a
plan for both providers: `PRODUCTS` in `engine/plan.py`.

**Changing a price.** The amounts are in `PRICES` in `engine/plan.py` (web copy)
and in each provider's dashboard. `native/ios/App/App/MySign.storekit` is the
local copy for Xcode, and `tests/test_apple.py` fails if it disagrees with
`PRICES`. The iPhone paywall has no amount typed into it: it shows the App
Store's own price string, in the buyer's currency.

## How it works

```
page (app/static/index.html)        the paywall, Settings, the delete warning
  │  window.MySignStore
native/bridge.js                    passes signed transactions to the server
  │  Capacitor plugin "PlusStore"
ios/App/App/PlusStorePlugin.swift   JSON in and out
ios/App/App/PlusStore.swift         StoreKit 2: products, purchase, updates, restore
                                                │ signed transaction (JWS)
app/apple.py  ◀── POST /api/billing/apple/sync ─┘   from the app
              ◀── POST /api/billing/apple/notify    from Apple (Server Notifications V2)
  │
engine/plan.py  provider_update → grant        source: "apple"
```

1. `/api/state` tells the app (and only the app: `X-Client: ios-app`) whether
   the shop is open, the product IDs, and her **appAccountToken**: a UUID made
   once per account (`apple.account_token`). StoreKit signs it into the
   purchase and every later notification, which is how a purchase finds its
   account.
2. She taps a plan. StoreKit shows Apple's sheet. The result is a transaction
   signed by Apple (a JWS).
3. The app posts it to `/api/billing/apple/sync`. The server checks the
   signature chain up to the pinned Apple Root CA - G3, the bundle ID and the
   product ID, then grants Plus until the paid period ends plus one day.
4. **Only after the server answers** does the app call `finish` on the
   transaction. If the server can't be reached, StoreKit hands the same
   transaction back at the next launch, and the app tries again.
5. Renewals, refunds, expiry, grace periods and billing retry arrive from Apple
   at `/api/billing/apple/notify`. Each notification is applied once
   (`notificationUUID`), and an older one never overwrites a newer one.

### Rules worth knowing

- **One subscription, one account.** The first account to sync a subscription
  holds it. Another signed-in account gets a 409. If the first account is
  deleted, Restore purchases moves the subscription to whoever restores it.
- **Never two silent grants.** Each provider's subscription is on its own line
  (`entitlements` in the store). The plan is one record. If Stripe and Apple
  are both live on one account, `billing.double_subscription` is logged,
  `/api/state` carries `plan.double`, and Settings tells her. One ending never
  removes what the other still pays for.
- **A web subscriber on iPhone keeps Plus and is offered nothing.** `offer` is
  false for anyone who already has Plus, and the bridge re-checks right before
  opening Apple's sheet. In the other direction `/api/billing/checkout` already
  refuses anyone on Plus.
- **No web checkout in the app.** The in-app paywall has no link, button or
  text about buying on the web, and `#planManage` (Stripe's portal) stays
  hidden there.
- **Deleting an account** does not cancel an Apple subscription, because Apple
  doesn't let an app do that. The client warns her before the final
  confirmation, in the app and on the web.

### Settings

| Setting | Where | Meaning |
|---|---|---|
| `apple_iap` = `1` (`MYSIGN_APPLE_IAP`) | the stack's secret | Opens the shop in the iPhone app. Off by default. Like Stripe's `billing` switch, it also starts the free limits (people cap, circles, Plus depth) for everyone, on the web too: `plan.for_sale()` is true when either shop is open. |
| `MYSIGN_APPLE_LOCAL_TESTING=1` | a laptop's environment only | Accepts transactions signed by Xcode's local test certificate. Off by default, **refused on Lambda whatever it is set to**, and not readable from the secret. |

Production accepts both the `Production` and `Sandbox` App Store environments,
because App Review buys in Sandbox against the production server. The `Xcode`
environment is accepted only through `MYSIGN_APPLE_LOCAL_TESTING`.

## Testing locally

No Apple account, signing team or device is needed for any of this.

**Server** (signature chain, each notification type, Stripe and Apple together):

```bash
python -m pytest tests/test_apple.py tests/test_billing.py -q
```

**StoreKit** (purchase, trial, renewal, restore, refund, expiry, plan change),
on a simulator against `MySign.storekit`:

```bash
cd native && npm ci && npm run sync
cd ios/App && xcodebuild test -project App.xcodeproj -scheme App \
  -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO
```

**The page** (paywall copy, buttons, the delete warning), in a browser with a
faked store:

```bash
npx playwright test tests/e2e/iap.spec.js --project=mobile
```

**By hand, end to end, in the simulator:**

1. Start a local server that accepts Xcode's certificate:
   `MYSIGN_APPLE_IAP=1 MYSIGN_APPLE_LOCAL_TESTING=1 ./run.sh` (port 8420).
2. `cd native && MYSIGN_API=http://localhost:8420 npm run sync && npx cap open ios`
3. In Xcode, check **Product → Scheme → Edit Scheme → Run → Options → StoreKit
   Configuration** says `MySign.storekit`, then Run on a simulator.
4. Sign in, send four chat messages (the fourth is refused and shows the
   paywall) or open Settings, and buy. **Debug → StoreKit → Manage
   Transactions** in Xcode renews, refunds and expires subscriptions on demand.

To check the server against a real Xcode-signed transaction without the app,
`TEST_RUNNER_MYSIGN_JWS_OUT=/tmp/x.jws xcodebuild test …` writes one from the
purchase test; `apple.transaction(open('/tmp/x.jws').read())` then refuses it
by default and accepts it with `MYSIGN_APPLE_LOCAL_TESTING=1`.

Xcode never sends Server Notifications, so `/notify` is covered only by the
server tests until there is a Sandbox.

## When enrollment clears

These are the steps left. None of them is done.

1. **Agreements.** App Store Connect → Business: the Paid Apps Agreement must
   be Active (bank and tax forms). Subscriptions can't be created before that.
2. **App record** with bundle ID `app.mysign.ios`.
3. **Subscription group** `My Sign Plus`, then the two products with the IDs,
   prices, names, descriptions, levels and 7-day free introductory offer in
   the founder's App Store product spec (`appstore/iap.md` in the company HQ
   folder, the source of truth). Family Sharing off.
   **Product IDs are permanent. Check them before clicking Create.**
4. **Server notifications.** App Store Connect → the app → App Information →
   App Store Server Notifications: set the Production and the Sandbox URL to
   `https://mysignapp.com/api/billing/apple/notify`, **Version 2**. Staging can
   take the Sandbox URL instead while testing.
5. **Keys.** This build needs **no** shared secret and no In-App Purchase key:
   everything it trusts arrives signed and is verified against Apple's root.
   An In-App Purchase key (Users and Access → Integrations → In-App Purchase)
   is needed only to *call* the App Store Server API, for example to ask for a
   test notification or look a subscription up. If that is added later, the
   key ID, issuer ID and `.p8` go in the stack's secret, never in the repo.
6. **Billing grace period** (optional, App Store Connect → Subscriptions →
   Billing Grace Period). The server already honours it if it is turned on.
7. **Switch on.** Add `apple_iap: "1"` to the staging secret, deploy, and in
   Xcode set the signing team and add the In-App Purchase capability.
8. **Sandbox test** on a real iPhone with a Sandbox Apple Account (Users and
   Access → Sandbox): buy monthly, see Plus unlock, see `SUBSCRIBED` arrive at
   `/notify` (the log line is `billing.apple_notify`), let it renew (Sandbox
   months last minutes), cancel, refund from the Sandbox account page, Restore
   on a second device, and delete the account to see the warning. Then use
   "Request a Test Notification" if the API key exists.
9. **Review.** The first subscriptions must be submitted together with an app
   version. Attach a screenshot of the in-app paywall to each product.
10. **Production.** `apple_iap: "1"` in the production secret.

## Not done, and why

- **Online revocation (OCSP) of Apple's signing certificates** is not checked.
  Apple's own server library treats it as optional.
- **The scheme's StoreKit file reference** was written by hand and could not be
  confirmed from the command line (only Xcode's Run uses it). Step 3 of the
  manual test above checks it. The tests don't depend on it.
- **No consumable packs, no US link-out to web checkout.** Out of scope for v1.
