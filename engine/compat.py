"""
Deep compatibility — everything two charts say about each other, itemised.

synastry.score() is the calibrated number. This is what sits underneath it when
she opens someone: every contact read in both directions, grouped into the
themes people actually ask about, the relationship's own (composite) chart,
where each of them lands in the other's houses, how the elements balance across
both whole charts, and what the sky is doing to the pair right now.

Two rules carried over from the rest of the engine:

1. Nothing here is estimated by a model. Positions come from the ephemeris,
   contacts from geometry, meanings from the tables below. The model only
   narrates what this returns.

2. Uncertainty is measured, not hidden. A birth time someone isn't sure of is
   not a point, it is a window — and the Moon crosses six or seven degrees of
   the zodiac in it, the Ascendant a whole sign every two hours. So each chart
   is sampled across its window, every contact is checked in every sample pair,
   and each one carries the share of the window it holds for. A Moon contact
   that only exists if she was born before 9am is reported as exactly that, and
   the score is given as the range it actually spans rather than one number
   pretending to know the birth time.
"""
import datetime as dt
import itertools
import re
import statistics

from engine import glossary, models, tradition
from engine import synastry as S
from engine.chart import SIGNS, house_of, jd_ut, natal, positions

# --------------------------------------------------------------- precision
# Half-width of the birth-time window, in hours, and how many samples span it.
# "approximate" is the honest default for "around 3pm" or "my mom thinks the
# morning"; "unknown" is the whole day.
PRECISION = {
    "exact": (0.0, 1),
    "approximate": (1.0, 5),
    "unknown": (12.0, 13),
}
PRECISIONS = tuple(PRECISION)

# 5: date-only pairs (and pairs where only the other person's time is known)
#    are normalised against their own reference distribution
#    (engine/synastry.py REFERENCE "none"/"partial_b"); they used to borrow
#    "partial" and read about fourteen points low.
REPORT_VERSION = 5
STABLE = 0.95         # holds across (nearly) the whole window
LIKELY = 0.70         # holds across most of it
KEEP = 0.50           # below this a contact is a guess, and is left out

# ------------------------------------------------------------------ points
# The calibrated score uses synastry.POINTS. The deep report adds the two
# points people ask about by name — the lunar node and Chiron — with tighter
# orbs, because they are read as signatures rather than as forces.
EXTRA = {"TrueNode": "North Node", "Chiron": "Chiron"}
EXTRA_ORB = 3.0
OUTER = {"Uranus", "Neptune", "Pluto", "Chiron", "TrueNode"}

KIND_WEIGHTS = {
    # How the three dimensions combine depends on what the relationship is.
    # Attraction matters to a partner and barely at all to a coworker.
    "romantic": {"spark": 0.40, "ease": 0.30, "staying": 0.30},
    "friend":   {"spark": 0.20, "ease": 0.50, "staying": 0.30},
    "family":   {"spark": 0.15, "ease": 0.40, "staying": 0.45},
    "work":     {"spark": 0.20, "ease": 0.40, "staying": 0.40},
}
KINDS = tuple(KIND_WEIGHTS)


_KIND_WORDS = {
    "family": ("mom", "mum", "dad", "mother", "father", "sister", "brother", "sibling",
               "aunt", "uncle", "cousin", "grandma", "grandpa", "grandmother",
               "grandfather", "son", "daughter", "niece", "nephew", "family", "stepmom",
               "stepdad", "parent"),
    "work": ("boss", "coworker", "co-worker", "colleague", "manager", "cofounder",
             "co-founder", "client", "employee", "work", "team", "mentor"),
    "friend": ("friend", "bestie", "bff", "roommate", "flatmate", "housemate"),
    "romantic": ("bf", "gf", "boyfriend", "girlfriend", "husband", "wife", "partner",
                 "dating", "date", "crush", "situationship", "talking", "ex", "fiance",
                 "fiancé", "fiancee", "lover", "hookup", "spouse", "seeing"),
}


def infer_kind(relation):
    """Best guess from the free-text relation. Explicit choice always wins."""
    words = (relation or "").lower().replace(",", " ").split()
    text = " ".join(words)
    for kind in ("family", "work", "friend", "romantic"):
        if any(w in words or (" " in w and w in text) for w in _KIND_WORDS[kind]):
            return kind
    return "romantic"


# ---------------------------------------------------------------- meanings
# What each point is, in the two voices a contact needs: theirs and hers.
NAME = {"TrueNode": "North Node"}

KEYWORD = {
    "Sun": "sense of self", "Moon": "feelings", "Mercury": "mind",
    "Venus": "affection", "Mars": "drive", "Jupiter": "generosity",
    "Saturn": "seriousness", "Uranus": "unpredictability", "Neptune": "idealism",
    "Pluto": "intensity", "Ascendant": "first impression", "Midheaven": "direction",
    "TrueNode": "path", "Chiron": "old wound",
}

FAMILY = {"conjunction": "fused", "trine": "flow", "sextile": "flow",
          "square": "friction", "opposition": "friction"}

