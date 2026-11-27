# Getting My Sign onto the App Store

How the PWA at mysignapp.com becomes an iOS app in the App Store, step by step,
with an owner on every step. It sits alongside `docs/launch-checklist.md`, which
covers the AWS side of launch day. This doc covers Apple's side.

Owners: **F** = founder, **E** = engineer, **D** = design and content.
Checked against Apple's guidelines and developer-forum reports as of
2026-10-28. Apple changes the rules often. Re-read the guideline numbers below
in the live App Store Review Guidelines before you submit.

---

## 0. The decision: wrap the PWA in Capacitor and add a thin native layer

| Option | Time to store | Reuses `index.html` | Review risk | Verdict |
|---|---|---|---|---|
| **PWA only** (Add to Home Screen) | 0 | 100% | none | Keep it as the web funnel, but it can't be the plan. It has no store listing, no search traffic, no IAP, and iOS web push only works after the user adds the app to her Home Screen. |
| **Capacitor shell + native plugins** | 3–5 weeks | ~95% | Medium: 4.2 and 4.3 | **Recommended** |
| **Native SwiftUI rewrite** | 3–5 months | 0% | Lowest for 4.2 | Not now. You'd rewrite 2,300 lines of tuned Velvet Sky UI before learning anything from the market. |

Why Capacitor:

1. **All of the product is already in one self-contained client.** The sky
   gradient, the frosted cards and the People orbits run fine in WKWebView. A
   rewrite would throw away the part users actually see.
2. **Guideline 4.2 is passed on native behaviour, not native code.** Reviewers
   reject wrappers that feel like Safari in a frame. They pass ones with native
   push, native purchase, haptics, share sheets, widgets and offline handling.
   Capacitor gives each of these as a plugin (section 3).
3. **It leaves room for later.** A SwiftUI widget extension and Live Activities
   can be added to the same Xcode project in v1.1 without touching the web layer.

**Rule for the build:** bundle the web assets *inside* the app (`webDir`) and
call `https://mysignapp.com/api/*`. Don't ship `server.url` pointing at the live
site. An app that loads a remote URL is the textbook 4.2.3 rejection, and it
also stops the app from opening offline.

---

## 1. Apple Developer Program enrollment (start today, it gates everything)

- **Enroll as an Organization, not an Individual.** On an individual account
  your legal name appears as the seller. A person's name next to an 18+ app that
  holds birth data looks less trustworthy, and transferring the app to a company
  later is painful.
- **Steps (F):**
  1. Make sure the legal entity exists (LLC is fine), and that its website
     mysignapp.com and a domain email (`hello@mysignapp.com`) both work. Apple
     checks the website.
  2. Look up or request a **D-U-N-S number** through Apple's D-U-N-S lookup
     tool, not D&B's paid expedite. It's free. It usually takes about 5 business
     days, but can take up to 30. After it's issued, Apple takes another 1–2
     days to sync it.
  3. Enroll at developer.apple.com/programs/enroll using an Apple Account with
     2FA ($99/yr). Apple calls to verify signing authority, so answer unknown
     numbers that week.
  4. In App Store Connect, go to **Agreements, Tax, and Banking** and sign the
     *Paid Applications* agreement, then add bank and tax details (W-9).
     **Subscriptions can't be created until this is Active.** People forget it
     and lose a week.
  5. Apply for the **App Store Small Business Program**. The commission drops
     from 30% to 15% while revenue is under $1M a year. Apply before launch:
     the rate applies from the following fiscal month.
- **Fallback:** if the D-U-N-S number hasn't arrived by the end of week 1, keep
  building against a free Apple ID (simulator plus personal device). Don't
  switch to an individual account.

---

## 2. Review-guideline risks specific to My Sign, and how to answer each

### 4.3(b) Spam: "there are already enough astrology apps" (highest risk)
Apple explicitly lists astrology, horoscopes, fortune telling and zodiac
reports as a saturated category. Developer forums show astrology apps rejected
over and over, and one astrology dating app (Struck) needed about 10 submissions.
**This is the rejection to plan for, not hope against.**

What gets us through:
- **Position the app as a daily journal and relationship companion that uses
  astrology as its lens, not as a horoscope app.** Use category **Lifestyle**
  as primary and **Health & Fitness** as secondary. Don't pick Entertainment.
- **Subtitle and first screenshot lead with what's unique:** People (daily
  compatibility that changes as her and their skies move), Memory (patterns from
  her own check-ins), and a chat that remembers.
