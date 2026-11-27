"""
A reply, as it should be heard.

The chat shows text; the psychic's room reads the same text aloud. The prompt
asks for speakable replies when CHANNEL is voice, and this is the net under it:
a symbol, an abbreviation or an internal name that would make a voice stumble
("TrueNode", "21°47'", "4/10", "Rx") becomes words.

The browser has the same rules (speakable() in app/static/index.html) for the
device's own voice; tests/fixtures/speech_cases.json holds the cases both must
agree on.
"""
import re

GLYPHS = {
    "☉": "Sun", "☽": "Moon", "☾": "Moon", "☿": "Mercury", "♀": "Venus", "♂": "Mars",
    "♃": "Jupiter", "♄": "Saturn", "♅": "Uranus", "♆": "Neptune", "♇": "Pluto",
    "♈": "Aries", "♉": "Taurus", "♊": "Gemini", "♋": "Cancer", "♌": "Leo", "♍": "Virgo",
    "♎": "Libra", "♏": "Scorpio", "♐": "Sagittarius", "♑": "Capricorn", "♒": "Aquarius",
    "♓": "Pisces", "☌": "conjunct", "☍": "opposite", "□": "square", "△": "trine",
    "⚹": "sextile", "℞": "retrograde",
}
WORDS = [
    (r"\bTrue ?Node\b|\bMean ?Node\b", "North Node"),
    (r"\b(?:AC|ASC)\b", "Ascendant"),
    (r"\bMC\b", "Midheaven"),
    (r"\bDSC\b", "Descendant"),
    (r"\[?\bRx\b\]?", "retrograde"),
    (r"\be\.g\.,?", "for example,"),
    (r"\bi\.e\.,?", "that is,"),
    (r"\bvs\.?(?=\s)", "versus"),
    (r"\betc\.", "and so on."),
    (r"\bw/(?=\s)", "with"),
    (r"\s&\s", " and "),
]
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-⛿✀-➿️]")
# Numbers someone would dial or text, read one digit at a time ("nine eight eight").
DIAL = re.compile(r"(?i)\b(call|text|dial|phone|line|number|txt)\b([^.?!\n]{0,24}?)"
                  r"(\b\d[\d ]{1,14}\d\b)")


# Spaced groups ("116 123", "0800 1111") are always a phone number.
GROUPED = re.compile(r"\b\d{3,4}(?: \d{3,4})+\b")


def _digits(m):
    return f"{m.group(1)}{m.group(2)}{' '.join(m.group(3).replace(' ', ''))}"


BREATH = 24          # words: past this a listener starts to lose the sentence
_JOINS = [r":\s+", r";\s+", r",\s+so\s+", r",\s+and\s+(?=(?:it|that|this|she|he|they|you|your|the)\b)",
          r",\s+which\s+(?=(?:is|means)\b)", r",\s+but\s+"]
_LEAD = {r":\s+": "", r";\s+": "", r",\s+so\s+": "So ",
         r",\s+and\s+(?=(?:it|that|this|she|he|they|you|your|the)\b)": "And ",
         r",\s+which\s+(?=(?:is|means)\b)": "That ", r",\s+but\s+": "But "}


def _nwords(s):
    return len(re.findall(r"[^\W\d_][\w']*", s))


def _cap(s):
    return s[:1].upper() + s[1:]


def breaths(text):
    """A sentence too long to follow by ear becomes two at its most natural
    join (a colon, a semicolon, ", so", ", and it", ", which is", ", but").
    Only for listening: the screen keeps the writing as it was."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
        todo = [sent]
        while todo:
            s = todo.pop(0)
            if _nwords(s) <= BREATH or '"' in s or "“" in s:
                out.append(s)
                continue
            for j in _JOINS:
                m = next((m for m in re.finditer(j, s)
                          if _nwords(s[:m.start()]) >= 6 and _nwords(s[m.end():]) >= 6), None)
                if m:
                    head = s[:m.start()].rstrip(",;: ") + "."
                    todo[:0] = [head, _LEAD[j] + _cap(s[m.end():]) if not _LEAD[j] else _LEAD[j] + s[m.end():]]
                    break
            else:
                out.append(s)
    return " ".join(out)


def speakable(text):
    t = text or ""
    # Markdown the prompt forbids but a model can still slip in.
    t = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), t)
    t = re.sub(r"(?m)^\s*#{1,6}\s+", "", t)
    t = re.sub(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+", "", t)
    t = re.sub(r"`", "", t)
    t = re.sub(r"https?://", "", t)
    t = re.sub(r"\b([\w-]+)\.(org|com|net|gov|uk|io)\b", lambda m: f"{m.group(1)} dot {m.group(2)}", t)
    for g, w in GLYPHS.items():
        t = t.replace(g, f" {w} ")
    for pat, w in WORDS:
        t = re.sub(pat, w, t)
    # 21°47' and 21° become degrees; minutes are noise out loud.
    t = re.sub(r"(\d+)\s*°\s*(\d+)\s*['′]?", r"\1 degrees", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*°", r"\1 degrees", t)
    t = re.sub(r"\b(\d{1,2})\s*/\s*10\b", r"\1 out of 10", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"\1 percent", t)
    t = re.sub(r"\b(\d{1,2})(?::(\d\d))?\s*([ap])\.?m\.?\b",
               lambda m: f"{m.group(1)}{(':' + m.group(2)) if m.group(2) else ''} {m.group(3).upper()}M", t, flags=re.I)
    t = GROUPED.sub(lambda m: " ".join(m.group(0).replace(" ", "")), t)
    t = DIAL.sub(_digits, t)
    # Asides: a voice can't do parentheses or dashes.
    t = re.sub(r"\s*\(([^()]{1,120})\)", r", \1,", t)
    t = re.sub(r"\s+[—–]\s+|—", ", ", t)
    t = re.sub(r"\s+/\s+", " or ", t)
    t = EMOJI.sub("", t)
    t = re.sub(r",\s*([.?!,;:])", r"\1", t)
    t = re.sub(r"\s+([.?!,;:])", r"\1", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    return breaths(t.strip())
