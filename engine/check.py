"""
The public compatibility check: two birthdays, one score, no account.

It is engine/compat.deep, the function behind a person's People tab, with both
birth times unknown. So the number is the same engine's, and it says what a
birthday alone can't settle: the score comes with the range it could land in
once real birth times are added.

Nothing is stored. The share token carries first names and the result, signed
so a link can't be edited into a score nobody got. It never carries a birth
date, because one of the two people didn't type theirs.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import unicodedata

from engine import compat

NAME_MAX = 20

# What the page offers, and the weighting each one reads with. An ex is read
# as the romance it was.
WEIGHTS = {"romantic": "romantic", "friend": "friend", "ex": "romantic"}
KINDS = tuple(WEIGHTS)

# Every line agrees with its band: the joke is on the people, the measurement
# stays honest. Short enough for three lines on the card.
LINES = {
    "romantic": {
        "rare": "Most pairs never get this close. Do not screw this up.",
        "strong": "The sky did its part. Whatever's going wrong is on you two.",
        "promising": "A little above an ordinary pair. Good enough to text back.",
        "mixed": "About what any two people get. The sky won't do this for you.",
        "effortful": "Below an ordinary pair. Real work, and you already knew that.",
        "hard going": "Well below an ordinary pair. You saw the flags. Now you have a number.",
    },
    "friend": {
        "rare": "Most friendships never get this close. Everyone else is a plus-one.",
        "strong": "Well above ordinary. You two are why there's a second group chat.",
        "promising": "A little above ordinary. Borrow the clothes, not the money.",
        "mixed": "About average. Great at brunch. Do not plan a trip.",
        "effortful": "Below ordinary. You're friends because of a third friend.",
        "hard going": "Well below ordinary. One of you is only here for the gossip.",
    },
    "ex": {
        "rare": "Most pairs never get this close. That's what made it so annoying.",
        "strong": "Well above ordinary. That is not a reason to text them.",
        "promising": "A little above ordinary. Still not a reason to text them.",
        "mixed": "Completely average. All that crying, for a completely average score.",
        "effortful": "Below ordinary. You knew. Your friends knew.",
        "hard going": "Well below ordinary. Frame this and keep it next to your phone.",
    },
}
SAME_SKY = "Born within hours of each other. This number can't tell you two apart."

DIMENSIONS = (("spark", "Spark", "the pull that starts things"),
              ("ease", "Ease", "how easy a normal day is"),
              ("staying", "Staying power", "what holds up over years"))


def line(kind, band):
    if band == "same sky":
        return SAME_SKY
    return LINES.get(kind, LINES["romantic"]).get(band, "")


def first_name(text):
    """One word of letters, or nothing. It ends up on a card and in a link
    preview under our domain, so anything else is dropped whole, not tidied."""
    words = unicodedata.normalize("NFKC", text or "").split()
    word = words[0] if words else ""
    if not all(unicodedata.category(c)[0] in "LM" or c in "-'’" for c in word):
        return ""
    return word.strip("-'’")[:NAME_MAX]


def old_enough(birth, minimum, today=None):
    return compat._age(birth, today or dt.date.today()) >= minimum


def _sign(samples):
    """The Sun sign, or both when the unknown hour straddles a change."""
    seen = []
    for chart in samples:
        s = compat._sign(chart["planets"]["Sun"]["lon"])
        if s not in seen:
            seen.append(s)
    return "/".join(seen)


def read(a, b, kind="romantic", today=None):
    """`a` and `b` are (year, month, day). You first: contacts read "your"."""
    kind = kind if kind in WEIGHTS else "romantic"
    ua, ub = [*a, 12.0, 0.0, 0.0], [*b, 12.0, 0.0, 0.0]
    d = compat.deep({"birth_ut": ua}, {"birth_ut": ub}, kind=WEIGHTS[kind],
                    precision_a="unknown", precision_b="unknown", today=today)
    sc = d["score"]
    same = sc["band"] == "same sky"
    certain = [c for c in d["contacts"] if c["certainty"] == "certain"]
    good = next((c for c in certain if c["harmony"] > 0), None)
    hard = next((c for c in certain if c["harmony"] < 0), None)
    return {
        "kind": kind,
        "score": sc["overall"],
        "low": sc["overall_range"][0],
        "high": sc["overall_range"][1],
        "band": sc["band"],
        "line": line(kind, sc["band"]),
        "same_sky": same,
        "signs": [_sign(compat.samples(ua, "unknown")), _sign(compat.samples(ub, "unknown"))],
        "dimensions": [{"key": k, "label": lab, "note": note, "score": sc["dimensions"][k]}
                       for k, lab, note in DIMENSIONS],
        "why": [] if same else [{"tone": tone, "text": c["text"], "meaning": c["meaning"]}
                                for tone, c in (("good", good), ("hard", hard)) if c],
    }


# ------------------------------------------------------------------ the link
def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _sig(body, secret):
    return _b64(hmac.new(secret, f"check:{body}".encode(), hashlib.sha256).digest()[:16])


def sign(result, name_a, name_b, secret):
    """Names and the result. No birth date goes in here, ever."""
    body = _b64(json.dumps({"a": name_a, "b": name_b, "s": result["score"],
                            "r": result["band"], "k": result["kind"]},
                           separators=(",", ":"), ensure_ascii=False).encode())
    return f"{body}.{_sig(body, secret)}"


def verify(token, secret):
    """The shared result, or None for anything not signed by us."""
    if not isinstance(token, str) or len(token) > 400 or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    # As bytes: on strings compare_digest raises for anything non-ASCII in a URL.
    if not hmac.compare_digest(sig.encode(), _sig(body, secret).encode()):
        return None
    try:
        p = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    return {"names": [p["a"], p["b"]], "score": p["s"], "band": p["r"], "kind": p["k"],
            "line": line(p["k"], p["r"])}