- **Review notes (copy, paste and adapt):**
  > My Sign is a private daily journal and relationship companion, not a horoscope
  > generator. Differentiators: (1) every sky fact is computed live from the Swiss
  > Ephemeris for the user's exact birth time and place, with no canned sign
  > horoscopes; (2) "People": the user adds people she knows, and the app tracks
  > how each relationship feels day to day through her own check-ins (Closer /
  > Steady / Strained) next to the day's transits; (3) "Memory": after weeks of
  > mood check-ins, the app shows patterns found in her own data. Demo account and
  > a 60-second walkthrough video are attached.
- **Attach a demo video** (screen recording of the golden path) and a **demo
  account** that already holds 30+ days of seeded check-ins and 3 People. That
  way the reviewer sees Memory and the People trend lines rather than an empty
  first day. (E: add a `tools/seed_review_account.py`.)
- **If rejected:** reply in Resolution Center the same day, restating the
  differentiators with screenshots. If it's rejected a second time, file an
  **App Review Board appeal**. Don't rename the app or strip out the astrology.
  Forum reports show that doesn't help, and it would hollow out the product.

### 4.2 Minimum functionality (wrapped web app)
The native features in section 3 are the answer. The checklist a reviewer will
feel: native push permission prompt, native purchase sheet, haptics on mood
check-in and People check-in, a native share sheet for the chart card, an
offline screen that isn't a WebView error, no visible browser chrome, no
pinch-zoom, and no links that open Safari to do core things.

### 3.1.1 In-App Purchase
Plus ($9.99/mo) unlocks digital content inside the app, so **on iOS it must be
sold through StoreKit IAP**. Don't show a Stripe button or a "subscribe on our
website" prompt in the iOS build (see 3.1.1(a) below for the US exception).
- Honest-pricing requirements that Apple *also* enforces (3.1.2): show price,
  period and what's included next to the buy button, plus links to Terms and
  Privacy, and say "cancel anytime in Settings". A free trial must state the
  price after the trial.
- **US link-out (3.1.1(a) / court-ordered):** since the 2025 Epic contempt
  order, US-storefront apps may link to web checkout. Apple currently takes no
  commission on those purchases, but a district court is now setting a rate
  (Apple proposed 15%). **v1: IAP only.** Revisit a web-checkout link for US
  users once the rate is settled. It is a margin play, not a launch blocker.

### 5.1.1 Privacy, data collection, and account deletion
- **Account deletion in-app (5.1.1(v)):** already real (`DELETE /api/account`
  in `app/server.py`). Make sure it is reachable from Settings in **two taps**
  and labelled "Delete account". Tell the reviewer where it is in the notes.
- **Subscription and deletion:** when a subscriber deletes her account, show a
  line that Apple subscriptions are cancelled in iOS Settings, with a button
  that opens `https://apps.apple.com/account/subscriptions`.
- **Other people's birth data:** People holds data about third parties. Keep
  the current rule (only what the user enters), and add one sentence at the
  People "add" form: "Only add details they'd be comfortable with you having.
  We never contact them." This heads off 5.1.2 questions.
- The privacy policy URL is `/privacy`. It must be reachable without signing in.

### 4.8 Sign in with Apple
Only *required* if the app offers a third-party or social login (Google,
Facebook…). Email-code sign-in alone doesn't trigger it. **Ship it anyway in
v1:** it's one tap instead of switching to Mail for a code, and it's the biggest
drop-off fix available on iOS. Implementation is in section 3.

### Age rating
Apple's 2025 system has 4+, 9+, 13+, 16+ and 18+, and the questionnaire now asks
about AI features, wellness content and in-app purchases. The product is 18+
(consent screen), so **answer the questionnaire honestly (it includes an AI chat
that generates text), then use "set a higher minimum age" to select 18+.** That
keeps the App Store rating in line with our Terms. It costs some top-of-funnel
under-18 traffic we don't want anyway.

### 1.4.1 / health claims
Don't call mood check-ins "mental health tracking" or imply diagnosis. Crisis
handling stays as is (`engine/safety.py`, `engine/crisis.py`). Mention in the
notes that the app routes users at risk to crisis lines. Reviewers see AI chat
plus wellness content and look for this.

---

## 3. Engineering work, in order (E)

