"""
Checks for a whole conversation, in either channel: typed (chat) or said out
loud (the psychic's room). Mechanical, like evals/rubric.py: decidable from the
text, so they can gate a prompt change without a model judging a model.

Each check returns (ok, detail). `channel` is "text" or "voice".
"""
import re

from evals import rubric as R

GLYPH_OR_SHORTHAND = re.compile(
    r"[°℞☉☽☾☿♀♂♃♄♅♆♇♈♉♊♋♌♍♎♏♐♑♒♓☌☍□△⚹&]|\b\d{1,2}\s*/\s*10\b|\be\.g\.|\bi\.e\.|\bvs\.?\s"
    r"|\b(?:AC|ASC|MC|DSC|Rx|TrueNode)\b|\*\*|^\s*[-*•]\s", re.M)
PAREN_OR_DASH = re.compile(r"\([^)]*\)|\s[—–]\s|—")
VISUAL = re.compile(r"\b(see (?:above|below)|(?:listed|shown) (?:above|below)|tap\b|scroll|"
                    r"click|swipe|on (?:your|the) screen|in your chart view|the chart below)\b", re.I)
BACKSTAGE = re.compile(r"\b(the block|context block|the context|data i (?:was|have been) given|"
                       r"system prompt|get_sky|get_natal|search_memory|my tools?|a lookup|"
                       r"i looked (?:it )?up)\b", re.I)
PSYCHIC_CLAIMS = re.compile(r"\b(i (?:can )?(?:see|sense|feel) (?:in|through) the (?:ball|crystal|orb)|"
                            r"the (?:ball|crystal|orb) (?:shows|says|reveals|tells)|the spirits|"
                            r"my (?:psychic )?powers|i(?:'m| am) (?:a )?(?:real )?psychic|"
                            r"i(?:'m| am) (?:a )?(?:real )?(?:human|person)\b)", re.I)
# She asked what she is talking to: only then must the reply say it's an AI.
ASKS_IDENTITY = re.compile(r"\b(human|real person|a person|a robot|a bot|chat ?gpt|an ai|is this ai|"
                           r"are you (?:real|ai|a psychic|psychic|a witch)|real psychic|who (?:is|am i) "
                           r"(?:this|talking to)|who are you|what are you)\b", re.I)
AI_SAID = re.compile(r"\b(ai|a computer program|software|an app|not a (?:person|human|psychic))\b", re.I)
WORDS = re.compile(r"[A-Za-zÀ-ÿ']+")


def _words(s):
    return len(WORDS.findall(s))


# ---------------------------------------------------------------- per reply

def speakable_as_written(text, channel):
    """Voice: nothing a voice would trip on, before the speech net fixes it."""
    if channel != "voice":
        return True, ""
    hits = sorted({m.group(0).strip() for m in GLYPH_OR_SHORTHAND.finditer(text)})
    hits += ["(aside)" for _ in PAREN_OR_DASH.finditer(text)][:1]
    return not hits, ", ".join(hits)


def ear_length_sentences(text, channel, limit=28):
    """Voice: no sentence a listener loses halfway through, in what she hears
    (engine/speech.speakable, which gives an over-long sentence a breath)."""
    if channel != "voice":
        return True, ""
    from engine.speech import speakable
    long = [s for s in R.sentences(speakable(text)) if _words(s) > limit]
    return not long, (f"{len(long)} sentence(s) over {limit} words: {long[0][:60]}…" if long else "")


def nothing_to_look_at(text, channel):
    if channel != "voice":
        return True, ""
    m = VISUAL.search(text)
    return not m, (m.group(0) if m else "")


def one_question(text, channel=None):
    # "Honestly?" or "Real talk?" opening a sentence is a beat, not a question to her.
    t = re.sub(r"(?:^|(?<=[.!?]\s))(?:\w+(?: \w+)?)\?(?=\s)", "", text)
    n = t.count("?")
    return n <= 1, (f"{n} questions" if n > 1 else "")


def backstage_hidden(text, channel=None):
    m = BACKSTAGE.search(text)
    return not m, (m.group(0) if m else "")


def no_psychic_claims(text, channel=None):
    m = PSYCHIC_CLAIMS.search(text)
    return not m, (m.group(0) if m else "")


def says_its_ai(text, channel=None):
    return bool(AI_SAID.search(text)), "never says it's an AI"


def plain_text(text, channel=None):
    m = re.search(r"\*\*|__|^#{1,6}\s|^\s*[-*•]\s", text, re.M)
    return not m, (m.group(0).strip() if m else "")


def clean_voice(text, channel=None):
    ok, hits = R.clean_voice(text)
    return ok, ", ".join(hits)


def leads_with_her(text, channel=None, asked=""):
    # Asked about a planet or a sign ("which planet is the messiest", "is it
    # mercury"), the planet is the right first word.
    from engine.grounding import PLANETS, SIGNS
    if re.search(rf"\b({'|'.join(PLANETS + SIGNS)}|planet|retro ?grade|sign)\b", asked or "", re.I):
        return True, ""
    return R.leads_with_her(text), "opens on a planet"


