"""
The knowledge behind a placement deep-dive — what each piece of a placement is
taken to mean, in the app's own words.

Provenance, so an audit is one read:

  Facts (uncopyrightable tables): sign rulers, exaltation and fall, the
  Chaldean decans (faces) and the Egyptian terms, as given in Ptolemy's
  Tetrabiblos I.17–21 (Ashmand 1822 / Robbins 1940) and Lilly's Table of
  Essential Dignities, Christian Astrology (1647) p.104.

  Sign imagery and the Sun/Moon/Ascendant distinction: informed by the
  public-domain tradition — Manilius (tr. Creech, 1697) for imagery, Lilly
  (1647) and Alan Leo (Astrology for All, 1899/1910; How to Judge a Nativity,
  1908) for the Sun as individuality, the Moon as the personality and its
  needs, and the Ascendant as how a life meets the world.

  The psychological layer (needs, shadow, growth edge, elements as functions)
  is written from concepts common across modern practice, never from any
  modern author's text.

All prose is original. Nothing here asserts a fact about a person; these are
the traditional meanings a reading draws on, and the reading says so.

NOT REVIEWED BY AN ASTROLOGER. Put it in front of one before launch.
"""

ROLE = {
    "sun": {
        "label": "Sun", "title": "Your Sun",
        "about": ("The Sun is the oldest centre of a chart. The astrologer Alan Leo "
                  "called it the individuality: the self you are growing into and the thing you "
                  "can't stop being. It is about vitality, direction and what makes "
                  "you feel like the main character of your own life."),
        "question": "What makes me feel most like myself?",
    },
    "moon": {
        "label": "Moon", "title": "Your Moon",
        "about": ("The Moon is the private self — what the astrologer Alan Leo called "
                  "the personality, the part that reacts before it thinks. In modern practice it is read as your "
                  "emotional needs: what makes you feel safe, how you self-soothe, "
                  "what home means, and the patterns you learned early."),
        "question": "What do I need in order to feel safe?",
    },
    "rising": {
        "label": "Rising", "title": "Your Rising",
        "about": ("The Ascendant is the degree of the zodiac that was coming up over "
                  "the eastern horizon the minute you were born. Tradition reads it as "
                  "the doorway of the chart: how you meet the world, the first "
                  "impression, your instinctive approach to anything new — the lens "
                  "your Sun shines through."),
        "question": "How do I meet the world — and what does it see first?",
    },
}

ELEMENT_FUNCTION = {
    # Elements as psychological functions — a widely used modern framing
    # (after Jung's typology). Written in-house.
    "fire": ("intuition and drive", "Fire runs on possibility: it acts first and makes "
             "meaning as it goes."),
    "earth": ("the senses and the practical", "Earth trusts what it can touch, "
              "measure and build; it learns by doing."),
    "air": ("thought and connection", "Air processes life through ideas, language and "
            "other people; it needs room to step back and see."),
    "water": ("feeling and attunement", "Water picks up on feelings first; it "
              "knows things before it can explain them."),
}
MODALITY_FUNCTION = {
    "cardinal": "Cardinal signs start things — they meet a season at its turning point.",
    "fixed": "Fixed signs hold things — they sit in the middle of a season and keep it going.",
    "mutable": "Mutable signs adapt — they close a season and carry it into the next.",
}

