"""
The interpretation corpus.

Roughly eleven hundred combinations, built compositionally rather than written
out flat — which is not a shortcut, it is how the subject actually works. An
astrologer does not memorise "Saturn in Scorpio in the 8th". They know what
Saturn does, what Scorpio does to whatever it touches, what the 8th house is
about, and they compose. Forty well-made primitives beat eleven hundred
mediocre strings, and they stay consistent with each other.

The piece that keeps it from reading mechanically is **essential dignity**.
Saturn in Libra is exalted and reads as competence; Saturn in Aries is in fall
and reads as the same function under strain. That is real traditional doctrine,
it is computable from the chart, and it is the first thing a practitioner checks.

This is source material handed to the model, not prose shown to a user. It needs
to be accurate, specific and dense — the voice happens downstream.

NOT REVIEWED BY AN ASTROLOGER. It is substantively conventional Western natal
astrology, and it is the thing to put in front of one instead of a blank page.
"""

# --------------------------------------------------------------- primitives

PLANETS = {
    "Sun":     dict(keyword="identity", function="what you are organised around",
                    wants="to matter, and to be seen mattering",
                    costs="everything becomes about whether it counted"),
    "Moon":    dict(keyword="emotional life", function="how you take care of yourself and register feeling",
                    wants="safety, and something familiar to return to",
                    costs="moods arrive before the reasons for them"),
    "Mercury": dict(keyword="mind", function="how you think and how you say it",
                    wants="to understand, and to be understood exactly",
                    costs="the explaining outlasts anyone's patience for it"),
    "Venus":   dict(keyword="desire", function="what you want and how you go about being wanted",
                    wants="closeness on terms that feel good",
                    costs="peace gets chosen over honesty"),
    "Mars":    dict(keyword="drive", function="how you push, and what you do with anger",
                    wants="to act, and to win the thing worth winning",
                    costs="the fight starts before the thinking finishes"),
    "Jupiter": dict(keyword="appetite for more", function="where you expand and what you believe is possible",
                    wants="more room, and a bigger frame to put things in",
                    costs="more is not always better and it takes a while to notice"),
    "Saturn":  dict(keyword="discipline", function="where you meet limits and build something that lasts",
                    wants="to be solid, and to have earned it",
                    costs="nothing feels finished, including you"),
    "Uranus":  dict(keyword="restlessness", function="where you break your own pattern",
                    wants="freedom, and the truth even when it costs the arrangement",
                    costs="disruption for its own sake reads as progress"),
    "Neptune": dict(keyword="imagination", function="where the edges dissolve — imagination, longing, escape",
                    wants="something larger than the literal",
                    costs="you cannot always tell what is true from what you needed"),
    "Pluto":   dict(keyword="intensity", function="what gets stripped down and rebuilt",
                    wants="depth, and control over what already controlled you",
                    costs="you cannot do it halfway even when halfway would do"),
}

# Points that are not planets but turn up in transits every day. Kept out of
# PLANETS so the placement/aspect tables (and coverage()) stay planet-only; the
# transit reading needs them, or about a third of Today's headlines arrived
# with an empty meaning.
POINTS = {
    "TrueNode":  dict(keyword="direction", name="North Node",
                      function="the direction you are growing toward, and the unfamiliar pull of it"),
    "Chiron":    dict(keyword="old sore spot", name="Chiron",
                      function="where an old hurt lives and where you know how to help others with it"),
    "Ascendant": dict(keyword="way of meeting the world", name="rising",
                      function="how you come across and how you start"),
    "Midheaven": dict(keyword="public direction", name="Midheaven",
                      function="career, reputation, what you are aiming at in public"),
}

