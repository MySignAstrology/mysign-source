"""
Compatibility between two charts.

Scored in code, narrated by the model — the same split the rest of the engine
uses. Every point below traces to a named contact with an orb, so a score can
always be explained rather than asserted. If this ever returns a number the
code cannot itemise, that is a bug.

Three dimensions instead of one number, because a single score hides the thing
people actually want to know. Tension raises SPARK while it lowers EASE; that
is not a rounding artefact, it is how the charts read, and collapsing it into
one average would throw away the interesting half.
"""

from engine.chart import natal, plain

# Synastry runs wider orbs than transits — these are contacts between two whole
# lives, not a planet brushing a point on a given afternoon.
ASPECTS = [
    ("conjunction", 0, 8.0),
    ("opposition", 180, 7.0),
    ("trine", 120, 6.0),
    ("square", 90, 6.0),
    ("sextile", 60, 4.0),
]

POINTS = ["Sun", "Moon", "Mercury", "Venus", "Mars",
          "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"]
ANGLES = ["Ascendant", "Midheaven"]

WEIGHT = {
    "Sun": 1.00, "Moon": 1.00, "Venus": 0.95, "Mars": 0.85, "Ascendant": 0.90,
    "Mercury": 0.60, "Saturn": 0.65, "Jupiter": 0.50, "Pluto": 0.45,
    "Midheaven": 0.40, "Uranus": 0.35, "Neptune": 0.35,
}

# The classic pairs carry more than the product of their parts.
PAIR_BOOST = {
    frozenset(("Sun", "Moon")): 1.40,
    frozenset(("Venus", "Mars")): 1.35,
    frozenset(("Moon", "Venus")): 1.20,
    frozenset(("Sun", "Venus")): 1.15,
    frozenset(("Sun", "Ascendant")): 1.15,
    frozenset(("Moon", "Ascendant")): 1.15,
    frozenset(("Venus", "Ascendant")): 1.10,
    frozenset(("Moon", "Mars")): 1.05,
    frozenset(("Saturn", "Sun")): 1.10,
    frozenset(("Saturn", "Moon")): 1.10,
    frozenset(("Saturn", "Venus")): 1.10,
}

HARMONY = {"trine": 1.0, "sextile": 0.7, "conjunction": 0.5,
           "square": -0.9, "opposition": -0.7}

# Which planets feed which dimension. A contact routes its strength into every
# dimension it appears in, scaled by the factor.
SPARK_PLANETS = {"Venus": 1.0, "Mars": 1.0, "Sun": 0.7, "Moon": 0.6,
                 "Pluto": 0.8, "Uranus": 0.5, "Ascendant": 0.6}
EASE_PLANETS = {"Moon": 1.0, "Venus": 0.9, "Sun": 0.8, "Mercury": 0.7,
                "Jupiter": 0.6, "Ascendant": 0.5}
STAY_PLANETS = {"Saturn": 1.0, "Sun": 0.7, "Moon": 0.7, "Jupiter": 0.5,
                "Midheaven": 0.4}

# B's planet landing in A's house. The relational houses are the ones that move
# a score; the rest are noise at this resolution.
HOUSE_OVERLAY = {
    1: ("spark", 0.6, "shows up as themselves around you"),
    5: ("spark", 0.9, "romance and play"),
    7: ("stay", 1.0, "partnership"),
    8: ("spark", 0.8, "intimacy and depth"),
    4: ("ease", 0.6, "home and safety"),
    10: ("stay", 0.5, "how you're seen together"),
    11: ("ease", 0.5, "friendship"),
    12: ("spark", 0.4, "what stays hidden between you"),
}
OVERLAY_PLANETS = {"Sun", "Moon", "Venus", "Mars", "Saturn", "Jupiter"}

ELEMENT = {"Aries": "fire", "Leo": "fire", "Sagittarius": "fire",
           "Taurus": "earth", "Virgo": "earth", "Capricorn": "earth",
           "Gemini": "air", "Libra": "air", "Aquarius": "air",
           "Cancer": "water", "Scorpio": "water", "Pisces": "water"}
MODALITY = {"Aries": "cardinal", "Cancer": "cardinal", "Libra": "cardinal",
            "Capricorn": "cardinal", "Taurus": "fixed", "Leo": "fixed",
            "Scorpio": "fixed", "Aquarius": "fixed", "Gemini": "mutable",
            "Virgo": "mutable", "Sagittarius": "mutable", "Pisces": "mutable"}


def _sep(a, b):
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def _points(chart, with_angles=True):
    pts = {k: v["lon"] for k, v in chart["planets"].items() if k in POINTS}
    if with_angles:
        pts.update({k: v["lon"] for k, v in chart.get("angles", {}).items()})
    return pts