# Per sign: an image from the old sky, then the traditional meaning broken into
# what a deep read needs. Second person, trade-offs named, no fortune-telling.
SIGN = {
    "Aries": {
        "image": "Its symbol is the Ram. It is the first sign, starting at the spring equinox as the season breaks open.",
        "gift": "Courage that doesn't wait for permission, and the ability to start from nothing.",
        "shadow": "Impatience that burns bridges, and treating every delay as a personal insult.",
        "need": "Movement, a challenge worth meeting, and the freedom to go first.",
        "growth": "Learning that finishing is its own kind of bravery.",
        "love": "You love directly and fast; you want to be chosen clearly and chased a little.",
        "stress": "You get louder and quicker, and you'd rather fight than feel stuck.",
        "best": "You're the one who makes the first move when everyone else is waiting.",
    },
    "Taurus": {
        "image": "Its symbol is the Bull. It belongs to settled, green spring, a season that ripens rather than starts.",
        "gift": "Steadiness, taste, and the patience to build something that lasts.",
        "shadow": "Digging in past the point of sense, and mistaking comfort for safety.",
        "need": "Physical ease, reliability, and time — nobody rushing you.",
        "growth": "Letting change in before it has to force its way in.",
        "love": "You are slow to choose and loyal once you do; you show love in what you give and keep.",
        "stress": "You go quiet and immovable, and food, sleep or spending become the fix.",
        "best": "You make any room feel like somewhere people want to stay.",
    },
    "Gemini": {
        "image": "Its symbol is the Twins. It belongs to late spring, when everything is in bloom at once.",
        "gift": "A quick, curious mind that connects people and ideas others keep apart.",
        "shadow": "Spreading yourself too thin, and talking around a feeling instead of having it.",
        "need": "Variety, conversation, and someone who can keep up.",
        "growth": "Staying with one thing long enough to let it change you.",
        "love": "You fall for minds first; banter is foreplay and silence feels like distance.",
        "stress": "Your thoughts race, you over-explain, and you start ten things at once.",
        "best": "You can make anyone feel interesting, and you remember what they said.",
    },
    "Cancer": {
        "image": "Its symbol is the Crab, with a shell that protects what is inside. It starts at the summer solstice, the longest day.",
        "gift": "Deep care, emotional memory, and the instinct to make people feel held.",
        "shadow": "Retreating into your shell, and keeping score of hurts you never named out loud.",
        "need": "Belonging, a home base, and people who stay.",
        "growth": "Asking for care as directly as you give it.",
        "love": "You are protective and devoted; you love by looking after people, and you need looking after too.",
        "stress": "You withdraw, get moody, and test whether people will come after you.",
        "best": "You're the person everyone calls when something has gone wrong.",
    },
    "Leo": {
        "image": "Its symbol is the Lion. It belongs to high summer, when the heat is at its height, and it is the Sun's own sign.",
        "gift": "Warmth, generosity, and the nerve to be seen.",
        "shadow": "Needing applause to believe in yourself, and pride that can't admit it's hurt.",
        "need": "Recognition, play, and a chance to create something that is yours.",
        "growth": "Shining without needing the room to confirm it.",
        "love": "You are big-hearted and loyal; you want to adore and be adored, out loud.",
        "stress": "You perform being fine, or you go cold when you feel overlooked.",
        "best": "You make other people braver just by being in the room.",
    },
    "Virgo": {
        "image": "Its symbol is the Maiden holding an ear of wheat. It belongs to harvest, the season of sorting what's good.",
        "gift": "Discernment, skill, and care shown through usefulness.",
        "shadow": "Turning the critical eye inward until nothing is ever good enough.",
        "need": "Order, a purpose, and the chance to get something right.",
        "growth": "Letting 'good enough' be enough, for yourself first.",
        "love": "You show love in the details — remembering, fixing, noticing.",
        "stress": "You worry, over-plan, and fix things that weren't broken.",
        "best": "You make chaos workable and people feel taken care of without fuss.",
    },
    "Libra": {
        "image": "Its symbol is the Scales. It starts at the autumn equinox, when day and night are in balance.",
        "gift": "Grace, fairness, and the ability to see every side.",
        "shadow": "Disappearing into whatever keeps the peace, and deciding by not deciding.",
        "need": "Harmony, beauty, and a partner in the thinking.",
        "growth": "Choosing yourself even when it makes the room uneasy.",
        "love": "You are romantic and considerate; you want a real partnership with an equal.",
        "stress": "You people-please, then resent it, and indecision gets louder.",
        "best": "You can make two people who disagree feel understood.",
    },
    "Scorpio": {
        "image": "Its symbol is the Scorpion. It belongs to deep autumn, the season of what lies under the surface.",
        "gift": "Depth, loyalty, and the nerve to look at what others avoid.",
        "shadow": "Using control as protection, and holding on to an old wound like evidence.",
        "need": "Trust that has been tested, intensity, and privacy.",
        "growth": "Letting yourself be known before you're sure it's safe.",
        "love": "You love all or nothing; you want to merge completely, and you notice everything.",
        "stress": "You go silent, strategic, or cutting; you'd rather end it than be betrayed.",
        "best": "You stay in the hard conversation long after everyone else has left.",
    },
    "Sagittarius": {
        "image": "Its symbol is the Archer, with an arrow aimed at the horizon. It belongs to late autumn.",
        "gift": "Faith, humour, and a mind that wants the bigger picture.",
        "shadow": "Leaving when life feels small or heavy, and promising more than you can carry.",
        "need": "Freedom, meaning, and something to look forward to.",
        "growth": "Finding the adventure in staying.",
        "love": "You are generous and honest; you need a partner who is also a fellow traveller.",
        "stress": "You get restless, preachy, or blunt, and start planning an exit.",
        "best": "You make other people's worlds bigger and their problems feel survivable.",
    },
    "Capricorn": {
        "image": "Its symbol is the Sea-Goat. It starts at the winter solstice, the longest night, as the days climb back toward light.",
        "gift": "Discipline, responsibility, and the long view.",
        "shadow": "Measuring your worth by output, and refusing help so you can't be let down.",
        "need": "Respect, structure, and goals that mean something.",
        "growth": "Resting without having earned it.",
        "love": "You are serious and steadfast; you show love by building a future with someone.",
        "stress": "You work harder, feel more alone, and get hard on yourself and others.",
        "best": "You're the one people trust to still be standing when it's over.",
    },
    "Aquarius": {
        "image": "Its symbol is the Water-Bearer, pouring out water for everyone. It belongs to deep winter.",
        "gift": "Originality, principle, and loyalty to the group and the future.",
        "shadow": "Detaching to stay safe, and loving humanity more easily than one person.",
        "need": "Freedom, friendship, and room to be different.",
        "growth": "Letting someone close enough to see the feelings behind the ideas.",
        "love": "For you, friendship comes first; you need a partner who respects your independence.",
        "stress": "You go cool, analytical and distant, and argue the principle instead of the feeling.",
        "best": "You see what's possible before anyone else, and you include the outsiders.",
    },
    "Pisces": {
        "image": "Its symbol is two fish swimming opposite ways. It closes winter, as one year dissolves into the next.",
        "gift": "Empathy, imagination, and an ear for what isn't said.",
        "shadow": "Absorbing everyone else's moods, and escaping instead of choosing.",
        "need": "Softness, meaning, and time alone to come back to yourself.",
        "growth": "Keeping a firm boundary, so other people's feelings don't sweep you away.",
        "love": "You are devoted and romantic; you love the person and also the dream of them.",
        "stress": "You drift, avoid, or disappear into a feeling or a screen.",
        "best": "You make people feel understood without them having to explain.",
    },
}

