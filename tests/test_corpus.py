"""
The interpretation corpus.

The dignity grid is checked against traditional doctrine in full rather than
spot-checked, because it is the one table here where "wrong" is objectively
wrong — a practitioner would catch a bad cell immediately, and it is exactly the
kind of error that makes an app look like it does not know the subject.
"""
import itertools
import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-corpus-"))

from engine import corpus as C

# Traditional essential dignities. Every non-empty cell, written out.
EXPECTED = {
    ("Sun", "Leo"): "domicile", ("Sun", "Aries"): "exalted",
    ("Sun", "Aquarius"): "detriment", ("Sun", "Libra"): "fall",
    ("Moon", "Cancer"): "domicile", ("Moon", "Taurus"): "exalted",
    ("Moon", "Capricorn"): "detriment", ("Moon", "Scorpio"): "fall",
    ("Mercury", "Gemini"): "domicile", ("Mercury", "Virgo"): "domicile",
    ("Mercury", "Sagittarius"): "detriment", ("Mercury", "Pisces"): "detriment",
    ("Venus", "Taurus"): "domicile", ("Venus", "Libra"): "domicile",
    ("Venus", "Pisces"): "exalted", ("Venus", "Scorpio"): "detriment",
    ("Venus", "Aries"): "detriment", ("Venus", "Virgo"): "fall",
    ("Mars", "Aries"): "domicile", ("Mars", "Scorpio"): "domicile",
    ("Mars", "Capricorn"): "exalted", ("Mars", "Libra"): "detriment",
    ("Mars", "Taurus"): "detriment", ("Mars", "Cancer"): "fall",
    ("Jupiter", "Sagittarius"): "domicile", ("Jupiter", "Pisces"): "domicile",
    ("Jupiter", "Cancer"): "exalted", ("Jupiter", "Gemini"): "detriment",
    ("Jupiter", "Virgo"): "detriment", ("Jupiter", "Capricorn"): "fall",
    ("Saturn", "Capricorn"): "domicile", ("Saturn", "Aquarius"): "domicile",
    ("Saturn", "Libra"): "exalted", ("Saturn", "Cancer"): "detriment",
    ("Saturn", "Leo"): "detriment", ("Saturn", "Aries"): "fall",
}


@pytest.mark.parametrize("cell,want", sorted(EXPECTED.items()))
def test_the_dignity_grid_matches_doctrine(cell, want):
    planet, sign = cell
    assert C.dignity(planet, sign) == want, f"{planet} in {sign}"


def test_no_cell_claims_a_dignity_that_is_not_real():
    """The inverse — the grid must not invent dignities in empty cells."""
    for planet in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn"):
        for sign in C.SIGNS:
            got = C.dignity(planet, sign)
            want = EXPECTED.get((planet, sign))
            assert got == want, f"{planet} in {sign}: got {got}, expected {want}"


def test_modern_planets_have_no_traditional_dignity():
    for planet in ("Uranus", "Neptune", "Pluto"):
        assert all(C.dignity(planet, s) is None for s in C.SIGNS)


# -------------------------------------------------------------- composition

def test_every_planet_sign_house_combination_composes():
    missing = [(p, s, h) for p in C.PLANETS for s in C.SIGNS for h in C.HOUSES
               if C.placement(p, s, h) is None]
    assert not missing, f"{len(missing)} gaps, e.g. {missing[:3]}"


def test_every_aspect_pair_composes():
    planets = list(C.PLANETS)
    missing = [(a, b, x) for a, b in itertools.combinations(planets, 2)
               for x in C.ASPECTS if C.aspect_between(a, b, x) is None]
    assert not missing, f"{len(missing)} gaps"


def test_every_transit_composes():
    missing = [(t, n, x) for t in C.PLANETS for n in C.PLANETS for x in C.ASPECTS
               if C.transit(t, n, x) is None]
    assert not missing, f"{len(missing)} gaps"


def test_every_synastry_overlay_composes():
    missing = [(p, h) for p in C.PLANETS for h in C.HOUSES
               if C.synastry(p, h) is None]
    assert not missing


def test_a_planet_cannot_aspect_itself():
    assert C.aspect_between("Mars", "Mars", "square") is None


def test_the_corpus_is_bigger_than_what_it_replaced():
    """It was 60 entries. The gap to a usable corpus was the point."""
    cov = C.coverage()
    total = sum(v for k, v in cov.items() if k != "dignities")
    assert total > 1000, f"only {total} combinations"
    assert cov["dignities"] == len(EXPECTED)