# Hand-written readings for the pairs that carry a relationship. Keyed by the
# unordered pair, then the aspect family. A pair not listed falls back to a
# reading built from the keywords — plainer, but never invented.
PAIR = {
    ("Moon", "Sun"): {
        "fused": "This is the classic marriage contact: one of you is the person the other feels at home with.",
        "flow": "Who one of you is fits easily with what the other needs, so you rarely have to explain yourselves.",
        "friction": "Who one of you is and what the other needs pull in different directions. It's real, but you can work with it.",
    },
    ("Sun", "Sun"): {
        "fused": "You share the same core, so you recognise each other fast — and can end up competing for the same space.",
        "flow": "Your identities support each other. You make each other more yourselves.",
        "friction": "You have very different ways of being yourselves, which is interesting and sometimes tiring.",
    },
    ("Moon", "Moon"): {
        "fused": "You feel things the same way, so you can comfort each other without explaining.",
        "flow": "Your emotional rhythms fit, so one person's mood rarely knocks the other off balance.",
        "friction": "When things get hard, you each need something different at the same moment.",
    },
    ("Mars", "Venus"): {
        "fused": "This is the classic attraction contact: one of you wants, and the other wants to be wanted.",
        "flow": "The chemistry comes easily — wanting and being wanted line up.",
        "friction": "The attraction is hot and a little combative: the pull is real, and so are the arguments.",
    },
    ("Venus", "Venus"): {
        "fused": "You like the same things and love in the same way.",
        "flow": "You have similar tastes in comfort and pleasure, and show affection in similar ways.",
        "friction": "You have different love languages, so each of you gives what the other doesn't quite want.",
    },
    ("Mars", "Mars"): {
        "fused": "You have the same kind of drive, so you either move together or collide head-on.",
        "flow": "You get things done together without stepping on each other.",
        "friction": "You get competitive. Fights start fast, so it helps to agree on a way to cool down.",
    },
    ("Moon", "Venus"): {
        "fused": "There's real tenderness here: one of you makes the other feel cared for without even trying.",
        "flow": "Affection and comfort line up, which makes this a soft, easy connection.",
        "friction": "Affection is offered in a form that doesn't quite soothe.",
    },
    ("Mars", "Moon"): {
        "fused": "Feelings get stirred up fast between you. You can be fiercely protective of each other, and quick to react.",
        "flow": "Energy from one of you lifts the other's mood.",
        "friction": "When one of you comes on fast or angry, it lands hard on the other's feelings.",
    },
    ("Sun", "Venus"): {
        "fused": "There's warm admiration here: one of you simply likes who the other is.",
        "flow": "You're fond of each other without having to try.",
        "friction": "You value different things, but the affection between you is still real.",
    },
    ("Mars", "Sun"): {
        "fused": "This is energising: one of you pushes the other into action.",
        "flow": "You spur each other on — good for doing things together.",
        "friction": "Your wills clash, and you can end up in power struggles over who leads.",
    },
    ("Mercury", "Mercury"): {
        "fused": "You think alike, so conversations flow and you finish each other's sentences.",
        "flow": "Talking is easy. You get each other's references.",
        "friction": "You process differently — and misread each other's tone.",
    },
    ("Mercury", "Moon"): {
        "fused": "One of you can put the other's feelings into words.",
        "flow": "Talking about feelings works between you.",
        "friction": "Logic meets feeling: one of you explains, and the other feels unheard.",
    },
    ("Mercury", "Sun"): {
        "fused": "One of you quickly understands what the other is about.",
        "flow": "You connect easily on ideas and are genuinely interested in each other's thinking.",
        "friction": "One of you can come across as critical of who the other is.",
    },
    ("Mercury", "Venus"): {
        "fused": "You sweet-talk each other, and your words land as affection.",
        "flow": "You say kind things easily, and mean them.",
        "friction": "Compliments don't always land where they're aimed.",
    },
    ("Mars", "Mercury"): {
        "fused": "Your conversations are fast and sharp — banter or arguments, sometimes both.",
        "flow": "Ideas turn into action between you.",
        "friction": "Debates can turn into fights, so watch your tone.",
    },
    ("Saturn", "Sun"): {
        "fused": "This brings commitment and weight: one of you takes the other seriously — sometimes too seriously.",
        "flow": "This is stabilising: one of you keeps the other's ambitions grounded.",
        "friction": "One of you can feel judged or held back by the other.",
    },
    ("Moon", "Saturn"): {
        "fused": "This bond is loyal and serious. It can feel safe, or it can feel cold.",
        "flow": "There's steady emotional support here: one of you is a rock for the other.",
        "friction": "Feelings meet restraint: one of you needs warmth that the other holds back.",
    },
    ("Saturn", "Venus"): {
        "fused": "This is a sign of commitment: love that means to last, with a sense of duty in it.",
        "flow": "Your affection has staying power. It isn't flashy, but it lasts.",
        "friction": "Love can come with conditions, and one of you may feel the other is holding back.",
    },
    ("Mars", "Saturn"): {
        "fused": "One of you puts the brakes on the other's drive, which can feel like discipline or like frustration.",
        "flow": "Your effort turns into structure, which is good for building something together.",
        "friction": "Things go stop-start: one of you pushes and the other blocks.",
    },
    ("Mercury", "Saturn"): {
        "fused": "Your talks are serious — useful, but sometimes heavy.",
        "flow": "Plans actually get made, and what you say to each other carries weight.",
        "friction": "One of you can make the other feel stupid or unheard.",
    },
    ("Jupiter", "Sun"): {
        "fused": "This is generous: one of you makes the other feel bigger.",
        "flow": "There's optimism and support here, and things feel possible together.",
        "friction": "You can overpromise, letting enthusiasm run ahead of reality.",
    },
    ("Jupiter", "Moon"): {
        "fused": "You're emotionally generous with each other, comforting and warm.",
        "flow": "One of you lifts the other's mood without effort.",
        "friction": "Feelings and promises run big, and sometimes go too far.",
    },
    ("Jupiter", "Venus"): {
        "fused": "Together you're indulgent, affectionate and a lot of fun.",
        "flow": "Pleasure and kindness come easily together.",
        "friction": "You can overdo the good things — spending, sweetness, and avoiding the hard conversations.",
    },
    ("Pluto", "Venus"): {
        "fused": "The pull is magnetic and possessive, and hard to walk away from.",
        "flow": "There's a deep attraction here that changes both of you.",
        "friction": "Obsession and control can creep into the affection.",
    },
    ("Moon", "Pluto"): {
        "fused": "This is emotionally intense: you see each other's depths — all of them.",
        "flow": "You share a profound emotional bond that can heal, if you handle it with care.",
        "friction": "Feelings get pushed hard, and jealousy or power plays can creep in.",
    },
    ("Pluto", "Sun"): {
        "fused": "This is transforming: one of you changes who the other is.",
        "flow": "This is empowering: you bring out each other's strength.",
        "friction": "You can struggle over control, and one of you can feel taken over.",
    },
    ("Mars", "Pluto"): {
        "fused": "The energy between you is raw, physical and high-voltage.",
        "flow": "Together your drive could move mountains.",
        "friction": "This is volatile: anger escalates fast between you.",
    },
    ("Uranus", "Venus"): {
        "fused": "The attraction is electric and unpredictable — exciting, but not restful.",
        "flow": "This keeps things fresh, with room for freedom inside the affection.",
        "friction": "The energy runs on and off, and the attraction resists settling down.",
    },
    ("Moon", "Uranus"): {
        "fused": "This is exciting and unsettling: feelings get jolted.",
        "flow": "You give each other room to be yourselves emotionally.",
        "friction": "Expect emotional whiplash: one of you needs security that the other can't promise.",
    },
    ("Neptune", "Venus"): {
        "fused": "This is romantic and dreamy, and you may idealise each other. It's beautiful, but keep your eyes open.",
        "flow": "The romance here is gentle, tender and imaginative.",
        "friction": "You may idealise each other in a way that sets up disappointment.",
    },
    ("Moon", "Neptune"): {
        "fused": "You feel close in an almost psychic way, and boundaries can blur.",
        "flow": "You understand each other's feelings without needing words.",
        "friction": "It can be hard to tell what the other actually feels.",
    },
    ("Neptune", "Sun"): {
        "fused": "One of you sees the other through a soft filter.",
        "flow": "You inspire each other and treat each other with compassion.",
        "friction": "You may fool yourselves, or each other, about who the other really is.",
    },
    ("Sun", "TrueNode"): {
        "fused": "This contact can feel fated: one of you points the other toward where their life is headed.",
        "flow": "You support each other's direction in life.",
        "friction": "You can pull each other off course, or back into old patterns.",
    },
    ("Moon", "TrueNode"): {
        "fused": "You may get a 'have we met before?' feeling — this feels emotionally familiar from the start.",
        "flow": "You support each other emotionally in the life each of you is growing into.",
        "friction": "The comfort here can keep one of you stuck in old habits.",
    },
    ("TrueNode", "Venus"): {
        "fused": "The attraction can feel destined, like love with a purpose.",
        "flow": "Your affection helps each of you grow.",
        "friction": "You may be drawn to what's familiar rather than to what's next for you.",
    },
    ("Chiron", "Moon"): {
        "fused": "One of you touches the other's old hurt — to heal it or to press on it.",
        "flow": "This can be gently healing: you feel safe enough to be tender.",
        "friction": "You can reopen each other's old wounds without meaning to.",
    },
    ("Chiron", "Sun"): {
        "fused": "One of you sees exactly where the other hurts.",
        "flow": "This can be healing: being truly known by the other softens something old.",
        "friction": "You can hit each other's sore spots, so kindness has to be deliberate.",
    },
    ("Chiron", "Venus"): {
        "fused": "Love touches an old hurt here, which feels tender and raw.",
        "flow": "Your affection can help heal old hurts.",
        "friction": "Fears of rejection can get triggered.",
    },
    ("Ascendant", "Sun"): {
        "fused": "You recognise each other instantly: one of you brings out the best in how the other comes across.",
        "flow": "You look good together and you know it.",
        "friction": "Your first impressions of each other clash, but the second ones are better.",
    },
    ("Ascendant", "Venus"): {
        "fused": "Physical attraction. One of you finds the other beautiful.",
        "flow": "The chemistry is easy and pleasant from the first meeting.",
        "friction": "There's attraction, but your tastes don't quite match.",
    },
    ("Ascendant", "Moon"): {
        "fused": "Comfortable from the start. One of you feels safe in the other's presence.",
        "flow": "You're easy for each other to be around, with a warmth that takes little upkeep.",
        "friction": "Your moods and manners rub each other a little the wrong way.",
    },
    ("Ascendant", "Mars"): {
        "fused": "There's a physical charge, maybe some irritation, and you're very aware of each other.",
        "flow": "You find each other energising to be around.",
        "friction": "One of you rubs the other the wrong way — and it's sometimes attractive.",
    },
}

