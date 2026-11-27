"""
What a chart actually says, computed rather than described.

Everything here is derived from the natal positions: the big three, element and
modality balance, stelliums, the tightest aspects, which houses are loaded, the
chart ruler. The model never decides any of it — it writes prose over facts this
module produced, exactly like the daily reading.

The interpretation text is a STARTER CORPUS. It is deliberately short, avoids
fortune-telling, and needs a working astrologer's pass before launch — that is
the "second moat" the spec calls for and it cannot be shortcut by an engineer
with a reference book.
"""
from engine import tradition
from engine.chart import SIGNS, jd_ut, positions

ELEMENT = {"Aries": "fire", "Leo": "fire", "Sagittarius": "fire",
           "Taurus": "earth", "Virgo": "earth", "Capricorn": "earth",
           "Gemini": "air", "Libra": "air", "Aquarius": "air",
           "Cancer": "water", "Scorpio": "water", "Pisces": "water"}

MODALITY = {"Aries": "cardinal", "Cancer": "cardinal", "Libra": "cardinal", "Capricorn": "cardinal",
            "Taurus": "fixed", "Leo": "fixed", "Scorpio": "fixed", "Aquarius": "fixed",
            "Gemini": "mutable", "Virgo": "mutable", "Sagittarius": "mutable", "Pisces": "mutable"}

# Traditional rulerships — used for the chart ruler, which is the planet the
# whole chart answers to.
RULER = {"Aries": "Mars", "Taurus": "Venus", "Gemini": "Mercury", "Cancer": "Moon",
         "Leo": "Sun", "Virgo": "Mercury", "Libra": "Venus", "Scorpio": "Mars",
         "Sagittarius": "Jupiter", "Capricorn": "Saturn", "Aquarius": "Saturn",
         "Pisces": "Jupiter"}

PERSONAL = ("Sun", "Moon", "Mercury", "Venus", "Mars")
ANGLES = ("Ascendant", "Midheaven")

SUN = {
    "Aries": "You start things. Momentum is how you think, and standing still costs you more than being wrong.",
    "Taurus": "You move at your own pace and it is slower than other people would like. It is also why what you build stays built.",
    "Gemini": "You need more than one thing going at once. Boredom, for you, is a genuine problem rather than a mood.",
    "Cancer": "You take things personally because you take things seriously. The two are harder to separate than people assume.",
    "Leo": "You want to be seen doing it, not just to have done it. That is not vanity, it is how you know it counted.",
    "Virgo": "You notice what is wrong before you notice what is fine. That helps at work and costs you sleep at 2am.",
    "Libra": "You think in relation to other people. Deciding alone feels like deciding with half the information.",
    "Scorpio": "You go all the way in or you do not bother. There is no casual setting and pretending otherwise never lasts.",
    "Sagittarius": "You need the bigger frame or the detail feels pointless. Confinement reads to you as a threat.",
    "Capricorn": "You are playing a longer game than the people around you and it can look like coldness from outside.",
    "Aquarius": "You see the bigger pattern, not just the single case. It makes you clear-eyed and occasionally very far away.",
    "Pisces": "You absorb the mood of the room. The hard part is telling which feelings were ever yours.",
}

MOON = {
    "Aries": "You feel it immediately and it passes almost as fast. Learning to sit with a feeling is the work.",
    "Taurus": "You settle slowly and unsettle slower. Comfort is not indulgence for you; it is how you steady yourself.",
    "Gemini": "You process by talking. An unspoken feeling stays unfinished.",
    "Cancer": "You remember how things felt long after everyone else has moved on.",
    "Leo": "You need your feelings witnessed. Being told they are valid matters less than being seen having them.",
    "Virgo": "You handle feeling by finding a task. It works until the task runs out.",
    "Libra": "You feel other people's moods before your own. Ask what you want before asking what would be fair.",
    "Scorpio": "You feel privately and completely. What surfaces is a fraction of it, on purpose.",
    "Sagittarius": "You need room when it gets heavy. Leaving is not always avoidance for you, but it can be.",
    "Capricorn": "You manage rather than feel, and call it fine. It usually is. Sometimes it is not.",
    "Aquarius": "You step back from your own feelings to look at them. Distance is how you steady yourself.",
    "Pisces": "Your mood and the mood of the room blur together easily. Knowing that is most of your protection.",
}

RISING = {
    "Aries": "You come across as direct, and faster than you mean to.",
    "Taurus": "You read as steady and unhurried, whatever is happening underneath.",
    "Gemini": "You read as quick and curious; people talk to you before they know you.",
    "Cancer": "You read as careful. People sense they are being assessed, and they are.",
    "Leo": "You take up more room than you think you do, in a way people remember.",
    "Virgo": "You read as composed and slightly reserved, like you are still checking.",
    "Libra": "You read as easy to be around, which makes it easy to hide behind.",
    "Scorpio": "You read as contained. People find you hard to place and stay interested anyway.",
    "Sagittarius": "You read as open and unbothered, even before you actually feel that way.",
    "Capricorn": "You read as older than you are and more in control than you feel.",
    "Aquarius": "You read as a little apart from the group, even inside it.",
    "Pisces": "You come across as gentle. People tell you things quickly.",
}

