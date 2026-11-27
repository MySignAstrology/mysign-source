"""
How you love, how you want, how you think.

The Sun, Moon and rising are the reading everyone gets. Venus, Mars and Mercury
are the reading people actually ask about — "why do I go for people like this",
"why do I go quiet when I'm angry", "why do I text like that" — and until now
they got one sign-style phrase shared by every planet, so Venus in Aries and
Mars in Aries said the same sentence.

These are written per planet, per sign. They are original prose in the app's
voice, built on the conventional Western meanings of each sign as they appear in
the public-domain tradition (Lilly's Christian Astrology, 1647; Alan Leo's
manuals, 1900s) and in practice since. Dignity is computed separately
(engine/corpus.py) and appended, so a Venus in her fall reads differently from
a Venus at home without anyone having to write that twice.

NOT REVIEWED BY AN ASTROLOGER. Put it in front of one.
"""
from engine import corpus

THEME = {
    "Venus": ("How you love", "what you find beautiful, what you want, and how you let yourself be wanted"),
    "Mars": ("How you want and fight", "desire, drive, and what you do with anger"),
    "Mercury": ("How you think and talk", "your mind, your mouth, and your texting"),
}

VENUS = {
    "Aries": ("You fall first and fast",
              "Attraction hits you like a decision already made, and the chase is half the pleasure. "
              "You'd rather be adored a little recklessly than courted carefully, and boredom kills it faster than conflict ever could."),
    "Taurus": ("You love with your whole body",
               "Touch, food, warmth, someone who shows up at the same time every week — that's romance to you. "
               "You're slow to choose and very slow to let go, and you know quality when you feel it."),
    "Gemini": ("You fall for minds first",
               "The best flirting you've ever done happened in conversation, probably in writing. "
               "You need someone who can keep up and keep surprising you; the moment it's predictable you're already somewhere else in your head."),
    "Cancer": ("You love like you're building a home",
               "You remember how they take their coffee and the name of their childhood dog. "
               "Safety is the aphrodisiac — and you test for it quietly, long before you say anything."),
    "Leo": ("You want to be adored, out loud",
            "Love should feel like being lit up, and you give it back with the same generosity — grand gestures, loyalty, pride in them. "
            "What wounds you is being taken for granted, not being argued with."),
    "Virgo": ("You love by noticing",
              "You show it in the details: the fixed hinge, the remembered appointment, the thing they said once and forgot. "
              "You're more devoted than you look and more critical than you'd like, mostly of yourself."),
    "Libra": ("You're in love with the relationship itself",
              "Partnership is where you come alive — the courtship, the balance, the beauty of two people getting it right. "
              "You'll smooth a lot over to keep the peace, so watch how much."),
    "Scorpio": ("You love all the way or not at all",
                "Casual has never really been on the table. You want to know everything and you notice everything, "
                "and once you've chosen someone the loyalty is total — so is the memory if they betray it."),
    "Sagittarius": ("You need love to feel like freedom",
                    "The right person feels like an adventure, not a cage. You fall for honesty, humour and a big life, "
                    "and you go cold on anyone who tries to make you smaller."),
    "Capricorn": ("You love like it's meant to last",
                  "You're not in a hurry and you're not easily fooled. Respect comes before romance for you, "
                  "and you show love by building something real — which can read as reserved until someone has earned it."),
    "Aquarius": ("You need to be friends first",
                 "You're drawn to the unusual and allergic to scripts. Love works when you can stay fully yourself inside it; "
                 "the fastest way to lose you is to try to own you."),
    "Pisces": ("You love like a romance novel",
               "You see the best version of people, sometimes before it exists. You're tender, forgiving and deeply romantic — "
               "and the work is making sure the person in front of you matches the one you imagined."),
}

MARS = {
    "Aries": ("Your anger is fast and clean",
              "You want what you want now, and you go straight at it. Your temper flares and it's over — "
              "you'd rather have the fight than the silence."),
    "Taurus": ("You're slow to anger and impossible to move",
               "Your drive is steady, sensual and patient; you get there by not stopping. "
               "Push you and you dig in. Push past that and it's a long time before you forget."),
    "Gemini": ("You fight with words",
               "Your energy scatters across ten things at once and your weapon is your tongue — quick, precise, sometimes cutting. "
               "Wanting comes through talk, and the right conversation is foreplay."),
    "Cancer": ("You go quiet when you're angry",
               "You protect before you attack, and you'd rather sulk than shout. "
               "Your drive is fierce when it's for people you love; the anger tends to come out sideways when it's for yourself."),
    "Leo": ("You want to win, and be seen winning",
            "Your drive is warm, theatrical and proud. You fight for your dignity more than for any particular outcome, "
            "and you're generous the moment you feel respected."),
    "Virgo": ("You put your anger into fixing things",
              "Your energy goes into getting it right. You push hardest through competence, "
              "and frustration comes out as criticism — precise, well-aimed, and mostly true."),
    "Libra": ("You hate the fight and you'll still win it",
              "You'd rather persuade than push and you're very good at it. "
              "Anger gets weighed, softened and delayed, which keeps the peace — until the pile gets too high."),
    "Scorpio": ("Your will runs deep and quiet",
                "Desire is intense and private. You don't announce what you want, you just don't stop. "
                "You hold anger a long time, and when you finally move you've already thought three steps ahead."),
    "Sagittarius": ("You fight for what's right",
                    "Your drive is restless and big-hearted; you need a cause or a horizon. "
                    "Your anger is blunt and blows over fast, and the thing that really sets you off is being told what to think."),
    "Capricorn": ("You want it, so you build it",
                  "Patient, strategic, relentless: your drive works on a long timeline and rarely gives up. "
                  "You get colder, not louder, when you're angry."),
    "Aquarius": ("You push against anything that boxes you in",
                 "Your drive is principled and contrarian, and you go hard for ideas and for other people. "
                 "Angry, you detach — and that cool distance is more cutting than shouting."),
    "Pisces": ("You get there sideways",
               "Your drive moves like water — around obstacles, not through them. "
               "You'd rather dissolve a fight than win one, and you feel other people's anger as if it were your own."),
}