# ------------------------------------------------------------------ themes
# Each contact goes to the first theme whose rule it matches, so a Saturn–Venus
# contact counts as commitment rather than being counted twice.
THEMES = [
    ("fate", "Fate and direction", lambda a, b: "TrueNode" in (a, b)),
    ("healing", "Old wounds", lambda a, b: "Chiron" in (a, b)),
    ("intensity", "Intensity", lambda a, b: "Pluto" in (a, b)),
    ("commitment", "Commitment", lambda a, b: "Saturn" in (a, b)),
    ("excitement", "Excitement", lambda a, b: "Uranus" in (a, b)),
    ("idealism", "Romance and illusion", lambda a, b: "Neptune" in (a, b)),
    ("growth", "Growth and luck", lambda a, b: "Jupiter" in (a, b)),
    ("attraction", "Attraction", lambda a, b: "Mars" in (a, b)
        and {a, b} - {"Mars"} <= {"Venus", "Ascendant", "Sun", "Moon"}),
    ("affection", "Affection", lambda a, b: "Venus" in (a, b)),
    ("emotional", "Emotional bond", lambda a, b: "Moon" in (a, b)),
    ("communication", "Communication", lambda a, b: "Mercury" in (a, b)),
    ("identity", "Who you are together", lambda a, b: True),
]
THEME_MEANS = {
    "fate": "whether this feels like it's going somewhere",
    "healing": "where you can hurt or heal each other",
    "intensity": "depth, pull and power",
    "commitment": "whether it's built to last",
    "excitement": "freshness and unpredictability",
    "idealism": "romance, and how clearly you see each other",
    "growth": "how much you expand each other",
    "attraction": "chemistry and desire",
    "affection": "how love is given and received",
    "emotional": "how safe your feelings are with each other",
    "communication": "how you talk and understand each other",
    "identity": "how your basic selves fit",
}
# Theme weight quartiles over 600 random pairs (p25, p75). "Strong" means the
# top quarter of ordinary pairs, "faint" the bottom — the same reasoning as the
# score's reference distribution: a fixed threshold called almost every theme
# strong, because every pair has dozens of contacts.
# Regenerate with tools/calibrate_compat.py after changing weights or orbs.
THEME_REF = {
    "affection": (0.99, 2.42), "attraction": (0.89, 2.10), "commitment": (1.32, 2.30),
    "communication": (0.45, 1.12), "emotional": (0.76, 2.04), "excitement": (0.56, 1.05),
    "fate": (0.31, 0.80), "growth": (0.74, 1.49), "healing": (0.30, 0.87),
    "idealism": (0.54, 1.09), "identity": (0.63, 1.63), "intensity": (0.77, 1.47),
}

# The planet each theme is about. A theme with no contacts of its own can still
# have that planet in play through a contact another theme claimed first (Venus
# conjunct Pluto counts as intensity), and saying "no direct Venus contacts" then
# is false — the report listed three (audit MEDIUM-2).
THEME_PLANET = {"affection": "Venus", "emotional": "Moon", "communication": "Mercury",
                "attraction": "Mars", "commitment": "Saturn"}
# Composite Suns whose blurb is about lasting. Read against the staying-power
# score, so the report never calls a 39 "built for the long haul".
DURABLE = {"Taurus", "Capricorn", "Scorpio"}

ABSENT = {
    "communication": "Nothing ties your minds together directly. Talking is something you'll do on purpose, not by default.",
    "emotional": "Your charts have no direct emotional links, so closeness comes from time and choice rather than instinct.",
    "attraction": "Your charts show no strong chemistry links. That's not a dealbreaker — some of the best pairs warm up slowly.",
    "commitment": "Nothing in the charts anchors this long-term, so staying power is a decision you both make.",
    "affection": "Nothing links directly to Venus, the planet of affection, so you may need to spell out affection more than usual.",
}

