"""
The compatibility read people actually want: the two of them, told like gossip
from a friend who happens to know both charts.

engine/compat.py does the measuring — themes, contacts, the composite, house
overlays, what the sky is doing to the pair right now — and that stays as the
full breakdown underneath. This turns it into something fun to read: a one-line
verdict, the dynamic, who texts first, how they fight, what they give each
other, a date idea. Every line carries a "because" naming placements from one
or both charts, and the grounding check drops any line that invents one.

It reads a friendship as a friendship and a coworker as a coworker: the
relationship kind is in the facts, and the prompt switches register on it.
"""
import json
import os

from engine import compat, grounding, models

MODEL = models.READING

SECTIONS = [
    ("chemistry", "The chemistry"),
    ("talk", "How you talk (and text)"),
    ("fights", "How you fight"),
    ("give", "What you give each other"),
    ("hard", "Where it gets hard"),
    ("long_game", "The long game"),
    ("date", "Your perfect date"),
]
PLATONIC_TITLES = {"chemistry": "The spark", "date": "Best thing to do together",
                   "long_game": "Where this goes"}

_ITEM = {"type": "object", "properties": {"text": {"type": "string"},
                                           "because": {"type": "string"}},
         "required": ["text", "because"], "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {"headline": {"type": "string"}, "dynamic": {"type": "string"},
                   **{k: _ITEM for k, _t in SECTIONS},
                   "green_flags": {"type": "array", "items": _ITEM},
                   "watch_outs": {"type": "array", "items": _ITEM}},
    "required": ["headline", "dynamic", *[k for k, _t in SECTIONS], "green_flags", "watch_outs"],
    "additionalProperties": False,
}

PROMPT = """You write the compatibility read in My Sign, an astrology app for
women 18–34. She has added {name} and wants to know what the two of them are
like together. Make it fun — the read she sends to the group chat — and make it
specific enough that it could only be about these two charts.

Voice: a perceptive, funny friend who knows both of them. Concrete behaviour
("you'll send a voice note, {name} will reply with a thumbs-up and mean it
warmly"), not abstractions. Address her as "you" and {name} by name. Short
sentences. Warm, a little cheeky. No therapy-speak, no "the universe", no emoji.

Fields:
- headline: the two of you in one line, under 12 words.
- dynamic: two or three sentences on what this pairing is mostly about.
- chemistry, talk, fights, give, hard, long_game, date: one to three sentences
  each. "date" is one specific idea that suits both charts.
- green_flags, watch_outs: three each, one short sentence each.
- every "because" names the placements behind the line, with whose they are:
  "your Venus in Aries, {name}'s Mars in Leo", "composite Sun in Taurus".

The relationship kind is in the facts. If it is not romantic, keep everything
platonic: "chemistry" is the spark between friends or colleagues, "date" is the
best thing to do together, and never imply romance.

Rules, no exceptions:
- Use ONLY the facts below. Every planet, sign, house and aspect you mention must
  be in them, and must belong to the person you say it belongs to.
- If a fact depends on a birth time or there is a CAVEAT, don't present it as certain.
- Never tell her to leave, stay, or end anything. Describe the dynamic; don't
  grade the person. A middling score is an ordinary pair.

FACTS
{facts}"""


def _person_lines(owner, chart, time_known):
    L = []
    for planet in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn"):
        p = chart["planets"].get(planet)
        if p:
            L.append(f"{owner} {planet} in {p['sign']}")
    if time_known:
        L.append(f"{owner} rising sign {chart['angles']['Ascendant']['sign']}")
    return L


def facts(report, name, mine, theirs, my_time_known, their_time_known):
    """The measured report plus both people's own placements, one per line."""
    L = [compat.facts_for_model(report, name)]
    L += ["HER PLACEMENTS:"] + _person_lines("your", mine, my_time_known)
    L += [f"{name.upper()}'S PLACEMENTS:"] + _person_lines(f"{name}'s", theirs, their_time_known)
    return "\n".join(L)


