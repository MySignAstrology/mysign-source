"""
The line of the day — the first thing she reads when she opens the app.

One short sentence that makes her want to be in the day, not a table of
aspects. It is still grounded: the line is chosen from the strongest real
transit on her chart today, by what that transit touches (her Venus, her
Moon...) and how it touches it (flowing, tense, or fused). The reason is
returned alongside, in plain words, so nothing is a horoscope pulled from a
hat.

Rules the copy keeps:
- Second person, present tense, under ~70 characters, one idea.
- An invitation about today, never a claim about who she is. Traits come
  from her own data elsewhere in the app, not from a sign.
- Warm and a little sensual; never crude, never a prediction of events.

Which of the three lines appears is fixed for the day and person (a hash of
both), so it doesn't change on refresh but does change tomorrow.
"""
import contextlib
import datetime as dt
import hashlib

from engine import corpus

FLOW, TENSE, FUSE = "flow", "tense", "fuse"

TONE = {
    "conjunction": FUSE,
    "trine": FLOW, "sextile": FLOW, "semisextile": FLOW,
    "square": TENSE, "opposition": TENSE, "quincunx": TENSE,
}

# What a transit to each natal point is about, in three moods.
LINES = {
    "Venus": {
        FLOW:  ["Let yourself be wanted today.",
                "Say yes to the thing that feels luxurious.",
                "Pleasure isn’t a reward. Have it now."],
        TENSE: ["Want what you want. Just don’t beg for it.",
                "Not everyone gets your softness today. Choose who does.",
                "Desire and pride are arguing. Let desire win, quietly."],
        FUSE:  ["You’re magnetic today. Use it on purpose.",
                "Dress for the version of you that gets kissed.",
                "What you want and who you are are the same thing today."],
    },
    "Moon": {
        FLOW:  ["Soft is a strength today. Lead with it.",
                "Ask for what you need. It’s lighter than you think.",
                "Your feelings are good company today. Trust them."],
        TENSE: ["Your mood is real, but it isn’t a verdict on you.",
                "Feel it fully, then decide. Not the other way round.",
                "Protect your peace like it’s someone you love."],
        FUSE:  ["Everything lands a little deeper today. Let it.",
                "Your heart is loud today. Listen before you answer.",
                "Text the person who feels like home."],
    },
    "Sun": {
        FLOW:  ["Take up the whole room today. It’s yours.",
                "You don’t need permission to shine.",
                "Be seen. Today is good for it."],
        TENSE: ["Don’t dim yourself to keep the peace.",
                "Pushback is proof you’re doing something.",
                "Choose yourself, gracefully."],
        FUSE:  ["Today runs on you. Act like it.",
                "Start the thing. Today has your name on it.",
                "Start something small today. Let it be yours."],
    },
    "Mercury": {
        FLOW:  ["Say the bold sentence. It’ll land.",
                "Your words have heat today. Send the text.",
                "Ideas come easy. Catch the good one."],
        TENSE: ["Sleep on the reply. Then make it shorter.",
                "Not every thought needs saying out loud today.",
                "Listen for what they mean, not what they said."],
        FUSE:  ["Your mind is especially sharp today. Use it.",
                "Say it plainly. Charm will follow.",
                "Write it down. You’ll want it later."],
    },
    "Mars": {
        FLOW:  ["Make the first move.",
                "Your desire is clear today. Follow it.",
                "Spend your energy on what thrills you."],
        TENSE: ["You can be fired up and still think clearly.",
                "Turn the frustration into momentum.",
                "Don’t fight the wrong battle beautifully."],
        FUSE:  ["Say what you want out loud.",
                "You’re bolder than usual today. Good.",
                "Go after it like it’s already yours."],
    },
    "Jupiter": {
        FLOW:  ["Luck likes you today. Ask for more.",
                "Say yes before you talk yourself out of it.",
                "Be generous first today. Notice what comes back."],
        TENSE: ["More isn’t always better. Choose the best one.",
                "Feelings and promises run big today. Sleep on both.",
                "Don’t overpay to be liked."],
        FUSE:  ["Think bigger. Today can hold it.",
                "Open the door you’ve been eyeing.",
                "Abundance looks good on you."],
    },
    "Saturn": {
        FLOW:  ["Build something that lasts today.",
                "Discipline is sexy. Keep the promise.",
                "Slow and certain wins today."],
        TENSE: ["Not now isn’t never.",
                "Do the hard part first. Then rest without guilt.",
                "The obstacle is teaching you something. Learn it, then get past it."],
        FUSE:  ["Take yourself seriously today. Others will follow.",
                "Your boundaries are beautiful today.",
                "Commit to one thing completely."],
    },
    "Ascendant": {
        FLOW:  ["You’re easy to fall for today.",
                "Walk in like you belong. You do.",
                "First impressions are in your favour."],
        TENSE: ["You don’t owe anyone a smaller you.",
                "Someone’s reacting to your edges. Keep them.",
                "Let them misread you. It isn’t your job."],
        FUSE:  ["Everyone’s looking. Give them something.",
                "Wear the thing you’ve been saving.",
                "Let your presence speak first today."],
    },
    "Midheaven": {
        FLOW:  ["Ambition looks good on you today.",
                "Ask for the opportunity. Out loud.",
                "Let your work be seen today."],
        TENSE: ["Don’t let someone else’s urgency set your pace.",
                "The goal is right. The route needs adjusting.",
                "Ambition and rest both want you. Pick on purpose."],
        FUSE:  ["Step into the role you’ve been rehearsing.",
                "Go for the title.",
                "How people see your work matters today."],
    },
    "Chiron": {
        FLOW:  ["Something that used to hurt feels lighter today.",
                "Healing doesn’t have to be loud.",
                "Be gentle with the old story. It’s changing."],
        TENSE: ["An old ache visits. You’re bigger than it now.",
                "It isn’t the past. It’s an echo.",
                "Treat the tender spot like it belongs to a friend."],
        FUSE:  ["Your softest place is your power today.",
                "What once hurt you is making room for something new.",
                "Be kind to your younger self today."],
    },
}
# Natal points without their own table (the outer planets, the Node...).
OTHER = {
    FLOW:  ["Follow the pull today. It knows something.",
            "Let the day surprise you.",
            "Something easy is opening. Walk through it."],
    TENSE: ["Change is knocking. Open the door slowly.",
            "Hold your ground and your heart at the same time.",
            "Uncomfortable isn’t the same as wrong."],
    FUSE:  ["Something in you is waking up. Let it.",
            "You’re not who you were last month. Good.",
            "Let today change your mind about something."],
}
# Days when nothing strong is touching her chart. The Moon is in a sign every
# day — it changes every two and a half — so it carries the line on a quiet
# day. Still an invitation about the day, never a claim about who she is.
MOON = {
    "Aries":       ["Start the thing. You can perfect it later.",
                    "Say yes before you talk yourself out of it.",
                    "Move your body. The answer is in there."],
    "Taurus":      ["Slow down enough to actually taste it.",
                    "Comfort is not a detour today. It’s the point.",
                    "Treat yourself like someone worth spoiling."],
    "Gemini":      ["Follow the conversation that makes you curious.",
                    "Send the message. Lightly.",
                    "Let yourself change your mind. That’s allowed."],
    "Cancer":      ["Go home to yourself today.",
                    "Feed someone. Let someone feed you.",
                    "Your softness is safe today."],
    "Leo":         ["Let yourself be adored a little today.",
                    "Wear the thing. Take the photo.",
                    "Joy is a good enough reason."],
    "Virgo":       ["One small thing done well is enough.",
                    "Tidy one corner and feel the whole day lift.",
                    "Care for your body like it’s on your side. It is."],
    "Libra":       ["Make something beautiful, even something small.",
                    "Let someone meet you halfway.",
                    "Choose the gentle version of the truth."],
    "Scorpio":     ["Go a layer deeper with someone you trust.",
                    "You’re allowed to want more than you show.",
                    "Keep one thing just for yourself today."],
    "Sagittarius": ["Say yes to something unplanned.",
                    "Go somewhere new, even if it’s a new street.",
                    "Laugh at the thing you’ve been taking seriously."],
    "Capricorn":   ["Build one brick of the life you want.",
                    "Your boundaries are a form of self-respect.",
                    "Do the hard thing first. Then rest without guilt."],
    "Aquarius":    ["Be the version of you nobody planned for.",
                    "Reach out to the friend who gets your weird.",
                    "Step back far enough to see the whole picture."],
    "Pisces":      ["Let the day be dreamy. Not everything needs a plan.",
                    "Make room for something tender.",
                    "Trust the feeling you can’t explain yet."],
}
QUIET = ["Make the day yours on purpose.",
         "The sky is clear. It’s a good day to want something new.",
         "Nothing loud is happening today. Listen to the quiet thing."]