### 3.1 Capacitor shell (2–3 days)
```
npm i @capacitor/core @capacitor/cli @capacitor/ios
npx cap init "My Sign" app.mysign.ios --web-dir app/static
npx cap add ios
```
- Bundle ID **`app.mysign.ios`**. Pick it once and never change it.
- `index.html` today uses relative `/api/...`. Add a single `API_BASE` constant:
  `''` on web, `'https://mysignapp.com'` when `window.Capacitor?.isNativePlatform()`.
- **Auth inside the shell:** the session is an httpOnly cookie today. From
  `capacitor://localhost` that is a cross-site cookie, and WKWebView will drop it.
  Add bearer-token support to the backend: `/api/auth/verify` returns
  `{"token": auth.sign(...)}` when the request has `X-Client: ios`, and
  `auth.current_user` accepts `Authorization: Bearer <same signed value>`.
  Store the token in the Keychain via `@capacitor-community/secure-storage` (or
  `@capacitor/preferences` for v1). Add the `capacitor://localhost` origin to CORS.
- `sw.js` doesn't run in the bundled shell. Guard registration with
  `if (!window.Capacitor)`.
- Status bar and splash screen in wine-black `#0F0B0D`. Disable bounce on the
  root, keep it on scroll containers. `viewport-fit=cover` with safe-area insets
  (already used for the PWA).

### 3.2 Native push via APNs
**Built, and tested with unit tests and the iOS Simulator. How it works, how
to test it and the steps left for the day enrollment clears are in
`docs/ios-push.md`.** It is off until the APNs key is in the stack's secret.

iOS web push doesn't fire inside a Capacitor WebView. The morning push is the
habit loop, so this can't slip.
- A small plugin of our own (`PushPlugin.swift`, `PushCenter.swift`), no
  third-party SDK. iOS is asked for permission only when she turns
  notifications on in Settings, never at launch.
- `POST /api/push/apns {token, tz}`, stored beside the web-push subscriptions
  as `apns`.
- `app/apns.py`: token-based auth with a `.p8` key, HTTP/2 via `httpx` + `h2`,
  a JWT signed ES256 and reused for 50 minutes, topic `app.mysign.ios`.
  `push.send()` sends to phones through APNs and to browsers through Web Push.
- The `.p8`, Key ID and Team ID go in the existing Secrets Manager secret.
- A one-time invitation after the first check-in reply ("Get your morning
  note as a notification"), the same sheet the web uses for Add to Home
  Screen. iOS's prompt follows only if she taps Turn on.

### 3.3 Subscriptions: StoreKit 2 + App Store Server API (3–4 days)
**Built, and tested against Xcode's local StoreKit file. How it works, how to
test it and the App Store Connect steps left are in `docs/ios-iap.md`.**

**App Store Connect setup (F + E):**
- Subscription group **"My Sign Plus"** (annual is level 1, monthly level 2):
  - `mysign_plus_monthly`: $9.99/mo, **7-day free trial** introductory offer
  - `mysign_plus_annual`: $59.99/yr (≈ $5/mo; a 49.96% saving, so "save 49%" or no percentage, never "save 50%"), 7-day trial
  - The IDs match the Stripe lookup keys, so `plan.PRODUCTS` maps both providers.
- Localized display name and description, plus a review screenshot of the paywall.
- **First-time subscriptions must be submitted together with an app version.**
  Attach them to the 1.0 submission, or they stay in "Ready to Submit" forever.

**Client:** a thin StoreKit 2 plugin in the shell (`PlusStore.swift`,
`PlusStorePlugin.swift`), no third-party SDK:
- `appAccountToken` = a UUID kept for her account (`apple.account_token`),
  so server notifications map to the uid.
- "Restore purchases" on the paywall and in Settings (Apple requires it).
- A transaction is finished only after the server has confirmed it.

**Server (source of truth stays `engine/plan.py`, which is already
provider-agnostic):**
- `POST /api/billing/apple/notify`: **App Store Server Notifications V2**
  endpoint (`app/apple.py`). The `signedPayload` JWS is verified with
  `cryptography` against the pinned Apple Root CA - G3, then bundle ID and
  environment. `SUBSCRIBED`, `DID_RENEW`, `OFFER_REDEEMED` and the like grant
  Plus with `source="apple"` through `plan.provider_update`; `EXPIRED`,
  `REFUND` and `REVOKE` end it at once. Idempotent on `notificationUUID`.
- `POST /api/billing/apple/sync {signedTransaction}`: called by the client right
  after purchase or restore, so access unlocks before the webhook lands.
- Configure the production **and** sandbox notification URLs in App Store Connect.
  Use the "Request a Test Notification" endpoint to confirm the wiring.
- **The free tier is untouched:** 3 chat messages/day, and the morning push is
  never gated. The paywall appears only when the 4th message is refused, and on
  a quiet "Plus" row in Settings. No countdown timers or fake scarcity. That
  matches the honest-pricing rule and 3.1.2.

### 3.4 Sign in with Apple (1 day)
- Plugin `@capacitor-community/apple-sign-in`. New endpoint
  `POST /api/auth/apple {identityToken, nonce}`: verify the JWT against Apple's
  JWKS (`https://appleid.apple.com/auth/keys`), `aud` = bundle ID, and match
  the nonce. Key the account on `sub`, keep the (possibly relay) email for codes.
- Handle "Hide My Email" relay addresses: add mysignapp.com as a sending domain
  under *Sign in with Apple for Email Communication*, or relay mail bounces.
- On account deletion, call Apple's **token revocation** endpoint (required
  since 2022 for Sign in with Apple accounts).

