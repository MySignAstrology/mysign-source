"""
The personality read: who she is, said the way a sharp friend would say it.

This is the part people screenshot. It reads like a person who knows her —
short, a little cheeky, specific — not like a table of placements. But every
line still comes from her actual chart, and each one carries a "because" naming
the placement behind it. That is the difference from apps that write the same
paragraph for every Scorpio: two people with the same Sun get different reads,
because their Moon, rising, Venus and Mars are different, and we say which.

One model call writes it from computed facts, as structured JSON. The grounding
check (engine/grounding.py) runs on every line; a line that names a placement
she doesn't have is dropped, not shown. Without a model — demo mode, or a
failed call — the authored fallback builds the same shape from the corpus.
"""
import json
import os

from engine import classical, corpus, glossary, grounding, models, personal
from engine import interpret as I

MODEL = models.READING

SECTIONS = [
    ("superpower", "Your superpower"),
    ("toxic_trait", "Your toxic trait"),
    ("in_love", "In love"),
    ("when_mad", "When you're mad"),
    ("at_work", "At work"),
    ("misread", "What people get wrong about you"),
    ("needs", "What you actually need"),
]

_ITEM = {"type": "object", "properties": {"text": {"type": "string"},
                                           "because": {"type": "string"}},
         "required": ["text", "because"], "additionalProperties": False}

SCHEMA = {
    "type": "object",
    "properties": {
        "tagline": {"type": "string"},
        "vibe": {"type": "string"},
        **{k: _ITEM for k, _t in SECTIONS},
        "green_flags": {"type": "array", "items": _ITEM},
        "red_flags": {"type": "array", "items": _ITEM},
    },
    "required": ["tagline", "vibe", *[k for k, _t in SECTIONS], "green_flags", "red_flags"],
    "additionalProperties": False,
}

PROMPT = """You write the personality read in My Sign, an astrology app for women
18–34. Think the best of Co–Star's bite, with more warmth and more specifics.

Write about HER, in second person. Make it feel like being seen by a funny,
perceptive friend: concrete behaviour she'll recognise ("you rewrite the text
three times and send the first version"), not abstractions ("you value
communication"). Short sentences. A little cheeky, never cruel. No therapy-speak,
no "the universe", no emoji, no predictions.

Fields:
- tagline: her in one line, under 12 words. The line she screenshots.
- vibe: two or three sentences on the overall energy she gives off.
- superpower, toxic_trait, in_love, when_mad, at_work, misread, needs: one to
  three sentences each. toxic_trait is affectionate, the kind of thing friends
  tease her about.
- green_flags, red_flags: three each, one short sentence each. Red flags are
  playful, not diagnoses.
- every "because" names the placement(s) behind the line, e.g. "Moon in
  Scorpio, 8th house" or "Mars square Venus". Use only placements in the facts.

Rules, no exceptions:
- Use ONLY the facts below. Never mention a planet, sign, house or aspect that
  isn't in them. If BIRTH TIME is unknown, never mention a rising sign or a house.
- Synthesize: the interesting part is where placements pull against each other
  (a fiery Sun with a private Moon). Don't just restate one placement per line.
- Don't repeat the same placement as the "because" more than twice.

FACTS
{facts}"""
# Beginners first: every technical word explained where it appears (engine/glossary).
PROMPT = PROMPT + "\n\n" + glossary.PLAIN_RULE



def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def facts(chart, analysis, time_known, sky=None, name=None):
    """Everything the model may draw on, one placement per line."""
    b = analysis["big3"]
    L = [f"NAME: {name or 'unknown'}",
         f"BIRTH TIME: {'known' if time_known else 'unknown — no rising sign, no houses'}"]
    for planet, p in chart["planets"].items():
        label = "North Node" if planet == "TrueNode" else planet
        where = f", {_ordinal(p['house'])} house" if time_known and p.get("house") else ""
        dig = corpus.dignity(planet, p["sign"]) if planet in corpus.PLANETS else None
        L.append(f"{label} in {p['sign']}{where}"
                 + (" (retrograde)" if p.get("retrograde") else "")
                 + (f" — {dig}" if dig else ""))
    if time_known and b.get("rising"):
        L.append(f"Rising sign: {b['rising']['sign']}")
    L.append(f"Dominant element: {analysis['element']}; modality: {analysis['modality']}")
    if analysis.get("chart_ruler"):
        r = analysis["chart_ruler"]
        L.append(f"Chart ruler: {r['planet']} in {r['sign']}"
                 + (f", {_ordinal(r['house'])} house" if r.get("house") else ""))
    for s in analysis["stelliums"]:
        where = f"the {_ordinal(s['where'])} house" if s["kind"] == "house" else s["where"]
        L.append(f"Stellium in {where}: {', '.join(s['planets'])}")
    for t in analysis["tight_aspects"][:6]:
        L.append(f"{t['a']} {t['aspect']} {t['b']} (orb {t['orb']}°)")
    if sky:
        sun = sky.get("sun") or {}
        if sun.get("sect"):
            L.append(f"Born at {'day' if sun['sect'] == 'day' else 'night'} ({sun['light']})")
        v = sky["venus_mercury"]["Venus"]
        if not v["in_suns_glare"]:
            L.append(f"Venus was the {v['star']} at birth")
        t = classical.temperament(chart, chart["planets"]["Sun"]["lon"],
                                  sky["moon"]["elongation"], time_known)
        L.append(f"Temperament (Lilly): {t['temperament']}"
                 + (f", leaning {t['leaning']}" if t["leaning"] else ""))
        for st in sky.get("stars", []):
            L.append(f"Fixed star {st['star']} conjunct {st['point']}")
    return "\n".join(L)