SIGNS = {
    "Aries":       dict(style="fast and first, without waiting for permission",
                        shadow="starts more than it finishes"),
    "Taurus":      dict(style="slowly and physically, and once it commits, it stays",
                        shadow="digs in past the point of usefulness"),
    "Gemini":      dict(style="through talk, in several directions at once",
                        shadow="skims the surface when depth was the point"),
    "Cancer":      dict(style="protectively, and it remembers everything",
                        shadow="takes it personally and keeps it"),
    "Leo":         dict(style="warmly and visibly, and it wants someone to see it",
                        shadow="needs the audience more than it admits"),
    "Virgo":       dict(style="carefully, noticing what is wrong first",
                        shadow="mistakes the flaw for the whole"),
    "Libra":       dict(style="in relation to someone else, weighing both sides",
                        shadow="loses its own position while finding the fair one"),
    "Scorpio":     dict(style="privately and completely, or not at all",
                        shadow="controls by withholding"),
    "Sagittarius": dict(style="broadly, chasing the bigger point",
                        shadow="leaves before the difficult part"),
    "Capricorn":   dict(style="patiently, building toward something structural",
                        shadow="postpones the living until the work is done"),
    "Aquarius":    dict(style="at a slight distance, seeing the bigger pattern rather than the single case",
                        shadow="detaches exactly when presence was needed"),
    "Pisces":      dict(style="openly, absorbing whatever mood is in the room",
                        shadow="struggles to find the line between itself and everyone else"),
}

HOUSES = {
    1:  "how you come across and how you start",
    2:  "money, worth, and what you hold onto",
    3:  "how you talk, siblings, and everyday life",
    4:  "home, family, and where you come from",
    5:  "romance, play, and whatever you make",
    6:  "work, routine, health, the daily grind",
    7:  "partnership, and who you become around one person",
    8:  "intimacy, other people's resources, what stays hidden",
    9:  "belief, travel, and what broadens your outlook",
    10: "career, reputation, what you are known for",
    11: "friends, groups, and the future you want",
    12: "solitude, memory, and what runs beneath the surface",
}

ASPECTS = {
    "conjunction": dict(quality="are fused, so they never operate separately in you",
                        tone="this makes both louder, without being good or bad in itself"),
    "opposition":  dict(quality="pull against each other, a tug-of-war you keep negotiating rather than settling",
                        tone="it usually shows up through other people"),
    "square":      dict(quality="rub against each other, and the friction keeps you moving",
                        tone="it's uncomfortable, and it's often the reason you act"),
    "trine":       dict(quality="flow together easily, and you can rely on that",
                        tone="because it comes so easily, you may under-use it"),
    "sextile":     dict(quality="can help each other, but only if you take the opening",
                        tone="it's available, but it doesn't happen automatically"),
}

# ------------------------------------------------------- essential dignity
# Traditional rulerships, matching engine/interpret.RULER. This is the table an
# astrologer checks first, and the difference between a planet working with the
# grain and against it.

DOMICILE = {"Sun": ["Leo"], "Moon": ["Cancer"], "Mercury": ["Gemini", "Virgo"],
            "Venus": ["Taurus", "Libra"], "Mars": ["Aries", "Scorpio"],
            "Jupiter": ["Sagittarius", "Pisces"], "Saturn": ["Capricorn", "Aquarius"]}
EXALTATION = {"Sun": "Aries", "Moon": "Taurus", "Mercury": "Virgo",
              "Venus": "Pisces", "Mars": "Capricorn", "Jupiter": "Cancer",
              "Saturn": "Libra"}
OPPOSITE = {"Aries": "Libra", "Taurus": "Scorpio", "Gemini": "Sagittarius",
            "Cancer": "Capricorn", "Leo": "Aquarius", "Virgo": "Pisces",
            "Libra": "Aries", "Scorpio": "Taurus", "Sagittarius": "Gemini",
            "Capricorn": "Cancer", "Aquarius": "Leo", "Pisces": "Virgo"}

DIGNITY_NOTE = {
    # About her, not about the rule. "Under strain in this sign" described the
    # planet; these describe what it's like to be her with it.
    "domicile":  "this comes naturally to you, and you don't have to work at it",
    "exalted":   "this is one of your real strengths, even if you don't give "
                 "yourself credit for it",
    "detriment": "this doesn't come easily, and that isn't a flaw: you've had to "
                 "work at it, so you understand it better than most people",
    "fall":      "this part of you can come out awkwardly under pressure, so be "
                 "patient with it; it's real even when it's clumsy",
}