### 3.5 Native touches that clear 4.2 (1–2 days)
- `@capacitor/haptics`: light impact on mood check-in, success notification on
  a People check-in, selection tick on tier reveal.
- `@capacitor/share`: share the chart card (`/api/card/chart.svg` rendered to
  PNG) and the "Today with {name}" card, via the native share sheet.
- Offline: cache the last `/api/today` and `/api/connections` responses with
  `@capacitor/preferences` and show them with "Last updated 7:30" when there's
  no network, instead of a blank screen.
- App icon set (1024×1024 no alpha) generated from `brand/` via `@capacitor/assets`.
- **v1.1 (not a launch blocker):** WidgetKit widget with today's line and the
  top People mover. Widgets are the strongest native-only retention surface
  and a strong answer to 4.3.

### 3.6 Privacy manifest and nutrition labels
- Add `PrivacyInfo.xcprivacy`, declaring required-reason APIs used by plugins
  (UserDefaults: `CA92.1`). Most Capacitor plugins ship their own manifest. Check
  with Xcode → Product → Archive → "Generate Privacy Report".
- **App Privacy answers (App Store Connect):** all data is *Linked to You*,
  *not used for tracking*:
  | Data type | Collected | Purpose |
  |---|---|---|
  | Contact Info: Email | yes | App Functionality (sign-in) |
  | User Content: Other (birth date/time/place, People entries, chat, check-ins) | yes | App Functionality, Product Personalization |
  | Health & Fitness: no (mood is a journal entry, not HealthKit). Declare it under User Content | — | — |
  | Purchases | yes | App Functionality |
  | Identifiers: User ID | yes | App Functionality |
  | Usage Data: Product Interaction (funnel events) | yes | Analytics |
  | Diagnostics: Crash Data | yes, if Sentry/Crashlytics added | App Functionality |
- **No ATT prompt.** We don't track across apps. Don't add an ad SDK in v1.
  Keeping "Data Not Used to Track You" on the listing is a selling point with this audience.

---

## 4. The store listing (D, F)

### Name, subtitle, keywords (character limits: 30 / 30 / 100)
- **Name:** `My Sign: Astrology & People` (27)
- **Subtitle:** `Daily compatibility, your sky` (29). It leads with People, the selling point.
- **Keywords** (comma-separated, no spaces, no words already in name or
  subtitle, no competitor names; that's 2.3.7):
  `horoscope,birth chart,zodiac,synastry,natal,transit,moon,tarot,journal,mood,friends,love,couple`
  (95 chars; add `crush` if you drop another word). Drop `tarot` if it pulls the wrong installs, and swap in `rising`.
- **Primary category:** Lifestyle. **Secondary:** Health & Fitness (journal and mood).
- Don't use any of these in metadata: "accurate predictions", "know your future",
  "psychic". That's honest copy and also avoids 2.3.1 / 1.1.6 trouble.

### Screenshots (D)
- Required: **6.9" iPhone, 1320×2868 portrait** (Apple scales down for smaller
  devices). If the iPad target is on, 13" iPad 2064×2752 is also required. For
  v1, **turn iPad off** in the build target (`TARGETED_DEVICE_FAMILY = 1`) to
  avoid that work.