def cross_aspects(chart_a, chart_b, angles_a=True, angles_b=True):
    """Every aspect between A's points and B's points, tightest first."""
    out = []
    for an, alon in _points(chart_a, angles_a).items():
        for bn, blon in _points(chart_b, angles_b).items():
            sep = _sep(alon, blon)
            for name, angle, orb in ASPECTS:
                delta = abs(sep - angle)
                if delta > orb:
                    continue
                closeness = 1 - (delta / orb)          # 1.0 exact, 0 at the edge
                w = WEIGHT.get(an, 0.3) * WEIGHT.get(bn, 0.3)
                w *= PAIR_BOOST.get(frozenset((an, bn)), 1.0)
                out.append({
                    "a": an, "b": bn, "aspect": name,
                    "orb": round(delta, 2),
                    "strength": round(w * (0.45 + 0.55 * closeness), 4),
                    "harmony": HARMONY[name],
                })
                break                                   # one aspect per pair
    return sorted(out, key=lambda x: -x["strength"])


def house_overlays(chart_a, chart_b, whose="theirs"):
    """Where B's planets land in A's houses. Needs A's birth time.
    `whose` says whose planets they are, from A's side: "theirs" when B is
    the other person, "yours" for the reverse direction."""
    from engine.chart import house_of
    cusps = chart_a.get("house_cusps")
    if not cusps:
        return []
    out = []
    for name, p in chart_b["planets"].items():
        if name not in OVERLAY_PLANETS:
            continue
        h = house_of(p["lon"], cusps)
        if h in HOUSE_OVERLAY:
            dim, factor, label = HOUSE_OVERLAY[h]
            out.append({"planet": name, "house": h, "dim": dim,
                        "weight": round(factor * WEIGHT.get(name, 0.4), 3),
                        "label": label, "whose": whose})
    return sorted(out, key=lambda x: -x["weight"])


# Mean and standard deviation of the raw accumulators over random pairs.
# A raw total mostly measures "two charts exist" — every pair throws roughly
# forty contacts — so the score is a z-score against ordinary pairs rather than
# an absolute sum. Without this the median pair scores in the eighties and the
# feature becomes a flattery machine.
# Regenerate with tools/calibrate_synastry.py after changing any weight above.
#
# One reference per combination of known birth times. Until 2026-10-29 there
# were only "full" and "partial", and every other case borrowed "partial" —
# so two date-only charts (no angles, no houses, fewer contacts) were compared
# against pairs that had them, and the median date-only pair scored 36 instead
# of 50: every friend added without a birth time read as "effortful".
REFERENCE = {
    # Recalibrated when overlays began counting in both directions (audit M3).
    "full": {"spark": (15.4513, 2.8055), "ease": (2.6519, 2.8879), "staying": (4.1032, 1.8018)},
    "partial": {"spark": (12.3621, 2.4343), "ease": (1.7789, 2.7847), "staying": (2.9092, 1.5143)},
    "none": {"spark": (9.3264, 2.1131), "ease": (1.2227, 2.6036), "staying": (1.8824, 1.3604)},
}


def reference_key(time_known_a, time_known_b):
    """One time known is one case whichever side it is on: overlays count in
    both directions, so the raw totals are symmetric (recalibrating each side
    separately gave the same distribution to within sampling noise)."""
    if time_known_a and time_known_b:
        return "full"
    return "partial" if (time_known_a or time_known_b) else "none"


def raw_dims(chart_a, chart_b, angles_a=True, angles_b=True):
    """The pre-normalisation accumulators. Shared with the calibration tool so
    the reference distribution can never drift from what score() computes."""
    asp = cross_aspects(chart_a, chart_b, angles_a=angles_a, angles_b=angles_b)
    # Both directions, each gated on that side's own time. Only B-in-A used to
    # count, so the score depended on who added whom: two linked people saw
    # different numbers — sometimes different bands — for one relationship.
    over = sorted((house_overlays(chart_a, chart_b, "theirs") if angles_a else [])
                  + (house_overlays(chart_b, chart_a, "yours") if angles_b else []),
                  key=lambda x: -x["weight"])
    spark = ease = stay = 0.0
    for x in asp:
        s, h = x["strength"], x["harmony"]
        sf = SPARK_PLANETS.get(x["a"], 0) + SPARK_PLANETS.get(x["b"], 0)
        ef = EASE_PLANETS.get(x["a"], 0) + EASE_PLANETS.get(x["b"], 0)
        tf = STAY_PLANETS.get(x["a"], 0) + STAY_PLANETS.get(x["b"], 0)
        # Friction is charge, not the absence of it — spark takes the magnitude.
        spark += s * sf * (1.0 if h >= 0 else 0.85)
        ease += s * ef * h
        stay += s * tf * (h if h >= 0 else h * 0.5)
    for o in over:
        bump = o["weight"] * 1.2
        if o["dim"] == "spark":
            spark += bump
        elif o["dim"] == "ease":
            ease += bump
        else:
            stay += bump
    return {"spark": spark, "ease": ease, "staying": stay}, asp, over