def dignity(planet, sign):
    """domicile / exalted / detriment / fall / None."""
    if sign in DOMICILE.get(planet, []):
        return "domicile"
    if EXALTATION.get(planet) == sign:
        return "exalted"
    if any(OPPOSITE[s] == sign for s in DOMICILE.get(planet, [])):
        return "detriment"
    ex = EXALTATION.get(planet)
    if ex and OPPOSITE[ex] == sign:
        return "fall"
    return None


# ---------------------------------------------------------------- composers
# Each returns structured facts, not finished prose. The model writes the voice;
# these decide what is true about the combination.

def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1:'st',2:'nd',3:'rd'}.get(n % 10,'th')}"


def placement(planet, sign, house=None):
    """A planet in a sign, optionally in a house. The most-asked combination."""
    p, s = PLANETS.get(planet), SIGNS.get(sign)
    if not p or not s:
        return None
    dig = dignity(planet, sign)
    out = {
        "planet": planet, "sign": sign, "house": house,
        "keyword": p["keyword"],
        "function": p["function"],
        "expressed": s["style"],
        "wants": p["wants"],
        "costs": p["costs"],
        "shadow": s["shadow"],
        "dignity": dig,
        "dignity_note": DIGNITY_NOTE[dig] if dig else None,
        "reading": f"Your {p['keyword']} works {s['style']}.",
    }
    if house:
        out["arena"] = HOUSES[house]
        out["reading"] += f" It plays out in {HOUSES[house]}."
    if dig:
        out["reading"] += f" {DIGNITY_NOTE[dig].capitalize()}."
    return out


# Two parts of her that are linked for life, said about her. The pair and the
# orb travel separately as the receipt.
LINK_FOR_YOU = {
    "conjunction": "{a} and {b} are fused in you. When one is moved, so is the other.",
    "trine": "{a} and {b} work together easily. You can lean on that, and you probably under-use it.",
    "sextile": "{a} and {b} can help each other, when you make the effort.",
    "square": "{a} and {b} rub against each other. It's uncomfortable, and it's often what gets you moving.",
    "opposition": "{a} and {b} pull you in two directions, and you keep finding a balance between them.",
}


def link_for_you(a, b, aspect):
    """A natal aspect as a sentence about her, or None without a table entry."""
    pa, pb = YOUR_AREA.get(a), YOUR_AREA.get(b)
    t = LINK_FOR_YOU.get(aspect)
    if not (pa and pb and t) or a == b:
        return None
    out = t.format(a=pa, b=pb)
    return out[0].upper() + out[1:]


def aspect_between(a, b, aspect):
    """Two natal placements in relationship."""
    pa, pb, asp = PLANETS.get(a), PLANETS.get(b), ASPECTS.get(aspect)
    if not pa or not pb or not asp or a == b:
        return None
    mechanism = (f"Your {pa['keyword']} and your {pb['keyword']} "
                 f"{asp['quality']}. {asp['tone'][0].upper()}{asp['tone'][1:]}.")
    return {
        "pair": f"{a} {aspect} {b}", "aspect": aspect,
        "quality": asp["quality"], "tone": asp["tone"],
        "reading": link_for_you(a, b, aspect) or mechanism,
        "mechanism": mechanism,
        "tension": aspect in ("square", "opposition"),
    }