# Any point, by mood: the wider pool a day draws on once her point's own three
# lines have been shown recently. Same copy rules as every table above.
GENERAL = {
    FLOW:  ["Take the easy yes today. It’s allowed.",
            "Something wants to go right. Let it.",
            "Lean on what already works.",
            "Today rewards showing up as you are.",
            "Make the call you’ve been putting off. It’ll go fine.",
            "When something feels easy, that tells you something too.",
            "Let one good thing be enough today.",
            "Do it the way that feels natural."],
    TENSE: ["Let the pressure push you. Just pick a direction.",
            "You don’t have to win the argument today.",
            "Name the friction, then move.",
            "Slow is fine. Stuck is a choice.",
            "Let the hard part be hard. Do it anyway.",
            "Say the true thing, kindly.",
            "One boundary, held, changes the whole day.",
            "Not every itch needs scratching today."],
    FUSE:  ["Turn the volume up on what you want.",
            "Today is loud in you. Use it.",
            "Begin before you feel ready.",
            "Let it be a little intense. You can take it.",
            "Put your whole self into one thing today.",
            "What you start today has weight.",
            "Be the first to say it.",
            "Notice what feels urgent. Then choose."],
}

# "Because the Sun is meeting your Venus" — plain words for each aspect.
VERB = {
    "conjunction": "is meeting", "trine": "is flowing to", "sextile": "is opening to",
    "semisextile": "is nudging", "square": "is pressing on", "opposition": "is pulling against",
    "quincunx": "is unsettling",
}
POINT = {"Ascendant": "rising sign", "Midheaven": "Midheaven", "MC": "Midheaven",
         "IC": "roots point", "Descendant": "partnership point",
         "North Node": "North Node", "Node": "North Node",
         # The engine's own name for it; "your TrueNode" reached the Home line.
         "TrueNode": "North Node"}
