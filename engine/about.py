"""
What she does with her days: hobbies, what she's into, what she's good at,
what lights her up. Asked once, right after the chart is built, and editable
from Settings.

The chart says what's possible; this says what she actually does with it. A
transit to the 5th house is a sentence about "creativity" until the model
knows she throws pots on Thursdays — then it's about Thursday.

Every option carries the planet that traditionally rules it and an element, so
the app can show where it sits in her chart ("your Venus is in Taurus") and
compare how her chart is built with how she spends her time. Those tags are
for display only: the model gets her choices in her words and nothing else,
so nothing here can vouch for a placement she doesn't have.
"""
import re
import time

from engine import interpret as I

STEPS = ("hobbies", "interests", "skills", "passions")
LIMITS = {"hobbies": 12, "interests": 8, "skills": 5, "passions": 3}
CUSTOM_MAX = 3           # her own words, per list
LABEL_MAX = 32
DREAM_MAX = 120
PLANETS = ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn")
ELEMENTS = ("fire", "earth", "air", "water")


def _o(id_, label, planet, element, line=None):
    o = {"id": id_, "label": label, "planet": planet, "element": element}
    if line:
        o["line"] = line
    return o


CATALOG = {
    # The swipe deck: one card each, so each has a line.
    "hobbies": [
        _o("hiking", "Hiking", "Jupiter", "fire", "Up early for a view you have to earn."),
        _o("running", "Running", "Mars", "fire", "Headphones in, problems out."),
        _o("gym", "Lifting & the gym", "Mars", "earth", "Progress you can actually measure."),
        _o("yoga", "Yoga & pilates", "Moon", "water", "Breathe first, then deal with it."),
        _o("dancing", "Dancing", "Sun", "fire", "The song comes on and you're gone."),
        _o("cooking", "Cooking & baking", "Moon", "water", "Feeding people is how you say it."),
        _o("reading", "Reading", "Mercury", "air", "Three books on the go, minimum."),
        _o("writing", "Writing & journaling", "Mercury", "air",
           "You don't know what you think until it's on paper."),
        _o("art", "Painting & drawing", "Venus", "water", "Hands busy, mind finally quiet."),
        _o("music", "Making music", "Venus", "air", "Playing, singing, or a playlist for every mood."),
        _o("photo", "Photography", "Venus", "air", "You see the shot before anyone else does."),
        _o("plants", "Plants & gardening", "Saturn", "earth", "Something alive that needs you, slowly."),
        _o("thrifting", "Thrifting & fashion", "Venus", "earth", "The find is the whole point."),
        _o("gaming", "Gaming", "Mars", "air", "One more round. Then sleep. Probably."),
        _o("travel", "Travel", "Jupiter", "fire", "Already planning the next one."),
        _o("tarot", "Tarot & ritual", "Moon", "water", "Candle lit, deck shuffled, question asked."),
    ],
    "interests": [
        _o("psychology", "Psychology", "Moon", "water"),
        _o("true_crime", "True crime", "Mars", "water"),
        _o("history", "History", "Saturn", "earth"),
        _o("science", "Science", "Mercury", "air"),
        _o("tech", "Tech", "Mercury", "air"),
        _o("philosophy", "Philosophy", "Jupiter", "fire"),
        _o("film", "Film & TV", "Sun", "fire"),
        _o("fashion", "Fashion", "Venus", "earth"),
        _o("beauty", "Beauty", "Venus", "air"),
        _o("food", "Food", "Venus", "earth"),
        _o("wellness", "Wellness", "Mercury", "earth"),
        _o("astrology", "Astrology", "Moon", "water"),
        _o("spirituality", "Spirituality", "Jupiter", "water"),
        _o("causes", "Politics & causes", "Saturn", "air"),
        _o("money", "Money & investing", "Saturn", "earth"),
        _o("design", "Design & interiors", "Venus", "air"),
        _o("nature", "Nature & animals", "Venus", "earth"),
        _o("pop_culture", "Pop culture", "Mercury", "air"),
        _o("relationships", "Love & relationships", "Venus", "water"),
        _o("sports", "Sports", "Mars", "fire"),
    ],
    "skills": [
        _o("listening", "Listening & advice", "Moon", "water"),
        _o("planning", "Planning & organising", "Saturn", "earth"),
        _o("funny", "Making people laugh", "Sun", "fire"),
        _o("words", "Writing & words", "Mercury", "air"),
        _o("explaining", "Explaining things", "Jupiter", "fire"),
        _o("hosting", "Bringing people together", "Venus", "fire"),
        _o("style", "Styling & taste", "Venus", "earth"),
        _o("fixing", "Fixing things", "Mars", "earth"),
        _o("leading", "Leading", "Sun", "fire"),
        _o("negotiating", "Negotiating", "Venus", "air"),
        _o("calm", "Staying calm in a crisis", "Saturn", "water"),
        _o("reading_people", "Reading people", "Mars", "water"),
        _o("money_smarts", "Money smarts", "Saturn", "earth"),
        _o("ideas", "Ideas, constantly", "Mercury", "air"),
        _o("making", "Making things by hand", "Venus", "earth"),
        _o("coding", "Coding & tech", "Mercury", "earth"),
    ],
    "passions": [
        _o("creating", "Making art", "Sun", "fire"),
        _o("building", "Building something of my own", "Saturn", "earth"),
        _o("healing", "Helping people heal", "Moon", "water"),
        _o("adventure", "Adventure & seeing the world", "Jupiter", "fire"),
        _o("home", "Family & home", "Moon", "water"),
        _o("mastery", "Getting really good at something", "Saturn", "earth"),
        _o("justice", "Causes & justice", "Saturn", "air"),
        _o("love", "Love & romance", "Venus", "water"),
        _o("freedom", "Freedom", "Mercury", "air"),
        _o("performing", "Performing & being seen", "Sun", "fire"),
        _o("outdoors", "Nature & the outdoors", "Venus", "earth"),
        _o("meaning", "Spirituality & meaning", "Jupiter", "water"),
        _o("learning", "Learning everything", "Mercury", "air"),
    ],
}
_BY_ID = {step: {o["id"]: o for o in opts} for step, opts in CATALOG.items()}