# Each house as an arena of life, with the arc it asks of you. Traditional
# significations (Lilly, Book I) with a modern developmental reading.
HOUSE = {
    1: ("the self and the body — how you begin",
        "Whatever lands here becomes visible: it's in your face and your first move."),
    2: ("worth, money and what you keep",
        "This is where you learn what you value — in your bank account and in yourself."),
    3: ("words, siblings, the everyday mind",
        "Here, life works through conversation, learning and the short trips of an ordinary day."),
    4: ("home, family and roots",
        "This is the floor of the chart: where you come from, and the private foundation you build."),
    5: ("pleasure, romance, play and creation",
        "It asks what you make for the joy of it — art, love affairs, children, fun."),
    6: ("work, routine and the body's upkeep",
        "Meaning comes through daily practice, service and how you look after yourself."),
    7: ("partnership and the other",
        "You meet yourself through one other person — and what you don't own in you, you find in them."),
    8: ("intimacy, shared resources and transformation",
        "It covers what's hidden, merged and shared: trust, sex, money that isn't only yours, endings and fresh starts."),
    9: ("belief, travel and the bigger picture",
        "It broadens your view through study, travel, philosophy and the search for what it all means."),
    10: ("calling, reputation and what you're known for",
         "This is the top of the chart: what you're building in public and how the world sees your work."),
    11: ("friends, groups and the future you want",
         "Life runs through community — the people you choose and the hopes you share."),
    12: ("solitude, the unconscious and what runs beneath the surface",
         "This is the most private place in the chart: retreat, dreams, compassion, and what you don't show."),
}

PLANET_KEY = {
    "Sun": "vitality, identity, purpose", "Moon": "feeling, need, instinct",
    "Mercury": "mind, speech, curiosity", "Venus": "love, beauty, pleasure, value",
    "Mars": "drive, desire, courage, anger", "Jupiter": "growth, faith, generosity",
    "Saturn": "structure, limits, mastery, time",
}