def _z(raw, ref):
    """z -> 0..100. The 16 is a readability choice, not a statistical one: at
    14 the middle 80% of pairs land inside 39-60 and every result reads
    "mixed", which is accurate and useless. This spreads the same ordering
    across enough range to be worth looking at."""
    mean, sd = ref
    return max(2, min(98, round(50 + 16 * ((raw - mean) / sd))))


def score(chart_a, chart_b, time_known_a=True, time_known_b=True):
    raw, asp, over = raw_dims(chart_a, chart_b,
                              angles_a=time_known_a, angles_b=time_known_b)
    ref = REFERENCE[reference_key(time_known_a, time_known_b)]
    dims = {k: _z(raw[k], ref[k]) for k in ("spark", "ease", "staying")}
    overall = round(0.35 * dims["spark"] + 0.35 * dims["ease"]
                    + 0.30 * dims["staying"])

    # A noon chart's Moon is up to ~7° from where it really was, so an orb to
    # it is a precision the data does not have: "their Moon sextile your Sun —
    # 0.4°" for someone saved with no birth time. Those are named without one.
    fuzzy = {("a", "Moon")} if not time_known_a else set()
    fuzzy |= {("b", "Moon")} if not time_known_b else set()
    drivers = [_phrase(x, True, fuzzy) for x in asp if x["harmony"] > 0][:4]
    frictions = [_phrase(x, False, fuzzy) for x in asp if x["harmony"] < 0][:3]

    sun_a = chart_a["planets"]["Sun"]["sign"]
    sun_b = chart_b["planets"]["Sun"]["sign"]

    return {
        "overall": overall,
        "dimensions": dims,
        "band": _band(overall),
        "drivers": drivers,
        "frictions": frictions,
        "overlays": [(f"their {o['planet']} in your {_ord(o['house'])} — {o['label']}"
                      if o.get("whose", "theirs") == "theirs" else
                      f"your {o['planet']} in their {_ord(o['house'])} — {o['label']}")
                     for o in over[:3]],
        "elements": {
            "a": {"sign": sun_a, "element": ELEMENT[sun_a], "modality": MODALITY[sun_a]},
            "b": {"sign": sun_b, "element": ELEMENT[sun_b], "modality": MODALITY[sun_b]},
            "note": _element_note(ELEMENT[sun_a], ELEMENT[sun_b]),
        },
        "contacts": len(asp),
        "confidence": "full" if (time_known_a and time_known_b)
                      else "partial" if time_known_a or time_known_b else "signs only",
    }


def _ord(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1:'st',2:'nd',3:'rd'}.get(n % 10, 'th')}"


def _phrase(x, good, fuzzy=()):
    a, b = plain(x["a"]), plain(x["b"])
    if ("a", x["a"]) in fuzzy or ("b", x["b"]) in fuzzy:
        return f"their {b} {x['aspect']} your {a} — approximate, birth time unknown"
    return (f"their {b} {x['aspect']} your {a}"
            f" — {x['orb']:.1f}°")


def _band(n):
    """Cut against the reference distribution: 50 is an ordinary pair, so
    "rare" has to actually be rare."""
    if n >= 72: return "rare"
    if n >= 62: return "strong"
    if n >= 54: return "promising"
    if n >= 46: return "mixed"
    if n >= 38: return "effortful"
    return "hard going"


_ELEM_NOTE = {
    ("fire", "fire"): "You run on the same fiery energy: things move fast, and neither of you slows the other down.",
    ("fire", "air"): "Your energies feed each other, so things get moving easily — though neither of you naturally slows down to plan.",
    ("fire", "earth"): "One of you wants to act now and the other wants a plan first. That tension can actually be useful.",
    ("fire", "water"): "Passion meets deep feeling, which makes things intense and hard to keep steady.",
    ("earth", "earth"): "You're both steady, sometimes to the point of getting stuck, but there's very little drama.",
    ("earth", "water"): "Your feelings and your practical sides support each other, which quietly makes this one of the more lasting combinations.",
    ("earth", "air"): "One of you thinks in ideas and the other wants to build something real, so you tend to argue about pace.",
    ("air", "air"): "You can talk for hours and decide nothing. It's delightful, but not very grounded.",
    ("air", "water"): "One of you leads with feelings and the other with analysis, so you both have to explain yourselves in the other's terms.",
    ("water", "water"): "You understand each other's feelings without explaining, but nobody is there to cool things down when emotions run high.",
}