# ------------------------------------------------------------- composite
COMPOSITE_SUN = {
    "Aries": "This relationship wants to start things. It's bold, quick and a bit impatient.",
    "Taurus": "This relationship wants security and pleasure, built slowly.",
    "Gemini": "This relationship runs on talk, curiosity and variety.",
    "Cancer": "This relationship is about home, care and feeling safe.",
    "Leo": "This relationship wants to be seen — warm, proud, a little dramatic.",
    "Virgo": "This relationship works best when it's useful: helping, fixing and improving things.",
    "Libra": "This relationship is about partnership itself: fairness, beauty, balance.",
    "Scorpio": "This relationship goes deep or not at all. It's private and intense.",
    "Sagittarius": "This relationship wants adventure, meaning and room to roam.",
    "Capricorn": "This relationship takes itself seriously — it wants goals, structure and a future.",
    "Aquarius": "This relationship values friendship and freedom over convention.",
    "Pisces": "This relationship is tender, intuitive and a little dreamy.",
}
COMPOSITE_MOON = {
    "Aries": "Together you feel things fast and say them faster.",
    "Taurus": "Together you need comfort, routine and touch.",
    "Gemini": "Together you process feelings by talking them out.",
    "Cancer": "Together you nest. Emotional safety comes first.",
    "Leo": "Together you need warmth, play and appreciation.",
    "Virgo": "Together you show care by taking care of things.",
    "Libra": "Together you need harmony — sometimes at the cost of saying what's wrong.",
    "Scorpio": "Together you feel everything at full depth.",
    "Sagittarius": "Together you need space and something to look forward to.",
    "Capricorn": "Together you keep feelings steady and a little guarded.",
    "Aquarius": "Together you give each other room — warm, but not clingy.",
    "Pisces": "Together you absorb each other's moods. It's gentle, and the line between your feelings can blur.",
}

TRANSIT_SLOW = ["Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"]
TRANSIT_TONE = {
    "Jupiter": "opening up, with more warmth and more possibility",
    "Saturn": "being tested on commitment, limits and what's real",
    "Uranus": "being shaken up by change, surprises and a need for space",
    "Neptune": "going soft-focus, which can mean romance or confusion",
    "Pluto": "under slow, deep pressure to change",
}


# ================================================================ geometry

def _sep(a, b):
    return S._sep(a, b)


def _midpoint(a, b):
    """Shorter-arc midpoint — the composite convention."""
    d = ((b - a + 540) % 360) - 180
    return (a + d / 2) % 360