- Up to 10. The first 3 do 90% of the work. Sequence:
  1. **People:** two orbits, a named tier, "Today with Maya: closer than
     yesterday" plus trend line. Caption: *Who you're closest to changes every day.*
  2. **Today:** line of the day on the sky gradient. Caption: *One line each
     morning, written from your real sky.*
  3. **Chat:** a warm reply that cites the actual transit. Caption: *Ask anything. It remembers.*
  4. **Memory:** "Your Tuesdays are lighter". Caption: *Patterns from your own days, not your sign.*
  5. Chart reading. 6. Privacy: *Your data. Delete it in two taps.*
- Use demo names only. Never a real person's chart, and never "Isabel".
- Every on-screen sky fact must come from a real ephemeris run of the demo
  account. Take the screenshots from the seeded review account, not a mock.

### App Preview video (optional, strong)
15–30 s, 886×1920 portrait for 6.9", captured on device, no device frame.
Script: open the app → morning line → tap People → Maya's trend line climbs →
check in "Closer" with haptic → the chat. It autoplays muted, so add captions.

### Other required fields
Support URL (`https://mysignapp.com/support`, or a mailto page), Marketing URL,
Privacy Policy URL (`/privacy`), copyright line, review contact phone and
email, demo account credentials (the seeded account, password-free: give the
reviewer an email that the review build accepts with a fixed code; see the
review-mode note below).

**Review sign-in:** reviewers can't receive the email code. Add a
server-side allowlist entry, `REVIEW_EMAIL` plus `REVIEW_CODE` from Secrets
Manager, that only works for that one seeded address. Remove or rotate it
after approval.

---

## 5. TestFlight

- **Internal testing** (up to 100 App Store Connect users): no review, builds
  available minutes after processing. Use it from the first archive.
- **External testing** (up to 10,000 testers, public link): the first build
  needs a short Beta App Review (usually under 48 h). **That review is a cheap
  preview of the 4.3 reaction.** Submit for external testing by end of week 3
  on purpose.
- Recruit 50–150 testers from the existing PWA users and pilot (`docs/pilot.md`)
  plus TikTok followers. Sandbox purchases are free in TestFlight. Tell testers
  so they actually try subscribing.
- Exit criteria before submitting 1.0: crash-free sessions ≥ 99.5%, push
  received next morning by ≥ 90% of opted-in testers, a sandbox purchase →
  `plan.grant` → unlocked chat verified end to end, and account deletion verified.

---

## 6. Week-by-week timeline

| Week | F (founder) | E (engineer) | D (design/content) |
|---|---|---|---|
| **1** | Entity check, **D-U-N-S request**, enroll, Small Business Program, domain email | Capacitor shell, bundled assets, `API_BASE`, bearer-token auth, runs on device | Icon 1024, splash, App Store name/subtitle/keywords draft |
| **2** | Paid Apps agreement, bank, tax. Create the app record and subscription group | APNs push end to end, haptics, share, offline cache | Paywall design (honest: price, period, trial terms, restore) |
| **3** | Recruit TestFlight testers. Draft review notes | StoreKit 2 / RevenueCat, `/api/billing/apple/notify` + `/sync`, Sign in with Apple, privacy manifest. **First external TestFlight build** | Screenshots v1 from seeded account, preview video storyboard |
| **4** | Read every tester message. Decide go/no-go | Fix TestFlight bugs, seed review account, review-login allowlist, Apple token revocation on delete | Final screenshots + video, privacy labels filled in |
| **5** | **Submit 1.0 + subscriptions** with **manual release**. Pre-write the 4.3 appeal | On call for review questions, server notifications sandbox → production | Launch content batch (section 7) |
| **6** | Approved → **release Tuesday or Wednesday morning ET**. If 4.3: same-day reply, then appeal | Watch crash, funnel and `plan` events. Hotfix lane | Post launch content daily |

Plan for **one 4.3 rejection** already inside week 5. If none happens, launch a
week early.

---

## 7. Launch week marketing

The audience is women 18–34 who already have Co-Star or The Pattern and
screenshot them. The hook isn't "another horoscope". It's **"who you're
closest to changes every day"**.

- **Pre-launch (weeks 4–5):**
  - Turn on **Pre-Order** in App Store Connect (up to 180 days before release).
    Pre-orders auto-download on day 1 and count toward first-day ranking.
  - Put a smart app banner on mysignapp.com
    (`<meta name="apple-itunes-app" content="app-id=…">`) once the app ID exists.
    Existing PWA users get one in-app card: "My Sign is in the App Store: your
    data comes with you." Same account, same history.
  - Seed 10–20 micro-creators in the astrology/relationship TikTok niche
    (10k–100k followers). Give each a free year of Plus via **offer codes** (App
    Store Connect → Subscriptions → Offer Codes) and **no script**, just "add
    your best friend in People and film tomorrow's result".
