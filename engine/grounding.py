"""
Did the reply say anything about the sky it was not given?

This was an eval-only check (evals/rubric.py). It lives in the engine now
because it runs on every live reply as well: the product's central promise is
that the model never invents a placement, and a promise measured only on eight
eval cases is not one you can put on a website. app/server.py scores each reply
and engine/accuracy.py keeps the running count the accuracy page shows.

Two levels, both mechanical, no model judging a model:

  names     every planet, sign and house mentioned appears somewhere in what the
            model was handed. Catches an invented Chiron, a 13th house.
  pairings  "Venus in Scorpio" and "Moon in your 12th house" must appear
            together on one line of the briefing or a tool result. Catches a
            real planet put in a real sign it is not in — the subtler failure,
            and the one presence alone could never see.
  ownership a natal claim ("your Sun in Scorpio") is vouched for only by her
            chart, never by today's sky; "his Venus in Leo" only by his chart.
            The pairing check used to accept any line holding both words, so the
            transit line "t.Sun 6°17' Scorpio" vouched for a natal Sun in
            Scorpio that was really in Sagittarius, and the accuracy page counted
            that reply as grounded (audit HIGH-3).
  angles    "rising sign is Leo", "Leo rising", "MC in Taurus" — the claims
            the in-sign pattern never saw.
  aspects   "Neptune square your Venus", "Saturn sitting on your Venus": the
            named aspect must be on a line with both points. A square described
            as a conjunction is a wrong claim even when both planets are real.
"""
import re

PLANETS = ["Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn",
           "Uranus", "Neptune", "Pluto", "Chiron", "Ascendant", "Midheaven",
           "North Node", "South Node"]
SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra",
         "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]
ASPECTS = ["conjunction", "conjunct", "opposition", "opposite", "square",
           "trine", "sextile"]

HOUSE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+house\b", re.I)

_P = "|".join(PLANETS)
_S = "|".join(SIGNS)
# "Venus in Scorpio", "your Moon, in Pisces", "Mars is in Leo" — up to two words
# between the planet and "in", so "Venus sitting in Scorpio" still counts.
_IN_SIGN = re.compile(rf"\b({_P})\b,?(?:\s+\w+){{0,2}}?\s+in\s+({_S})\b", re.I)
_IN_HOUSE = re.compile(rf"\b({_P})\b,?(?:\s+\w+){{0,2}}?\s+in\s+(?:your\s+|her\s+|the\s+)?"
                       r"(\d{1,2})(?:st|nd|rd|th)\s+house\b", re.I)


def _word(w, text):
    # Whole words: a substring test reads "Sunday" as the Sun and "honeymoon"
    # as the Moon.
    return re.search(rf"\b{re.escape(w)}\b", text, re.I) is not None


def mentions(text):
    """Everything celestial the reply claims."""
    low = text.lower()
    return {
        "planets": [p for p in PLANETS if _word(p, text)],
        "signs": [s for s in SIGNS if _word(s, text)],
        "houses": [int(m) for m in HOUSE_RE.findall(text)],
        "aspects": [a for a in ASPECTS if a in low],
    }


def _together(ctx_lines, *needles):
    return any(all(re.search(n, line, re.I) for n in needles) for line in ctx_lines)


# ------------------------------------------------------------- whose claim
#
# Context lines carry a namespace, so a claim is checked against the facts it
# is about. "her" is her natal chart, "sky" is the moving sky (today's or a
# looked-up day's), "them" is someone she saved, None is everything else
# (memory, compatibility summaries, the facts other surfaces hand over), which
# vouches for any claim, as all lines used to.

HER, SKY, THEM = "her", "sky", "them"
_SECTION = [
    (re.compile(r"^\s*(HER CHART|HER BIRTH SKY|HER PLACEMENTS)\b"), HER),
    (re.compile(r"^\s*SKY TODAY\b"), SKY),
    (re.compile(r"^\s*(THEIR CHART|[A-Z][A-Z' ]{0,40}['’]S PLACEMENTS)\b"), THEM),
]
_TOOL_NS = {"get_natal": HER, "get_sky": SKY, "get_lunations": SKY,
            "get_synastry": THEM, "get_person_day": THEM, "compare_connections": THEM}
_TOOL_RE = re.compile(r"^\[(\w+)\]\s")