def _sign(lon):
    return SIGNS[int(lon // 30) % 12]


def _shift(birth_ut, hours):
    """The same birth moved by some hours, as natal() arguments."""
    y, m, d, h, lat, lon = birth_ut
    t = dt.datetime(int(y), int(m), int(d)) + dt.timedelta(hours=float(h) + hours)
    return [t.year, t.month, t.day, t.hour + t.minute / 60 + t.second / 3600, lat, lon]


ADULT = 18

# The bottom bands, said gently for family. "Hard going" for a newborn and her
# parent is a verdict on a baby, and family is not chosen on chemistry.
FAMILY_BANDS = {"effortful": "takes work", "hard going": "takes patience"}


def _kind_band(kind, n):
    band = S._band(n)
    return FAMILY_BANDS.get(band, band) if kind == "family" else band


def _age(birth_ut, today=None):
    today = today or dt.date.today()
    y, m, d = int(birth_ut[0]), int(birth_ut[1]), int(birth_ut[2])
    return today.year - y - ((today.month, today.day) < (m, d))


def _same_sky(ca, cb):
    """Two charts so close every inner planet sits within a degree of its twin:
    the same person entered twice, or twins. The Moon moves ~0.5° an hour, so
    this is births within about two hours."""
    inner = ("Sun", "Moon", "Mercury", "Venus", "Mars")
    return all(_sep(ca["planets"][p]["lon"], cb["planets"][p]["lon"]) < 1.0 for p in inner)


def samples(birth_ut, precision, houses=None):
    """Charts across the birth-time window, centre first. `houses` is HER
    house system: both charts of a comparison are drawn in one tradition, or
    the overlays on People disagree with the houses in her own reading."""
    half, n = PRECISION.get(precision, PRECISION["exact"])
    if n == 1:
        return [natal(*birth_ut, houses=houses)]
    offsets = [0.0] + [(-half + 2 * half * i / (n - 1)) for i in range(n)]
    seen, out = set(), []
    for o in offsets:
        if round(o, 4) in seen:
            continue
        seen.add(round(o, 4))
        out.append(natal(*_shift(birth_ut, o), houses=houses))
    return out


def _points(chart, angles):
    pts = {k: v["lon"] for k, v in chart["planets"].items()
           if k in S.POINTS or k in EXTRA}
    if angles:
        pts.update({k: v["lon"] for k, v in chart.get("angles", {}).items()})
    return pts


def _contacts(a, b, angles_a, angles_b):
    """Every aspect between A's points and B's, including the node and Chiron."""
    out = {}
    pa, pb = _points(a, angles_a), _points(b, angles_b)
    for an, alon in pa.items():
        for bn, blon in pb.items():
            # Generational, not personal: everyone born within a few years
            # shares these, so they say nothing about this pair.
            if an == bn and an in EXTRA:
                continue
            if an in OUTER and bn in OUTER:
                continue
            sep = _sep(alon, blon)
            extra = an in EXTRA or bn in EXTRA
            for name, angle, orb in S.ASPECTS:
                orb = min(orb, EXTRA_ORB) if extra else orb
                delta = abs(sep - angle)
                if delta <= orb:
                    out[(an, bn, name)] = delta
                    break
    return out


# ============================================================ interpretation

def _pair_key(a, b):
    return tuple(sorted((a, b)))


def meaning(a, b, aspect, shared=False):
    """One line for a contact. Hand-written where the pair matters, built from
    keywords otherwise. `shared` is a contact inside the composite, which
    belongs to both of them, so the built line can't say "your" and "their"."""
    fam = FAMILY[aspect]
    entry = PAIR.get(_pair_key(a, b))
    if entry:
        return entry[fam]
    ka, kb = KEYWORD.get(a, a.lower()), KEYWORD.get(b, b.lower())
    pair = (f"In the relationship itself, {ka} and {kb}" if shared
            else f"Your {ka} and their {kb}")
    if fam == "fused":
        return f"{pair} are bound together — they amplify each other."
    if fam == "flow":
        return f"{pair} cooperate. This part takes little effort."
    return f"{pair} rub against each other. You can work with it, but this is where friction shows up."


def _label(p):
    return NAME.get(p, p)


def _and(items):
    items = list(dict.fromkeys(items))
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _or(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


def _theme_of(a, b):
    for key, _title, rule in THEMES:
        if rule(a, b):
            return key
    return "identity"


# =================================================================== deep

def deep(a, b, kind="romantic", precision_a="exact", precision_b="exact",
         name_b="them", today=None, houses=None):
    """
    The full report. `a` and `b` are {"birth_ut": [...]} for her and for them.
    Precision is one of PRECISIONS; "unknown" also drops angles and houses for
    that person, since without a time there are none.
    """
    kind = kind if kind in KIND_WEIGHTS else "romantic"
    pair_notes = []
    minor = _age(b["birth_ut"], today) < ADULT
    if kind == "romantic" and minor:
        # She is 18+ (the age gate); someone under 18 is not read as a romance
        # whatever the relation box says. Friendship weights, and said so.
        kind = "friend"
        pair_notes.append(f"{name_b.capitalize() if name_b != 'them' else 'They'} "
                          "is under 18, so this is read as a bond, not a romance.")
    sa = samples(a["birth_ut"], precision_a, houses)
    sb = samples(b["birth_ut"], precision_b, houses)
    angles_a, angles_b = precision_a != "unknown", precision_b != "unknown"
    pairs = list(itertools.product(sa, sb))

    # ---- contacts, with the share of the window each one survives
    tally, orbs = {}, {}
    for ca, cb in pairs:
        for key, orb in _contacts(ca, cb, angles_a, angles_b).items():
            tally[key] = tally.get(key, 0) + 1
            orbs.setdefault(key, []).append(orb)
    centre = _contacts(sa[0], sb[0], angles_a, angles_b)
    contacts = []
    for (an, bn, asp), n in tally.items():
        share = n / len(pairs)
        if share < KEEP:
            continue
        orb = centre.get((an, bn, asp), statistics.median(orbs[(an, bn, asp)]))
        w = S.WEIGHT.get(an, 0.3) * S.WEIGHT.get(bn, 0.3)
        w *= S.PAIR_BOOST.get(frozenset((an, bn)), 1.0)
        aspect_orb = next(o for nm, _a, o in S.ASPECTS if nm == asp)
        if an in EXTRA or bn in EXTRA:
            aspect_orb = min(aspect_orb, EXTRA_ORB)
            w = max(w, 0.35)
        strength = w * (0.45 + 0.55 * (1 - orb / aspect_orb)) * share
        contacts.append({
            "yours": _label(an), "theirs": _label(bn), "aspect": asp,
            "orb": round(orb, 2), "harmony": S.HARMONY[asp],
            "strength": round(strength, 4),
            "certainty": ("certain" if share >= STABLE else
                          "likely" if share >= LIKELY else "depends on birth time"),
            "theme": _theme_of(an, bn),
            "text": f"Their {_label(bn)} {asp} your {_label(an)}",
            "meaning": meaning(an, bn, asp),
        })
    contacts.sort(key=lambda c: -c["strength"])

    # ---- themes
    themes = []
    for key, title, _rule in THEMES:
        mine = [c for c in contacts if c["theme"] == key]
        total = sum(c["strength"] for c in mine)
        if mine:
            tone_v = sum(c["strength"] * c["harmony"] for c in mine) / total
            tone = "flowing" if tone_v > 0.35 else "charged" if tone_v < -0.2 else "mixed"
        else:
            tone = None
        lo, hi = THEME_REF[key]
        presence = ("strong" if total >= hi else "present" if total >= lo
                    else "faint" if mine else "absent")
        if key == "identity" and not mine:
            continue
        note = ABSENT.get(key) if not mine else None
        planet = THEME_PLANET.get(key)
        related = [c for c in contacts if planet in (c["yours"], c["theirs"])] if planet else []
        if not mine and related:
            elsewhere = [t for k_, t, _r in THEMES if any(c["theme"] == k_ for c in related)]
            note = (f"{planet} is in play, but through contacts read under "
                    f"{_and(elsewhere)}: " + "; ".join(c["text"] for c in related[:3]) + ".")
            presence = "faint"
        themes.append({
            "key": key, "title": title, "means": THEME_MEANS[key],
            "presence": presence, "tone": tone,
            "weight": round(total, 3),
            "contacts": mine[:5],
            "note": note,
        })
    themes.sort(key=lambda t: -t["weight"])

    # ---- the score, across the window rather than at one guessed moment
    scored = [S.score(ca, cb, time_known_a=angles_a, time_known_b=angles_b)
              for ca, cb in pairs]
    centre_score = scored[0]
    w = KIND_WEIGHTS[kind]

    def kind_overall(sc):
        return round(sum(w[d] * sc["dimensions"][d] for d in w))
    overalls = [kind_overall(sc) for sc in scored]
    ranges = {d: [min(sc["dimensions"][d] for sc in scored),
                  max(sc["dimensions"][d] for sc in scored)]
              for d in ("spark", "ease", "staying")}
    same = _same_sky(sa[0], sb[0])
    if same:
        pair_notes.append(
            "These two charts are nearly the same sky — born within hours of each "
            "other. Every contact between a chart and itself reads as exact, so the "
            "number measures that, not the two of you.")
    score = {
        "kind": kind, "weights": w,
        "overall": kind_overall(centre_score),
        "overall_range": [min(overalls), max(overalls)],
        "dimensions": centre_score["dimensions"],
        "dimension_ranges": ranges,
        "band": "same sky" if same else _kind_band(kind, kind_overall(centre_score)),
        "notes": pair_notes,
        # Only meaningful when someone's time is uncertain; a range of zero
        # width is reported as exact.
        "exact": len(pairs) == 1,
    }

    # ---- houses, both directions, kept only where the window agrees
    overlays = (_overlays(sa, sb, "theirs_in_yours") if angles_a else []) + \
               (_overlays(sb, sa, "yours_in_theirs") if angles_b else [])

    composite = _composite(sa[0], sb[0], angles_a and angles_b,
                           moon_stable=_composite_moon_stable(sa, sb))
    stay = score["dimensions"]["staying"]
    if composite["signs"]["Sun"] in DURABLE and stay < 50:
        composite["sun"] += (f" Staying power scores {stay}, though, so lasting is "
                             "something you'd build on purpose, not something given.")
    balance = _balance(sa[0], sb[0], angles_a, angles_b,
                       moon_ok_a=_moon_stable(sa), moon_ok_b=_moon_stable(sb))
    now = _now(composite, today or dt.date.today())

    # The pair notes lead the caveats, which the report view and the model's
    # summary already show.
    uncertain = list(pair_notes)
    for who, prec in (("your", precision_a), (f"{name_b}'s", precision_b)):
        if prec == "unknown":
            uncertain.append(f"Without {who} birth time, that chart has no rising sign (Ascendant) "
                             f"or houses, and Moon contacts are checked across the whole "
                             f"day — only the ones that hold for most of it are shown.")
        elif prec == "approximate":
            uncertain.append(f"{who.capitalize()} birth time is approximate, so the rising "
                             f"sign (Ascendant) and houses are shown only where they stay "
                             f"the same across that time range.")
    return {
        # 2: the balance no longer counts a Moon the birth-time window can't fix.
        # 3: theme notes see contacts other themes claimed; the composite Sun
        #    blurb is read against staying power.
        # 4: house overlays count both ways, so the score no longer depends on
        #    who added whom; drawn in her house system. Older reports recompute
        #    the next time they are read (report_for).
        "version": REPORT_VERSION,
        "houses": tradition.house_system(houses),
        "score": score,
        "themes": themes,
        "contacts": contacts[:40],
        "overlays": overlays,
        "composite": composite,
        "balance": balance,
        "now": now,
        "uncertainty": uncertain,
        "samples": len(pairs),
    }


def _overlays(host_samples, guest_samples, direction):
    """Where the guest's planets fall in the host's houses, kept when the
    house holds for most of both windows."""
    votes = {}
    pairs = list(itertools.product(host_samples, guest_samples))
    for host, guest in pairs:
        cusps = host.get("house_cusps")
        if not cusps:
            continue
        for name, p in guest["planets"].items():
            if name not in S.OVERLAY_PLANETS and name not in ("Mercury",):
                continue
            h = house_of(p["lon"], cusps)
            votes[(name, h)] = votes.get((name, h), 0) + 1
    out = []
    for (name, h), n in votes.items():
        share = n / len(pairs)
        if share < KEEP:
            continue
        factor = S.HOUSE_OVERLAY.get(h, (None, 0.2, None))[1]
        out.append({
            "planet": name, "house": h, "direction": direction,
            "label": HOUSE_MEANS[h], "relational": h in S.HOUSE_OVERLAY,
            "certainty": "certain" if share >= STABLE else "likely",
            "text": (f"Their {name} in your {S._ord(h)} house" if direction == "theirs_in_yours"
                     else f"Your {name} in their {S._ord(h)} house"),
            "weight": round(factor * S.WEIGHT.get(name, 0.4) * share, 3),
        })
    return sorted(out, key=lambda o: -o["weight"])[:8]


HOUSE_MEANS = {
    1: "how they show up around you, and how you see yourself with them",
    2: "money, security and what you value",
    3: "conversation and everyday contact",
    4: "home, family and feeling safe",
    5: "romance, play and fun",
    6: "routines, health and practical help",
    7: "partnership itself",
    8: "intimacy, trust and shared resources",
    9: "beliefs, travel and widening each other's world",
    10: "ambition and how you're seen together",
    11: "friendship and shared hopes",
    12: "what stays hidden or private between you",
}


def _composite(a, b, with_angles, moon_stable=True):
    pts = {}
    for name in list(S.POINTS) + ["TrueNode"]:
        if name in a["planets"] and name in b["planets"]:
            pts[name] = _midpoint(a["planets"][name]["lon"], b["planets"][name]["lon"])
    if with_angles:
        for name in ("Ascendant", "Midheaven"):
            pts[name] = _midpoint(a["angles"][name]["lon"], b["angles"][name]["lon"])
    signs = {k: _sign(v) for k, v in pts.items()}
    aspects = []
    names = list(pts)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            if x == "TrueNode" or y == "TrueNode":
                continue
            sep = _sep(pts[x], pts[y])
            for asp, angle, orb in S.ASPECTS:
                orb = min(orb, 6.0)
                if abs(sep - angle) <= orb:
                    aspects.append({"a": _label(x), "b": _label(y), "aspect": asp,
                                    "orb": round(abs(sep - angle), 2),
                                    "meaning": meaning(x, y, asp, shared=True).replace(
                                        "One of you", "Each of you").replace(
                                        "one of you", "each of you")})
                    break
    key = [x for x in aspects if {x["a"], x["b"]} & {"Sun", "Moon", "Venus", "Mars", "Saturn"}]
    key.sort(key=lambda x: x["orb"])
    return {
        "signs": {_label(k): v for k, v in signs.items()},
        "positions": {_label(k): round(v, 2) for k, v in pts.items()},
        "sun": COMPOSITE_SUN[signs["Sun"]],
        "moon": COMPOSITE_MOON[signs["Moon"]] if moon_stable else None,
        "moon_certain": moon_stable,
        "rising": signs.get("Ascendant"),
        "aspects": key[:8],
    }


def _composite_moon_stable(sa, sb):
    found = {_sign(_midpoint(x["planets"]["Moon"]["lon"], y["planets"]["Moon"]["lon"]))
             for x, y in itertools.product(sa, sb)}
    return len(found) == 1


WEIGHTED = {"Sun": 3, "Moon": 3, "Ascendant": 2, "Mercury": 1, "Venus": 1, "Mars": 1}


def _moon_stable(samples_):
    """True when every chart across the birth-time window has the Moon in one
    sign. It moves ~13° a day, so with no time it changes sign on ~44% of days."""
    return len({c["planets"]["Moon"]["sign"] for c in samples_}) == 1


MOON_UNSURE = {"yours": "a sign that depends on your birth time",
               "theirs": "a sign that depends on their birth time"}


def _balance(a, b, angles_a, angles_b, moon_ok_a=True, moon_ok_b=True):
    def counts(chart, angles, moon_ok):
        c = {"fire": 0, "earth": 0, "air": 0, "water": 0}
        for name, wt in WEIGHTED.items():
            if name == "Ascendant":
                if not angles:
                    continue
                sign = chart["angles"]["Ascendant"]["sign"]
            elif name == "Moon" and not moon_ok:
                # The noon Moon is a guess on a day it changed sign. Weighting
                # it ×3 would tilt the whole balance on a coin flip.
                continue
            else:
                sign = chart["planets"][name]["sign"]
            c[S.ELEMENT[sign]] += wt
        return c
    ca, cb = counts(a, angles_a, moon_ok_a), counts(b, angles_b, moon_ok_b)
    both = {k: ca[k] + cb[k] for k in ca}
    total = sum(both.values()) or 1
    share = {k: round(v / total, 2) for k, v in both.items()}
    missing = [k for k, v in share.items() if v < 0.08]
    dom = max(share, key=share.get)

    def top(c):
        return max(c, key=c.get)
    moon_a, moon_b = a["planets"]["Moon"]["sign"], b["planets"]["Moon"]["sign"]
    ven_a, mars_b = a["planets"]["Venus"]["sign"], b["planets"]["Mars"]["sign"]
    ven_b, mars_a = b["planets"]["Venus"]["sign"], a["planets"]["Mars"]["sign"]
    return {
        "together": share,
        "dominant": dom,
        "missing": missing,
        "yours": top(ca), "theirs": top(cb),
        "note": (S._element_note(top(ca), top(cb))
                 + (f" Between you there's almost no {_or(missing)} — "
                    f"{_MISSING[missing[0]]}" if missing else "")),
        "moons": ({"yours": moon_a, "theirs": moon_b, "certain": True,
                   "note": S._element_note(S.ELEMENT[moon_a], S.ELEMENT[moon_b])}
                  if moon_ok_a and moon_ok_b else
                  {"yours": moon_a if moon_ok_a else MOON_UNSURE["yours"],
                   "theirs": moon_b if moon_ok_b else MOON_UNSURE["theirs"],
                   "certain": False,
                   "note": "The Moon changed sign on that birthday, so I won't "
                           "compare your Moons until there's a birth time."}),
        "venus_mars": [
            {"text": f"Your Venus in {ven_a}, their Mars in {mars_b}",
             "note": S._element_note(S.ELEMENT[ven_a], S.ELEMENT[mars_b])},
            {"text": f"Their Venus in {ven_b}, your Mars in {mars_a}",
             "note": S._element_note(S.ELEMENT[ven_b], S.ELEMENT[mars_a])},
        ],
    }


_MISSING = {
    "fire": "spontaneity and nerve have to be brought in deliberately.",
    "earth": "practical follow-through won't happen by itself.",
    "air": "talking things through is something you'll have to practise.",
    "water": "emotional attunement takes conscious effort.",
}


def _now(composite, day):
    """Slow transits to the relationship's own chart today — the season the
    pair is in, as distinct from what either of them is going through."""
    j = jd_ut(day.year, day.month, day.day, 12.0)
    sky = positions(j)
    targets = {k: v for k, v in composite["positions"].items()
               if k in ("Sun", "Moon", "Venus", "Mars", "Ascendant")}
    if not composite.get("moon_certain"):
        targets.pop("Moon", None)
    out = []
    for t in TRANSIT_SLOW:
        if t not in sky:
            continue
        for k, lon in targets.items():
            sep = _sep(sky[t]["lon"], lon)
            for asp, angle, _o in S.ASPECTS:
                if abs(sep - angle) <= 2.0:
                    out.append({"transit": t, "aspect": asp, "point": k,
                                "orb": round(abs(sep - angle), 2),
                                "text": f"Transiting {t} {asp} the relationship's {k}",
                                "meaning": f"The relationship's {KEYWORD.get(k, k.lower())} "
                                           f"{'are' if k == 'Moon' else 'is'} "
                                           f"{TRANSIT_TONE[t]}."})
                    break
    return sorted(out, key=lambda x: x["orb"])[:5]


# ================================================================== facts

def facts_for_model(report, name):
    """The report flattened into the lines the model is allowed to narrate."""
    sc = report["score"]
    L = [f"PERSON: {name} ({sc['kind']})",
         f"SCORE: {sc['overall']}/100 ({sc['band']})"
         + ("" if sc["exact"] else f", range {sc['overall_range'][0]}-{sc['overall_range'][1]} across the birth-time window"),
         "DIMENSIONS: " + ", ".join(f"{d} {v}" for d, v in sc["dimensions"].items()),
         "THEMES (strongest first):"]
    for t in report["themes"][:7]:
        L.append(f"  {t['title']} — {t['presence']}" + (f", {t['tone']}" if t["tone"] else "")
                 + (f": {t['note']}" if t["note"] else ""))
        for c in t["contacts"][:3]:
            L.append(f"    {c['text']} ({c['orb']}°, {c['certainty']}): {c['meaning']}")
    comp = report["composite"]
    L.append(f"COMPOSITE: Sun in {comp['signs']['Sun']} — {comp['sun']}")
    if comp.get("moon"):
        L.append(f"  Moon in {comp['signs']['Moon']} — {comp['moon']}")
    for o in report["overlays"][:4]:
        L.append(f"HOUSE: {o['text']} — {o['label']}")
    L.append(f"BALANCE: {report['balance']['note']}")
    for n in report["now"]:
        L.append(f"NOW: {n['text']} ({n['orb']}°) — {n['meaning']}")
    for u in report["uncertainty"]:
        L.append(f"CAVEAT: {u}")
    return "\n".join(L)


# ================================================================ narration

NARRATE = """You are writing the compatibility reading inside My Sign, an astrology app.
She has added someone and wants to understand the two of them.

Write three short paragraphs — under 220 words in total, because she is reading
this on a phone. Plain prose, no headings, no lists, no emoji:
1. The shape of this pairing — what it is mostly about, from the strongest themes.
2. Where it flows and where it rubs. Pick the two or three contacts that matter
   most; do not walk through every one — the itemised list is right below this.
3. What the relationship itself is (the composite) and the season it is in now.

Rules, no exceptions:
- Use ONLY the facts below. Every planet, sign, house, aspect, date or number you
  mention must appear in them. Do not add placements, do not compute anything.
- Address her as "you" and the other person by name. "Their Mars" is theirs,
  "your Moon" is hers — keep the direction exactly as given.
- If a fact is marked "depends on birth time" or there is a CAVEAT, say so briefly
  where it matters rather than presenting it as certain.
- Never tell her to leave, stay, or end anything. This describes a dynamic, not a
  verdict on a person. Fifty is an ordinary pair, not a bad grade.
- Warm, direct, a little funny. No therapy-speak, no "the universe".

FACTS
{facts}"""
# Beginners first: every technical word explained where it appears (engine/glossary).
NARRATE = NARRATE + "\n\n" + glossary.PLAIN_RULE



def narrate(report, name, client=None, model=None):
    """The written reading, or None. Anything short of a clean finish is None,
    and the caller keeps showing the itemised report — which is the substance;
    the prose is a convenience."""
    import os
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        import anthropic
        client = anthropic.Anthropic()
    try:
        r = client.messages.create(
            model=model or models.READING, max_tokens=6000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": NARRATE.format(
                facts=facts_for_model(report, name))}])
    except Exception as e:
        from app import log
        log.error("compat.narrate.failed", exc=e)
        return None
    text = "".join(b.text for b in r.content if b.type == "text").strip()
    if not text or r.stop_reason != "end_turn":
        return None
    bad = ungrounded(text, facts_for_model(report, name))
    try:
        from engine import accuracy
        accuracy.record_outcome(not bad, bad, "compatibility", withheld=bool(bad))
    except Exception:
        pass        # a stats write never costs her the reading
    if bad:
        # Same rule as the chat: a placement the facts don't contain is the one
        # thing this product promises not to do. Show the itemised report alone
        # rather than a reading that invented something.
        from app import log
        log.emit("compat.narrate.ungrounded", level="error", claims=bad[:5])
        return None
    return text


