"""
Which voice the chat speaks in.

v1 answers and stops: a hard sentence cap, one transit, no questions back. It
reads like a very good horoscope. v2 talks: it follows up on what she says,
asks about what was coming, gives her something to watch for and report back,
and lets her choose how deep to go. Same rules about the sky, same safety tiers.

MYSIGN_VOICE=v1 brings the old voice back without a code change, so the two
can be compared on real people before v1 is deleted.
"""
import os
import pathlib
import re as _re

VOICE = os.environ.get("MYSIGN_VOICE", "v2")
_PROMPTS = pathlib.Path(__file__).parent.parent / "prompts"


def system_prompt(voice=None):
    v = voice or VOICE
    name = "system-prompt-v1.md" if v == "v1" else "system-prompt.md"
    return (_PROMPTS / name).read_text()


def history_window(voice=None):
    """Messages of conversation re-sent each turn. v2 holds a thread together
    across more than a couple of exchanges, which v1 never tried to."""
    return 10 if (voice or VOICE) == "v1" else 16


# ---------------------------------------------------------------- rhythm
#
# The audit's voice numbers (MEDIUM-4): 92% of replies ended on a question,
# 30 of 78 ran past LENGTH, and the same user heard "(a tense angle…)" again
# and again. The prompt already said all three things. What it could not see
# was the conversation so far, so these are computed and briefed, and the
# length ceiling is held in code.

_SENT_END = _re.compile(r"(?<=[.!?…])[\"”’)]*\s+")
_DEEP = _re.compile(r"\b(why|how|explain|deep(?:er)?|deep cut|in detail|tell me more|"
                    r"por ?qu[eé]|c[oó]mo|expl[ií]ca\w*|pourquoi|comment|warum|wie)\b", _re.I)

# Technical words worth explaining once. The glossary's English forms plus the
# Spanish ones the model uses with Spanish speakers.
_TERMS = {
    "square": ["square", "squares", "squaring", "cuadratura"],
    "trine": ["trine", "trines", "trígono", "trigono"],
    "sextile": ["sextile", "sextil"],
    "opposition": ["opposition", "opposite", "oposición", "oposicion"],
    "conjunction": ["conjunction", "conjunct", "conjunción", "conjuncion"],
    "rising sign": ["rising sign", "ascendant", "ascendente"],
    "Midheaven": ["midheaven", "medio cielo", "mediocielo"],
    "house": ["house", "casa"],
    "transit": ["transit", "transiting", "tránsito", "transito"],
    "retrograde": ["retrograde", "retrógrado", "retrogrado"],
    "orb": ["orb", "orbe"],
    "composite": ["composite", "compuesta"],
    "North Node": ["north node", "nodo norte"],
    "applying": ["applying"],
}


def explained_terms(replies):
    """Technical terms already used (and so explained, per the prompt) in this
    conversation's replies, in the order they first appeared."""
    seen = []
    for text in replies:
        low = (text or "").lower()
        for term, forms in _TERMS.items():
            if term not in seen and any(_re.search(rf"\b{_re.escape(f)}\b", low) for f in forms):
                seen.append(term)
    return seen


def question_streak(replies):
    """How many of the last replies in a row ended on a question."""
    n = 0
    for text in reversed(replies):
        if not (text or "").rstrip().endswith("?"):
            break
        n += 1
    return n


def sentence_limit(max_sentences, message):
    """Sentences before the closing question: LENGTH's number, or the deep-cut
    ceiling when she asked why or how."""
    n = int(max_sentences or 4)
    return n + 3 if _DEEP.search(message or "") else n


class Limiter:
    """
    Holds a streamed reply to its sentence ceiling without buffering it.

    The first `limit` sentences stream as they arrive. After that nothing more
    is shown until the reply ends; then, if it ended on a question, that
    question follows, so a long reply loses its middle and keeps its ask —
    the cut the prompt describes ("the second transit, every date but one, the
    caveat"), made when the model didn't make it.
    """

    def __init__(self, limit):
        self.limit = max(1, int(limit))
        self.shown = ""
        self.raw = ""
        self.cut = False
        self.grace = False

    def _complete(self, text):
        return len(_SENT_END.findall(text))

    def feed(self, chunk):
        """The part of `chunk` to show now ('' once past the ceiling)."""
        self.raw += chunk
        if self.cut:
            return ""
        out = ""
        for ch in chunk:
            out += ch
            if self._complete(self.shown + out) >= self.limit:
                # A paragraph that only just opened gets to finish its thought
                # (two more sentences at most) instead of hanging on a lead-in
                # like "It isn't all fighting, though."
                para = (self.shown + out).rsplit("\n\n", 1)
                if len(para) == 2 and not self.grace and self._complete(para[1]) <= 1:
                    self.grace = True
                    self.limit += 2
                    continue
                # The limit-th sentence just ended; its trailing space is kept.
                self.cut = True
                break
        self.shown += out
        return out

    def finish(self, full=None):
        """(text to store, tail to show now). `full` is the model's whole reply."""
        full = (full if full is not None else self.raw).strip()
        if not self.cut:
            return full, ""
        rest = full[len(self.shown.strip()):] if full.startswith(self.shown.strip()) else ""
        parts = [p for p in _SENT_END.split(rest.strip()) if p.strip()]
        if len(parts) <= 1:
            # Only the question (or one sentence) was left: nothing to cut.
            return full, full[len(self.shown):] if full.startswith(self.shown) else ""
        tail = parts[-1].strip() if parts[-1].rstrip().endswith("?") else ""
        if not tail:
            return self.shown.strip(), ""
        # What is already on her screen ends in a space; the question goes on
        # a new paragraph if the model put it on one.
        para = "\n\n" in rest and not self.shown.endswith("\n")
        shown_tail = ("\n\n" if para else "") + tail
        if not self.shown[-1:].isspace() and not para:
            shown_tail = " " + shown_tail
        return (self.shown + shown_tail).strip(), shown_tail
