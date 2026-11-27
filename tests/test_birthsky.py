"""
The richer reading: the sky at birth, and Venus, Mars and Mercury by sign.

Astronomy facts are checked against things that are independently known —
famous eclipses, sunrise tables, the planetary-hour arithmetic — so the
reading can say them as plain fact.
"""
import json
import pathlib

import pytest

from engine import birthsky, context, personal, reading
from engine.chart import SIGNS, natal

CHICAGO_1999 = [1999, 3, 14, 10.7, 41.8781, -87.6298]       # 04:42 CST, before sunrise
LONDON_2003 = [2003, 7, 2, 15.25, 51.5074, -0.1278]         # 16:15 BST, afternoon


def facts(bu, tz, known=True):
    return birthsky.compute(bu, natal(*bu), known, tz)


# ------------------------------------------------------------- personal


@pytest.mark.parametrize("table", [personal.VENUS, personal.MARS, personal.MERCURY])
def test_every_sign_has_its_own_text(table):
    assert set(table) == set(SIGNS)
    bodies = [b for _t, b in table.values()]
    assert len(set(bodies)) == 12, "two signs share a body"


def test_venus_mars_mercury_never_share_a_sentence():
    """The bug this replaces: Venus in Aries and Mars in Aries said the same thing."""
    for sign in SIGNS:
        texts = {personal.VENUS[sign][1], personal.MARS[sign][1], personal.MERCURY[sign][1]}
        assert len(texts) == 3, sign


def test_cards_hide_houses_without_a_birth_time():
    cards = personal.cards(natal(*CHICAGO_1999), time_known=False)
    assert [c["planet"] for c in cards] == ["Venus", "Mars", "Mercury"]
    assert not any("arena" in c for c in cards)


# ---------------------------------------------------------- the birth sky


def test_eclipses_match_the_ones_history_recorded():
    """16 Feb 1999 annular and 11 Aug 1999 total (the great European eclipse)
    bracket a March 1999 birth; 31 May 2003 annular precedes a July 2003 one."""
    e = facts(CHICAGO_1999, "America/Chicago")["eclipses"]
    assert (e["solar_before"]["date"], e["solar_before"]["type"]) == ("1999-02-16", "annular")
    assert (e["solar_after"]["date"], e["solar_after"]["type"]) == ("1999-08-11", "total")
    assert e["lunar_before"]["date"] == "1999-01-31"
    e = facts(LONDON_2003, "Europe/London")["eclipses"]
    assert (e["solar_before"]["date"], e["solar_before"]["type"]) == ("2003-05-31", "annular")


def test_sunrise_sect_and_the_planetary_hour():
    """04:42 on Sunday 14 March 1999 in Chicago is before sunrise, so it belongs
    to Saturday's planetary day — and by Chaldean order the 11th night hour of
    a Saturday is Jupiter's."""
    s = facts(CHICAGO_1999, "America/Chicago")["sun"]
    assert s["sect"] == "night" and s["sunrise"].startswith("06:0")
    assert s["planetary_day"] == "Saturn"
    assert (s["hour_of"], s["hour_number"], s["planetary_hour"]) == ("night", 11, "Jupiter")
    s = facts(LONDON_2003, "Europe/London")["sun"]
    # 2 July 2003 was a Wednesday, which is Mercury's day.
    assert s["sect"] == "day" and s["planetary_day"] == "Mercury"


def test_a_birth_without_a_time_gets_no_hour_and_no_lots():
    f = facts(CHICAGO_1999, "America/Chicago", known=False)
    assert "sun" not in f and "lots" not in f and "overhead" not in f
    assert f["moon"]["phase"] and f["eclipses"]


def test_the_descriptions_read_as_sentences():
    for bu, tz in ((CHICAGO_1999, "America/Chicago"), (LONDON_2003, "Europe/London")):
        items = birthsky.describe(facts(bu, tz))
        kinds = [i["kind"] for i in items]
        assert {"light", "moon", "venus", "eclipse"} <= set(kinds)
        for i in items:
            assert " a annular" not in i["body"] and " a eclipse" not in i["body"]
            assert i["body"].endswith(".")


def test_degree_labels_never_show_sixty_minutes():
    assert birthsky._sign(4.9999)["label"] == "5°00' Aries"
    assert birthsky._sign(29.9999)["label"] == "29°59' Aries"


