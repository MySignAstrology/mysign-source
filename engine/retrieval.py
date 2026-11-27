"""
Which past days matter to what she just said.

Recency alone loses the good callbacks — the strongest line the voice test
produced cited episodes 24 and 11 days back. Substring matching only found those
because she happened to name the person both times.

This is BM25 over the episode corpus: standard, dependency-free, and good enough
that the difference between it and embeddings is smaller than the difference
between it and what was here before. The seam for a real embedding model is
`score()` — swap the body, keep the signature.
"""
import math
import re
from collections import Counter

K1, B = 1.5, 0.75

STOP = {"the", "a", "an", "and", "or", "but", "if", "so", "to", "of", "in", "on",
        "at", "for", "with", "is", "am", "are", "was", "were", "be", "been", "it",
        "its", "this", "that", "i", "me", "my", "you", "your", "he", "she", "they",
        "them", "his", "her", "do", "did", "does", "just", "like", "really", "very",
        "about", "what", "why", "how", "when", "should", "would", "could", "not"}


def stem(w):
    """
    Crude suffix folding, applied to queries and episodes alike. Without it
    "overexplaining" could not find "why do i always overexplain" — the model
    searched in its own words, matched nothing, and told her she had never
    mentioned it. Collisions this causes are cheaper than a miss like that.
    """
    for suf in ("ation", "ion", "ing", "ous", "ed", "es", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[:-len(suf)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 4 else w


def tokens(text):
    words = re.findall(r"[a-z']{2,}", (text or "").lower().replace("-", ""))
    return [stem(w) for w in words if w not in STOP]


# What she asks in one word and the extractor wrote in another. "what's my mood
# been like" matched only the one episode whose tags said "mood", so the model
# concluded the 4 it had quoted the day before did not exist and took it back
# (audit HIGH-2). Groups, not a thesaurus: each word finds the others.
SYNONYMS = [
    {"mood", "feel", "feeling", "felt", "emotion", "vibe", "anxious", "anxiety", "nervous",
     "nerves", "spiral", "spiraling", "stressed", "stress", "sad", "happy", "down", "low"},
    {"work", "job", "career", "interview", "boss", "office", "hire", "hiring", "offer", "role"},
    {"negotiate", "negotiating", "negotiation", "salary", "offer", "pay"},
    {"sleep", "slept", "tired", "insomnia", "awake"},
    {"boyfriend", "girlfriend", "partner", "dating", "date", "situationship", "crush", "ex"},
    {"mom", "mum", "mother", "dad", "father", "parents", "family", "sister", "brother"},
    {"friend", "friends", "mate", "mates", "bestie"},
    {"fight", "fighting", "argue", "argument", "tiff", "row"},
]
_SYN = {}
for group in SYNONYMS:
    stems = {stem(w) for w in group}
    for t in stems:
        _SYN.setdefault(t, set()).update(stems)


def expand(query):
    """The query's tokens plus their synonyms, for an explicit search."""
    q = tokens(query)
    return q + sorted({s_ for t in q for s_ in _SYN.get(t, ()) if s_ not in q})


def _doc(ep):
    parts = [ep.get("summary", ""), " ".join(ep.get("her_words", [])),
             " ".join(ep.get("topics", [])), " ".join(ep.get("people", []))]
    return tokens(" ".join(parts))


def score(query, episodes, synonyms=False):
    """Return [(episode, score)] sorted high to low. Empty query -> empty."""
    q = expand(query) if synonyms else tokens(query)
    if not q or not episodes:
        return []

    docs = [_doc(e) for e in episodes]
    n = len(docs)
    avgdl = sum(len(d) for d in docs) / n or 1
    df = Counter(t for d in docs for t in set(d))

    out = []
    for ep, d in zip(episodes, docs):
        tf, dl, s = Counter(d), len(d), 0.0
        for t in q:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * dl / avgdl))
        if s > 0:
            out.append((ep, round(s, 3)))
    return sorted(out, key=lambda x: -x[1])


def relevant(query, episodes, exclude_dates=(), limit=4, floor=0.6, synonyms=False):
    """Top matches from the whole history, minus anything already shown."""
    seen = set(exclude_dates)
    return [ep for ep, s in score(query, episodes, synonyms)
            if ep["date"] not in seen and s >= floor][:limit]
