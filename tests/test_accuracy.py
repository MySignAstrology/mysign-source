"""
The claims the accuracy page makes, each held to a test.

If one of these fails, the page is saying something untrue — fix the engine or
change the page, never loosen the number here to make it pass.
"""
import datetime as dt
import json

import pytest

from engine import accuracy, ephemeris, grounding, localtime
from engine.chart import aspects, natal, positions

# ------------------------------------------------------------ against NASA


def test_the_full_ephemeris_is_installed():
    """Without the data files the engine silently drops to Moshier and loses
    Chiron. The page says full precision, so that has to be true."""
    assert ephemeris.HAVE_FILES, "engine/ephe/*.se1 missing — positions are low precision"
    assert ephemeris.SOURCE["full_precision"]


def test_every_body_agrees_with_nasa_jpl_horizons_to_an_arcsecond():
    """Sun through Pluto and Chiron, 24 instants from 1955 to 2035, against
    positions fetched from NASA JPL Horizons (engine/reference/horizons.json)."""
    rep = accuracy.verify_against_nasa()
    assert rep["comparisons"] == 24 * 11
    assert set(rep["per_body"]) == {"Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter",
                                    "Saturn", "Uranus", "Neptune", "Pluto", "Chiron"}
    worst = max(rep["per_body"].values())
    assert worst < accuracy.CLAIM_ARCSEC, f"worst disagreement {worst}\" vs NASA"
    assert rep["max_arcsec"] == worst


def test_full_precision_holds_on_a_worker_thread():
    """
    The server answers on worker threads, and on Linux Swiss Ephemeris keeps its
    file path per thread. The first CI run of /accuracy got a 500 from exactly
    this; pytest had passed because everything ran on the main thread. Compute
    from a fresh thread, uncached, and check the files were actually used.
    """
    import threading

    import swisseph as swe
    out = {}

    def work():
        try:
            _, flag = swe.calc_ut(2451545.0, swe.CHIRON, ephemeris.FLAGS)
            out["flag"] = flag
            out["report"] = accuracy.verify_against_nasa.__wrapped__()
        except Exception as e:
            out["error"] = repr(e)

    t = threading.Thread(target=work)
    t.start()
    t.join()
    assert "error" not in out, out.get("error")
    assert out["flag"] & swe.FLG_SWIEPH, "worker thread fell back to Moshier"
    assert out["report"]["passes"]


def test_chiron_is_in_every_chart_now():
    nat = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)
    assert "Chiron" in nat["planets"]
    assert nat["planets"]["Chiron"]["house"] in range(1, 13)


# --------------------------------------------------- applying / separating


def _transit(lon, speed):
    return {"X": {"lon": lon, "speed": speed, "retrograde": speed < 0}}


def _natal_at(lon):
    return {"planets": {"P": {"lon": lon}}, "angles": {}}


@pytest.mark.parametrize("lon,target,speed,applying", [
    (98.0, 10.0, 1.0, True),     # 2° short of a square, moving toward it
    (102.0, 10.0, 1.0, False),   # 2° past it, moving away
    (102.0, 10.0, -0.5, True),   # past it but retrograde — backing onto the point
    (358.5, 0.0, 1.0, True),     # closing a conjunction across 0° Aries
])
def test_applying_follows_the_real_motion(lon, target, speed, applying):
    hits = aspects(_transit(lon, speed), _natal_at(target))
    assert hits, "no aspect found"
    assert hits[0]["applying"] is applying


def test_live_transits_carry_a_phase():
    nat = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)
    hits = aspects(positions(2461340.5), nat)
    assert hits and all(isinstance(a["applying"], bool) for a in hits)


# ------------------------------------------------------------ clock changes


def test_a_repeated_hour_is_flagged_with_the_other_instant():
    # 2001-10-28 01:30 in Chicago happened twice (CDT, then CST) — before 2007
    # the US went back on the last Sunday of October.
    utc, issue = localtime.resolve(2001, 10, 28, 1, 30, "America/Chicago")
    assert issue["kind"] == "repeated" and issue["assumed"] == "first"
    assert utc == dt.datetime(2001, 10, 28, 6, 30, tzinfo=dt.timezone.utc)
    assert issue["other_utc"] - utc == dt.timedelta(hours=1)