# How each element lives, in a few words — for the elemental-mix reveal.
LIVES = {"fire": "movement and momentum", "earth": "what's real and made by hand",
         "air": "ideas and people", "water": "feeling and closeness"}

_CTRL = re.compile(r"[\x00-\x1f\x7f<>]")


def _text(s, limit):
    s = _CTRL.sub("", str(s or "")).strip()
    return re.sub(r"\s+", " ", s)[:limit]


def clean(data):
    """Her answers, normalised, or ValueError with a sentence she can read.

    Catalog picks are kept by id; anything else is her own words, capped in
    length and number so a list can't become a diary."""
    out = {}
    for step in STEPS:
        raw = data.get(step) or []
        if not isinstance(raw, list):
            raise ValueError("That list didn't come through right.")
        seen, picks, custom = set(), [], 0
        for item in raw:
            t = _text(item, LABEL_MAX)
            if not t or t.lower() in seen:
                continue
            if t not in _BY_ID[step]:
                custom += 1
                if custom > CUSTOM_MAX:
                    raise ValueError(f"Up to {CUSTOM_MAX} of your own per list.")
            seen.add(t.lower())
            picks.append(t)
        if len(picks) > LIMITS[step]:
            raise ValueError(f"Pick up to {LIMITS[step]} for that one.")
        out[step] = picks
    out["dream"] = _text(data.get("dream"), DREAM_MAX)
    return out


def done(about):
    return bool(about) and (any(about.get(s) for s in STEPS) or bool(about.get("dream")))


def label(step, item):
    o = _BY_ID[step].get(item)
    return o["label"] if o else item


def chart_view(natal, time_known=True):
    """Where each ruling planet sits in her chart, for the cards — and how the
    chart's elements balance, for the reveal."""
    if not natal:
        return None
    planets = {}
    for name in PLANETS:
        p = natal["planets"][name]
        text = f"{name} in {p['sign']}"
        if time_known and p.get("house"):
            text += f", {_ordinal(p['house'])} house"
        planets[name] = {"text": text, "sign": p["sign"], "element": I.ELEMENT[p["sign"]]}
    counts = I.analyze(natal, time_known)["element_counts"]
    return {"planets": planets, "elements": _share(counts)}


def _share(counts):
    total = sum(counts.get(e, 0) for e in ELEMENTS) or 1
    return {e: round(100 * counts.get(e, 0) / total) for e in ELEMENTS}


def mix(about, chart):
    """How she spends her time, by element, next to how her chart is built.
    Only catalog picks count — her own words have no element."""
    counts = {}
    for step in STEPS:
        for item in (about or {}).get(step, []):
            o = _BY_ID[step].get(item)
            if o:
                counts[o["element"]] = counts.get(o["element"], 0) + 1
    if not counts:
        return None
    life = _share(counts)
    lived = max(ELEMENTS, key=lambda e: (life[e], -ELEMENTS.index(e)))
    out = {"life": life, "lived": lived}
    if chart:
        built = max(ELEMENTS, key=lambda e: (chart["elements"][e], -ELEMENTS.index(e)))
        out.update(chart=chart["elements"], built=built)
        if built == lived:
            out["text"] = (f"Your chart leans {built}, and so does the way you spend your time: "
                           f"{LIVES[lived]}. No wonder it feels like you.")
        else:
            out["text"] = (f"Your chart leans {built}: {LIVES[built]}. But you fill your days "
                           f"with {lived}: {LIVES[lived]}. That isn't a contradiction. "
                           f"It's where you go to feel balanced.")
    return out


def context_lines(about):
    """For the chat briefing. Her words only — no planets, no signs."""
    if not done(about):
        return []
    L = ["ABOUT HER (she chose these herself. Use one when it makes the sky concrete — "
         "a 5th-house day and the thing she makes, a Mars day and the run. Never list them "
         "back to her, never invent others)"]
    for step, head in (("hobbies", "does"), ("interests", "into"),
                       ("skills", "good at"), ("passions", "lights her up")):
        items = [label(step, i) for i in about.get(step, [])]
        if items:
            L.append(f"  {head}: {', '.join(items)}")
    if about.get("dream"):
        L.append(f"  would do it for free: \"{about['dream']}\"")
    return L


def stamp(about):
    return {**about, "updated_at": int(time.time())}


def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