def _clean(out, fact_text):
    dropped = []

    def ok(text):
        good, bad = grounding.check(text, fact_text)
        if not good:
            dropped.extend(bad)
        return good

    for k, _t in SECTIONS:
        if not ok(f"{out[k]['text']} {out[k]['because']}"):
            out[k] = None
    for k in ("green_flags", "watch_outs"):
        out[k] = [i for i in out[k] if ok(f"{i['text']} {i['because']}")][:3]
    for k in ("headline", "dynamic"):
        if not ok(out[k]):
            out[k] = None
    return out, dropped


# She writes in Spanish: the read is for her, so it is in Spanish. Planet and
# sign names stay English in the JSON grounding check, so only the prose moves.
LANG_NOTE = {"es": "\n\nWrite every field in Spanish (Latin American, tú). Keep it natural, not translated."}


def generate(report, name, mine, theirs, my_time_known, their_time_known, client=None,
             lang="en"):
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        import anthropic
        client = anthropic.Anthropic()
    fact_text = facts(report, name, mine, theirs, my_time_known, their_time_known)
    try:
        r = client.messages.create(
            model=MODEL, max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user",
                       "content": PROMPT.format(name=name, facts=fact_text)
                       + LANG_NOTE.get(lang, "")}])
    except Exception as e:
        from app import log
        log.error("compat_fun.failed", exc=e)
        return None
    if r.stop_reason != "end_turn":
        return None
    try:
        out = json.loads(next((b.text for b in r.content if b.type == "text"), ""))
    except ValueError:
        return None
    out, dropped = _clean(out, fact_text)
    if dropped:
        from app import log
        log.emit("compat_fun.ungrounded", level="error", claims=dropped[:5])
    try:
        from engine import accuracy
        accuracy.record_outcome(not dropped, dropped, "compatibility", withheld=bool(dropped))
    except Exception:
        pass
    return shape(out, report) if out.get("headline") else None


def shape(out, report):
    platonic = report["score"]["kind"] != "romantic"
    sections = []
    for k, title in SECTIONS:
        if out.get(k):
            sections.append({"key": k, "title": PLATONIC_TITLES.get(k, title) if platonic else title,
                             **out[k]})
    return {"headline": out.get("headline"), "dynamic": out.get("dynamic"),
            "sections": sections, "green_flags": out.get("green_flags") or [],
            "watch_outs": out.get("watch_outs") or []}


def fallback(report, name):
    """Demo mode: the same shape from the measured report's own wording."""
    sc, themes = report["score"], report["themes"]
    top = themes[0] if themes else None
    comp = report["composite"]
    out = {
        "headline": f"{sc['band'].capitalize()}: this one is mostly about {top['title'].lower()}." if top
                    else f"{sc['band'].capitalize()}.",
        "dynamic": (f"Together you read like {'an' if comp['signs']['Sun'][0] in 'AEIOU' else 'a'} "
                    f"{comp['signs']['Sun']} — {comp['sun'][0].lower()}{comp['sun'][1:]}"),
        "green_flags": [], "watch_outs": [],
    }
    # Sorted by each contact's own aspect, not its theme's tone: a "flowing"
    # theme can still lead with an opposition, and "One of you can come across
    # as critical" must never be listed as a green flag.
    HARD = {"square", "opposition"}
    by_tone = {"flowing": [], "charged": []}
    for t in themes:
        if t["contacts"]:
            c = t["contacts"][0]
            item = {"text": c["meaning"][0].upper() + c["meaning"][1:], "because": c["text"]}
            hard = c.get("aspect") in HARD or (c.get("aspect") not in {"trine", "sextile"}
                                               and t.get("tone") == "charged")
            by_tone["charged" if hard else "flowing"].append(item)
    out["give"] = by_tone["flowing"][0] if by_tone["flowing"] else None
    out["hard"] = by_tone["charged"][0] if by_tone["charged"] else None
    out["long_game"] = {"text": comp["sun"], "because": f"composite Sun in {comp['signs']['Sun']}"}
    for k, _t in SECTIONS:
        out.setdefault(k, None)
    out["green_flags"] = by_tone["flowing"][1:4]
    out["watch_outs"] = by_tone["charged"][1:4]
    return shape(out, report)
