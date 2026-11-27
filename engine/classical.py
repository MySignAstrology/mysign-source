"""
What the old astrologers would say — each doctrine with where it comes from.

Every rule here is taken from a public-domain primary text, and every item the
reading shows carries its citation, because "ancient wisdom" with no source is
exactly the kind of claim this app exists not to make:

  Ptolemy, Tetrabiblos, tr. J. M. Ashmand (1822)        archive.org/details/b29293066
  William Lilly, Christian Astrology (1647, 1st ed.)     archive.org/details/b30338724
  Cassius Dio, Roman History 37.18–19 (tr. Cary, 1914)   penelope.uchicago.edu/Thayer
  Vivian Robson, The Fixed Stars and Constellations
    in Astrology (1923)                                  archive.org/details/in.ernet.dli.2015.128091

Page numbers are those editions'. The prose is ours: paraphrase in the app's
voice, never quotation at length. Where the sources disagree we follow the
older one and say so in a comment, and anything we could not trace to a
public-domain text — the Lot of Spirit, a waxing/waning "personality" for the
Moon's phase (that is Rudhyar, 1967) — is left out rather than dressed up.

NOT REVIEWED BY AN ASTROLOGER.
"""

PTOLEMY = "Ptolemy, Tetrabiblos"
LILLY = "Lilly, Christian Astrology (1647)"

ELEMENT = {"Aries": "fire", "Leo": "fire", "Sagittarius": "fire",
           "Taurus": "earth", "Virgo": "earth", "Capricorn": "earth",
           "Gemini": "air", "Libra": "air", "Aquarius": "air",
           "Cancer": "water", "Scorpio": "water", "Pisces": "water"}
RULER = {"Aries": "Mars", "Taurus": "Venus", "Gemini": "Mercury", "Cancer": "Moon",
         "Leo": "Sun", "Virgo": "Mercury", "Libra": "Venus", "Scorpio": "Mars",
         "Sagittarius": "Jupiter", "Capricorn": "Saturn", "Aquarius": "Saturn",
         "Pisces": "Jupiter"}

# ------------------------------------------------------------ temperament
# Lilly, Christian Astrology, ch. CVI, pp. 532–533. Signs by triplicity; the
# Moon by quarter of her phase (Lilly's scheme — Ptolemy I.8 assigns single
# qualities instead); the season by the Sun's quarter of the zodiac.
QUAL_ELEMENT = {"fire": ("hot", "dry"), "earth": ("cold", "dry"),
                "air": ("hot", "moist"), "water": ("cold", "moist")}
QUAL_MOON = [("hot", "moist"), ("hot", "dry"), ("cold", "dry"), ("cold", "moist")]
QUAL_SEASON = [("hot", "moist"), ("hot", "dry"), ("cold", "dry"), ("cold", "moist")]

TEMPERAMENT = {
    ("hot", "dry"): ("choleric",
                     "Quick to heat, quick to act, impatient with anything slow. You run on fire, "
                     "and you need somewhere to point it."),
    ("hot", "moist"): ("sanguine",
                       "Warm, social, hopeful, easily delighted: generous with people, and bad at "
                       "staying angry with them."),
    ("cold", "dry"): ("melancholic",
                      "Deep, careful, exacting, with a long memory. The melancholic temper "
                      "thinks before it moves and feels more than it shows — the scholar's temperament."),
    ("cold", "moist"): ("phlegmatic",
                        "Calm, receptive, slow to rouse and hard to unsettle. The phlegmatic "
                        "temper absorbs rather than reacts, and keeps the peace by instinct."),
}


def _moon_sign_steady(chart):
    if not chart.get("jd"):
        return True
    from engine.interpret import sign_steady
    return sign_steady(chart["jd"], "Moon")[0]