# How a moving planet touches a fixed one, for today. ASPECTS describes two
# natal planets bound for life ("fused, so they never operate separately in
# you"), and the transit gloss used to borrow it: "Mars is meeting your identity
# — fused, so they never operate separately in you. It passes quickly." A square
# was still "meeting". The model echoed it (audit MEDIUM-5).
TRANSIT_ASPECTS = {
    "conjunction": dict(verb="is arriving on",
                        quality="that part of you is switched on and louder than usual"),
    "square":      dict(verb="is pressing on",
                        quality="it chafes, and the friction pushes you to act"),
    "opposition":  dict(verb="is pulling against",
                        quality="it tends to show up through someone else, asking for balance"),
    "trine":       dict(verb="is flowing to",
                        quality="that part of you comes easier than usual"),
    "sextile":     dict(verb="is opening a door to",
                        quality="there is an opening, if you take it"),
}


# ---------------------------------------------------- transits, about her
#
# The reading used to be built from the mechanism: "Chiron is arriving on your
# appetite for more: that part of you is switched on and louder than usual."
# Every word was true, and she had to translate it into her own life herself.
# Now the sentence is about her life — which part of it, and what today does
# to it — and the planets travel with it as the reason, shown underneath.

# The part of her life each natal point stands for, as the object of a sentence.
YOUR_AREA = {
    "Sun": "who you are", "Moon": "your heart", "Mercury": "what's on your mind",
    "Venus": "your love life", "Mars": "what you're going after",
    "Jupiter": "your hopes", "Saturn": "your responsibilities",
    "Uranus": "your need for freedom", "Neptune": "your dreams",
    "Pluto": "what you keep private", "Chiron": "an old hurt",
    "TrueNode": "the direction you're growing in", "Ascendant": "how you come across",
    # One noun phrase each, no "and": two of them get joined in one sentence.
    "Midheaven": "your career", "IC": "your home life",
    "Descendant": "your closest relationships",
}

# What each moving planet does to that part of her life: meeting it (fuse),
# easing it (flow), or pressing on it (tense). Fast movers speak of today;
# slow ones of right now, and say how long.
_TRANSIT_TONE = {"conjunction": "fuse", "trine": "flow", "sextile": "flow",
                 "square": "tense", "opposition": "tense"}
YOUR_DAY = {
    "Sun": dict(
        fuse="Today shines a light on {area}. You'll notice it more, and so will other people.",
        flow="Today brings a little warmth and confidence to {area}.",
        tense="Today puts {area} under a spotlight you didn't ask for. Notice what makes you defensive."),
    "Moon": dict(
        fuse="Your mood is tied up with {area} today, and it can change by the hour.",
        flow="There's an easy, soft feeling around {area} today.",
        tense="You may feel touchy about {area} today. It passes within a day or two."),
    "Mercury": dict(
        fuse="You'll be thinking and talking about {area} today.",
        flow="It's easier to say what you mean about {area} today.",
        tense="Your thoughts about {area} might loop or come out wrong today. Read it twice before you send it."),
    "Venus": dict(
        fuse="Today brings some sweetness to {area}. Let yourself enjoy it.",
        flow="There's something pleasant and easy around {area} today.",
        tense="You may crave comfort or approval around {area} today."),
    "Mars": dict(
        fuse="There's extra heat and urgency around {area} today. Use it, but don't pick the fight.",
        flow="You have energy for {area} today, so act on it.",
        tense="Things around {area} could feel irritating or rushed today. Count to ten first."),
    "Jupiter": dict(
        fuse="There's room to grow around {area} right now. Let yourself want more.",
        flow="Luck and room are on your side around {area} right now.",
        tense="You might overdo things around {area} right now. More isn't always better."),
    "Saturn": dict(
        fuse="Life is testing {area} right now. It's asking you to grow up about something, and what you build now lasts.",
        flow="Effort you put into {area} right now holds.",
        tense="Things around {area} feel heavier than usual right now. It's a test of patience, not a verdict."),
    "Uranus": dict(
        fuse="Expect surprises around {area} right now, and room to change.",
        flow="There's room for something new around {area} right now.",
        tense="You may feel restless about {area} right now. Change something, but don't blow it all up to feel free."),
    "Neptune": dict(
        fuse="Things around {area} feel dreamy and blurry right now. Enjoy it, but trust it a little less.",
        flow="There's extra imagination and softness around {area} right now.",
        tense="You might not see {area} clearly right now. Wait for the fog to lift before you decide."),
    "Pluto": dict(
        fuse="Something deep is changing around {area} right now, and something old is being cleared out.",
        flow="You have real power over {area} right now.",
        tense="Things around {area} feel intense, maybe a little out of your hands, right now. It's a long, deep shift."),
    "Chiron": dict(
        fuse="An old tender spot around {area} is close to the surface right now, and so is the chance to heal it.",
        flow="You're in a good place to make peace with {area} right now.",
        tense="Something about {area} might sting right now. It's an old wound asking for care, not a new one."),
    "TrueNode": dict(
        fuse="Life is pointing {area} toward the person you're becoming.",
        flow="Growth around {area} comes more naturally right now.",
        tense="You may feel torn between old habits and new growth around {area}."),
}