def takes_a_position(text, channel=None):
    return R.takes_a_position(text), "hedges"


# A verdict tells her what to do: "you should leave him", "just quit". Quoting her
# ("when you say end it"), asking ("whether you leave him is yours") or "leave him
# alone for today" is not one.
_DIRECTIVE = re.compile(r"(?:^|[.!?]\s+|\b(?:you should|you need to|you have to|just|honestly,?|"
                        r"i'd|i would|my advice is to|time to)\s+)(?:\w+\s+){0,2}$", re.I)


def withholds_verdict(text, channel=None):
    """Amber: never decide an irreversible thing for her."""
    low = text.lower()
    for v in R.VERDICT_WORDS:
        for m in re.finditer(rf"\b{re.escape(v)}\b", low):
            before, after = low[max(0, m.start() - 40):m.start()], low[m.end():m.end() + 12]
            if re.match(r"\s*(alone|out of|is yours|or (?:stay|not))", after):
                continue
            if re.search(r"\b(whether|if|when you say|you said|say|not|never|no|n't|reason to)\b[^.?!]{0,25}$", before):
                continue
            if _DIRECTIVE.search(before):
                return False, f"says '{v}'"
    return True, ""


def no_astrology_at_all(text, channel=None):
    return R.no_astrology_at_all(text), "astrology in a reply that should have none"


def names_a_mechanism(text, channel=None):
    # A quiet day said plainly is the right answer, not a missing mechanism.
    return R.names_a_mechanism(text, "") or R.says_nothing_is_happening(text), \
        "no planet, house or aspect named"


def not_empty(text, channel=None):
    return bool(text.strip()), "empty reply"


ALWAYS = [not_empty, plain_text, clean_voice, one_question, backstage_hidden, no_psychic_claims,
          speakable_as_written, ear_length_sentences, nothing_to_look_at]
NAMED = {f.__name__: f for f in [leads_with_her, takes_a_position, withholds_verdict,
                                 no_astrology_at_all, names_a_mechanism, says_its_ai]}


# Only the answer to her opening message has to do these; "thanks, bye" doesn't.
FIRST_ONLY = {"names_a_mechanism", "takes_a_position", "leads_with_her"}


def grade_reply(text, channel, extra=(), first=True, asked=""):
    out = {}
    for f in ALWAYS:
        out[f.__name__] = f(text, channel)
    for name in extra:
        if name in NAMED and (first or name not in FIRST_ONLY):
            out[name] = (NAMED[name](text, channel, asked) if name == "leads_with_her"
                         else NAMED[name](text, channel))
    return {k: {"ok": bool(v[0]), "detail": v[1]} for k, v in out.items()}


# ---------------------------------------------------------------- per conversation

def no_repeats(replies):
    """Don't say the same thing twice in one conversation."""
    seen = {}
    for i, r in enumerate(replies):
        for s in R.sentences(r):
            k = re.sub(r"\W+", " ", s.lower()).strip()
            if _words(k) >= 6 and k in seen and seen[k] != i:
                return False, f"repeated: {s[:60]}"
            seen.setdefault(k, i)
    return True, ""


def question_rhythm(replies):
    """Not every reply ends on a question (the prompt's QUESTION: skip)."""
    run = best = 0
    for r in replies:
        run = run + 1 if r.rstrip().endswith("?") else 0
        best = max(best, run)
    return best <= 2, (f"{best} replies in a row ended on a question" if best > 2 else "")


def parity(chat, voice):
    """
    Same scenario, typed vs said: the same answer, as well made. Both first
    replies pass the same core checks, and the spoken one is not much longer.
    (Which transit it picks varies between any two runs of the same question,
    chat or not, so the planets named are reported by same_sky, not gated on.)
    chat / voice: lists of turns, each {"text", "checks"}.
    """
    if not chat or not voice:
        return False, "missing a channel"
    core = ("grounded_turn", "takes_a_position", "withholds_verdict", "says_its_ai", "one_question")
    for name in core:
        a = (chat[0].get("checks") or {}).get(name)
        b = (voice[0].get("checks") or {}).get(name)
        if a and b and a["ok"] and not b["ok"]:
            return False, f"orb fails {name} where chat passes"
    la, lb = _words(chat[0]["text"]), _words(voice[0]["text"])
    if la and lb > la * 1.5 + 10:
        return False, f"orb {lb} words vs chat {la}"
    return True, ""


def same_sky(chat_text, voice_text):
    """Informational: share of planets named in common (Jaccard), or None."""
    from engine.grounding import mentions
    pa, pb = set(mentions(chat_text)["planets"]), set(mentions(voice_text)["planets"])
    return len(pa & pb) / len(pa | pb) if (pa or pb) else None