_HOUSE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+house\b", re.I)
_BODIES = ["Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus",
           "Neptune", "Pluto", "Chiron", "Ascendant", "Midheaven", "North Node"]


def ungrounded(text, facts):
    """Every planet, sign and house the prose names must be in the facts."""
    low, ctx = text.lower(), facts.lower()
    bad = [w for w in _BODIES + SIGNS if w.lower() in low and w.lower() not in ctx]
    bad += [f"{h} house" for h in _HOUSE_RE.findall(text)
            if not re.search(rf"\b{h}(st|nd|rd|th)\b", ctx)]
    return bad


# ================================================================== storage
# One item per person ("compat-<id>"), not inside the connections list: a full
# report is tens of kilobytes, and a hundred of them in one item would cross
# DynamoDB's 400KB ceiling.

def my_precision(profile):
    return (profile or {}).get("time_precision") or (
        "exact" if (profile or {}).get("birth_time_known", True) else "unknown")


def their_precision(row):
    return row.get("time_precision") or (
        "exact" if row.get("birth_time_known") else "unknown")


def inputs_sig(prof, row):
    """
    Everything the written read depends on, hashed: both birth instants, what
    the relationship is, and how sure each time is. The read is a model call
    (engine/compat_fun.py), so it is redone only when one of these changes —
    not when the report is rebuilt for an unrelated reason, like a PATCH that
    changed nothing (audit H1).
    """
    import hashlib
    import json
    basis = [prof.get("birth_ut"), row.get("birth_ut"),
             row.get("kind") or infer_kind(row.get("relation")),
             my_precision(prof), their_precision(row),
             tradition.house_system(prof.get("houses")), REPORT_VERSION]
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()[:24]