MOVER = {"Sun": "the Sun", "Moon": "the Moon", "TrueNode": "the North Node"}


# The reason, about her first. "Because Chiron is meeting your Jupiter" was
# true and told her nothing; now it says which part of her life today touches
# and how, and the planets follow as the receipt.
_WHY_TONE = {FUSE: "puts {area} front and center",
             FLOW: "makes {area} a little easier",
             TENSE: "puts some pressure on {area}"}


def _why(top, natal, who):
    verb = VERB.get(top["aspect"], "is touching")
    point = POINT.get(natal, natal)
    area = corpus.YOUR_AREA.get(natal)
    tone = TONE.get(top["aspect"], FLOW)
    if not area or tone not in _WHY_TONE:
        return f"Because {who} {verb} your {point}."
    return f"Because today {_WHY_TONE[tone].format(area=area)}. {who[0].upper()}{who[1:]} {verb} your {point}."


def sky_of(why):
    """The short planetary reason inside a line's "why", for places that need
    a few words — a notification title, the share card's receipt. Returns the
    planets' sentence without its full stop, or None for a quiet-sky note."""
    if not why or not why.startswith("Because "):
        return None
    if why.startswith("Because today ") and ". " in why:
        return why.rsplit(". ", 1)[1].rstrip(".")
    return why.removeprefix("Because ").rstrip(".")


def _pick(options, day, uid, salt=""):
    h = hashlib.sha256(f"{day}|{uid}|{salt}".encode()).digest()
    return options[h[0] % len(options)]