def _clean(out, fact_text):
    """Drop any line the grounding check fails. Return (profile, dropped)."""
    dropped = []

    def ok(item):
        good, bad = grounding.check(f"{item['text']} {item['because']}", fact_text)
        if not good:
            dropped.extend(bad)
        return good

    for k, _t in SECTIONS:
        if not ok(out[k]):
            out[k] = None
    for k in ("green_flags", "red_flags"):
        out[k] = [i for i in out[k] if ok(i)][:3]
    for k in ("tagline", "vibe"):
        good, bad = grounding.check(out[k], fact_text)
        if not good:
            dropped.extend(bad)
            out[k] = None
    return out, dropped


def generate(chart, analysis, time_known, sky=None, name=None, client=None):
    """The model-written read, or None if there is no model or it misfired."""
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        import anthropic
        client = anthropic.Anthropic()
    fact_text = facts(chart, analysis, time_known, sky, name)
    try:
        r = client.messages.create(
            model=MODEL, max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(facts=fact_text)}])
    except Exception as e:
        from app import log
        log.error("profile.failed", exc=e)
        return None
    if r.stop_reason != "end_turn":
        return None
    text = next((b.text for b in r.content if b.type == "text"), "")
    try:
        out = json.loads(text)
    except ValueError:
        return None
    out, dropped = _clean(out, fact_text)
    if dropped:
        from app import log
        log.emit("profile.ungrounded", level="error", claims=dropped[:5])
    try:
        from engine import accuracy
        accuracy.record_outcome(not dropped, dropped, "profile", withheld=bool(dropped))
    except Exception:
        pass
    return out if out.get("tagline") else None


# ------------------------------------------------------------ fallback

# Written to be sent to a friend: short enough to post, and about a thing she does.
TOXIC_TRAITS = {
    "Aries": "You apologize by pretending it never happened.",
    "Taurus": "One changed plan and your whole day is ruined.",
    "Gemini": "You only answer the easy part of the text.",
    "Cancer": "You say “I'm fine” like you're keeping receipts.",
    "Leo": "Your healing journey needs a witness.",
    "Virgo": "You fix everyone's life and leave your own on read.",
    "Libra": "You'd rather stay confused than start a fight.",
    "Scorpio": "You've checked the read receipt. Twice. Today.",
    "Sagittarius": "You leave before it gets real and call it freedom.",
    "Capricorn": "You schedule your breakdowns.",
    "Aquarius": "You vanish for a week and come back with a theory.",
    "Pisces": "You fell for who they could be. Again.",
}


def fallback(chart, analysis, time_known):
    """The same shape from authored text, for demo mode and failed calls."""
    b = analysis["big3"]
    cards = {c["planet"]: c for c in personal.cards(chart, time_known)}
    sun, moon = b["sun"]["sign"], b["moon"]["sign"]
    venus, mars, merc = cards.get("Venus"), cards.get("Mars"), cards.get("Mercury")
    strong = [p for p in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn")
              if corpus.dignity(p, chart["planets"][p]["sign"]) in ("domicile", "exalted")]
    sp = strong[0] if strong else "Sun"
    sp_sign = chart["planets"][sp]["sign"]

    def item(text, because):
        return {"text": text, "because": because}

    rising = f", {b['rising']['sign']} rising" if time_known and b.get("rising") else ""
    return {
        "tagline": f"{sun} Sun, {moon} Moon{rising} — {(venus or {}).get('title', 'all heart').lower()}.",
        "vibe": f"{I.SUN[sun]} {I.MOON[moon]}",
        "superpower": item(f"Your {corpus.PLANETS[sp]['keyword']} is a strength. It works "
                           f"{corpus.SIGNS[sp_sign]['style']}.",
                           f"{sp} in {sp_sign}"),
        "toxic_trait": item(TOXIC_TRAITS[sun], f"Sun in {sun}"),
        "in_love": item(venus["body"], f"Venus in {venus['sign']}") if venus else None,
        "when_mad": item(mars["body"], f"Mars in {mars['sign']}") if mars else None,
        "at_work": item(merc["body"], f"Mercury in {merc['sign']}") if merc else None,
        "misread": item(f"People see the {sun} first. Underneath, {I.MOON[moon][0].lower()}{I.MOON[moon][1:]}",
                        f"Sun in {sun}, Moon in {moon}"),
        "needs": item(f"{corpus.PLANETS['Moon']['wants'].capitalize()}. You get there "
                      f"{corpus.SIGNS[moon]['style']}.",
                      f"Moon in {moon}"),
        "green_flags": [], "red_flags": [],
    }


def build(chart, analysis, time_known, sky=None, name=None):
    out = generate(chart, analysis, time_known, sky, name)
    generated = out is not None
    if not generated:
        out = fallback(chart, analysis, time_known)
    sections = [{"key": k, "title": t, **out[k]} for k, t in SECTIONS if out.get(k)]
    return {"tagline": out.get("tagline"), "vibe": out.get("vibe"),
            "sections": sections, "green_flags": out.get("green_flags") or [],
            "red_flags": out.get("red_flags") or [], "generated": generated}