def transit_for_you(transiting, natal_point, aspect):
    """The personal sentence for a transit, or None when there is no table
    entry (the caller falls back to the mechanism-led reading)."""
    tone = _TRANSIT_TONE.get(aspect)
    area = YOUR_AREA.get(natal_point)
    day = YOUR_DAY.get(transiting)
    if not tone or not area or not day:
        return None
    s = day[tone].format(area=area)
    return s[0].upper() + s[1:]


def transit(transiting, natal_point, aspect):
    """A moving planet meeting a fixed one. What the daily reading is built on."""
    pt = PLANETS.get(transiting) or POINTS.get(transiting)
    pn = PLANETS.get(natal_point) or POINTS.get(natal_point)
    asp = TRANSIT_ASPECTS.get(aspect)
    if not pt or not pn or not asp:
        return None
    slow = transiting in ("Jupiter", "Saturn", "Uranus", "Neptune", "Pluto",
                          "TrueNode", "Chiron")
    mover = pt.get("name", transiting)
    mechanism = (f"{'The ' if transiting == 'TrueNode' else ''}{mover} {asp['verb']} your {pn['keyword']}: "
                 f"{asp['quality']}.")
    you = transit_for_you(transiting, natal_point, aspect)
    reading = (f"{you} This lasts a season, not just a day." if you and slow
               else you or f"{mechanism} {'This lasts a season, not just a day.' if slow else 'It passes quickly.'}")
    return {
        "transit": f"{transiting} {aspect} natal {natal_point}",
        "moving": pt["function"], "fixed": pn["function"],
        "quality": asp["quality"],
        "duration": "weeks to months — this is a season, not a day" if slow
                    else "days — it passes",
        # About her first; the mechanism is kept for anyone who wants it.
        "reading": reading,
        "mechanism": mechanism,
        "slow": slow,
    }


def synastry(their_planet, my_house):
    """Their planet landing in one of your houses — the overlay that explains
    why one person changes a particular area of your life."""
    p = PLANETS.get(their_planet)
    if not p or my_house not in HOUSES:
        return None
    return {
        "overlay": f"their {their_planet} in your {_ordinal(my_house)}",
        "their_function": p["function"],
        "your_arena": HOUSES[my_house],
        "reading": f"Their {p['keyword']} lands in your {_ordinal(my_house)} house: "
                   f"{HOUSES[my_house]}. That part of your life stirs when they're around.",
    }


def coverage():
    """Every combination this corpus can answer — the number that used to be 60."""
    signs, houses = list(SIGNS), list(HOUSES)
    planets, aspects = list(PLANETS), list(ASPECTS)
    pairs = [(a, b) for i, a in enumerate(planets) for b in planets[i + 1:]]
    return {
        "planet_sign": len(planets) * len(signs),
        "planet_sign_house": len(planets) * len(signs) * len(houses),
        "aspect_pairs": len(pairs) * len(aspects),
        "transits": len(planets) * len(planets) * len(aspects),
        "synastry": len(planets) * len(houses),
        "dignities": sum(1 for p in planets for s in signs if dignity(p, s)),
    }
