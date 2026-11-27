"""
The reading someone sees the moment their chart exists.

Handing over your birth time is the biggest ask in onboarding and until now it
bought you a chat box. This is the payoff: what the chart actually says, before
anyone types a question.

Every claim traces to `interpret.analyze` — same rule as everywhere else. With
an API key the model writes the opening paragraph over those facts; without one
the corpus text stands on its own, which is why the corpus has to be readable
rather than a pile of keywords.
"""

from engine import birthsky, classical, corpus, glossary, models, personal, profile
from engine import interpret as I

# What each planet is like in her, as something she does — so a strength or a
# strain reads as a sentence about her rather than a rule about dignity.
GIFT = {"Sun": "being yourself", "Moon": "taking care of your feelings",
        "Mercury": "saying what you mean", "Venus": "letting yourself be loved",
        "Mars": "going after what you want", "Jupiter": "believing things will work out",
        "Saturn": "sticking with things"}


MODEL = models.READING

OPENING = """Write the opening of someone's natal chart reading.

Two short paragraphs, second person, no greeting and no preamble. Name the
mechanism — the placement — and say what it means for how they actually move
through a day. Specific over flattering. No predictions, no "the universe", no
emoji, and never tell them what will happen.

You are given computed facts. Use them; invent nothing.

{facts}"""
# Beginners first: every technical word explained where it appears (engine/glossary).
OPENING = OPENING + "\n\n" + glossary.PLAIN_RULE



def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(n % 10, 'th')}"


def _facts(a, name=None):
    b = a["big3"]
    lines = [
        f"Name: {name or 'unknown'}",
        f"Sun in {b['sun']['sign']}, {_ordinal(b['sun']['house'])} house",
        f"Moon in {b['moon']['sign']}, {_ordinal(b['moon']['house'])} house",
        f"{b['rising']['sign']} rising",
        f"Dominant element: {a['element']}   Dominant modality: {a['modality']}",
    ]
    if a["chart_ruler"]:
        r = a["chart_ruler"]
        lines.append(f"Chart ruler: {r['planet']} in {r['sign']}, "
                     f"{_ordinal(r['house'])} house")
    for s in a["stelliums"]:
        where = (f"the {_ordinal(s['where'])} house" if s["kind"] == "house"
                 else s["where"])
        lines.append(f"Stellium in {where}: {', '.join(s['planets'])}")
    if a["retrogrades"]:
        lines.append(f"Retrograde at birth: {', '.join(a['retrogrades'])}")
    for t in a["tight_aspects"][:3]:
        lines.append(f"{t['a']} {t['aspect']} {t['b']}, orb {t['orb']}°")
    return "\n".join(lines)


def _fallback_opening(a):
    b = a["big3"]
    rising = f" {I.RISING[b['rising']['sign']]}" if b["rising"] else ""
    return (f"{I.SUN[b['sun']['sign']]}{rising}\n\n"
            f"{I.MOON[b['moon']['sign']]} {I.ELEMENT_NOTE[a['element']]}")


# Openers drawn from the chart, because "ask me anything" is the hardest prompt
# in the world to answer. Each one is gated on a placement actually being there,
# so nobody is offered a question about a house they have nothing in.
def _starters(a):
    b, out = a["big3"], []
    houses = {s["where"] for s in a["stelliums"] if s["kind"] == "house"}
    sun_h, moon_h = b["sun"]["house"], b["moon"]["house"]

    if 12 in houses or moon_h == 12:
        out.append("Why do I need so much time on my own?")
    if "Mercury" in a["retrogrades"]:
        out.append("Why do I always overexplain myself?")
    if 7 in houses or moon_h == 7:
        out.append("Why do I lose myself in relationships?")
    if 2 in houses:
        out.append("Why am I so weird about money?")
    if 10 in houses or sun_h == 10:
        out.append("Why does work feel like my whole identity?")
    # Not the Ascendant–Midheaven square: at many latitudes the house system
    # puts those two near 90° for everyone, so it is geometry, not a tension of
    # hers. And in plain words — "TrueNode" is our internal name.
    angles = {"Ascendant", "Midheaven"}
    sq = next((t for t in a["tight_aspects"] if t["aspect"] == "square"
               and not {t["a"], t["b"]} <= angles), None)
    if sq:
        name = {"TrueNode": "North Node"}
        out.append(f"What is the tension between my {name.get(sq['a'], sq['a'])} and "
                   f"my {name.get(sq['b'], sq['b'])} actually about?")
    if a["modality"] == "fixed":
        out.append("Why is it so hard for me to let something go?")
    if a["modality"] == "cardinal":
        out.append("Why do I start more than I finish?")
    if a["element"] == "water":
        out.append("How do I tell my feelings apart from everyone else's?")

    out.append("What should I know about today?")
    return out[:4]


def starters(chart, time_known=True, birth_ymd=None, rulerships=None):
    """Her chart's own first questions, without the rest of the reading.

    The same list build() returns, from the same analysis, but no model call
    and no birth sky: a few milliseconds. Today shows these on a brand-new
    account, which used to wait for the model to write her whole reading first.
    """
    return _starters(I.analyze(chart, time_known=time_known, birth_ymd=birth_ymd,
                               rulerships=rulerships))