def _segments(context):
    """[(namespace, text)]. A tool result arrives as one line of JSON holding a
    whole chart, which would vouch for any planet with any sign, so it is split
    at the end of each object and between string-valued keys ("Ascendant":
    "19° Leo", "Midheaven": ...) so each placement is judged on its own."""
    out, section = [], None
    for line in context.splitlines():
        if not line.strip():
            section = None
            continue
        m = _TOOL_RE.match(line)
        if m:
            ns = _TOOL_NS.get(m.group(1))
            section = None
        else:
            for rx, sec in _SECTION:
                if rx.match(line):
                    section = sec
                    break
            ns = section
            # The moving sky is always written t.X, wherever it appears.
            if re.match(r"^\s*t\.", line):
                ns = SKY
        for seg in re.split(r"\}\s*,|\",\s*(?=\"[A-Z])", line):
            out.append((ns, seg))
    return out


_NATAL_PRE = re.compile(r"(?:\byour|\bmy|\bnatal|\bbirth)\s+(?:natal\s+|own\s+|birth\s+)?$", re.I)
_THEIR_PRE = re.compile(r"(?:\b(?i:his|her|their|composite)|\b[A-Z][a-z]+['’]s)\s+(?:(?i:natal|own)\s+)?$")
_SKY_PRE = re.compile(r"(?:\bthe|\btransiting|\btoday['’]s|\btonight['’]s|\bcurrent)\s+$", re.I)
_NATAL_SENT = re.compile(r"\b(your chart|you were born|birth chart|born with|natal|your (?:sun|moon|rising) sign)\b", re.I)
_SKY_SENT = re.compile(r"\b(today|tonight|right now|currently|at the moment|this (?:week|month|year)|"
                       r"in the sky|transit\w*|moving|tomorrow)\b", re.I)


def _sentence_at(text, i):
    start = max(text.rfind(c, 0, i) for c in ".!?\n") + 1
    ends = [j for j in (text.find(c, i) for c in ".!?\n") if j != -1]
    return text[start:min(ends) if ends else len(text)], text[start:i]


def owner(text, i):
    """Whose placement the claim starting at text[i] is about: HER, THEM, SKY
    or None when the words do not say."""
    sentence, before = _sentence_at(text, i)
    if _NATAL_PRE.search(before):
        return HER
    if _THEIR_PRE.search(before):
        return THEM
    if _SKY_PRE.search(before):
        return SKY
    natal, sky = bool(_NATAL_SENT.search(sentence)), bool(_SKY_SENT.search(sentence))
    if natal and not sky:
        return HER
    if sky and not natal:
        return SKY
    return None


def _allowed(segs, who):
    """Lines that may vouch for a claim about `who`: never another namespace's
    chart. Unlabelled lines vouch for everyone, as before."""
    if who is None:
        return [t for _ns, t in segs]
    return [t for ns, t in segs if ns in (None, who)]


# ------------------------------------------------------- angles and aspects

_ALIAS = {"rising": "Ascendant", "asc": "Ascendant", "ascendant": "Ascendant",
          "mc": "Midheaven", "midheaven": "Midheaven"}
_RISING = re.compile(
    rf"\b(?:rising(?:\s+sign)?|ascendant)\b(?:\s+(?:is|was|would be|sits|falls|lands))?"
    rf"(?:\s+(?:in|at|now|probably|actually|around|about))*\s*,?\s+(?:\d{{1,2}}°\s*(?:\d{{1,2}}['’]\s*)?)?({_S})\b"
    rf"|\b({_S})\s+(?:rising|ascendant)\b", re.I)
_MC = re.compile(rf"\b(?:MC|Midheaven)\b(?:\s+(?:is|was|sits))?(?:\s+in)?\s+({_S})\b")
_HEDGE = re.compile(r"\b(not|n['’]t|no|never|whether|if|would|could|might|without|unless|"
                    r"says|say|said|think|thinks|call(?:ed|s)?|everyone|people|or)\b", re.I)

_ASP_WORD = (r"(conjunct(?:ion|s|ing)?|opposi(?:te|tion|ng)|oppos(?:es|ing)|squar(?:e|es|ing)"
             r"|trin(?:e|es|ing)|sextil(?:e|es|ing))")
_POINT = rf"({_P}|rising|MC)"
_OWN = r"(?:your|my|his|her|their|the|[A-Z][a-z]+['’]s)?\s*(?:natal\s+|own\s+|birth\s+)?"
_ASPECT_CLAIM = re.compile(
    rf"\b{_POINT}\b(?:['’]s)?(?:\s+[\w'’]+){{0,3}}?\s+{_ASP_WORD}\s+(?:to\s+|with\s+)?{_OWN}{_POINT}\b")