RULER = {"Aries": "Mars", "Taurus": "Venus", "Gemini": "Mercury", "Cancer": "Moon",
         "Leo": "Sun", "Virgo": "Mercury", "Libra": "Venus", "Scorpio": "Mars",
         "Sagittarius": "Jupiter", "Capricorn": "Saturn", "Aquarius": "Saturn",
         "Pisces": "Jupiter"}

# Chaldean faces (decans): starting Mars at 0–10 Aries, then the Chaldean order.
_CHALDEAN = ["Saturn", "Jupiter", "Mars", "Sun", "Venus", "Mercury", "Moon"]
SIGNS = list(SIGN)


def face(lon):
    """(decan 1–3, ruling planet) for an ecliptic longitude."""
    i = int(lon // 10) % 36
    return i % 3 + 1, _CHALDEAN[(2 + i) % 7]


# Egyptian terms (bounds): (planet, end degree) per sign — Ptolemy I.20 /
# Lilly p.104 report the Egyptian set.
TERMS = {
    "Aries": [("Jupiter", 6), ("Venus", 12), ("Mercury", 20), ("Mars", 25), ("Saturn", 30)],
    "Taurus": [("Venus", 8), ("Mercury", 14), ("Jupiter", 22), ("Saturn", 27), ("Mars", 30)],
    "Gemini": [("Mercury", 6), ("Jupiter", 12), ("Venus", 17), ("Mars", 24), ("Saturn", 30)],
    "Cancer": [("Mars", 7), ("Venus", 13), ("Mercury", 19), ("Jupiter", 26), ("Saturn", 30)],
    "Leo": [("Jupiter", 6), ("Venus", 11), ("Saturn", 18), ("Mercury", 24), ("Mars", 30)],
    "Virgo": [("Mercury", 7), ("Venus", 17), ("Jupiter", 21), ("Mars", 28), ("Saturn", 30)],
    "Libra": [("Saturn", 6), ("Mercury", 14), ("Jupiter", 21), ("Venus", 28), ("Mars", 30)],
    "Scorpio": [("Mars", 7), ("Venus", 11), ("Mercury", 19), ("Jupiter", 24), ("Saturn", 30)],
    "Sagittarius": [("Jupiter", 12), ("Venus", 17), ("Mercury", 21), ("Saturn", 26), ("Mars", 30)],
    "Capricorn": [("Mercury", 7), ("Jupiter", 14), ("Venus", 22), ("Saturn", 26), ("Mars", 30)],
    "Aquarius": [("Mercury", 7), ("Venus", 13), ("Jupiter", 20), ("Mars", 25), ("Saturn", 30)],
    "Pisces": [("Venus", 12), ("Jupiter", 16), ("Mercury", 19), ("Mars", 28), ("Saturn", 30)],
}


def term(lon):
    sign = SIGNS[int(lon // 30) % 12]
    deg = lon % 30
    return next(p for p, end in TERMS[sign] if deg < end)


PROMPTS = {
    "sun": [
        "When did you last feel completely like yourself? What were you doing?",
        "Which part of you do you only show when you feel safe — and what would happen if you led with it?",
        "What would you build if no one were watching or grading?",
        "Where have you been dimming yourself to keep the peace?",
        "What are you becoming that you weren't five years ago?",
    ],
    "moon": [
        "What do you reach for when you're upset — and does it actually help?",
        "Who made you feel safe as a child, and how did they do it?",
        "What does 'home' feel like to you, in one sentence?",
        "What need do you hide because it feels like too much to ask for?",
        "How do you know when you're running on empty — what's the first sign?",
    ],
    "rising": [
        "What do people assume about you before they know you? How close is it?",
        "How do you walk into a room where you know no one?",
        "What did you have to become early on to get by?",
        "Which first impression of yours would you most like to change?",
        "When you start something new, what's your first move — every time?",
    ],
}

HONEST = ("The sky in this reading is real: every position is computed from NASA JPL "
          "ephemeris data and checked against JPL Horizons. What astrology makes of that "
          "sky is a symbolic language centuries old, not a proven science — controlled "
          "studies haven't found that signs determine personality. So read this the way a "
          "good journal prompt works: as a lens. Anything we say about you as a pattern "
          "comes from your own check-ins and conversations, and we show the numbers.")
