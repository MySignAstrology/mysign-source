"""
Never shows her an invented placement.

The product's first rule is that the model never invents a planetary position.
engine/grounding.py has always been able to tell when a reply breaks it, but
only after the fact: chat streams, so the reply was already on her screen. The
10,000-conversation eval measured how often: about one reply in twelve named an
aspect the sky didn't have, nearly always by turning a real one into "X is
sitting on your Y" (a conjunction) when the block said opposite or square.

This holds each sentence back until it is complete, checks it against
everything the model was given (the block and every lookup so far), and lets it
through only if it adds no ungrounded claim. A sentence that does is dropped,
so she sees the rest of the reply and never the invention. Streaming is delayed
by one sentence at most, which the length limiter already does.

The raw reply is still scored and counted (engine/accuracy.py), so the public
number stays honest about the model; this only changes what she sees.
"""
import re

from engine import grounding

# The end of a sentence, and the whitespace after it (kept with the sentence).
_END = re.compile(r"(?<=[.!?…])[\"”’)]*\s+")


class Guard:
    def __init__(self, context):
        self.context = context or ""
        self.buf = ""            # text not yet released: at most one unfinished sentence
        self.shown = ""          # what she has seen
        self.dropped = []        # (sentence, problems) held back
        self.fed = ""            # everything the stream has handed over

    def add_context(self, text):
        """A lookup result: anything in it may now be said."""
        self.context += "\n" + text

    def _new_problems(self, sentence):
        # Checked with what came before it, so "your" still points at the right
        # chart; only problems this sentence adds count against it.
        _, before = grounding.check(self.shown, self.context)
        _, after = grounding.check(self.shown + sentence, self.context)
        return [p for p in after if p not in before]

    def _release(self, sentence):
        problems = self._new_problems(sentence)
        if problems:
            self.dropped.append((sentence.strip(), problems))
            return ""
        self.shown += sentence
        return sentence

    def feed(self, chunk):
        """The part of the stream that may be shown now."""
        self.fed += chunk
        self.buf += chunk
        out = ""
        while True:
            m = _END.search(self.buf)
            if not m:
                return out
            sentence, self.buf = self.buf[:m.end()], self.buf[m.end():]
            out += self._release(sentence)

    def finish(self, full=None):
        """(text to show now, text to store). `full` is the model's whole reply:
        any of it the stream never handed over is checked here too."""
        out = ""
        if full and full.startswith(self.fed) and len(full) > len(self.fed):
            out = self.feed(full[len(self.fed):])
        out += self._release(self.buf) if self.buf.strip() else ""
        self.buf = ""
        return out, self.shown.strip()
