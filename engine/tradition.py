"""
Which astrology this is.

The engine had taken positions on contested questions without anyone qualified
making the call: Placidus houses, traditional rulerships, the five Ptolemaic
aspects. Those choices were made by writing code, which is the wrong way to make
them — and switching later meant a rewrite, which is the wrong reason not to.

They are configuration now. The stakes, measured on the fixture chart:

    Placidus     Moon 12th, Uranus 12th, Neptune 12th   — solitude, memory
    whole sign   Moon  1st, Uranus  1st, Neptune  1st   — how you come across

**Every one of that chart's ten placements changes house between the two.** The
strongest answer the voice test produced rested on that 12th house; under whole
sign it would be wrong. This is not a preference toggle, it is the single
decision that most changes what the app says, which is exactly why it belongs in
one place with the evidence next to it.

Default stays Placidus — the modern Western default and what everything so far
was built and calibrated against. An astrologer may well say whole sign; when
they do, it is a setting, and `app/server.py` invalidates the caches that depend
on it.
"""
import os

# Swiss Ephemeris house-system codes.
HOUSE_SYSTEMS = {
    "placidus": dict(code=b"P", label="Placidus",
                     note="Modern Western default. Unequal houses derived from "
                          "the time it takes each degree to rise. Distorts badly "
                          "at high latitudes."),
    "whole_sign": dict(code=b"W", label="whole sign",
                       note="The Hellenistic system, and increasingly the modern "
                            "one. The rising sign is the whole 1st house. Robust "
                            "at any latitude."),
    "equal": dict(code=b"E", label="equal",
                  note="Thirty degrees each from the ascendant. A middle path; "
                       "keeps the ASC as the 1st cusp without Placidus's "
                       "latitude problems."),
    "koch": dict(code=b"K", label="Koch",
                 note="Common in 20th-century European practice. Same high-"
                      "latitude failure as Placidus."),
}

# Traditional gives Mars to Scorpio, Saturn to Aquarius, Jupiter to Pisces.
# Modern hands those to Pluto, Uranus and Neptune. It decides the chart ruler,
# and it should agree with whatever the corpus voice is.
RULERSHIPS = {
    "traditional": {"Aries": "Mars", "Taurus": "Venus", "Gemini": "Mercury",
                    "Cancer": "Moon", "Leo": "Sun", "Virgo": "Mercury",
                    "Libra": "Venus", "Scorpio": "Mars", "Sagittarius": "Jupiter",
                    "Capricorn": "Saturn", "Aquarius": "Saturn", "Pisces": "Jupiter"},
    "modern": {"Aries": "Mars", "Taurus": "Venus", "Gemini": "Mercury",
               "Cancer": "Moon", "Leo": "Sun", "Virgo": "Mercury",
               "Libra": "Venus", "Scorpio": "Pluto", "Sagittarius": "Jupiter",
               "Capricorn": "Saturn", "Aquarius": "Uranus", "Pisces": "Neptune"},
}

DEFAULT_HOUSES = "placidus"
DEFAULT_RULERS = "traditional"


def house_system(name=None):
    key = (name or os.environ.get("MYSIGN_HOUSE_SYSTEM") or DEFAULT_HOUSES).lower()
    if key not in HOUSE_SYSTEMS:
        raise ValueError(f"unknown house system {key!r}; "
                         f"choose from {sorted(HOUSE_SYSTEMS)}")
    return key


def house_code(name=None):
    return HOUSE_SYSTEMS[house_system(name)]["code"]


def rulerships(name=None):
    key = (name or os.environ.get("MYSIGN_RULERSHIPS") or DEFAULT_RULERS).lower()
    if key not in RULERSHIPS:
        raise ValueError(f"unknown rulership scheme {key!r}; "
                         f"choose from {sorted(RULERSHIPS)}")
    return RULERSHIPS[key]


def houses_label(houses=None, chart=None):
    """The house system a chart was actually drawn in, in words. Inside the
    polar circles Placidus and Koch cannot be drawn and engine/chart.natal
    falls back to Porphyry; this says so instead of "Placidus"."""
    used = (chart or {}).get("houses_used") or {}
    if used.get("fallback"):
        return (f"{used['label']} (Placidus can't be drawn at this latitude)"
                if used.get("requested") == "placidus" else
                f"{used['label']} ({HOUSE_SYSTEMS[used['requested']]['label']} "
                "can't be drawn at this latitude)")
    return HOUSE_SYSTEMS[house_system(houses)]["label"]


def describe(houses=None, rulers=None, chart=None):
    h = house_system(houses)
    r = (rulers or os.environ.get("MYSIGN_RULERSHIPS") or DEFAULT_RULERS).lower()
    used = (chart or {}).get("houses_used") or {}
    fallback = bool(used.get("fallback"))
    return {"houses": h, "houses_label": houses_label(h, chart),
            "houses_note": HOUSE_SYSTEMS[h]["note"], "rulerships": r,
            # Set when her chart could not be drawn in the chosen system.
            "houses_fallback": ({"used": used["system"], "why": used["why"]}
                                if fallback else None),
            "coherent": (h, r) in (("whole_sign", "traditional"),
                                   ("placidus", "modern")),
            "note": "whole sign pairs with traditional rulerships; Placidus grew "
                    "up alongside the modern ones. Mixing them is common in "
                    "practice and worth a deliberate answer rather than a "
                    "default."}