def test_the_reading_and_the_briefing_carry_it():
    n = natal(*CHICAGO_1999)
    r = reading.build(n, want_prose=False, birth_ut=CHICAGO_1999, tz="America/Chicago")
    assert r["personal"] and r["sky"]
    lines = context.birth_sky_lines(n, {"birth_ut": CHICAGO_1999, "tz": "America/Chicago",
                                        "birth_time_known": True})
    block = "\n".join(lines)
    assert "planetary day of Saturn" in block and "VENUS — how you love" in block


# ------------------------------------------------- against NASA's catalogue
# engine/reference/eclipses.json: greatest eclipse in UT from NASA GSFC's Five
# Millennium Canon. It ships, so /api/accuracy reruns the same comparison live.
_REF = json.loads((pathlib.Path(__file__).resolve().parent.parent
                    / "engine" / "reference" / "eclipses.json").read_text())
NASA_SOLAR = [(e["ut"], e["type"]) for e in _REF["solar"]]
NASA_LUNAR = [(e["ut"], e["type"]) for e in _REF["lunar"]]


def _jd(stamp):
    import datetime as dt

    import swisseph as swe
    t = dt.datetime.fromisoformat(stamp)
    return swe.julday(t.year, t.month, t.day, t.hour + t.minute / 60 + t.second / 3600)


@pytest.mark.parametrize("stamp,kind", NASA_SOLAR)
def test_solar_eclipses_match_nasa(stamp, kind):
    import swisseph as swe

    from engine.ephemeris import BASE
    nasa = _jd(stamp)
    flag, t = swe.sol_eclipse_when_glob(nasa - 10, BASE, 0, False)
    off_s = abs(t[0] - nasa) * 86400
    assert off_s < 10, f"{stamp}: {off_s:.0f}s from NASA's greatest eclipse"
    assert birthsky._kind(flag, birthsky._SOLAR) == kind


@pytest.mark.parametrize("stamp,kind", NASA_LUNAR)
def test_lunar_eclipses_match_nasa(stamp, kind):
    import swisseph as swe

    from engine.ephemeris import BASE
    nasa = _jd(stamp)
    flag, t = swe.lun_eclipse_when(nasa - 10, BASE, 0, False)
    off_s = abs(t[0] - nasa) * 86400
    assert off_s < 10, f"{stamp}: {off_s:.0f}s from NASA's greatest eclipse"
    assert birthsky._kind(flag, birthsky._LUNAR) == kind


# ------------------------------------------------------------- classical


def test_every_classical_item_cites_its_source():
    from engine import classical
    for bu, tz in ((CHICAGO_1999, "America/Chicago"), (LONDON_2003, "Europe/London")):
        n = natal(*bu)
        items = classical.items(n, birthsky.compute(bu, n, True, tz), True)
        assert len(items) >= 5
        for i in items:
            assert i["source"] and ("Ptolemy" in i["source"] or "Lilly" in i["source"]
                                    or "Robson" in i["source"]), i


def test_fortune_is_ptolemys_formula_by_day_and_night():
    """Ptolemy III.13: Asc + Moon − Sun 'in all cases, both by night and day'."""
    n = natal(*CHICAGO_1999)
    want = (n["angles"]["Ascendant"]["lon"] + n["planets"]["Moon"]["lon"]
            - n["planets"]["Sun"]["lon"]) % 360
    for sect in ("day", "night"):
        assert abs(birthsky.lots(n, sect)["Fortune"]["lon"] - want) < 1e-3
    assert "Spirit" not in birthsky.lots(n)


def test_ptolemy_is_never_cited_for_planets_he_never_saw():
    from engine import classical
    sky = {"stations": [{"planet": "Pluto", "turning": "retrograde", "days_from_birth": 0}],
           "venus_mercury": {"Venus": {"in_suns_glare": True}, "Mercury": {"in_suns_glare": True}},
           "moon": {"elongation": 10.0}, "stars": [], "syzygy": None}
    items = classical.items(natal(*CHICAGO_1999), sky, False)
    assert not any(i["kind"] == "station" for i in items)


def test_temperament_counts_the_votes():
    from engine import classical
    t = classical.temperament(natal(*CHICAGO_1999), 353.4, 317.0, True)
    assert t["temperament"] in ("choleric", "sanguine", "melancholic", "phlegmatic")
    assert len(t["votes"]) == 5
    t = classical.temperament(natal(*CHICAGO_1999), 353.4, 317.0, False)
    assert len(t["votes"]) == 3      # no rising sign, no ruler of it