def build(chart, name=None, want_prose=True, time_known=True, birth_ymd=None,
          rulerships=None, birth_ut=None, tz=None):
    a = I.analyze(chart, time_known=time_known, birth_ymd=birth_ymd,
                  rulerships=rulerships)
    b = a["big3"]
    sky = None
    if birth_ut:
        try:
            sky = birthsky.compute(birth_ut, chart, time_known, tz)
            for k in sky["eclipses"]:
                sky["eclipses"][k].pop("jd", None)
        except Exception as e:
            # Extra material: never worth failing the reading over.
            from app import log
            log.error("reading.birthsky.failed", exc=e)

    sun_h, moon_h = b["sun"]["house"], b["moon"]["house"]
    cards = [
        {"label": "Sun", "value": b["sun"]["sign"],
         "sub": (f"{_ordinal(sun_h)} house" if sun_h
                 else ("" if a.get("sun_sign_certain", True) else "not certain")),
         "body": I.SUN[b["sun"]["sign"]],
         "note": (f"What you're built around. It shows up most in {I.HOUSE[sun_h]}."
                  if sun_h else
                  ("What you're built around." if a.get("sun_sign_certain", True) else
                   "What you're built around — but the Sun changed sign on your birthday, "
                   "so without a time you're on the cusp, and this one could go either way."))},
        {"label": "Moon", "value": b["moon"]["sign"],
         "sub": (f"{_ordinal(moon_h)} house" if moon_h
                 else ("" if a["moon_sign_certain"] else "not certain")),
         "body": I.MOON[b["moon"]["sign"]],
         "note": (f"How you process feelings. It shows up most in {I.HOUSE[moon_h]}."
                  if moon_h else
                  ("How you process." if a["moon_sign_certain"] else
                   "How you process — but the Moon changed sign on your birthday, "
                   "so without a time this one is a coin flip."))},
    ]
    if b["rising"]:
        cards.append({"label": "Rising", "value": b["rising"]["sign"], "sub": "1st house",
                      "body": I.RISING[b["rising"]["sign"]],
                      "note": "What people meet before they meet you."})
    else:
        # Not "approximate" — unknowable. The ascendant moves through all twelve
        # signs in a day, so a guess here would be a fabrication wearing a
        # confident font.
        cards.append({
            "label": "Rising", "value": "Unknown", "sub": "needs a birth time",
            "body": "The rising sign changes roughly every two hours — eight "
                    "different ones across a single day. Without your birth time "
                    "I would be making it up, so I would rather not.",
            "note": "Add your birth time in Settings and this fills in, along "
                    "with every house placement.",
            "missing": True,
        })

    standout = []
    for s in a["stelliums"]:
        n = len(s["planets"])
        if s["kind"] == "house":
            where = f"the {_ordinal(s['where'])} house"
            if s.get("also_sign"):
                where += f", all in {s['also_sign']}"
            theme = I.HOUSE[s["where"]]
        else:
            where, theme = s["where"], f"everything {s['where']} touches"
        standout.append({
            "kind": "stellium_house" if s["kind"] == "house" else "stellium",
            "title": ("Where a lot of your energy goes" if s["kind"] == "house"
                      else "A side of you that's turned up loud"),
            "body": f"{theme[0].upper()}{theme[1:]} matters more to you than it would to "
                    f"most people. A lot of your chart is gathered there.",
            "because": f"{n} planets in {where}: {', '.join(s['planets'])}.",
        })
    if a["chart_ruler"]:
        r = a["chart_ruler"]
        standout.append({
            "kind": "ruler",
            "title": "What everything comes back to",
            "body": f"When something feels unresolved, it usually comes back to "
                    f"{I.HOUSE[r['house']]}.",
            "because": f"Your chart ruler, {r['planet']}, is in {r['sign']} in your "
                       f"{_ordinal(r['house'])} house.",
        })
    if a["retrogrades"]:
        rl = a["retrogrades"]
        standout.append({
            "kind": "retrograde",
            "title": "You work things out privately first",
            "body": "You tend to think things through on your own before you say or "
                    "show them, and you revise before you share. It isn't a flaw or a warning.",
            "because": f"{', '.join(rl[:-1]) + ' and ' + rl[-1] if len(rl) > 1 else rl[0]} "
                       f"{'were' if len(rl) > 1 else 'was'} retrograde when you were born.",
        })
    if a["angular"]:
        standout.append({
            "kind": "angular",
            "title": "The part of you people notice first",
            "body": "Some of who you are shows up before you've said a word, whether "
                    "or not you mean it to.",
            "because": f"{', '.join(a['angular'])} sit on an angle of your chart, where "
                       f"it meets the world.",
        })

    if not time_known:
        standout.append({
            "kind": "unknown_time",
            "title": "What I can't tell you yet",
            "body": "Without a birth time I can't know your " +
                    ", ".join(a["unknown"][:-1]) + ", or " + a["unknown"][-1] +
                    ". Signs and aspects still hold — those barely move in a day. "
                    "Houses move constantly, so I've left them out rather than guess.",
        })

    # Essential dignity — the first thing a practitioner checks, and absent
    # until now. A planet in domicile or exaltation works with the grain; one in
    # detriment or fall does the same job against it.
    dignities = []
    for name in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn"):
        pl = chart["planets"].get(name)
        if not pl:
            continue
        # Dignity is a claim about the sign. With the Moon's sign a coin flip
        # it would be "Moon in Capricorn — working uphill" about a Moon that
        # may be in Sagittarius.
        if name == "Moon" and not a["moon_sign_certain"]:
            continue
        d = corpus.dignity(name, pl["sign"])
        if d:
            dignities.append({"planet": name, "sign": pl["sign"], "dignity": d,
                              "note": corpus.DIGNITY_NOTE[d]})
    dignity_items = []
    if dignities:
        strong = [d for d in dignities if d["dignity"] in ("domicile", "exalted")]
        strained = [d for d in dignities if d["dignity"] in ("detriment", "fall")]
        def _and(xs):
            return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
        if strong:
            gifts = [GIFT[d["planet"]] for d in strong[:2]]
            dignity_items.append({
                "kind": "dignity_strong",
                "title": "What comes naturally to you",
                "body": f"You're naturally good at {_and(gifts)}. You probably don't "
                        f"give yourself enough credit for it.",
                "because": _and([f"{d['planet']} in {d['sign']}" for d in strong[:2]])
                           + (f", one of the strongest placements {strong[0]['planet']} can have."
                              if len(strong[:2]) == 1 else
                              ", among the strongest placements those planets can have."),
            })
        if strained:
            gifts = [GIFT[d["planet"]] for d in strained[:2]]
            first = _and(gifts)
            dignity_items.append({
                "kind": "dignity_strained",
                "title": "Where you work harder than people see",
                "body": f"{first[0].upper()}{first[1:]} {'is' if len(gifts) == 1 else 'are'}n't "
                        f"always easy for you, especially under pressure. That isn't a flaw: "
                        f"you've had to work at it, so you understand it better than most people do.",
                "because": _and([f"{d['planet']} in {d['sign']}" for d in strained[:2]])
                           + (f", a placement where {strained[0]['planet']} has to work harder."
                              if len(strained[:2]) == 1 else
                              ", placements where those planets have to work harder."),
            })

    # A house stellium first, then dignity, then everything else. Three sign
    # stelliums in a row is repetition; a planet in domicile is a finding.
    # Ordering, most distinctive first — but the honesty card is never the thing
    # the cap removes. On a chart with no birth time it is the only explanation
    # for why the houses are missing, so it outranks everything.
    cannot = [x for x in standout if x["title"] == "What I can't tell you yet"]
    houses_first = [x for x in standout if x.get("kind") == "stellium_house"]
    rest = [x for x in standout if x not in houses_first and x not in cannot]
    standout = (cannot + houses_first[:1] + dignity_items + houses_first[1:] + rest)[:4]          # past four this stops being a reading

    wiring = [{
        "pair": f"{t['a']} {t['aspect']} {t['b']}",
        "orb": t["orb"],
        "body": corpus.link_for_you(t["a"], t["b"], t["aspect"])
                or I.ASPECT_NOTE[t["aspect"]].capitalize() + ".",
    } for t in a["tight_aspects"]]

    # One model call writes the whole personality read — tagline, vibe, the
    # sections people screenshot — as structured JSON from computed facts. It
    # replaced the separate opening-paragraph call, so the reading still costs
    # one request. Without a model the authored fallback fills the same shape.
    prof = profile.build(chart, a, time_known, sky, name) if want_prose else \
        {**profile.fallback(chart, a, time_known), "generated": False}
    if not want_prose:
        prof["sections"] = [{"key": k, "title": t, **prof[k]}
                            for k, t in profile.SECTIONS if prof.get(k)]
    generated = prof["generated"]
    opening = prof.get("vibe") or _fallback_opening(a)

    return {
        "opening": opening,
        "starters": _starters(a),
        "time_known": time_known,
        "unknown": a["unknown"],
        "opening_generated": generated,
        "cards": cards,
        "balance": {
            "element": a["element"], "modality": a["modality"],
            "element_note": I.ELEMENT_NOTE[a["element"]],
            "modality_note": I.MODALITY_NOTE[a["modality"]],
            "counts": a["element_counts"],
        },
        "standout": standout,
        "wiring": wiring,
        "facts": a,
        # Venus, Mars, Mercury, each with its own text — the questions people ask.
        "personal": personal.cards(chart, time_known),
        "profile": {k: prof.get(k) for k in ("tagline", "vibe", "sections",
                                             "green_flags", "red_flags")},
        # The sky itself at birth: measured, not interpreted.
        "sky": birthsky.describe(sky) if sky else [],
        # The classical layer: each doctrine with its public-domain source.
        "old": classical.items(chart, sky, time_known) if sky else [],
        "sky_facts": sky,
    }