def _element_note(a, b):
    return _ELEM_NOTE.get((a, b)) or _ELEM_NOTE.get((b, a), "")


def chart_from_birth(birth_ut):
    """birth_ut is the profile's stored [y, m, d, hour_ut, lat, lon]."""
    return natal(*birth_ut)


# ------------------------------------------------------------- comparison
# Saving one person is a party trick. Comparing the people you have saved is
# the thing this app is for, and it is where the temptation to overclaim is
# strongest — a ranked list looks authoritative whether or not the gaps mean
# anything.
#
# Measured: a single aspect contact moves a dimension by 6 to 9 points on the
# 0-100 scale. A gap smaller than that cannot correspond to even one different
# contact between two people, so it is not a difference. It is the same
# instrument reading the same thing twice.

RESOLUTION = {"spark": 6, "ease": 6, "staying": 9, "overall": 7}

CONFIDENCE_RANK = {"full": 2, "partial": 1, "signs only": 0}

DIMENSION_MEANS = {
    "spark": "attraction and charge",
    "ease": "how simple it is day to day",
    "staying": "whether it holds up over time",
}


def _rank(rows, key, resolution):
    """
    Rank, collapsing anything inside the instrument's resolution into a tie.

    Returns [{names, value, position}], best first — a group per distinguishable
    level rather than a strict ordering that implies precision we do not have.
    """
    ordered = sorted(rows, key=lambda r: -r[key])
    groups, current = [], []
    for row in ordered:
        if current and (current[0][key] - row[key]) >= resolution:
            groups.append(current)
            current = []
        current.append(row)
    if current:
        groups.append(current)
    return [{"position": i + 1,
             "names": [r["name"] for r in g],
             "values": [r[key] for r in g],
             "tied": len(g) > 1}
            for i, g in enumerate(groups)]


def compare(connections):
    """
    Compare everyone she has saved.

    `connections` are stored rows: name, overall, dimensions, confidence.
    """
    rows = [c for c in connections if c.get("dimensions")]
    if len(rows) < 2:
        return {"ok": False, "reason": "needs at least two people",
                "have": len(rows)}

    flat = [{"name": c["name"], "relation": c.get("relation"),
             "confidence": c.get("confidence", "full"),
             "overall": c["overall"], **c["dimensions"]} for c in rows]

    # Someone whose birth time is unknown is not measured on the same
    # instrument as someone whose is. Ranking them together silently is the
    # comparison equivalent of inventing a rising sign.
    confidences = {r["confidence"] for r in flat}
    mixed = len(confidences) > 1

    out = {
        "ok": True,
        "people": len(flat),
        "overall": _rank(flat, "overall", RESOLUTION["overall"]),
        "dimensions": {d: _rank(flat, d, RESOLUTION[d])
                       for d in ("spark", "ease", "staying")},
        "resolution": RESOLUTION,
        "mixed_confidence": mixed,
        "confidence_note": (
            "Some of these were scored without a birth time, which changes what "
            "can be measured. Treat the ranking as rough where that is true."
            if mixed else None),
    }

    # What each person is actually best at, relative to the others — only where
    # the gap clears resolution.
    strengths = {}
    for dim in ("spark", "ease", "staying"):
        groups = out["dimensions"][dim]
        if len(groups) < 2:
            continue                              # everyone level on this one
        top = groups[0]
        for name in top["names"]:
            strengths.setdefault(name, []).append(
                {"dimension": dim, "means": DIMENSION_MEANS[dim],
                 # Shared means tied at the top, not uniquely ahead. Saying
                 # someone is "best at" something they tie on is the kind of
                 # small dishonesty that adds up.
                 "shared_with": [n for n in top["names"] if n != name]})
    out["strongest_at"] = strengths

    # The honest headline.
    best = out["overall"][0]
    if len(out["overall"]) == 1:
        out["verdict"] = ("These are level. The differences between them are "
                          "smaller than this can actually measure.")
    elif best["tied"]:
        out["verdict"] = (f"{' and '.join(best['names'])} are level at the top — "
                          f"genuinely, not diplomatically.")
    else:
        second = out["overall"][1]["names"][0]
        gap = best["values"][0] - out["overall"][1]["values"][0]
        out["verdict"] = (f"{best['names'][0]} scores highest, {gap} points clear "
                          f"of {second}. That gap is real; anything under "
                          f"{RESOLUTION['overall']} would not be.")
    return out
