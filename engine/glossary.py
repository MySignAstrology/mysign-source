"""
Astrology, in plain words — for someone who has never read a horoscope.

Every term the app (or the model) uses gets two short lines: what it is, and
what it means for her. The front end underlines these words wherever they
appear — readings, deep-dives, today, people, chat — and a tap opens the card.
That covers model-written text automatically, which no list of hand-edited
labels ever could.

Rules for the copy: second person, no other jargon inside an explanation (or
it is itself a term with its own card), one idea per line, and nothing that
claims astrology is proven — the app's stance is that the sky is measured and
the meanings are a lens (see engine/placement_kb.HONEST).

`match` lists the forms the word takes in running text; longest wins, so
"rising sign" is caught before "rising" and "Moon sign" before "Moon".
"""

GLOSSARY = {
    # ------------------------------------------------------------ the basics
    "birth chart": {
        "match": ["birth chart", "natal chart", "your chart", "the chart"],
        "what": "It's a map of where the Sun, Moon and planets were in the sky at the exact minute and place you were born.",
        "you": "Everything in My Sign starts from yours. It never changes — it's the 'you' the daily sky is compared against.",
    },
    "sign": {
        "match": ["zodiac sign", "zodiac"],
        "what": "The sky is divided into 12 equal slices, named Aries through Pisces. A planet's sign is the slice it was in.",
        "you": "Each sign is a style — fiery, practical, chatty, sensitive and so on. Your planets each have one, so you're a mix, not just one sign.",
    },
    "Sun sign": {
        "match": ["Sun sign", "star sign"],
        "what": "It's the sign the Sun was in on your birthday — the one people mean when they ask 'what's your sign?'",
        "you": "It's read as your core: what energises you and the person you're growing into.",
    },
    "Moon sign": {
        "match": ["Moon sign"],
        "what": "It's the sign the Moon was in when you were born. The Moon moves fast, so it changes every two and a half days.",
        "you": "It's read as your emotional side: what you need to feel safe, and how you react before you think.",
    },
    "rising sign": {
        "match": ["rising sign", "Ascendant", "Rising"],
        "what": "It's the sign that was coming up over the eastern horizon at the minute you were born. It needs an accurate birth time.",
        "you": "It's read as how you come across — your first impression and your instinctive way of starting things.",
    },
    "Midheaven": {
        "match": ["Midheaven", "MC"],
        "what": "It's the zodiac degree crossing your birthplace's meridian (due south, or north below the equator) as you were born — where the midday Sun stands. Usually high in the sky; near the poles it can be below the horizon.",
        "you": "It's linked to your public life: career, reputation and what you want to be known for.",
    },
    "big three": {
        "match": ["big three", "big 3"],
        "what": "It's your Sun, Moon and rising signs taken together.",
        "you": "It's the quickest summary of you: your core, your feelings, and how you come across.",
    },
    "placement": {
        "match": ["placements", "placement"],
        "what": "It's where something sits in your chart — for example 'Venus in Libra' is a placement.",
        "you": "Each placement is one piece of the reading about you.",
    },

    # ------------------------------------------------------------ the planets
    "Sun": {"match": ["the Sun", "Sun"],
            "what": "In astrology the Sun stands for your identity and vitality.",
            "you": "Where it sits in your chart describes what makes you feel most like yourself."},
    "Moon": {"match": ["the Moon", "Moon"],
             "what": "The Moon stands for feelings, moods and comfort.",
             "you": "Its place in your chart describes your emotional needs and how you look after yourself."},
    "Mercury": {"match": ["Mercury"],
                "what": "Mercury is the planet of thinking and communication.",
                "you": "It describes how your mind works and how you talk, text and learn."},
    "Venus": {"match": ["Venus"],
              "what": "Venus is the planet of love, beauty and pleasure.",
              "you": "It describes what you find attractive, how you show affection, and what you value."},
    "Mars": {"match": ["Mars"],
             "what": "Mars is the planet of drive, desire and anger.",
             "you": "It describes how you go after what you want, and how you handle conflict."},
    "Jupiter": {"match": ["Jupiter"],
                "what": "Jupiter is the planet of growth, luck and generosity.",
                "you": "Where it sits shows where life tends to open up for you."},
    "Saturn": {"match": ["Saturn"],
               "what": "Saturn is the planet of discipline, limits and time.",
               "you": "It shows where you work hardest and grow the most — often slowly."},
    "Uranus": {"match": ["Uranus"],
               "what": "Uranus is the planet of change, surprise and independence.",
               "you": "It moves very slowly, so it mostly describes your generation — and sudden shake-ups when it touches your chart."},
    "Neptune": {"match": ["Neptune"],
                "what": "Neptune is the planet of dreams, imagination and things that feel blurry.",
                "you": "It colours a generation; when it touches your chart, things can feel inspired or confusing."},
    "Pluto": {"match": ["Pluto"],
              "what": "Pluto is the planet of deep, slow transformation.",
              "you": "It describes big, gradual changes and what you hold power over — or what holds power over you."},
    "Chiron": {"match": ["Chiron"],
               "what": "It's a small body between Saturn and Uranus that astrologers call 'the wounded healer'.",
               "you": "It's read as an old sore spot — and the place where you end up helping others most."},
    "North Node": {"match": ["North Node", "South Node", "Node"],
                   "what": "It isn't a planet. It's a point where the Moon's path crosses the Sun's.",
                   "you": "It's read as a direction of growth — where life keeps nudging you."},

    # ------------------------------------------------------------ houses
    "house": {
        "match": ["houses", "house"],
        "what": "The chart is also split into 12 'houses', each one an area of life — home, money, love, work and so on. Which house a planet lands in depends on your birth time.",
        "you": "A planet's house tells you where in your life that part of you shows up most. '7th house' means relationships; '10th house' means career.",
    },
    "1st house": {"match": ["1st house", "first house"], "what": "This house covers the self and the body.", "you": "Planets here are visible in how you come across."},
    "2nd house": {"match": ["2nd house", "second house"], "what": "This house covers money and what you value.", "you": "Planets here shape your relationship with money and self-worth."},
    "3rd house": {"match": ["3rd house", "third house"], "what": "This house covers talking, learning and everyday life.", "you": "Planets here play out in conversations, siblings and your neighbourhood."},
    "4th house": {"match": ["4th house", "fourth house"], "what": "This house covers home and family.", "you": "Planets here shape your roots and what home means to you."},
    "5th house": {"match": ["5th house", "fifth house"], "what": "This house covers fun, romance and creativity.", "you": "Planets here show up in dating, hobbies and what you make."},
    "6th house": {"match": ["6th house", "sixth house"], "what": "This house covers work routines and health.", "you": "Planets here shape your daily habits and how you look after yourself."},
    "7th house": {"match": ["7th house", "seventh house"], "what": "This house covers partnership.", "you": "Planets here show up in close one-to-one relationships."},
    "8th house": {"match": ["8th house", "eighth house"], "what": "This house covers intimacy, trust and shared money.", "you": "Planets here play out in deep bonds and big life changes."},
    "9th house": {"match": ["9th house", "ninth house"], "what": "This house covers travel, study and beliefs.", "you": "Planets here show up in adventures and how you make sense of life."},
    "10th house": {"match": ["10th house", "tenth house"], "what": "This house covers career and reputation.", "you": "Planets here shape your work life and how the world sees you."},
    "11th house": {"match": ["11th house", "eleventh house"], "what": "This house covers friends and community.", "you": "Planets here show up in your friendships and the groups you belong to."},
    "12th house": {"match": ["12th house", "twelfth house"], "what": "This house covers solitude and your inner world.", "you": "Planets here are private — they show up when you're alone, in dreams, or behind the scenes."},

    # ------------------------------------------------------------ aspects
    "aspect": {
        "match": ["aspects", "aspect"],
        "what": "It's an angle between two planets. Certain angles are said to make them work together or rub against each other.",
        "you": "Aspects show which parts of you cooperate and which ones pull in different directions.",
    },
    "conjunction": {"match": ["conjunctions", "conjunction", "conjunct"],
                    "what": "It's when two planets sit in almost the same spot in the sky.",
                    "you": "Those two parts of you are fused — they always act together, for better or worse."},
    "opposition": {"match": ["oppositions", "opposition", "opposes"],
                   "what": "It's when two planets sit on opposite sides of the sky.",
                   "you": "Two needs pull against each other, and you're always balancing them."},
    "square": {"match": ["squares", "square"],
               "what": "It's when two planets are a quarter of the sky apart (90°).",
               "you": "It creates friction that pushes you to act — the source of a lot of growth, and some stress."},
    "trine": {"match": ["trines", "trine"],
              "what": "It's when two planets are a third of the sky apart (120°).",
              "you": "Two parts of you work together easily and naturally."},
    "sextile": {"match": ["sextiles", "sextile"],
                "what": "It's when two planets are a sixth of the sky apart (60°).",
                "you": "Two parts of you can help each other, and it comes easily if you make use of it."},
    "orb": {"match": ["orb"],
            "what": "It's how close an angle is to exact, in degrees. A smaller number means a stronger effect.",
            "you": "A 1° orb is felt strongly; a 6° orb only lightly."},
    "degree": {"match": ["degrees", "degree"],
               "what": "Each sign is 30 degrees wide, so '14° Leo' means 14 degrees into Leo.",
               "you": "The exact degree is what makes your chart different from everyone else's with the same sign."},

    # ------------------------------------------------------------ the moving sky
    "transit": {
        "match": ["transiting", "transits", "transit"],
        "what": "It's where a planet is in the sky today, compared with where it was when you were born.",
        "you": "Transits are why your days feel different: today's sky is touching your birth chart. It's what the daily readings are built on.",
    },
    "retrograde": {
        "match": ["retrograde", "Rx"],
        "what": "It's when a planet looks like it's moving backwards from Earth. It isn't really — it's a viewing angle, like passing a slower car on the highway.",
        "you": "Astrologers read it as a time to review and redo rather than start fresh, in whatever that planet rules.",
    },
    "station": {"match": ["stationing", "stations", "stationary", "standing still"],
                "what": "These are the days when a planet seems to stop before changing direction.",
                "you": "Its themes are said to feel strongest around then."},
    "new moon": {"match": ["new moon"], "what": "It's when the Moon is dark — between the Earth and the Sun.", "you": "It's traditionally a time for fresh starts and setting intentions."},
    "full moon": {"match": ["full moon"], "what": "It's when the Moon is fully lit — opposite the Sun.", "you": "It's traditionally a time when things come to a head or feel more intense."},
    "waxing": {"match": ["waxing crescent", "waxing gibbous", "waxing"], "what": "It's the Moon growing from new to full.", "you": "It's read as a building phase, when momentum gathers."},
    "waning": {"match": ["waning crescent", "waning gibbous", "waning"], "what": "It's the Moon shrinking from full to new.", "you": "It's read as a letting-go phase, a time for rest and finishing."},
    "eclipse": {"match": ["eclipses", "eclipse"],
                "what": "It's when the Sun, Moon and Earth line up and one blocks the light of another.",
                "you": "Astrologers treat eclipses as turning points, especially when they fall on a sensitive spot in your chart."},
    "lunation": {"match": ["lunations", "lunation"], "what": "It's a new moon or a full moon.", "you": "Astrologers read each month through this rhythm of new and full moons."},
    "progressions": {"match": ["progressed", "progressions", "progression"],
                     "what": "It's a technique that moves your birth chart forward slowly — one day after your birth for each year of your life.",
                     "you": "It's read as your inner seasons: the chapter you're in right now."},
    "solar return": {"match": ["solar return"], "what": "It's the moment each year when the Sun returns to exactly where it was when you were born — your astrological birthday.", "you": "A chart for that moment is read as the theme of your year ahead."},
    "Saturn return": {"match": ["Saturn return"], "what": "It's when Saturn comes back to where it was at your birth — around ages 29, 58 and 87.", "you": "It's often a growing-up moment: big decisions about work, love and who you are."},
    "season": {"match": ["backdrop"], "what": "It's the effect of the slow-moving planets, lasting weeks or months.", "you": "It works in the background: it sets the mood but doesn't decide the day."},

    # ------------------------------------------------------------ style of a sign
    "element": {"match": ["elements", "element"],
                "what": "Each sign belongs to one of four elements: fire, earth, air or water.",
                "you": "Your mix of elements is a quick picture of your temperament."},
    "fire": {"match": ["fire signs", "fire sign"], "what": "The fire signs are Aries, Leo and Sagittarius.", "you": "Fire is about energy, passion and acting first."},
    "earth": {"match": ["earth signs", "earth sign"], "what": "The earth signs are Taurus, Virgo and Capricorn.", "you": "Earth is about practicality, patience and the physical world."},
    "air": {"match": ["air signs", "air sign"], "what": "The air signs are Gemini, Libra and Aquarius.", "you": "Air is about ideas, talk and people."},
    "water": {"match": ["water signs", "water sign"], "what": "The water signs are Cancer, Scorpio and Pisces.", "you": "Water is about feelings, intuition and closeness."},
    "modality": {"match": ["modality", "cardinal", "mutable", "fixed sign"],
                 "what": "Signs also come in three types: cardinal (starters), fixed (sustainers) and mutable (adapters).",
                 "you": "It describes whether you'd rather start things, keep them going, or adapt."},
    "polarity": {"match": ["polarity"], "what": "Signs alternate between outward (fire, air) and inward (earth, water) energy.", "you": "It gives a rough sense of whether you recharge out in the world or on your own."},

    # ------------------------------------------------------------ deeper detail
    "decan": {"match": ["decans", "decan"],
              "what": "Each sign is split into three 10-degree parts, and each part has its own ruling planet.",
              "you": "It adds a flavour to a placement — two people with the same sign can be in different decans."},
    "term": {"match": ["term of", "Egyptian terms"],
             "what": "It's an old system that splits each sign into five uneven parts, each ruled by a planet.",
             "you": "It's a fine-grained detail astrologers use to add nuance to an exact degree."},
    "ruler": {"match": ["ruling planet", "chart ruler", "ruler chain", "ruled by", "ruler"],
              "what": "Every sign has a planet that 'rules' it — Leo is ruled by the Sun, Libra by Venus, and so on.",
              "you": "Following the ruler shows what a part of you answers to: where that planet sits adds to the story."},
    "dignity": {"match": ["dignity", "domicile", "exalted", "exaltation", "detriment", "in its fall"],
                "what": "It describes how comfortable a planet is in a sign. 'At home' and 'exalted' mean strong; 'detriment' and 'fall' mean it works harder.",
                "you": "A planet in a hard spot isn't bad — it just takes more effort and often gives more depth."},
    "stellium": {"match": ["stellium"], "what": "It's when three or more planets are bunched in one sign or house.", "you": "That area of life is a big focus for you."},
    "angular": {"match": ["angular", "angles"], "what": "These are planets close to the four main points of the chart (rising, midheaven and their opposites).", "you": "These planets are especially visible in your life and personality."},
    "sect": {"match": ["day birth", "night birth", "sect"], "what": "It describes whether you were born with the Sun above the horizon (day) or below it (night).", "you": "Old astrologers read day and night charts differently, including which planets take the lead."},
    "temperament": {"match": ["temperament", "phlegmatic", "choleric", "sanguine", "melancholic"],
                    "what": "It's an old idea that sorts people into four temperaments based on hot, cold, wet and dry qualities.",
                    "you": "It's a historical lens, not a diagnosis — it's included for the tradition, not as science."},
    "Lot of Fortune": {"match": ["Lot of Fortune", "Part of Fortune"], "what": "It's a point calculated from the positions of your Sun, Moon and rising sign.", "you": "It's traditionally read as where luck and wellbeing come most easily."},
    "fixed star": {"match": ["fixed stars", "fixed star"], "what": "It's a real star that astrologers note when a planet sits right on top of it.", "you": "It adds a specific flavour to that planet in your chart."},
    "evening star": {"match": ["evening star", "morning star"], "what": "It's Venus or Mercury seen just after sunset (evening) or before sunrise (morning).", "you": "Which one it was at your birth is a real sky fact; tradition reads them slightly differently."},
    "precession": {"match": ["precession", "constellation"],
                   "what": "Signs are fixed to the seasons; constellations are star patterns. Over 2,000 years the two have drifted apart by about 25° (the Fagan–Bradley measure; other conventions say 24°).",
                   "you": "That's why the stars behind your Sun may not match your sign. Both are true — they're two different maps."},

    # ------------------------------------------------------------ people
    "synastry": {"match": ["synastry"],
                 "what": "It's comparing two people's birth charts side by side.",
                 "you": "It shows where you and someone else naturally click, and where you clash."},
    "composite": {"match": ["composite chart", "composite", "the relationship's"],
                  "what": "It's a chart made from the midpoints between two people's planets — a chart for the relationship itself.",
                  "you": "It describes the 'us' — the pair as its own thing — rather than either of you."},
    "compatibility": {"match": ["compatibility score", "compatibility"],
                      "what": "It's a score based on how two birth charts line up.",
                      "you": "A score of 50 is an ordinary pair. It's a conversation starter, not a verdict — real relationships are made, not predicted."},

    # ------------------------------------------------------------ the app's own terms
    "check-in": {"match": ["check-ins", "check-in", "checked in"],
                 "what": "It's a quick note on how your day is going — a mood from 1 to 10 and, if you want, a line about it.",
                 "you": "Over time this is how My Sign learns your real patterns, instead of guessing from your sign."},
    "pattern": {"match": ["verified patterns", "verified pattern"],
                "what": "It's something that keeps happening in your own check-ins — tested so it isn't just chance.",
                "you": "It's a pattern from your data, with the numbers shown — never a claim based on your sign alone."},
    "tropical": {"match": ["tropical zodiac", "tropical", "sidereal"],
                 "what": "The way the signs are measured. Tropical (what My Sign uses) ties them to the seasons; sidereal ties them to the stars.",
                 "you": "It's why your sign here matches the one in most Western horoscopes."},
    "house system": {"match": ["house system", "Placidus", "Whole Sign"],
                     "what": "These are the different methods astrologers use to draw the 12 houses.",
                     "you": "You can switch in Settings; it can move a planet into a neighbouring house."},
    "ephemeris": {"match": ["ephemeris", "Swiss Ephemeris"],
                  "what": "It's a precise table of where the planets are at any moment, from NASA data.",
                  "you": "It's why the positions in My Sign are exact — the astronomy is real, even though the meanings are a lens."},
}


def terms():
    """[{term, match, what, you}] for the front end, longest match first so a
    phrase is caught before a word inside it."""
    out = [{"term": t, "match": v["match"], "what": v["what"], "you": v["you"]}
           for t, v in GLOSSARY.items()]
    return out


PLAIN_RULE = (
    "She may know nothing about astrology. Write so a complete beginner can follow: "
    "the first time you use any technical word (house, transit, aspect, square, trine, "
    "rising sign, retrograde, decan, ruler, composite and so on), explain it in a short "
    "plain phrase right there — e.g. 'Mars squares your Moon (a tense angle between your "
    "drive and your feelings)'. Lead with what it means for her life, then name the "
    "astrology. Never stack jargon. Never write anything she'd need a dictionary for. "
    "Write in complete sentences that make sense on their own: no verbless fragments, "
    "no stacked adjectives like 'Protective, reactive, alive.', and no metaphors she "
    "would have to decode. Short is good; clipped is not."
)