def line(headlines, day, uid, quiet=False, moon_sign=None):
    """headlines: verdict["headlines"] — dicts with transit, aspect, natal.
    Returns {"text": ..., "why": ...}.

    A low-salience day that still has transits uses the strongest of them —
    it's real, and it's hers. Only a day with nothing within orb falls back to
    the Moon's sign, rather than a line that says nothing is happening: every
    day gets something to hold, and the reason is still true. `quiet` is kept
    for callers; it no longer hides real transits."""
    if not headlines:
        if moon_sign in MOON:
            return {"text": _pick(MOON[moon_sign], day, uid, "moon" + moon_sign),
                    "why": f"Because the Moon is in {moon_sign} today, and nothing else is touching your chart."}
        return {"text": _pick(QUIET, day, uid, "quiet"), "why": "Nothing in the sky is touching your chart strongly today."}
    top = headlines[0]
    tone = TONE.get(top["aspect"], FLOW)
    natal = "Ascendant" if top["natal"] in ("Ascendant", "Rising", "ASC") else top["natal"]
    table = LINES.get(natal, OTHER)
    text = _pick(table[tone], day, uid, natal + tone)
    mover = top["transit"]
    who = MOVER.get(mover, mover)
    why = _why(top, natal, who)
    return {"text": text, "why": why}


# ---------------------------------------------------------- per person
#
# Three lines per point and mood, picked by a day hash: a slow transit holds
# the top headline for weeks, so "The goal is right. The route needs
# adjusting." came round about every third day (audit MEDIUM-7). This keeps
# what each person was shown, and a day draws first on lines she has not seen
# in SEEN_DAYS — her top transit's own lines, then the next two transits', then
# the wider pool for that mood — and only when all are spent, the one she saw
# longest ago. The day's choice is stored, so Today, the widget and the push
# always agree.

SEEN_DAYS = 60
KEEP_DAYS = 90


def _candidates(headlines, moon_sign):
    """[[(text, why)]] in order of preference, every one grounded in a real
    reason; each group carries the salt line() uses, so with nothing seen yet
    the pick is exactly line()'s."""
    out = []
    for top in headlines[:3]:
        tone = TONE.get(top["aspect"], FLOW)
        natal = "Ascendant" if top["natal"] in ("Ascendant", "Rising", "ASC") else top["natal"]
        mover = top["transit"]
        who = MOVER.get(mover, mover)
        why = _why(top, natal, who)
        out.append(_Group(((t, why) for t in LINES.get(natal, OTHER)[tone]), natal + tone))
    if headlines:
        tone = TONE.get(headlines[0]["aspect"], FLOW)
        out.append(_Group(((t, out[0][0][1]) for t in GENERAL[tone]), "general" + tone))
    elif moon_sign in MOON:
        why = f"Because the Moon is in {moon_sign} today, and nothing else is touching your chart."
        out.append(_Group(((t, why) for t in MOON[moon_sign]), "moon" + moon_sign))
    else:
        out.append(_Group(((t, "Nothing in the sky is touching your chart strongly today.") for t in QUIET), "quiet"))
    return out


class _Group(list):
    def __init__(self, items, salt):
        super().__init__(items)
        self.salt = salt


def for_user(headlines, day, uid, quiet=False, moon_sign=None):
    """line(), remembering what she has been shown. Never fails: without the
    store it is line()."""
    from engine.store import load, save
    try:
        seen = load(uid, "daily_seen", {}) or {}
    except Exception:
        return line(headlines, day, uid, quiet, moon_sign)
    if day in seen:
        return seen[day]
    d = dt.date.fromisoformat(day)
    recent = {}
    for k, v in seen.items():
        age = _age(d, k)
        if age is not None and 0 < age <= SEEN_DAYS:
            recent[v["text"]] = min(age, recent.get(v["text"], age))
    pick = None
    for group in _candidates(headlines, moon_sign):
        fresh = [c for c in group if c[0] not in recent]
        if fresh:
            pick = _pick(fresh, day, uid, group.salt)
            break
    if pick is None:
        every = [c for group in _candidates(headlines, moon_sign) for c in group]
        pick = max(every, key=lambda c: recent.get(c[0], SEEN_DAYS + 1))
    out = {"text": pick[0], "why": pick[1]}
    keep = {k: v for k, v in seen.items()
            if _age(d, k) is not None and _age(d, k) <= KEEP_DAYS}
    keep[day] = out
    with contextlib.suppress(Exception):
        save(uid, "daily_seen", keep)
    return out


def _age(d, k):
    try:
        return (d - dt.date.fromisoformat(k)).days
    except ValueError:
        return None