- **Launch day (Tue/Wed):**
  - TikTok first (from `docs/launch-checklist.md`), then Instagram Reels and a
    founder post. Format that works for this category: a green-screen reaction
    to "Today with [best friend]: Strained" → the chat explains the actual
    transit → they check in. It's real, not staged, and it shows the People tab
    doing its job.
  - **Ask for ratings natively**: `SKStoreReviewController` (via
    `@capacitor-community/in-app-review`) after the 3rd day she logs a mood
    check-in, never on first launch. Apple caps it at 3 prompts/year.
  - Submit to Apple's editorial team via the **App Store "Featuring Nominations"**
    form in App Store Connect at least 3 weeks before launch. Story angle:
    an astrology app built on real ephemeris math and a privacy-first memory.
- **Launch week:**
  - **In-App Events** (App Store Connect): create one for the next real sky
    event the ephemeris confirms (e.g. a full moon or a planet stationing that
    week). Events show on the product page and in search. Use the engine's date
    and don't invent one.
  - **Referral loop that respects consent:** "Invite them so their side is
    exact". The invite shares *only* a link. The friend enters her own birth
    data. Never share the user's or the friend's data without the friend's action.
  - Apple Search Ads: Basic is fine at launch. Bid on `birth chart`,
    `compatibility`, `synastry`, `astrology friends`. Cap at $20–30/day until
    install → trial conversion is known.
- **Metrics to write down every day of launch week:** product page conversion
  rate, installs, onboarded, push opt-in %, day-1 return, trial starts, People
  added per user. The People number is the thesis. If it's under 1.5 by day 7,
  fix the add-person flow before spending on ads.

---

## 8. Pre-submission checklist

- [ ] Organization enrollment active, Paid Apps agreement **Active**
- [ ] Bundle `app.mysign.ios`, iPhone-only, iOS 16+ minimum
- [ ] Bundled web assets, no remote `server.url`
- [ ] Bearer-token auth works on device. Cookie path unchanged on web
- [ ] APNs morning push received on device (production APNs on the release build)
- [ ] IAP: monthly and annual, 7-day trial, restore button, honest paywall copy, Terms and Privacy links
- [ ] Server Notifications V2 verified in sandbox and production
- [ ] Sign in with Apple + token revocation on delete
- [ ] Delete account reachable in two taps from Settings
- [ ] Privacy manifest + App Privacy answers match section 3.6
- [ ] Age rating questionnaire answered, minimum age set to 18+
- [ ] Review account seeded (30+ days, 3 People), review code allowlisted, demo video attached
- [ ] Review notes include the 4.3 differentiation paragraph and the crisis-resource note
- [ ] Screenshots 1320×2868, every sky fact from a real ephemeris run
- [ ] Subscriptions attached to the 1.0 version submission
- [ ] Release set to **Manual**

## Sources

- Apple, App Store Review Guidelines (4.2, 4.3, 3.1.1, 3.1.2, 5.1.1, 4.8, 2.3.7): developer.apple.com/app-store/review/guidelines
- Astrology 4.3 rejections: [Apple Dev Forums 737999](https://developer.apple.com/forums/thread/737999), [820697](https://developer.apple.com/forums/thread/820697), [iMore on WTFuture](https://imore.com/apple-rejects-developers-horoscope-app-says-app-store-has-enough), [TechCrunch on Struck](https://techcrunch.com/?p=2019244)
- Wrapper 4.2 rejections: [Apple Dev Forums 812889](https://developer.apple.com/forums/thread/812889), [MobiLoud on WebView wrappers](https://www.mobiloud.com/blog/app-store-review-guidelines-webview-wrapper)
- Age ratings 13+/16+/18+: [Apple developer news](https://developer.apple.com/news/?id=ks775ehf)
- US external purchase links and commission status: [MacDailyNews, Aug 2026](https://macdailynews.com/2026/08/14/u-s-supreme-court-clears-path-for-app-store-commission-showdown-as-apple-must-defend-its-rates-in-lower-court/), [The Next Web, May 2026](https://thenextweb.com/news/supreme-court-apple-epic-contempt-stay-denial)