def temperament(chart, sun_lon, moon_elong, time_known):
    """A simplified reading of Lilly's method: the ascendant sign and its
    lord's sign, the Moon's sign and quarter, and the season. Lilly also weighs
    aspects to the ascendant and Moon and the Lord of the Geniture; we do not,
    and the item says it is simplified."""
    votes = []
    planets = chart["planets"]
    if time_known:
        asc = chart["angles"]["Ascendant"]["sign"]
        votes.append(("your rising sign", QUAL_ELEMENT[ELEMENT[asc]]))
        lord = planets.get(RULER[asc])
        if lord:
            votes.append(("its ruler", QUAL_ELEMENT[ELEMENT[lord["sign"]]]))
    # Without a time the Moon may change sign during her birth day; a vote
    # from a sign she may not have is left out rather than counted.
    if time_known or _moon_sign_steady(chart):
        votes.append(("your Moon's sign", QUAL_ELEMENT[ELEMENT[planets["Moon"]["sign"]]]))
    votes.append(("the Moon's phase", QUAL_MOON[int(moon_elong // 90) % 4]))
    # Lilly's seasons are the Sun's quarters of the tropical zodiac — spring
    # from the March equinox — which are the northern hemisphere's. Said so,
    # because for someone born in Sydney in December "the season you were born
    # in" was named as winter.
    votes.append(("the Sun's quarter of the year (Lilly's seasons, reckoned "
                  "for the northern hemisphere)", QUAL_SEASON[int(sun_lon // 90) % 4]))

    heat = sum(1 if v[0] == "hot" else -1 for _w, v in votes)
    wet = sum(1 if v[1] == "moist" else -1 for _w, v in votes)
    hot = "hot" if heat >= 0 else "cold"
    moist = "moist" if wet >= 0 else "dry"
    name, body = TEMPERAMENT[(hot, moist)]
    mixed = None
    if heat == 0:
        mixed = TEMPERAMENT[("cold" if hot == "hot" else "hot", moist)][0]
    elif wet == 0:
        mixed = TEMPERAMENT[(hot, "dry" if moist == "moist" else "moist")][0]
    return {"temperament": name, "leaning": mixed, "body": body,
            "votes": [{"factor": w, "qualities": list(q)} for w, q in votes]}


# --------------------------------------------------------------- stars
# Ptolemy's planetary "natures" (Tetrabiblos I.9–I.11) and Robson's 1923
# paraphrase of the tradition, in our words. Achernar is left out: which
# Eridanus star Ptolemy meant is disputed. Algol is not named by Ptolemy at all
# (Perseus as a whole is Jupiter–Saturn), so it cites Robson alone.
STARS = {
    "Regulus": ("Jupiter and Mars", "I.9",
                "the royal star: big-hearted ambition and a talent for being noticed — with the old warning that what rises fast can fall the same way"),
    "Spica": ("Venus, with some Mars", "I.9",
              "the most fortunate star in the old books: gifts, grace, and things going your way"),
    "Antares": ("Mars, with some Jupiter", "I.9",
                "the heart of the Scorpion: courage that runs headlong, and a temper that can burn things down"),
    "Aldebaran": ("Mars", "I.9",
                  "the eye of the Bull: drive, prominence, and integrity tested under pressure"),
    "Alcyone": ("the Moon and Mars", "I.9",
                "the brightest of the Pleiades: ambition and restlessness, and something to grieve over"),
    "Castor": ("Mercury", "I.9", "a quick, sharp, inventive mind"),
    "Pollux": ("Mars", "I.9", "subtle and bold, with a fighter's edge"),
    "Deneb Algedi": ("Saturn and Jupiter", "I.9",
                     "the goat's tail: sorrow and good fortune in turn, and the steadiness to hold both"),
    "Arcturus": ("Jupiter and Mars", "I.10", "the pathfinder's star: success won by doing it your own way"),
    "Vega": ("Venus and Mercury", "I.10", "refined, artistic, idealistic — and changeable"),
    "Capella": ("Mars and Mercury", "I.10", "curiosity, a love of anything new, and the honour that comes with it"),
    "Fomalhaut": ("Venus and Mercury", "I.11", "a fortunate, imaginative star — the dreamer's luck"),
    "Sirius": ("Jupiter, with some Mars", "I.11", "the brightest star in the sky: honour, faithfulness, guardianship"),
    "Procyon": ("Mercury, with some Mars", "I.11", "a sudden rise — and the advice to keep your footing once you're up"),
    "Betelgeuse": ("Mars and Mercury", "I.11", "Orion's shoulder: martial courage and distinction"),
    "Rigel": ("Jupiter and Saturn", "I.11", "Orion's foot: steady benevolence and a teacher's instinct"),
    "Algol": (None, None,
              "the old books' most feared star — Robson's word was 'evil'. Read it now as intensity you have to handle on purpose"),
}

POINT_NAME = {"Ascendant": "your rising degree", "Midheaven": "your Midheaven"}


def _the(planet):
    return f"the {planet}" if planet in ("Sun", "Moon") else planet


def _ord(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def items(chart, sky, time_known):
    """The classical layer, as reading items with their sources."""
    out = []
    sun = sky.get("sun") or {}

    if sun and not sun.get("polar"):
        if sun["sect"] == "day":
            body = ("The Sun was above the horizon when you were born. Ptolemy sorts the planets "
                    "by day and night: the Sun and Jupiter belong to the day, the Moon and Venus to "
                    "the night. In a day birth he looks to the Sun first — your conscious, public "
                    "self carries the chart.")
        else:
            body = ("The Sun was below the horizon when you were born. Ptolemy sorts the planets "
                    "by day and night: the Sun and Jupiter belong to the day, the Moon and Venus to "
                    "the night. In a night birth he looks to the Moon first — your instincts and "
                    "your private life carry the chart.")
        out.append({"kind": "sect", "title": f"A {sun['sect']} birth", "body": body,
                    "source": f"{PTOLEMY} I.7, III.13 (Ashmand 1822, pp. 22–23, 132–133)"})

        out.append({"kind": "hour",
                    "title": f"Born on {_the(sun['planetary_day'])}'s day, in the hour of {_the(sun['planetary_hour'])}",
                    "body": (f"The old planetary clock starts each day at sunrise with that day's planet, "
                             f"then runs through the seven in Chaldean order, one per hour. You arrived in "
                             f"the {_ord(sun['hour_number'])} hour of the {sun['hour_of']} "
                             f"({sun['hour_minutes']:.0f}-minute hours that day), under {_the(sun['planetary_hour'])}."),
                    "source": f"{LILLY} pp. 482–483; Cassius Dio, Roman History 37.18–19"})

    vm = sky["venus_mercury"]
    for name in ("Venus", "Mercury"):
        v = vm[name]
        if v["in_suns_glare"]:
            continue
        if v["position"] == "oriental":
            body = (f"{name} rose before the Sun when you were born. Ptolemy calls a planet in that "
                    f"position masculine and outgoing, and says it makes people frank, self-assured "
                    f"and brave.")
        else:
            body = (f"{name} set after the Sun when you were born. Ptolemy calls a planet in that "
                    f"position feminine and receptive, and says {name} there makes the mind ingenious "
                    f"and perceptive, drawn to what's hidden.")
        out.append({"kind": f"{name.lower()}-orient",
                    "title": f"{name} as the {v['star']}", "body": body,
                    "source": f"{PTOLEMY} I.6, III.18 (Ashmand 1822, pp. 21–22, 159)"})

    t = temperament(chart, chart["planets"]["Sun"]["lon"], sky["moon"]["elongation"], time_known)
    lean = f", leaning {t['leaning']}" if t["leaning"] else ""
    out.append({"kind": "temperament", "title": f"Your temperament: {t['temperament']}{lean}",
                "body": t["body"] + " Weighed from "
                        + ", ".join(v["factor"] for v in t["votes"]) + ".",
                "source": f"{LILLY} ch. CVI, pp. 532–533 (simplified: aspects and the Lord of the Geniture not weighed)"})

    if sky.get("lots"):
        f = sky["lots"]["Fortune"]
        house = None
        cusps = chart.get("house_cusps")
        if cusps:
            from engine.chart import house_of
            house = house_of(f["lon"], cusps)
        where = f" in your {_ord(house)} house" if house else ""
        out.append({"kind": "fortune", "title": f"Your Lot of Fortune: {f['label']}",
                    "body": ("Measure from your Sun to your Moon, then lay the same arc from your rising "
                             f"degree: that's the Lot of Fortune, here{where}. The old astrologers read it "
                             "as where luck and material well-being tend to collect."),
                    "source": f"{PTOLEMY} III.13 (Ashmand 1822, pp. 131–132); {LILLY} pp. 143–144"})

    # Ptolemy wrote about the five planets he could see; Uranus, Neptune and
    # Pluto standing still is real astronomy, already in the birth sky, but not
    # something his text speaks to.
    for s in [s for s in sky["stations"] if s["planet"] in ("Mercury", "Venus", "Mars", "Jupiter", "Saturn")][:1]:
        out.append({"kind": "station", "title": f"Born with {s['planet']} at a standstill",
                    "body": ("Ptolemy singles out planets at their stations, when they seem to stop in "
                             "the sky, and connects them with a reflective, constant mind and a good memory."),
                    "source": f"{PTOLEMY} III.18 (Ashmand 1822, pp. 159–160)"})

    syz = sky.get("syzygy")
    if syz:
        out.append({"kind": "syzygy", "title": f"The {syz['kind']} before you: {syz['label']}",
                    "body": (f"{syz['days_before']:.0f} days before you were born. Ptolemy used the last "
                             "new or full Moon before a birth to check the birth time itself — one of the "
                             "earliest written methods for it."),
                    "source": f"{PTOLEMY} III.3 (Ashmand 1822, pp. 108–110)"})

    for st in sky["stars"]:
        if st["star"] not in STARS:
            continue
        nature, ch, gloss = STARS[st["star"]]
        point = POINT_NAME.get(st["point"], f"your {st['point']}")
        src = (f"{PTOLEMY} {ch} (nature of {nature}); Robson, Fixed Stars (1923)"
               if nature else "Robson, Fixed Stars (1923)")
        out.append({"kind": "star", "title": f"{st['star']} on {point}",
                    "body": f"{st['star']} sat within {st['orb']}° of {point} — {gloss}.",
                    "source": src})
    return out
