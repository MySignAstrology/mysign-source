# My Sign

A daily companion whose lens is astrology. Not an astrology chatbot — that
category is saturated and stateless. The differentiator here is that it
**remembers her life**, and the astrology is what gives that memory a voice.

The loop: a 7:30am push computed from her real transits → a fifteen-second mood
check-in → a conversation where she asks the actual question → a silent nightly
write to memory that makes tomorrow better.

Full spec and the voice-test transcript are in [`docs/`](docs/).

---


**Licence:** GNU AGPL v3 (`LICENSE`, with what it doesn't cover in `NOTICE.md`). The source of each production deploy is published at https://github.com/MySignAstrology/mysign-source by `tools/publish_source.sh`.

## Two rules the whole codebase exists to enforce

**The model never invents a planetary position.** Swiss Ephemeris computes the
sky; the model receives it as facts and does synthesis and voice. Every claim it
makes about the sky traces to a row in a computed table. This is why it can tell
a user Mercury *isn't* retrograde — a correction no GPT wrapper can make, and the
highest-trust moment the product has.

**The model proposes patterns; code verifies them.** `engine/memory.py` never lets
a model write "4 of the last 5 cycles." It emits a machine-checkable predicate and
Python does the counting, behind three gates — support, lift over her own base
rate, and a Bonferroni-corrected binomial test. Measured detection threshold is
**~90 days** of history. Before the correction was in, a decoy survived at thirty
days: *"you feel worse around the full moon,"* 3 of 4, lift 1.88. Plausible,
quotable, false.

---

## Layout

```
engine/     chart.py      natal + transits + aspects (pyswisseph)
            salience.py   is anything actually happening today
            memory.py     nightly extraction, pattern mining + verification
            context.py    per-turn context assembly
            store.py      JSON store — swap for DynamoDB, the shape is the contract

rig/        Test 2 harness: 10 people, 14 days, SMS, human in the loop
            app.py        Twilio webhook + approval queue
            safety.py     tier classifier — runs BEFORE the astrology layer
            agent.py      draft one message (never sends)
            operator.py   the human: approve / edit / kill
            scheduler.py  morning push, nightly write, Sunday pattern job
            metrics.py    instrumentation for the question Test 2 asks
            enroll.py     participant enrollment + consent text

prompts/    system-prompt.md
docs/       spec.html, voice-test.html
tests/      test_rig.py — engine tests against real ephemeris
```

## Salience, calibrated

Thresholds are the 85th and 40th percentile of a full year of one chart's daily
scores, not hand-picked. The mix that produces: **13% high / 46% medium / 40%
low** — about 147 days a year where the app is licensed to say "quiet day, those
exist."

The non-obvious part is novelty decay. Pluto sits within one degree of a natal
point for six weeks; scoring on orb alone marks forty straight days significant.
Slow transits halve each time the app surfaces them, and re-sharpen on their
exact date.

## The app

An in-app chat, which is where this actually lives now — the SMS rig in `rig/` is
still there for the pilot, but day to day you want `app/`.

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # omit to run in demo mode
./run.sh                            # http://localhost:8420/app
```

`/` is the public website (`site/`, see `site/README.md`) and the app lives at
`/app`. Anyone with a session cookie, and old links carrying `?invite=`, `?p=`
or `?sky=`, is redirected from `/` to `/app` with the query kept. The edge does
this in production (`deploy/edge/router.js`); `app/server.py` does the same when
it serves `/` itself, locally and with `staticFromS3` off.

Sign in with an email and a six-digit code, agree to the terms, then onboarding
takes a name, birth date, birth time, and city. From there: the day's reading at
the top, a fifteen-second check-in, and the chat. Replies stream.

Multi-user. Every route that touches personal data resolves the signed-in user
from a signed cookie and 401s without one; a second account sees none of the
first's data. Both are covered by tests.

Without `ANTHROPIC_API_KEY` it runs in **demo mode** and answers from a small
canned set, flagged in the header. Everything else is real even in demo mode —
Swiss Ephemeris computes the chart and today's transits, salience is scored, the
context block is assembled, memory is written.

```
POST /api/auth/request   email -> a six-digit code
POST /api/auth/verify    code  -> signed session cookie
POST /api/auth/signout
POST /api/consent        required before anything personal is stored
GET  /api/state          signed in? consented? onboarded? thread
GET  /api/today          the day's reading + salience band
POST /api/onboard        birth data -> natal chart
POST /api/checkin        mood + one line
POST /api/message        SSE stream: meta -> delta* -> done
GET  /api/memory         episodes, people, patterns
DELETE /api/account      deletes everything, for real
```

## The morning notification

The 7:30am push is the product. Everything else is what happens after it lands —
without it, My Sign is a page you have to remember to visit.

`app/cron.py` runs every fifteen minutes and pushes to whoever just crossed 07:30
in their own timezone, idempotent per user per day. On Lambda it is a **second
function off the same zip** with a plain handler and no web adapter: EventBridge
invokes Lambda directly, and routing a cron event through an HTTP adapter would
mean inventing a request that never existed.

**Quiet days matter more here than anywhere else.** Roughly 40% of days score low,
and a notification that says "nothing is happening" every third morning is how
this becomes something she swipes away. Those days get a bare check-in prompt
instead — the ritual, without the invented significance.

### iOS

Web Push on iPhone only works once the site is on the Home Screen (16.4+). That
is why there is a manifest, a service worker, and an icon set — not to be a PWA
for its own sake, but because it is the only route to a notification on the phone
the target user actually has. Settings says so plainly rather than letting the
button fail for a reason nobody could guess.

This is also the strongest argument for the native app in v1: an install step
before the core mechanism works is a real funnel cost.

## The reading

Handing over a birth time is the biggest ask in onboarding, and until now it
bought an empty chat box. `engine/reading.py` is the payoff: the big three, how
the chart is weighted, what stands out, and the tightest natal wiring — shown the
moment the chart exists, before anyone types anything.

Every claim traces to `engine/interpret.py`, which computes stelliums, element
and modality balance, the chart ruler, angular planets, retrogrades and tight
natal aspects from the positions. The model never decides any of it; with a key
it writes the opening paragraph over those facts, and without one the corpus text
stands alone.

### Without a birth time, it says so

The ascendant moves through all twelve signs in a day — measured on one real
chart, **eight distinct rising signs across twenty-four hours**. So without a
birth time the rising sign, every house placement, the chart ruler and anything
angular are not approximate. They are invented.

They are omitted instead. The reading shows what still holds (signs, aspects,
element balance computed without the angles) and names what it cannot know. A
birth time can be added later from Settings, and doing so produces a chart
identical to having entered it at signup — there is a test for that.

**The interpretation text is a starter corpus.** Short, non-predictive, and it
needs a working astrologer's pass before launch. That is the second moat the spec
names and it cannot be shortcut by an engineer with a reference book.

The reading ends by asking what you want to know, with openers drawn from your
own placements — "ask me anything" is the hardest prompt in the world to answer.
Each is gated on the placement actually being present, so nobody is offered a
question about a house they have nothing in.

## What it remembers about your questions

Episodes record what happened. `questions` records what you went looking for,
which is a different thing and often more revealing — someone can log a perfectly
fine week and still have asked about the same person nine times.

`recurring_themes` only names something back to you at three mentions or more.
Saying "you keep asking about this" when you asked twice is the same overclaiming
the pattern verifier exists to prevent; it just costs less to get wrong.

## Birthplaces

34,106 places from GeoNames cities15000 — every settlement over 15,000 people,
244 countries — bundled at ~900KB gzipped rather than called. A geocoding API
would mean a key, a rate limit, a network round trip inside signup, and a third
party learning where every user was born.

The dataset carries an **IANA timezone per city**, which is the field that
actually matters: without it a birth time cannot be converted to UT, and every
house placement depends on that conversion.

Search accepts `chicago`, `chicago, il`, `chicago, illinois` and `chicago,
united states`, is accent-insensitive, and covers the English exonyms GeoNames
does not (Cologne, Seville, Kiev, Bombay). Worst case is ~13ms.

Attribution is required by CC BY 4.0 — see [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md),
which also flags the **Swiss Ephemeris AGPL question**, which needs a decision
before the first paying user.

## Browser tests

```bash
npm ci && npx playwright install chromium
npx playwright test
```

39 tests across mobile, tablet and desktop, in about 40 seconds. Playwright
starts the server itself on its own port and its own data directory, so a run
never touches local state.

These exist because every change up to now was verified by driving a browser by
hand, and because two people editing `app/server.py`, `app/static/index.html`
and `tests/test_app.py` at the same time have already produced three merge
conflicts and one `NameError` that only appeared at runtime. The pytest suite
cannot see any of it — none of it is reachable without a browser.

They cover the whole path (account → chart → question → memory), the
unknown-birth-time path, the crisis path, and layout at each breakpoint.

**Each assertion was checked against a real break**, because a test that cannot
fail is decoration:

| Break | What fires |
| --- | --- |
| remove `flex-wrap` from `.wire` — a bug that shipped | *the aspect pair wrapped onto two lines* |
| `main { padding: 0 }` | *#rdName has no left margin* |
| body text set to the ground colour | *body text fails AA contrast*, both themes |

## The conversation, sharded

`engine/thread.py` is the only module that knows how the conversation is stored.

It used to be one item. A message pair is ~560 bytes, so at twenty messages a day
a user crosses DynamoDB's 400KB ceiling in about **ten weeks** — and the old
behaviour there was a hard error on save, meaning every message that user sent
would fail from then on, permanently. The lockout would have hit the most engaged
users first.

Now one item per user per month, and a heavy month spills into a continuation
shard so no item grows without bound. Monthly alone was not enough: a year at
twenty messages a day already fills a shard to half the ceiling. Measured on an
extreme month — 1,860 messages of 600 characters — it produces nine shards, the
largest 150KB.

`recent()` walks newest to oldest and stops once it has enough, so a two-year-old
account costs the same read as a two-day-old one. `/api/state` returns the last
100 messages rather than the whole archive, which was previously read and sent on
every poll.

Pre-shard data migrates on first touch. That history is the memory the product is
built on; orphaning it would be the worst regression available.

## Is the chart right?

Swiss Ephemeris is the reference implementation and was never in question. What
was in question is everything built on top of it — timezone conversion, house
assignment, the angles, aspect matching — all of which had only ever been
compared against itself.

`tests/test_astronomy.py` checks it against facts published independently, and
against physical invariants that must hold anywhere:

| Check | Result |
| --- | --- |
| Sun at 0° Aries, March equinox 2026 (20 Mar 14:46 UTC) | **0.00′** off |
| Sun at 0° Cancer, June solstice | **0.02′** off |
| Sun at 0° Capricorn, December solstice | **0.01′** off |
| Sun–Moon conjunct at the 12 Aug 2026 eclipse | 5.8′ — expected, greatest eclipse is surface obscuration, not geocentric conjunction |
| Sun on the ascendant at sunrise, four cities | 40–61′ — expected, sunrise accounts for refraction and disc centre |
| Ascendant reaches all twelve signs in a day | yes |
| House cusps span exactly 360° | yes |

The sunrise check is the strongest available offline: it exercises the
ephemeris, the timezone handling, the house system and the angles at once, and
it holds in the southern hemisphere and near the Arctic as well as Chicago.

## Narrowing an unknown birth time

`engine/rectify.py` scores candidate birth times against dated life events —
moves against the IC, relationships against the descendant, career against the
midheaven — using slow-planet transits only.

**The first version was noise.** With five slow planets, three aspects and a 3°
orb, something is near some angle for almost any candidate, so the best of 360
always looked impressive: eight of eight random event sets produced a confident
nine-minute window. A z-score against the candidate spread did not fix it either
— the maximum of 360 overlapping candidates is not normally distributed.

What fixed it is a permutation null: shuffle the event dates, take the maximum
again, repeat, and require the real result to beat 99% of that distribution.
Measured after the change:

- **0 of 30** random event sets produce a result
- given events that genuinely encode a birth time, it recovers it to **0 minutes**

Both are pinned by tests. The flow says plainly that "nothing here points at a
particular time" is the normal answer, and that asking whoever was in the room
beats it.

## It's an agent

The model used to get a briefing assembled before the call and answer from it.
Anything the briefing did not cover — another date, a person, a memory older than
the retrieval window — it had to decline, because the system prompt correctly
forbids inventing. Honest, and a ceiling on what this could be. A real astrologer
looks things up.

`engine/agent.py` streams a reply, pauses when the model asks for a fact it does
not have, computes it, and resumes.

| Tool | Answers |
| --- | --- |
| `get_sky` | "what about Thursday?" — any date, past or future |
| `get_natal` | "where's my Saturn?" |
| `get_synastry` | "is it me or him?" |
| `search_memory` | "have I felt like this before?" — the whole history |
| `find_next` | "when does this end?" |
| `get_lunations` | "when's the next full moon?" |

Every tool returns computed facts, never prose. The engine does the astronomy,
the model does synthesis and voice — the same division as everywhere else,
extended so the model can ask for more facts mid-answer.

Three things that are easy to get wrong and are pinned by tests:

- **All tool results go back in one user message.** Splitting them across several
  silently trains the model out of making parallel calls.
- **Grounding is the briefing plus every tool result.** A fact the agent looked up
  is legitimately citable even though it was not in the opening block; without
  this every tool-assisted answer scores as a hallucination.
- **The loop has a ceiling.** One that can call tools indefinitely is a bill.

Demo mode looks things up for real — the model is absent, the engine is not — so
the tool path and its UI are exercised without an API key.

## Comparing people

Saving one person is a party trick. The list is the product, and comparing
across it is the reason to keep using this.

`GET /api/connections/compare` ranks everyone she has saved, and the agent can
reach it with `compare_connections` — so "who's actually better for me?" gets an
answer built from her saved charts rather than from the model's impression of
what she wants to hear.

**The discipline is knowing when not to rank.** Measured: a single aspect contact
moves a dimension by 6 to 9 points on the 0–100 scale. A gap smaller than that
cannot correspond to even one different contact between two charts — it is the
same instrument reading the same thing twice. Those are reported as **level**,
and the prompt says the tie is a measurement rather than diplomacy.

```
1. Sam                     [65]
2. Theo = Jules = Ari      [53, 51, 50]   level
```

Three people inside six points is not a ranking, and presenting it as one is how
this becomes a horoscope with extra steps. Mixed confidence is flagged too —
someone scored without a birth time is not on the same instrument as someone who
has one.

## Which astrology this is

`engine/tradition.py`. The engine had taken positions on contested questions by
being written — Placidus houses, traditional rulerships — and switching later
meant a rewrite, which is the wrong reason not to.

They are configuration now, and the stakes are measurable:

```
python tools/compare_traditions.py 1999-03-14 04:42 America/Chicago 41.8781 -87.6298
```

On the fixture chart **all ten placements change house** between Placidus and
whole sign. The Moon goes 12th → 1st:

| | |
| --- | --- |
| Placidus | 12th — solitude, memory, what runs underneath |
| whole sign | 1st — how you come across and how you start |

The strongest answer the voice test ever produced rested on that 12th house.
Under whole sign it would be wrong. This is the single decision that most changes
what the app says.

At high latitude it is worse — a Reykjavik chart puts Mercury in the 12th under
Placidus, the 10th under whole sign and the 9th under equal. That three-house
spread is the standard argument against Placidus, and now you can show it rather
than cite it.

**The default is unchanged.** Everything — the salience thresholds, the voice
test, the corpus — was built against Placidus, so moving it is a product decision
rather than a tidy-up. `GET/POST /api/tradition` switches it per user; every
derived artefact is keyed by tradition, so a change misses cache instead of
needing an invalidation pass.

Placidus with traditional rulerships is flagged as **not coherent** — whole sign
pairs with traditional, Placidus grew up alongside modern. Mixing them is common
in practice and worth a deliberate answer.

## The interpretation corpus

`engine/corpus.py`. **2,405 combinations**, up from 60.

Built compositionally rather than written out flat, which is not a shortcut —
it is how the subject works. An astrologer does not memorise "Saturn in Scorpio
in the 8th"; they know what Saturn does, what Scorpio does to whatever it
touches, what the 8th is about, and they compose. Forty well-made primitives beat
eleven hundred mediocre strings and stay consistent with each other.

| | |
| --- | --- |
| planet × sign × house | 1,440 |
| transits | 500 |
| aspect pairs | 225 |
| planet × sign | 120 |
| synastry overlays | 120 |

The piece that stops it reading mechanically is **essential dignity**. Saturn in
Libra is exalted and reads as competence; Saturn in Aries is in fall and reads as
the same function under strain. That is traditional doctrine, it is computable,
and it is the first thing a practitioner checks — it was entirely absent before.
The full grid is pinned cell by cell in `tests/test_corpus.py`.

The daily context now carries meaning, not just orbs:

```
t.Neptune conjunction n.Mercury — orb 0.99°  [Rx]
    Neptune is meeting your mind — fused, so they never operate
    separately in you. A season, not a day.
```

Handing the model an orb and nothing else leaves it to supply the meaning from
wherever it happened to learn it, which is the problem the engine exists to
solve.

**This has not been reviewed by an astrologer.** It is substantively conventional
Western natal astrology, and it is the thing to put in front of one instead of a
blank page. What still needs a practitioner is the tradition question — the
engine assumes Placidus houses and traditional rulerships while the voice is
modern psychological, and those do not fully cohere.

## Measuring the voice

```bash
python tools/eval_voice.py
```

Until now the voice had only ever been judged by a person reading it, which does
not scale and cannot gate a prompt change. Eight scenarios, scored against
mechanical criteria — no model judging another model, because a judge would need
its own eval.

The criterion that matters is **grounded**: every planet, sign and house named in
a reply must appear in the context the model was handed. That is the product's
central promise, and it is checkable rather than hoped for.

Without an API key it scores the demo replies; with one, the numbers are about
the real product. It runs in CI either way.

**It earned its keep on the first run**, finding three things a person reading
replies had missed:

- the demo replies claimed a 12th house on a day the context had none — the
  exact failure the engine exists to prevent. They are now built from the day's
  actual top aspect instead of written in advance.
- a reply exceeded its own length ceiling.
- **"should i end it with him" was classified green, and "I want to end things"
  was classified red** — see below.

## A safety misrouting

The red-tier pattern matched `ending things`, so *"I want to end things with my
boyfriend"* returned the 988 crisis reply. That is a false positive in the worst
possible place: it alarms someone who is fine, and it teaches them the crisis
message is noise for the day they are not fine.

The other direction was just as wrong — `end it with`, `call it off`, `dump him`,
`walk away from him` and `cut him off` were all green, so an irreversible
relationship decision could get a verdict.

Both fixed, with a seventeen-case matrix pinned in tests. Ending a relationship
is amber. Ending your life is red. The distinction is whether a partner follows
the phrase.

## Logs

One JSON object per line, a request id on every one, and an `x-request-id` header
on every response. A 500 returns that id in the body so someone can quote it back.

Written after debugging the first deploy meant grepping raw CloudWatch streams
for tracebacks with nothing tying a failed request to the exception behind it.
That was survivable for the person who wrote the code; it will not be during a
pilot with ten real people.

**Logs record shape, never content.** This app holds people's messages about
their relationships, their families and their worst days. None of that belongs
in an operational log, so the redaction list is enforced inside `log.emit` rather
than left as a convention — and a test fails if a message body ever reaches a
log line.

A whole session reads like this:

```
auth.verify.ok    uid=u57ad…
consent.given     uid=u57ad…
onboarded         uid=u57ad… time_known=true country=US
message           uid=u57ad… tier=green chars=31 demo=true
message           uid=u57ad… tier=red   chars=31
safety.red        uid=u57ad… chars=31
alert.unrouted    component=safety severity=critical
```

Useful queries in CloudWatch Insights:

```
fields @timestamp, event, uid, tier
| filter event = "safety.red"
| sort @timestamp desc
```

```
fields @timestamp, path, status, ms
| filter level = "error"
| sort @timestamp desc
```

```
fields @timestamp, path, ms
| filter event = "request" and ms > 3000
| sort ms desc
```

## Safety

Classification runs **before** the astrology layer, on every inbound message,
and it is structural rather than a line in the prompt.

Two stages, and the order is the point. A local regex pre-filter runs first and
can only escalate — it catches the unambiguous cases with no network call, so
crisis handling keeps working when the API is down, the key is missing, or rate
limits hit. Anything safety-critical that depends on a successful HTTP request
is not a safety system. The model classifier runs second for the cases patterns
cannot catch, and can raise the tier but never lower it.

Red tier never reaches the model. The canned response goes out byte for byte,
renders as a visibly different message, and carries no astrology at all. There
is a test asserting exactly that.

### Memory accumulates from use

There is no cron and no background worker. `rollover()` runs on every state read:
any day in the thread that has ended and has no episode yet gets extracted, keyed
by date, so it is idempotent and re-extracting replaces rather than duplicates.
Today is left alone until it is over — half a day makes a misleading episode.

The pattern job fires at most weekly and only past ten episodes, which means it
correctly produces nothing for the first three months. The Memory view says so
out loud rather than showing an empty section.

Without an API key, extraction falls back to a heuristic pass — keyword topics,
capitalized-name detection, and her longest messages kept verbatim. Episodes
extracted that way are tagged `rough` in the UI. It keeps her exact words, which
is the part that matters, and guesses at everything else.

## Test 2

The question is: **when it recalls something from week one, does she light up or
recoil?** The rig is built to measure that, not just to run the loop — every
outbound message is tagged for whether it contained a memory callback, and every
reply is measured against the message it answered.

It also tracks operator edit distance by week. If that falls over fourteen days,
the voice is close to shippable. If it doesn't, the voice isn't there yet
regardless of what participants say out loud.

```bash
pip install -r requirements.txt

python -m rig.enroll --name Maya --phone +1312… \
    --birth 1999-03-14 --time 04:42 --tz America/Chicago \
    --lat 41.8781 --lon -87.6298

uvicorn rig.app:app --reload      # Twilio webhook -> POST /sms
python -m rig.operator            # the human in the loop
```

Cron:

```
0,15,30,45 * * * *   python -m rig.scheduler morning
0 4 * * *            python -m rig.scheduler nightly
```

Env: `ANTHROPIC_API_KEY`, `TWILIO_SID`, `TWILIO_TOKEN`, `TWILIO_FROM`,
`OPERATOR_SMS`, `MYSIGN_DATA`.

## Build notes

`deploy/build.sh` installs in three passes rather than one. `pywebpush` requires
`http-ece`, which publishes no wheel — under `--only-binary` pip's resolver
backtracks through every `pywebpush` version hunting for a combination that
cannot exist. Measured: 16+ minutes of churn before failing. Naming the push
chain explicitly with `--no-deps`, then installing the pure-Python `http-ece`
without platform pins, builds in 40 seconds.

## Deploying

One Lambda, one DynamoDB table, one secret, one URL. No Docker anywhere — pip's
`--platform` flag builds the Linux package from a laptop, which is why the runtime
is pinned to **Python 3.11**: `pyswisseph` publishes manylinux wheels up to cp311
and no further.

```bash
./deploy/build.sh                       # -> deploy/build/mysign.zip
cd deploy && pip install -r requirements.txt
cdk deploy -c targetAccount=<account-id> -c targetRegion=us-east-1
```

**The target account is required and has no default.** `cdk deploy` without one
refuses to synthesize. This is deliberate: CDK's normal behaviour is to use
whatever credentials happen to be in the shell, which is how a personal project
ends up in an employer's account. Naming the account also makes CDK abort if the
ambient credentials belong to a different one, so a wrong-account deploy fails
before it creates anything.

Check `aws sts get-caller-identity` before you fill it in.

Then populate the secret it created — the app reads nothing until you do:

```bash
aws secretsmanager put-secret-value --secret-id <SecretArn>   --secret-string '{"anthropic_api_key":"sk-ant-...","access_code":"pick-something"}'
```

Visit `<Url>?code=<access_code>` once and it sets a 90-day cookie.

**Check the Lambda Web Adapter layer version** in `deploy/cdk.json` before the first
deploy — `lwaLayerVersion` is pinned to a number that moves. Latest versions are at
[awslabs/aws-lambda-web-adapter](https://github.com/awslabs/aws-lambda-web-adapter).

The Function URL runs in `RESPONSE_STREAM` mode because the chat is server-sent
events. In buffered mode every reply lands as one silent lump after ten seconds.

### Sessions

HMAC-signed cookies, nothing server-side to look up. Rotating `session_secret`
invalidates every session at once, which is the behaviour you want when something
goes wrong. Set `MYSIGN_FROM_EMAIL` to send codes over SES; without it, codes are
printed to the log and returned to the client, which is a local-development
affordance and is labelled as one in the UI.

### Storage

`MYSIGN_TABLE` switches `engine/store.py` from JSON files to DynamoDB; nothing
downstream knows the difference. One item per (user, kind), which keeps reads to a
single GetItem and costs you DynamoDB's 400KB item ceiling. The thread will reach it
eventually — `store_dynamo._check_size` fails loudly rather than truncating history
silently. Shard the thread by month before that matters.

## Ground rules for the pilot

Nothing reaches a participant without a human reading it first — drafting and
sending are separate functions on purpose. Red-tier messages bypass the queue
entirely: the canned crisis reply goes out immediately, no astrology, and the
operator is paged. `rig/enroll.py` carries the consent text; don't start texting
anyone who hasn't replied YES to it.

## Running a pilot

`/ops` is the operator view: who is in the study, how much they are talking,
every red and amber turn, and — only where there is a safety flag or a report —
the thread itself. Operators are app accounts with a role (`analyst`, `safety`,
`admin`); the first admin comes from `MYSIGN_OPS_ADMINS` and the old shared
`MYSIGN_OPS_TOKEN` survives only as break-glass. Absent entirely until one of
those is configured — a 404 rather than a 401, so it is not advertised. Every
read lands in an audit trail kept 400 days. See docs/operations.md.

**The in-app pilot is monitored, not gated.** `rig/operator.py` held every SMS
for approval before it sent; you cannot do that to a reply that is already
streaming word by word without destroying the thing being tested. So paging
fires immediately on a red tier and the console is where you go once it has.
That is a real difference from the SMS rig, not a rename.

## Ceilings and promises

- **Quota.** Eight messages a minute, a hundred and twenty a day. Sign-in was
  rate limited and the endpoint that calls a paid model was not. The daily
  refusal is deliberately not a scolding — someone sending that many messages is
  usually not abusing anything.
- **Export.** `GET /api/export` hands back everything the app holds, as a file.
  The consent screen promises deletion; this is the other half of the same
  promise and the harder one to argue against.
- **Offline.** The shell is cached, the API never is — a cached reading is about
  a day that has already ended. Offline says so instead of showing a blank page.
- **Reachable.** The conversation is an `aria-live` log, the mood dial is a
  labelled radiogroup, and a browser test fails if any visible control lacks an
  accessible name.

## Known gaps

- Retrieval is recency plus keyword match on named people and topics. It needs
  embeddings — the best line the voice test produced cited episodes 24 and 11
  days back, both outside any sensible recency window.
- Birth time is close to load-bearing. Six of eight voice-test conversations
  depended on house placement. Participants without one are testing a different
  product; decide whether that's a supported segment before launch.
- ~~Salience thresholds are global.~~ Fitted per chart at signup, from a
  simulated year that carries the same novelty decay production applies —
  calibrating without it fits thresholds to scores no real user ever sees.
- Retrieval is BM25, not embeddings. Good enough that the gap to embeddings is
  smaller than the gap was to substring matching, but it is not the end state.
- People are learned by name only. Relationship and birth data stay empty until
  she offers them — never guess at a third party's chart.
- The heuristic extractor misses names it has never seen capitalized once. It
  catches them from then on, but the first mention can slip.