def test_composed_sentences_agree_grammatically():
    """Keywords are singular nouns so "Your X works ..." cannot disagree."""
    for planet in C.PLANETS:
        first = C.placement(planet, "Taurus", 3)["reading"].split(".")[0]
        assert " works " in first, first


def test_dignity_changes_what_a_placement_says():
    exalted = C.placement("Saturn", "Libra", 7)
    fallen = C.placement("Saturn", "Aries", 7)
    assert exalted["dignity"] == "exalted" and fallen["dignity"] == "fall"
    assert exalted["reading"] != fallen["reading"]
    assert "real strengths" in exalted["reading"]
    assert "awkwardly" in fallen["reading"]


# ------------------------------------------------------------------ wiring

def test_the_daily_context_carries_meaning_not_just_orbs():
    import datetime as dt

    from engine import context
    from engine.chart import SIGNS, aspects, house_of, jd_ut, moon_phase, natal, positions
    from engine.store import save

    nat = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)
    save("corpus-ctx", "profile", {"name": "Maya", "people": [],
                                   "birth_ut": [1999, 3, 14, 10.7, 41.8781, -87.6298]})
    day = dt.date(2026, 8, 22)
    j = jd_ut(day.year, day.month, day.day, 17.0)
    tp = positions(j)
    sky = {"aspects_to_natal": aspects(tp, nat), "moon": moon_phase(j),
           "moon_sign": SIGNS[int(tp["Moon"]["lon"] // 30)],
           "active_houses": sorted({house_of(v["lon"], nat["house_cusps"])
                                    for v in tp.values()}), "upcoming": []}
    block, _ = context.build("corpus-ctx", nat, sky, {"mood": 4, "text": ""}, today=day)
    assert any(v["verb"] + " your" in block for v in C.TRANSIT_ASPECTS.values()), \
        "the context still hands over bare orbs"


def test_the_reading_reports_dignity():
    from engine import reading
    from engine.chart import natal
    r = reading.build(natal(1999, 3, 14, 10.7, 41.8781, -87.6298), name="Maya")
    # About her in the title; the placement is named in the small print.
    because = " ".join(x.get("because", "") for x in r["standout"])
    titles = [x["title"] for x in r["standout"]]
    # Mars in Scorpio is domicile, Venus in Aries is detriment
    assert "Mars in Scorpio" in because or "Venus in Aries" in because
    assert "What comes naturally to you" in titles or "Where you work harder than people see" in titles


def test_the_honesty_card_is_never_cut_by_the_cap():
    """
    Adding dignity findings pushed "What I can't tell you yet" out of the top
    four on a no-birth-time chart — the one card explaining why the houses are
    absent. It outranks everything now.
    """
    from engine import reading
    from engine.chart import natal
    r = reading.build(natal(1999, 3, 14, 18.0, 41.8781, -87.6298),
                      name="Nia", time_known=False, birth_ymd=(1999, 3, 14))
    titles = [x["title"] for x in r["standout"]]
    assert "What I can't tell you yet" in titles
    assert titles[0] == "What I can't tell you yet", titles


def test_transit_wording_is_not_natal_wording():
    """'Mars is meeting your identity — fused, so they never operate separately
    in you' was a natal sentence on a passing transit, and a square still said
    'meeting' (audit MEDIUM-5)."""
    natal = {a["quality"] for a in C.ASPECTS.values()}
    for asp in C.ASPECTS:
        r = C.transit("Mars", "Sun", asp)
        assert r["quality"] not in natal and "never operate separately" not in r["reading"]
    assert C.transit("Saturn", "Venus", "square")["mechanism"].startswith("Saturn is pressing on your")
    assert "meeting" not in C.transit("Saturn", "Venus", "square")["mechanism"]


def test_a_transit_reading_is_about_her_life_first():
    """She reads about herself, then why: the sentence names a part of her
    life, and the planets travel separately as the mechanism."""
    r = C.transit("Saturn", "Moon", "square")
    assert r["reading"].startswith("Things around your heart")
    assert "Saturn" not in r["reading"] and "Saturn" in r["mechanism"]
    for mover in C.YOUR_DAY:
        for point in C.YOUR_AREA:
            for asp in ("conjunction", "trine", "sextile", "square", "opposition"):
                text = C.transit_for_you(mover, point, asp)
                assert text and text[0].isupper() and "{" not in text and "  " not in text
