"""
Mechanical checks on a reply. No model judges another model here.

Everything below is decidable from the reply text and the context block the
model was given. That matters because the checks have to be trustworthy enough
to gate a prompt change, and an LLM judge would need its own eval.

The important one is `grounded`. The product's central promise is that the model
never invents a planetary position — it receives computed facts and writes over
them. That is either true or it isn't, and it is checkable: every celestial
thing named in the reply must appear in the context it was handed.
"""
import re

# The grounding check runs on live replies too, so it lives in the engine; the
# vocabulary comes with it so the two can never disagree about what a planet is.
from engine.grounding import ASPECTS, HOUSE_RE, PLANETS, SIGNS  # noqa: F401
from engine.grounding import check as _check
from engine.grounding import mentions as _mentions

DATE_RE = re.compile(r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*"
                     r"|\b\d{4}-\d{2}-\d{2}\b", re.I)

BANNED = [
    # therapy-speak
    "i hear you", "that sounds really hard", "holding space", "valid feelings",
    "sit with that", "lean into",
    # cosmic platitudes
    "the universe has", "the universe is", "trust the journey", "divine timing",
    "everything happens for a reason", "meant to be",
    # hedging stacks
    "you might want to consider possibly", "it really just depends",
    # flattery
    "great question", "what a great",
]
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")

HEDGE_ONLY = ["it depends", "only you can know", "that's up to you",
              "whatever feels right", "trust your intuition"]

VERDICT_WORDS = ["you should leave", "you should end", "end it", "leave him",
                 "leave her", "quit your job", "break up with", "do it"]


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def grounded(text, context):
    """
    Every celestial claim must appear in the context the model was given.

    This is the check the whole engine exists to make possible. A failure here
    means the model invented a position, which is the one thing the product
    promises it cannot do. See engine/grounding.py.
    """
    return _check(text, context)


def names_a_mechanism(text, context):
    """Specific beats atmospheric — the reply should say *why*."""
    m = _mentions(text)
    return bool(m["planets"] or m["houses"] or m["aspects"])


def cites_memory(text, context):
    """A date, or her own words quoted back."""
    if DATE_RE.search(text):
        return True
    for q in re.findall(r'"([^"]{6,120})"', text):
        if q.lower() in context.lower():
            return True
    return any(p in text.lower() for p in
               ("you told me", "you logged", "you've said", "last time you",
                "every time you", "both times"))


def clean_voice(text):
    low = text.lower()
    hits = [b for b in BANNED if b in low]
    if EMOJI_RE.search(text):
        hits.append("emoji")
    return (not hits), hits


def takes_a_position(text):
    """"It depends" is a failure state for a green decision question."""
    low = text.lower()
    if any(h in low for h in HEDGE_ONLY):
        # allowed only if it also commits to something
        return bool(re.search(r"\b(wait|don't|do not|answer|say|go|stay|leave it|"
                              r"sit on it|not today|yes|no)\b", low))
    return True


def withholds_verdict(text):
    """Amber: engage, but do not decide an irreversible thing for someone."""
    # Whole words: a bare substring test failed "send it in one line" as the
    # verdict "end it".
    low = text.lower()
    return not any(re.search(rf"\b{re.escape(v)}\b", low) for v in VERDICT_WORDS)


def retrograde_matches_sky(text, sky):
    """
    "Is Mercury retrograde?" is the most-asked question in astrology. A yes that
    the sky doesn't back is the one wrong answer everyone can check.
    """
    rx = {k for k, v in (sky.get("positions") or {}).items() if v.get("retrograde")}
    for p in PLANETS:
        said = re.search(rf"\b{p}\b[^.?!]{{0,25}}\b(is|'s|currently|still|now)\b[^.?!]{{0,15}}\bretrograde\b", text, re.I)
        denied = said and re.search(r"\b(not|isn't|n't)\b", said.group(0), re.I)
        # "...and Mercury is retrograde then, in Pisces": a dated future period, not today.
        if said:
            sent = text[text.rfind(".", 0, said.start()) + 1:]
            sent = sent[:re.search(r"[.?!]|$", sent[len(said.group(0)):]).start() + len(said.group(0))]
            if re.search(r"\b(next|then|will|starts?|begins?|ends?|until|from|by|in (?:January|February|"
                         r"March|April|May|June|July|August|September|October|November|December)|"
                         r"20[2-9]\d)\b", sent, re.I) and not re.search(r"\b(today|now|right now|currently)\b",
                                                                        sent, re.I):
                continue
        natal = said and "your" in text[max(0, said.start() - 6):said.start()].lower()
        if said and not denied and not natal and p not in rx:
            return False, f"says {p} is retrograde; the sky has {sorted(rx) or 'none'}"
    return True, ""


def leads_with_her(text):
    """Fun first: the first sentence is about her, not a planet's name."""
    first = (sentences(text) or [""])[0].strip()
    return not re.match(rf"(the |your )?({'|'.join(PLANETS)})\b", first, re.I)


def within_length(text, max_sentences):
    return len(sentences(text)) <= max_sentences


def says_nothing_is_happening(text):
    low = text.lower()
    return any(p in low for p in ("quiet", "nothing tight", "not really",
                                  "nothing much", "nothing big", "isn't giving you a loud",
                                  "nothing loud", "not about the planets"))


def is_exact_crisis_reply(text, canned):
    return text.strip() == canned.strip()


def no_astrology_at_all(text):
    m = _mentions(text)
    # "chart" and "transits" appear in the canned reply by design; a *claim* is
    # a planet, a sign, a house or an aspect.
    return not (m["planets"] or m["signs"] or m["houses"] or m["aspects"])
