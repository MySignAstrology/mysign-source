"""
The line of the day (engine/lines.py) — the first thing she reads.

The copy is the product here, so these pin the rules the copy promises:
short, grounded in a real transit, stable for the day, fresh tomorrow.
"""
import re

from engine import lines

SUN_VENUS = [{"transit": "Sun", "aspect": "conjunction", "natal": "Venus", "orb": 1.4}]


def every_line():
    for table in lines.LINES.values():
        for options in table.values():
            yield from options
    for options in lines.OTHER.values():
        yield from options
    for options in lines.MOON.values():
        yield from options
    yield from lines.QUIET


def test_every_table_has_three_moods_and_three_lines():
    for point, table in list(lines.LINES.items()) + [("other", lines.OTHER)]:
        assert set(table) == {lines.FLOW, lines.TENSE, lines.FUSE}, point
        for tone, options in table.items():
            assert len(options) == 3, (point, tone)
            assert len(set(options)) == 3, (point, tone)


def test_lines_are_short_typeset_and_never_a_question():
    for text in every_line():
        assert len(text) <= 70, text
        assert "'" not in text, f"straight apostrophe: {text}"
        assert not text.endswith("?"), text
        assert text[0].isupper() and text[-1] in ".!", text


def test_no_line_is_a_sign_trait_or_a_prediction():
    # An invitation about today — never "you are a Scorpio, you are X", and
    # never a promise that an event will happen.
    banned = re.compile(r"\b(Aries|Taurus|Gemini|Cancer|Leo|Virgo|Libra|Scorpio|Sagittarius|"
                        r"Capricorn|Aquarius|Pisces|will meet|will happen|destined)\b", re.I)
    for text in every_line():
        assert not banned.search(text), text


def test_grounded_in_the_strongest_transit():
    out = lines.line(SUN_VENUS, "2026-10-28", "u1")
    assert out["text"] in lines.LINES["Venus"][lines.FUSE]
    assert lines.sky_of(out["why"]) == "The Sun is meeting your Venus" and out["why"].startswith("Because today")


def test_tone_follows_the_aspect():
    sq = [{"transit": "Mars", "aspect": "square", "natal": "Moon"}]
    tri = [{"transit": "Jupiter", "aspect": "trine", "natal": "Moon"}]
    assert lines.line(sq, "2026-10-28", "u")["text"] in lines.LINES["Moon"][lines.TENSE]
    assert lines.line(tri, "2026-10-28", "u")["text"] in lines.LINES["Moon"][lines.FLOW]


def test_points_without_a_table_and_plain_names():
    out = lines.line([{"transit": "Pluto", "aspect": "opposition", "natal": "Neptune"}], "2026-10-28", "u")
    assert out["text"] in lines.OTHER[lines.TENSE]
    asc = lines.line([{"transit": "Mars", "aspect": "opposition", "natal": "Ascendant"}], "2026-10-28", "u")
    assert lines.sky_of(asc["why"]) == "Mars is pulling against your rising sign" and asc["why"].startswith("Because today")


def test_stable_within_a_day_and_varied_across_days():
    same = {lines.line(SUN_VENUS, "2026-10-28", "u1")["text"] for _ in range(5)}
    assert len(same) == 1
    week = {lines.line(SUN_VENUS, f"2026-11-{d:02d}", "u1")["text"] for d in range(1, 15)}
    assert len(week) == 3, "a fortnight of the same transit should use all three lines"


def test_quiet_days_get_a_quiet_line():
    """No sign known: the generic quiet copy, still with a reason."""
    out = lines.line([], "2026-10-28", "u1", quiet=True)
    assert out["text"] in lines.QUIET and out["why"]


def test_a_day_with_nothing_in_orb_is_carried_by_the_moon():
    """It used to say "Nothing's pulling at you today" — no affirmation at all."""
    out = lines.line([], "2026-10-28", "u1", quiet=True, moon_sign="Libra")
    assert out["text"] in lines.MOON["Libra"]
    assert out["why"].startswith("Because the Moon is in Libra")


def test_a_low_day_with_transits_still_uses_them():
    """quiet=True came from band == "low", about 40% of days — and hid real
    transits behind a line that said nothing was happening."""
    out = lines.line(SUN_VENUS, "2026-10-28", "u1", quiet=True, moon_sign="Libra")
    assert out["text"] in lines.LINES["Venus"][lines.FUSE]
    assert lines.sky_of(out["why"]) == "The Sun is meeting your Venus" and out["why"].startswith("Because today")


def test_every_moon_sign_has_three_lines():
    assert len(lines.MOON) == 12
    for sign, options in lines.MOON.items():
        assert len(set(options)) == 3, sign


def test_general_pool_keeps_the_copy_rules():
    for options in lines.GENERAL.values():
        assert len(set(options)) == len(options) >= 8
        for text in options:
            assert len(text) <= 70 and "'" not in text and not text.endswith("?"), text


def test_a_slow_transit_no_longer_repeats_every_third_day(tmp_path, monkeypatch):
    """Pluto square her Midheaven for months: 'The goal is right. The route
    needs adjusting.' came back about every third day (audit MEDIUM-7)."""
    import datetime as dt

    from engine import store
    monkeypatch.setattr(store, "ROOT", tmp_path)
    head = [{"transit": "Pluto", "aspect": "square", "natal": "Midheaven", "orb": 0.3}]
    start = dt.date(2026, 10, 1)
    shown = [lines.for_user(head, (start + dt.timedelta(i)).isoformat(), "u-slow")["text"]
             for i in range(40)]
    pool = len(lines._candidates(head, None)[0]) + len(lines.GENERAL[lines.TENSE])
    for i, t in enumerate(shown):
        assert t not in shown[max(0, i - pool + 1):i], (i, t)
    # Stable for the day, wherever it is asked from.
    again = lines.for_user(head, start.isoformat(), "u-slow")
    assert again["text"] == shown[0] and lines.sky_of(again["why"]) == "Pluto is pressing on your Midheaven"


def test_the_next_transits_lines_are_used_before_repeating():
    import datetime as dt  # noqa: F401
    heads = [{"transit": "Pluto", "aspect": "square", "natal": "Midheaven"},
             {"transit": "Moon", "aspect": "trine", "natal": "Venus"}]
    groups = lines._candidates(heads, None)
    assert lines.sky_of(groups[1][0][1]) == "The Moon is flowing to your Venus"