def test_the_second_occurrence_can_be_chosen():
    utc, issue = localtime.resolve(2001, 10, 28, 1, 30, "America/Chicago", fold=1)
    assert utc.hour == 7 and issue["assumed"] == "second"


def test_a_skipped_hour_is_flagged():
    # 2001-04-01 02:30 never appeared on a Chicago clock.
    _utc, issue = localtime.resolve(2001, 4, 1, 2, 30, "America/Chicago")
    assert issue["kind"] == "skipped"
    assert "never appeared" in localtime.describe(issue)


def test_an_ordinary_time_is_left_alone():
    utc, issue = localtime.resolve(1999, 3, 14, 4, 42, "America/Chicago")
    assert issue is None and utc.hour == 10


# ------------------------------------------------------------- grounding


CTX = "\n".join([
    "  Venus 12°04' Scorpio — 8th house",
    "  Moon 3°10' Pisces — 12th house",
    "  t.Saturn square n.Venus — orb 1.20° applying",
])


def test_a_true_pairing_passes():
    ok, bad = grounding.check("Your Venus in Scorpio is why Saturn's square stings.", CTX)
    assert ok, bad


def test_a_real_planet_in_the_wrong_sign_is_caught():
    """Every word is in the context — Moon, Scorpio — but not together. The old
    presence check passed this; it is the subtle way a model invents a chart."""
    ok, bad = grounding.check("Your Moon in Scorpio explains the intensity.", CTX)
    assert not ok and bad == ["pairing not in context: Moon in Scorpio"]


def test_the_wrong_house_is_caught():
    ok, bad = grounding.check("Venus in your 12th house wants privacy.", CTX)
    assert not ok and any("Venus in house 12" in b for b in bad)


def test_sunday_is_not_the_sun():
    ok, bad = grounding.check("Sunday is a good day to rest, honeymoon or not.", "nothing")
    assert ok, bad


# --------------------------------------------------------- live counters


def test_replies_are_counted_and_failures_kept(tmp_path, monkeypatch):
    from engine import store
    monkeypatch.setattr(store, "ROOT", tmp_path)
    accuracy.record_reply("Your Venus in Scorpio.", CTX)
    accuracy.record_reply("Your Moon in Scorpio.", CTX)
    accuracy.record_outcome(False, ["sign not in context: Leo"], "compatibility", withheld=True)
    s = accuracy.live_stats()
    assert s["replies_checked"] == 3 and s["replies_grounded"] == 1
    assert s["replies_withheld"] == 1 and s["shown_ungrounded"] == 1
    fails = store.load(accuracy.GLOBAL, "grounding_failures", [])
    assert fails[-2]["problems"] == ["pairing not in context: Moon in Scorpio"]
    # Her words stay in her thread. The failure log keeps what was wrong, not
    # what she was told.
    assert "Your Moon" not in json.dumps(fails)


# ------------------------------------- whose placement (audit HIGH-3)
#
# Built from replies the audit caught going out as "grounded".

CHLOE = "\n".join([
    "TODAY: Friday 30 October 2026",
    "",
    "HER CHART (computed — Swiss Ephemeris; tropical zodiac, Placidus houses)",
    "  Sun 9°33' Sagittarius — 2nd house",
    "  Moon 29°02' Sagittarius — 2nd house",
    "  Venus 17°40' Capricorn — 3rd house",
    "  Ascendant 14°10' Scorpio",
    "  Midheaven 25°31' Leo",
    "  BIRTH TIME: known — houses and rising sign are valid",
    "",
    "SKY TODAY (computed — authoritative)",
    "  t.Sun 6°17' Scorpio",
    "  t.Moon 14°02' Gemini",
    "  t.Saturn trine n.Sun — orb 0.10°  separating",
    "  t.Neptune square n.Venus — orb 1.26°  applying",
])


def test_a_natal_claim_is_not_vouched_for_by_todays_sky():
    """'Sun in Scorpio' went to Chloe as her chart, whose Sun is in Sagittarius;
    the line that let it through was today's transiting Sun."""
    text = ("Your chart is calculated from where and when you were born, and the zodiac "
            "is measured from the equinoxes, so Sun in Scorpio is Sun in Scorpio in "
            "Sydney and in Stockholm.")
    ok, bad = grounding.check(text, CHLOE)
    assert not ok and any("Sun in Scorpio" in b for b in bad)
    ok, bad = grounding.check("Your Sun in Scorpio explains it.", CHLOE)
    assert not ok