_ON_CLAIM = re.compile(
    rf"\b{_POINT}\b(?:\s+[\w'’]+){{0,2}}?\s+(?:is\s+|sits\s+|sitting\s+|sat\s+|lands\s+|landing\s+|right\s+)"
    rf"(?:almost\s+|nearly\s+|right\s+|exactly\s+|directly\s+|still\s+)*on\s+"
    rf"(?:your|my|his|her|their)\s+(?:natal\s+)?{_POINT}\b")
_ASP_NORM = [("conjunct", "conjunct"), ("opposi", "opposi"), ("oppos", "opposi"),
             ("squar", "square"), ("trin", "trine"), ("sextil", "sextile")]


# How each angle is written in the facts handed over: the briefing says
# "Ascendant 14°10' Scorpio", other surfaces "Rising sign: Scorpio".
_NEEDLE = {"Ascendant": r"\b(?:Ascendant|rising|Asc)\b", "Midheaven": r"\b(?:Midheaven|MC)\b"}


def _needle(point):
    return _NEEDLE.get(point, rf"\b{re.escape(point)}\b")


def _point(name):
    return _ALIAS.get(name.lower(), next((p for p in PLANETS if p.lower() == name.lower()), name))


def _asp_needle(word):
    stem = next(n for k, n in _ASP_NORM if word.lower().startswith(k))
    return rf"\b{stem}"


def _hedged(text, start):
    """'If you were Scorpio rising', 'you're not Leo rising' — a claim the
    reply is declining to make is not a claim."""
    _sentence, before = _sentence_at(text, start)
    return bool(_HEDGE.search(before[-60:]))


def check(text, context):
    """(ok, problems). Every celestial claim must trace to the context."""
    m = mentions(text)
    bad = []
    for p in m["planets"]:
        if not _word(p, context):
            bad.append(f"planet not in context: {p}")
    for s in m["signs"]:
        if not _word(s, context):
            bad.append(f"sign not in context: {s}")
    for h in m["houses"]:
        if not re.search(rf"\b{h}(st|nd|rd|th)?\b", context):
            bad.append(f"house not in context: {h}")

    segs = _segments(context)

    def add(problem):
        if problem not in bad:
            bad.append(problem)

    for mt in _IN_SIGN.finditer(text):
        planet, sign = mt.group(1), mt.group(2)
        who = owner(text, mt.start())
        if not _together(_allowed(segs, who), rf"\b{planet}\b", rf"\b{sign}\b"):
            add(f"pairing not in context: {planet} in {sign}"
                + (f" ({who})" if who and _together(_allowed(segs, None),
                                                    rf"\b{planet}\b", rf"\b{sign}\b") else ""))
    for mt in _IN_HOUSE.finditer(text):
        planet, house = mt.group(1), mt.group(2)
        # An ordinal or a JSON "house": n — never a bare number, or "12°04'"
        # on the Venus line would vouch for Venus in the 12th.
        if not _together(_allowed(segs, owner(text, mt.start())), rf"\b{planet}\b",
                         rf"\b{house}(st|nd|rd|th)\b|\"house\":\s*{house}\b"):
            add(f"pairing not in context: {planet} in house {house}")
    for rx, point in ((_RISING, "Ascendant"), (_MC, "Midheaven")):
        for mt in rx.finditer(text):
            sign = next(g for g in mt.groups() if g)
            if _hedged(text, mt.start()):
                continue
            who = owner(text, mt.start())
            if not _together(_allowed(segs, who), _needle(point), rf"\b{sign}\b"):
                add(f"pairing not in context: {point} in {sign.capitalize()}")
    for rx in (_ASPECT_CLAIM, _ON_CLAIM):
        for mt in rx.finditer(text):
            if _hedged(text, mt.start()):
                continue
            if rx is _ASPECT_CLAIM:
                a, word, b = mt.group(1), mt.group(2), mt.group(3)
            else:
                a, word, b = mt.group(1), "conjunction", mt.group(2)
            a, b = _point(a), _point(b)
            if a == b:
                continue
            if not _together([t for _ns, t in segs], _needle(a), _needle(b), _asp_needle(word)):
                add(f"aspect not in context: {a} {_asp_needle(word)[2:]} {b}")
    return (not bad), bad