def report_for(uid, row, recompute=False):
    """The stored report for one saved person, computed if missing or stale.
    A rebuilt report keeps the written read when its inputs are unchanged."""
    from engine.store import load, save
    key = f"compat-{row['id']}"
    old = load(uid, key, None)
    rep = None if recompute else old
    # A change of house system rebuilds every report at the moment it is made
    # (server.set_tradition), so the cache check needs no profile read — the
    # People list reads one report per person and is counted.
    if rep and rep.get("version") == REPORT_VERSION and rep.get("kind") == row.get("kind"):
        return rep
    prof = load(uid, "profile", {}) or {}
    houses = tradition.house_system(prof.get("houses"))
    rep = deep({"birth_ut": prof["birth_ut"]}, {"birth_ut": row["birth_ut"]},
               kind=row.get("kind") or infer_kind(row.get("relation")),
               precision_a=my_precision(prof), precision_b=their_precision(row),
               name_b=row.get("name", "them"), houses=houses)
    rep["kind"] = row.get("kind")
    rep["inputs"] = inputs_sig(prof, row)
    if old and old.get("fun") and old.get("inputs") == rep["inputs"]:
        rep["fun"], rep["reading"] = old["fun"], old.get("reading")
    save(uid, key, rep)
    return rep


def _contact_for_model(c, row):
    out = {k: c[k] for k in ("text", "orb", "certainty", "meaning")}
    if their_precision(row) == "unknown" and c.get("theirs") in ("Moon",):
        out["orb"] = "approximate — their birth time is unknown"
    return out