def test_true_natal_and_transit_claims_both_pass():
    for text in ("Your Sun in Sagittarius is why you need room.",
                 "The Sun in Scorpio today isn't touching anything of yours.",
                 "Your rising sign is Scorpio, at 14°.",
                 "You're a Scorpio rising with a Leo Midheaven.",
                 "Saturn trine your Sun is easing off now."):
        ok, bad = grounding.check(text, CHLOE)
        assert ok, (text, bad)


def test_a_wrong_rising_sign_is_caught():
    for text in ("Your rising sign is Leo.", "You're a Leo rising.",
                 "Your MC in Scorpio makes sense of the job."):
        ok, _ = grounding.check(text, CHLOE)
        assert not ok, text


def test_a_rising_sign_she_is_being_told_she_does_not_have_is_not_a_claim():
    ok, bad = grounding.check("Everyone says you give off Leo rising energy, but it's Scorpio.", CHLOE)
    assert ok, bad


def test_a_square_called_a_conjunction_is_caught():
    """Lena: 'Neptune… sitting almost exactly on your Venus' — said of a square."""
    ok, bad = grounding.check("Neptune is sitting almost exactly on your Venus.", CHLOE)
    assert not ok and bad == ["aspect not in context: Neptune conjunct Venus"]
    ok, bad = grounding.check("Neptune squares your Venus right now.", CHLOE)
    assert ok, bad
    ok, bad = grounding.check("Neptune is trine your Venus right now.", CHLOE)
    assert not ok


def test_a_partners_rising_needs_his_own_chart():
    """Jake: 'His rising sign is Leo, around 19°' was counted as grounded with no
    line anywhere giving his Ascendant. With his chart in the tool result it is."""
    ctx = CHLOE + '\n[get_synastry] {"name": "Jake", "their_chart": {"placements": ' \
                  '{"Moon": {"at": "0°02\' Aquarius"}}, "angles": {"Ascendant": "19°31\' Leo", ' \
                  '"Midheaven": "3°10\' Taurus"}}}'
    ok, bad = grounding.check("His rising sign is Leo, around 19°.", CHLOE)
    assert not ok
    ok, bad = grounding.check("His rising sign is Leo, around 19°, and his Moon in Aquarius.", ctx)
    assert ok, bad
    # Her own Ascendant is Scorpio; his chart cannot vouch for hers, nor hers for his.
    ok, _ = grounding.check("Your rising sign is Leo.", ctx)
    assert not ok
    ok, _ = grounding.check("His Moon in Sagittarius fits.", ctx)
    assert not ok
    # And a JSON object holding both angles vouches for each one only.
    ok, _ = grounding.check("His Midheaven in Leo.", ctx)
    assert not ok


def test_the_live_count_restarts_under_the_stricter_checker(tmp_path, monkeypatch):
    """Replies scored by the old checker are kept aside, not blended into the
    headline: it called Chloe's wrong Sun sign grounded."""
    from engine import store
    monkeypatch.setattr(store, "ROOT", tmp_path)
    for _ in range(5):
        store.incr(accuracy.GLOBAL, "grounding", "checked")
        store.incr(accuracy.GLOBAL, "grounding", "grounded")
    accuracy.record_reply("Your Sun in Scorpio.", CHLOE)
    s = accuracy.live_stats()
    assert s["checker"] == 2 and s["replies_checked"] == 1 and s["replies_grounded"] == 0
    assert s["grounded_pct"] == 0 and s["since"]
    assert s["legacy"] == {"checker": 1, "replies_checked": 5, "replies_grounded": 5}


def test_rising_written_as_rising_in_the_facts_still_vouches():
    """The personality profile's facts say 'Rising sign: Scorpio', not
    'Ascendant'; the first cut of the rising check withheld a true line."""
    facts = "Sun in Sagittarius, 2nd house\nRising sign: Scorpio\nMidheaven in Leo"
    for text in ("Your Scorpio rising reads as intense.", "Your rising sign is Scorpio.",
                 "Your Midheaven in Leo wants to be seen."):
        ok, bad = grounding.check(text, facts)
        assert ok, (text, bad)
    ok, _ = grounding.check("Your rising sign is Leo.", facts)
    assert not ok