MERCURY = {
    "Aries": ("You think out loud, fast",
              "Your mind reaches conclusions before the explanation does. You say what you think, "
              "debate for fun, and send the reply before you've finished reading the message."),
    "Taurus": ("You think slowly and you're usually right",
               "You need time to take something in, and once you've decided you rarely change your mind. "
               "Your words are practical, your voice is calming, and you can't be rushed."),
    "Gemini": ("Your mind never stops",
               "Curious, quick, funny, always three tabs ahead. You talk and text a lot, love a good argument, "
               "and get bored the moment something has been fully explained."),
    "Cancer": ("You think with your feelings and your memory",
               "You remember what people said, how they said it, and how it felt. "
               "Your mind is intuitive, protective, and a little defensive when you don't feel safe."),
    "Leo": ("You speak with conviction",
            "You're a natural storyteller and you like being heard. You think in big pictures and strong opinions, "
            "and you take criticism of your ideas personally."),
    "Virgo": ("You notice everything",
              "Precise, analytical, quietly funny. You catch the typo, the inconsistency, the thing nobody else saw. "
              "The cost is the late-night loop of overthinking it."),
    "Libra": ("You see every side",
              "You think in conversation and weigh everything before you decide — sometimes for a long time. "
              "You're diplomatic, persuasive and very good at saying hard things kindly."),
    "Scorpio": ("You read between the lines",
                "Your mind is an investigator. You hear what people mean rather than what they say, "
                "you keep secrets well, and you can find the sore spot in an argument instantly."),
    "Sagittarius": ("You think in big ideas",
                    "Philosophical, honest, sometimes too honest. You'd rather talk about meaning than details, "
                    "and your mouth runs about half a second ahead of your tact."),
    "Capricorn": ("You think like a strategist",
                  "Practical, dry, organised, with a sharp and understated sense of humour. "
                  "You say less than you know and you mean every word."),
    "Aquarius": ("Your mind runs on its own logic",
                 "Original, contrarian, ahead of the conversation. You think in systems and patterns, "
                 "and you'll argue the other side just to see whether it holds."),
    "Pisces": ("You think in images and feelings",
               "Your mind is intuitive, poetic and absorbent; you pick up on things nobody said. "
               "Linear explanations bore you, and you remember the feeling of a conversation better than its words."),
}

TABLES = {"Venus": VENUS, "Mars": MARS, "Mercury": MERCURY}


def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def cards(chart, time_known=True):
    """One card per planet: headline, body, where it plays out, dignity."""
    out = []
    for planet in ("Venus", "Mars", "Mercury"):
        p = chart["planets"].get(planet)
        if not p:
            continue
        title, body = TABLES[planet][p["sign"]]
        theme, about = THEME[planet]
        card = {"planet": planet, "theme": theme, "about": about, "sign": p["sign"],
                "label": p["label"], "title": title, "body": body,
                "retrograde": p.get("retrograde", False)}
        if time_known and p.get("house"):
            card["house"] = p["house"]
            card["arena"] = (f"This shows up most in {corpus.HOUSES[p['house']]}. "
                             f"That's your {_ordinal(p['house'])} house.")
        dig = corpus.dignity(planet, p["sign"])
        if dig:
            card["dignity"] = dig
            card["dignity_note"] = corpus.DIGNITY_NOTE[dig].capitalize() + "."
        if card["retrograde"]:
            # Modern reading, said as such: the old texts count retrogradation
            # as a debility, and the "turned inward" gloss is twentieth-century.
            card["retro_note"] = ("You tend to work this out privately before it shows. "
                                  f"Astrologers link that to {planet} being retrograde when you "
                                  "were born, which means that from Earth it looked like it was "
                                  "moving backwards.")
        out.append(card)
    return out
