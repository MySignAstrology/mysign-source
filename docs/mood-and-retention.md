# Mood, chat activation and retention

November 2026. Why people land and leave, what we changed on `feat/mood-and-retention`,
how we compare with Co-Star, and every idea that came out of the research, ranked.

## What we found in the product

Walked as a new visitor on a phone:

1. **Landing → free reading** works: a birthday and a city, a result in about two seconds.
2. **Then a wall.** Before a new person could say a word to My Sign, they went through:
   sign-up code → a consent screen → the birth form again → **four swipe decks (up to 64
   cards)** → the chart page. The chart page is about 8,800px tall on a phone, and the
   only "ask" was at the very bottom.
3. **Today was busy and silent.** Promo cards ("Before you date", "Your chart in real
   life") came before the check-in. The check-in was a bare 1–10. The chat was an empty
   "Ask me anything…" bar. The suggested questions sat below everything else.
4. **The check-in and the chat were two separate things.** Checking in got "Noted." and
   nothing else. The most natural first message of the day, "here's how I feel", never
   reached the chat.

That is why nobody has used the chat. It wasn't reachable in under ten screens, and when
it was, it waited for the user to start.

## What shipped on this branch

| Change | Why |
| --- | --- |
| **The check-in opens the chat.** After checking in, the check-in is sent as the day's first message and My Sign answers it: names the feeling, treats the sky as context and never as the cause, then asks one question. | Turns every check-in into a conversation. The research is clear that the AI should speak first and that the user should never face an empty box. |
| **The check-in reply is free** (once a day, enforced server-side) and doesn't use up the 3 free messages. | "The check-in is never limited" was already the promise. Co-Star charges after 3 one-shot questions; this is the contrast. |
| **Richer check-in.** 1–10, then optional single taps for energy (wired / slow), one to three feeling words from that quadrant, and what it's about (sleep, work, love, friends, family, body, money, alone time, outdoors, creating, phone, school). | Mood Meter quadrants (Yale RULER). Affect labelling: naming a feeling dampens it. Emotional granularity: precise words predict better regulation. Tags make insights something you can act on. |
| **Each feeling word shows its meaning** ("lonely: wanting connection you're not getting"). | Builds vocabulary rather than just logging. |
| **"Yesterday you were a 3, drained."** shown above today's check-in. | A memory callback from day 2, which the research found brings people back. |
| **New Mood tab** (was Memory): weekly average against last week, "9/14 days, no streak to lose", feeling words used, a five-week colour calendar, where your days land (quadrants), your words, and **what goes with your better and harder days**. Each insight has a button into the chat ("Plan more outdoors this week"). | Daylio and Bearable patterns, with My Sign's honesty rule: shown with counts, called "an early read, not a pattern yet", at least 3 days each side and at least a 1-point gap. Only `memory.py`'s verifier can call something a pattern. |
| **My Sign speaks first.** An empty conversation opens with a message: "Before I say anything about the sky, I'd rather hear about you. How's today, really?" | Pi, Character.ai and Finch all open with the AI. |
| **The check-in and conversation moved right under the line of the day**, above the promo cards. | The check-in is the habit loop, so it goes first. |
| **The 64-card swipe deck is gone from sign-up.** It is still offered once on Home ("Your chart in real life"). | It was the biggest wall between a new chart and anything the person came for. |
| **"Ask about this"** with two of the reading's own questions, right under the big three. | It moved from about 8,800px down to about 1,400px. |
| **Ask on the website.** The free reading result now ends with "Now ask it anything about you", with three suggested questions and a box. The question is kept on the device only; after sign-up, My Sign asks and answers it first. | Gives people a reason to make an account, and their first chat message is one they chose. |
| The AI's context now includes energy, feelings and tags, and the system prompt has a "When she checks in" section. | The real model answers check-ins well, not only the demo. |

Tests: `tests/test_mood.py` (engine and API, including that the reply is free and only
once a day) and `tests/e2e/mood.spec.js` (the whole flow on a phone). The browser specs
that expected the swipe deck at sign-up now expect the reading.

## Co-Star, the direct competitor

Midjourney bought Co-Star in spring 2026 (Bloomberg, 24 July 2026). It has about 4.3M
monthly users and a 4.8 rating on iOS.

- **Onboarding** takes about 9 screens: birth date, time (skippable), place, a "NASA
  data" credibility screen, a witty "building your chart" wait, a warm-up before the push
  permission prompt, then add friends from contacts.
- **Home** has a daily theme, a Do/Don't list, area-of-life ratings, and the transit
  behind each reading.
- **Its strength is the voice.** Blunt one-line pushes that people screenshot as memes.
- **"The Void" (Ask the stars)** is one-shot Q&A with no memory. Three free questions,
  then $0.99 for 1 up to $6.99 for 25.
- **Premium:** Pro-Star at $8.99/month, plus one-off reports ($4.99–$11.99).
- **What users complain about:**
  - About 40% of negative reviews: features that used to be free (the daily update, the
    widget) moved behind the paywall. Users call it a "cash grab".
  - About 20%: generic, repetitive content.
  - Login bugs after updates.
  - Pushes creeping to 2–3 a day, with upsells disguised as readings.
- **What it doesn't have:** a mood check-in, memory, or personal patterns. Nothing in
  Co-Star learns about you over time.

**Our position:** Co-Star tells everyone something clever. My Sign gets to know *you*. It
remembers, it asks how the interview went, and its patterns come from your own days and
are checked before you hear them. Everything above leans into exactly that gap.

Other apps, one idea each:

- **The Pattern:** long personal timing cycles with dates (slow-burn reasons to return).
- **Sanctuary:** readings delivered like incoming texts.
- **CHANI:** a ritual layer (journal prompts, meditations) that people keep even after
  cancelling.
- **Nebula:** a long quiz builds sunk cost before the paywall. Don't copy that.

## The mood science, in one place

- **Self-monitoring raises emotional self-awareness, which reduces depressive symptoms.**
  Kauer et al. 2012, an RCT in JMIR. Logging is part of the help, not just data
  collection.
- **Affect labelling.** Putting a feeling into words lowers the amygdala response
  (Lieberman et al. 2007). So ask for a word, not just a number.
- **Emotional granularity.** People who tell their negative feelings apart regulate
  distress better (Kashdan, Barrett & McKnight 2015). Start coarse (a quadrant), then
  offer precise words.
- **Behavioural activation** has a large effect on low mood (Ekers et al. 2014, a
  meta-analysis of 26 RCTs). "Your better days have outdoors in them, so do more of it"
  is the evidence-based shape of an insight.
- **Missing a day doesn't hurt habit formation** (Lally et al. 2010). Streaks that reset to
  zero cause the "what-the-hell" quit. That's why there is no streak, only "9 of 14".
- **How often to ask:** once a day is enough. Prompt frequency doesn't predict
  compliance.
- **Avoid:** claiming causation (planets causing moods), guilt pushes, medicalised
  language, and paywalling the user's own data.

## Every idea, ranked

Status: **✓ done** (on `feat/mood-and-retention`, `feat/mood-next` for 5, 7, 15, 16, 18, 26 and 33, `feat/mood-3` for 19, 20, 25, 28, 34 and the weekly share card, or `feat/mood-4` for 8, 10, 21, 23 and 24), **next** (small, high value), **later**.

### Chat activation

1. ✓ The check-in becomes the day's first message, and My Sign replies.
2. ✓ My Sign speaks first in an empty conversation.
3. ✓ Ask on the website, answered first after sign-up.
4. ✓ "Ask about this" near the top of the reading.
5. ✓ (v2: "Does this land? Yes / Sort of / Not really", free once a day) End the daily line with a one-tap question ("Venus is pulling on your Sun.
   Is it showing up as needing reassurance, or as wanting to be seen?"). Each answer
   opens the chat with a tailored follow-up already written.
6. **next** Generous free chat for the first 7 days (for example 10 a day), then 3, in
   contrast to Co-Star's 3 one-shot questions. `plan.claim_message` is the one place to
   change it; watch `costs.py`.
7. ✓ Rotate the composer placeholder through real questions from her chart
   ("Why do I start more than I finish?") instead of "Ask me anything…".
8. ✓ (the push lands on the line with "Does this land?" focused) A "Reply" chip under the morning push's line that opens the chat with that
   line quoted.
9. later: Voice notes for the check-in line, transcribed.
10. ✓ "Ask about them" from any person card in People, prefilled with their name.

### Mood that leads somewhere

11. ✓ Energy, feeling words with meanings, and tags.
12. ✓ Mood tab: calendar, weekly comparison, words, early reads with counts.
13. ✓ Insights end in a small action and a chat button.
14. ✓ No reset-to-zero streak.
15. ✓ (on the Mood tab, with a chat button; now with a share card) **Weekly recap card** on Sunday: the week's colours, top three words, the
    biggest sky event, and "what helped". Make it shareable like the existing share cards.
    This is the screenshot moment, Daylio's year-in-pixels equivalent.
16. ✓ **"What helped?"** The day after a low check-in (≤ 4), ask in the check-in
    card: "Yesterday was a 3. Anything help since?" Keep the answers as a personal
    toolkit list on the Mood tab, and offer one back on the next low day.
17. **next** **An optional evening check-in** (one tap) for the within-day swing. Pushed
    only if she opts in.
18. ✓ **Word growth.** "You've used 14 different words for how you feel, up from
    6 in your first month." This is a granularity milestone.
19. ✓ (as `tag` and `tag_day_before` conditions; a verified one also gives that day's heads-up) Feed tags into `memory.py`'s pattern miner as predicates (`tag_present`) so
    "after a poor-sleep night your next day lands 2 points lower" can be *verified*, not
    just described.
20. ✓ (new and full moons) A sky overlay on the Mood calendar (full moons, retrograde spans) as context
    only. The verifier is still the only thing allowed to connect them.
21. ✓ (a "period" tag, so it can be an early read or a verified pattern) Cycle tracking as an optional tag set (period, PMS), common in this audience.
22. later: Apple Health sleep and steps through the iOS shell, as automatic tags.
23. ✓ (a note written on a 7+ day, handed back on a 4-or-under one) A "letter to future you" written on a low day, delivered on a high one (Co-Star
    has a weaker version).
24. ✓ (on the Mood tab, with a share card) A year-in-colour poster at the end of the year, for sharing.

### Retention loops

25. ✓ (the title, the morning after a low check-in) **Push copy that carries yesterday**: "Yesterday you were drained. The Moon
    leaves your 6th today; the heavy stuff eases by afternoon." One push a day, never an
    upsell (already the rule in `plan.py`).
26. ✓ (the existing add-to-Home-Screen offer now waits for the first check-in reply) A push permission warm-up shown right after the first check-in reply ("Want
    this every morning at 7:30?"), not before.
27. **next** An email fallback for web users who can't get push: tomorrow's line plus
    "how are you?" with 1–10 links that check in from the email.
28. ✓ Day 3, 7, 14 and 30 milestones on the Mood tab ("first early read", "first
    month in colour"), with no penalties for gaps.
29. later: Linked friends get a gentle nudge when someone they share mood with logs a
    low day ("Maya's having a 3. Send something?"), opt-in on both sides.
30. later: A home-screen widget showing today's line and the check-in (free forever; Co-Star
    paywalled theirs).

### Simplify

31. ✓ No swipe deck at sign-up.
32. ✓ The check-in and conversation come first on Today.
33. ✓ Show at most one promo card on Today, and only after the check-in.
34. ✓ Collapse the reading to the big three plus "Ask about this", with the rest
    behind "Read the whole chart". It is 8,800px on a phone.
35. **next** The landing page is about 10 screens. Cut it to: hero with the free reading,
    one example of the morning note, the mood example, privacy, FAQ. "The science",
    "Your people" and "Memory" can be one line each with links.
36. later: Defer consent text to a short summary with "read all"; the full text stays
    one tap away. (Needs a legal read first.)

## Measure it

Most of these already exist in `activity` and `funnel`. Watch:

- **Free reading → account.**
- **Account → first message.** The headline number for this branch. Split it by
  `message_sent.source = checkin | typed` to see how many first chats the check-in
  creates.
- **Share of first chats that reach turn 5.** The research says these users retain 2–3×
  better.
- **Check-in → reply → second message rate.**
- **Return on day 1 and day 7.**
- **`free_ask` clicks** on the landing page.