HOUSE = {
    1: "how you come across and how you start things", 2: "money, worth, and what you hold onto",
    3: "how you talk, siblings, and everyday life", 4: "home, family, where you come from",
    5: "romance, play, whatever you make", 6: "work, routine, health, the daily grind",
    7: "partnership and who you become around one person", 8: "intimacy, other people's resources, what is hidden",
    9: "belief, travel, and what broadens your outlook", 10: "career, reputation, what you are known for",
    11: "friends, groups, the future you want", 12: "solitude, memory, and what runs beneath the surface",
}

ELEMENT_NOTE = {
    "fire": "You run on momentum and go flat without something to move toward.",
    "earth": "You trust what you can touch, verify, and finish.",
    "air": "You handle life by thinking and talking about it first.",
    "water": "You take everything in feeling-first, whether or not you want to.",
}

MODALITY_NOTE = {
    "cardinal": "You start things. Finishing is the part that needs a system.",
    "fixed": "You hold your position. Changing your mind is expensive, which is why it means something when you do.",
    "mutable": "You adapt fast, which is a strength until nothing has a fixed point.",
}

ASPECT_NOTE = {
    "conjunction": "these two are fused, so they never operate separately in you",
    "opposition": "these two pull in opposite directions, and you keep negotiating between them rather than settling it",
    "square": "these two rub against each other; it is uncomfortable, and it is what pushes you to act",
    "trine": "these two work together easily, so you can rely on it and may under-use it",
    "sextile": "these two can support each other, but only when you make the effort",
}


def natal_aspects(chart, max_orb=6.0):
    """Aspects between the natal placements themselves. `chart.aspects` compares
    transits to natal, which would pair every planet with itself here."""
    pts = {k: v["lon"] for k, v in chart["planets"].items()}
    pts.update({k: v["lon"] for k, v in chart["angles"].items()})
    angles = [("conjunction", 0), ("opposition", 180), ("square", 90),
              ("trine", 120), ("sextile", 60)]
    names = list(pts)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sep = abs(pts[a] - pts[b]) % 360
            if sep > 180:
                sep = 360 - sep
            for label, exact in angles:
                orb = abs(sep - exact)
                if orb <= max_orb:
                    out.append({"a": a, "b": b, "aspect": label, "orb": round(orb, 2)})
    return sorted(out, key=lambda x: x["orb"])