def _composite_for_model(comp):
    """The composite as the chat sees it. A composite Moon built on a noon Moon
    is not a sign, and listing one was how 'Aquarius' reached her unflagged."""
    out = {k: comp[k] for k in ("signs", "sun", "moon", "rising", "aspects")}
    if not comp.get("moon_certain", True):
        out["signs"] = {**comp["signs"], "Moon": "uncertain — depends on a birth time"}
        out["aspects"] = [a for a in comp["aspects"] if "Moon" not in (a["a"], a["b"])]
    return out


def their_chart(row):
    """
    Their own placements, for the chat (audit MEDIUM-1).

    The agent's person tools returned contacts, themes and a composite but never
    the other person's chart, so "what's his rising?" was worked out backwards
    from contact orbs — right, as it happened, and then disowned a turn later
    because nothing it could see said so. Sign and degree for each placement;
    houses and angles only when the birth time holds them; with no time, any
    placement that changes sign across the birth day is given as the signs it
    could be (the Moon, mostly), with no degree, because a noon degree is a
    precision the data does not have.
    """
    prec = their_precision(row)
    if not row.get("birth_ut"):
        return None
    charts = samples(row["birth_ut"], prec)
    centre = charts[0]
    known = prec == "exact"
    placements = {}
    for name, v in centre["planets"].items():
        label = _label(name)
        signs = []
        for c in charts:
            sg = c["planets"][name]["sign"]
            if sg not in signs:
                signs.append(sg)
        if len(signs) > 1:
            lons = sorted(((c["planets"][name]["lon"] - v["lon"] + 180) % 360 - 180)
                          for c in charts)
            lo, hi = [_sign((v["lon"] + d) % 360) for d in (lons[0], lons[-1])]
            share = {sg: sum(c["planets"][name]["sign"] == sg for c in charts) / len(charts)
                     for sg in signs}
            lean = max(share, key=share.get)
            placements[label] = {"sign": f"{lo} or {hi}", "uncertain":
                                 "changes sign across the birth-time window — give both",
                                 "most_of_the_day": lean if share[lean] >= 0.75 else None}
            continue
        entry = {"at": v["label"] if known or name not in ("Moon",) else v["sign"]}
        if not known:
            entry["approximate"] = True if name == "Moon" else None
            entry = {k: x for k, x in entry.items() if x is not None}
        if known and v.get("house"):
            entry["house"] = v["house"]
        if v.get("retrograde"):
            entry["retrograde"] = True
        placements[label] = entry
    out = {"birth_time": prec, "placements": placements}
    if known:
        out["angles"] = {k: a["label"] for k, a in centre["angles"].items()}
    elif prec == "approximate":
        # Only an angle whose sign holds across the hour either way.
        out["angles"] = {k: centre["angles"][k]["sign"] for k in ("Ascendant", "Midheaven")
                         if len({c["angles"][k]["sign"] for c in charts}) == 1}
        out["note"] = ("Birth time approximate: houses are not given, and an angle is "
                       "listed only where its sign holds across the window.")
    else:
        out["note"] = ("No birth time: no rising sign, no Midheaven, no houses — never name "
                       "one. Placements are from the whole birth day; contacts to their "
                       "Moon are approximate, so give no exact orb for them.")
    return out


def summary(rep, row):
    """What the chat agent gets: the substance, without the long tail."""
    return {
        "name": row.get("name"), "relation": row.get("relation"),
        "kind": rep["score"]["kind"],
        "score": {k: rep["score"][k] for k in
                  ("overall", "overall_range", "band", "dimensions", "exact")},
        "themes": [{"title": t["title"], "presence": t["presence"], "tone": t["tone"],
                    "note": t["note"],
                    "contacts": [_contact_for_model(c, row) for c in t["contacts"][:3]]}
                   for t in rep["themes"]],
        "their_chart": their_chart(row),
        "composite": _composite_for_model(rep["composite"]),
        "houses": [o["text"] + " — " + o["label"] for o in rep["overlays"][:6]],
        "balance": rep["balance"]["note"],
        "now": rep["now"],
        "caveats": rep["uncertainty"] + ([row["birth_time_note"].replace("Her ", "Their ")]
                                         if row.get("birth_time_note") else []),
    }
