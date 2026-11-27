# Test 2 — the pilot

Ten people, fourteen days, in-app. One question: **when My Sign recalls
something from week one, does she light up or recoil?**

## Go / no-go criteria (item 59 — decided before, not after)

| Signal | Criterion | Why |
| --- | --- | --- |
| Reply latency after a memory callback | ≤ 80% of the no-callback median | A callback that lands gets answered faster |
| Reply length after a callback | ≥ 115% of the no-callback median | …and with more |
| Operator edit distance, week two | ≤ 0.15 | The voice is converging, whatever people say out loud |
| Still checking in at day 7 | ≥ 60% of participants | The ritual survives contact with a real week |
| Red-tier events reaching a person | 100%, within five minutes | Non-negotiable |

`tools/pilot_report.py` scores these. **The numbers point; the transcripts
decide.** A short fast reply after a callback can be delight or alarm, and only
the text tells you which. Every transcript gets read, by a person, in full.

All five met → build v1 (Phase 8 onward). Any miss → read the transcripts, then
decide whether it's the prompt, the memory, or the thesis.

## Who (item 61)

Women 18–34, US, who already use an astrology app and are not friends of anyone
on the team. Friends will say it's great. Recruit through the astrology corners
of TikTok and Instagram, or an existing app's comment section.

## Intake (item 62)

Collected through the app's own onboarding, plus three questions by email:

1. Which astrology app do you use now, and how often?
2. Do you know your birth time? If not, could you find it?
3. Anything you want us to know before we start?

Consent is the in-app screen. Nobody is enrolled who hasn't tapped it.

## Runbook (item 60)

**Every day, 8am–10pm local to the participants:**

- One person is "on console" — `/api/ops/roster` open, red-tier queue visible.
- Red-tier page → open the thread within five minutes, confirm the canned reply
  went out, decide whether to follow up by email. Log the time.
- Read yesterday's threads. Note anything that reads wrong, verbatim, in
  `docs/pilot-log.md`.
- Check `tools/pilot_report.py` once a day from day 3. Don't act on it before
  day 7.

**Never:** edit a reply after it's sent, message a participant from a personal
account, or discuss a participant's thread with anyone not on console.

**If the model provider is down:** the app falls back to demo replies and says
so in the header. Tell participants; don't let them think the app changed.

## Exit interview (item 63)

Five questions, by voice if they'll do it, fifteen minutes.

1. What did it get right about you?
2. Tell me about a time it referred back to something you'd said before. How did that feel?
3. Was there a reply that felt wrong, or off, or like it didn't know you?
4. Would you pay $10 a month for this instead of what you use now? Why or why not?
5. What would you want it to do that it doesn't?

Question 2 is the pilot. Everything else is context.