def moon_sign_certain(y, m, d):
    """
    Without a birth time the Moon sign is not guaranteed either. The Moon covers
    roughly 13 degrees a day, so it crosses a sign boundary on about two days in
    five. Check this chart's actual day rather than assuming.
    """
    a = SIGNS[int(positions(jd_ut(y, m, d, 0.0))["Moon"]["lon"] // 30)]
    b = SIGNS[int(positions(jd_ut(y, m, d, 23.99))["Moon"]["lon"] // 30)]
    return a == b, a, b


def sign_steady(jd_centre, body):
    """
    (certain, signs) for a body across the 24 hours centred on `jd_centre`.

    An unknown-time chart is built at local noon, so noon plus or minus twelve
    hours is exactly her local birth day — no timezone needed. The old check
    used the UT day, and in Los Angeles that called a third of Moon-ingress
    days certain when they weren't.
    """
    seen = []
    for h in range(-12, 13):
        sign = SIGNS[int(positions(jd_centre + h / 24)[body]["lon"] // 30)]
        if sign not in seen:
            seen.append(sign)
    return len(seen) == 1, seen


def analyze(chart, time_known=True, birth_ymd=None, rulerships=None):
    """
    Everything the reading is allowed to claim.

    With no birth time, that is much less than it looks. The ascendant moves
    through all twelve signs in a day — measured on one real chart, eight
    distinct rising signs across twenty-four hours — so the rising sign, every
    house placement, the chart ruler and anything angular are not approximate
    without a time. They are invented. They get omitted rather than guessed.
    """
    planets, angles = chart["planets"], chart["angles"]

    def sign_of(lon):
        return SIGNS[int(lon // 30)]

    asc_sign = sign_of(angles["Ascendant"]["lon"])
    big3 = {
        "sun": {"sign": planets["Sun"]["sign"],
                "house": planets["Sun"]["house"] if time_known else None},
        "moon": {"sign": planets["Moon"]["sign"],
                 "house": planets["Moon"]["house"] if time_known else None},
        "rising": {"sign": asc_sign} if time_known else None,
    }

    moon_certain = sun_certain = True
    moon_signs = [planets["Moon"]["sign"]]
    if not time_known:
        if chart.get("jd"):
            moon_certain, moon_signs = sign_steady(chart["jd"], "Moon")
            sun_certain, _ = sign_steady(chart["jd"], "Sun")
        elif birth_ymd:
            moon_certain, lo, hi = moon_sign_certain(*birth_ymd)
            moon_signs = [lo] if moon_certain else [lo, hi]

    # Weighted: the personal planets and the angles carry a chart, the outers
    # describe a generation.
    elements, modalities = {}, {}
    for name, p in planets.items():
        if name in ("TrueNode", "Chiron"):
            continue
        w = 2 if name in PERSONAL else 1
        elements[ELEMENT[p["sign"]]] = elements.get(ELEMENT[p["sign"]], 0) + w
        modalities[MODALITY[p["sign"]]] = modalities.get(MODALITY[p["sign"]], 0) + w
    # The angles are weighted heavily because they carry a chart — which is
    # exactly why they must not count when the birth time is unknown. Including
    # a guessed ascendant here would tilt the whole balance on noise.
    if time_known:
        for a in ANGLES:
            sg = sign_of(angles[a]["lon"])
            elements[ELEMENT[sg]] = elements.get(ELEMENT[sg], 0) + 2
            modalities[MODALITY[sg]] = modalities.get(MODALITY[sg], 0) + 2

    by_house, by_sign = {}, {}
    for name, p in planets.items():
        if name in ("TrueNode", "Chiron"):
            continue
        by_house.setdefault(p["house"], []).append(name)
        # A Moon that could be in either of two signs is not evidence for a
        # stellium in one of them: "3 planets in Capricorn: Moon, Uranus,
        # Neptune" was two planets and a coin flip.
        if name == "Moon" and not moon_certain:
            continue
        by_sign.setdefault(p["sign"], []).append(name)

    # A stellium usually shows up twice — the same planets sit in one sign AND
    # one house. Reporting both reads as two findings when it is one, so they
    # are merged and the house leads, being the more specific claim.
    stelliums = []
    house_st = {h: ps for h, ps in sorted(by_house.items()) if len(ps) >= 3}
    sign_st = {sg: ps for sg, ps in by_sign.items() if len(ps) >= 3}
    for h, ps in house_st.items():
        also = next((sg for sg, sps in sign_st.items() if set(sps) == set(ps)), None)
        stelliums.append({"kind": "house", "where": h, "planets": ps, "also_sign": also})
        if also:
            sign_st.pop(also)
    for sg, ps in sign_st.items():
        stelliums.append({"kind": "sign", "where": sg, "planets": ps, "also_sign": None})

    aspects = natal_aspects(chart)
    tight = [a for a in aspects if a["orb"] <= 2.0
             and (a["a"] in PERSONAL + ANGLES or a["b"] in PERSONAL + ANGLES)]
    if not time_known:
        # The angles move all day; an aspect to one of them means nothing here.
        tight = [a for a in tight if a["a"] not in ANGLES and a["b"] not in ANGLES]
    tight = tight[:5]

    if not time_known:
        # A house stellium is an artifact of the assumed noon; a sign stellium
        # is not. Keep the second, drop the first.
        stelliums = [s for s in stelliums if s["kind"] == "sign"]

    # Whichever scheme the app is configured for — it decides the chart
    # ruler, and it should agree with the corpus voice.
    ruler = tradition.rulerships(rulerships).get(asc_sign, RULER[asc_sign])
    return {
        "big3": big3,
        "time_known": time_known,
        "moon_sign_certain": moon_certain,
        # Every sign the Moon passes through across her birth day, in order.
        "moon_signs": moon_signs,
        "sun_sign_certain": sun_certain,
        "unknown": [] if time_known else [
            "rising sign", "every house placement", "the chart ruler",
            "which planets are angular",
        ],
        "element": max(elements, key=elements.get),
        "element_counts": elements,
        "modality": max(modalities, key=modalities.get),
        "modality_counts": modalities,
        "stelliums": stelliums,
        "tight_aspects": tight,
        "retrogrades": [n for n, p in planets.items()
                        if p["retrograde"] and n not in ("TrueNode",)],
        "angular": [n for n, p in planets.items()
                    if p["house"] in (1, 4, 7, 10) and n in PERSONAL] if time_known else [],
        "loaded_houses": ([h for h, ps in sorted(by_house.items()) if len(ps) >= 2]
                          if time_known else []),
        "chart_ruler": ({"planet": ruler, "sign": planets[ruler]["sign"],
                         "house": planets[ruler]["house"]}
                        if time_known and ruler in planets else None),
    }
