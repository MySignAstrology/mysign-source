"""
Deep compatibility: the report behind a saved person.
"""
import os
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-compat-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import server
from engine import compat, store, tools
from engine.chart import natal
from tests.test_app import last_uid, onboard, sign_in

HER = {"birth_ut": [2001, 6, 9, 18.4167, 33.749, -84.388]}
THEM = {"birth_ut": [2000, 11, 2, 18.0, 41.88, -87.63]}


@pytest.fixture
def c():
    return TestClient(server.app)


# ------------------------------------------------------------------ engine

def test_an_exact_time_gives_an_exact_score():
    r = compat.deep(HER, THEM, precision_a="exact", precision_b="exact")
    assert r["score"]["exact"]
    lo, hi = r["score"]["overall_range"]
    assert lo == hi == r["score"]["overall"]


def test_an_unknown_time_gives_a_range_not_a_guess():
    r = compat.deep(HER, THEM, precision_a="exact", precision_b="unknown")
    lo, hi = r["score"]["overall_range"]
    assert not r["score"]["exact"] and lo < hi
    assert lo <= r["score"]["overall"] <= hi
    assert any("birth time" in u for u in r["uncertainty"])


def test_no_angles_or_houses_for_someone_without_a_time():
    r = compat.deep(HER, THEM, precision_a="exact", precision_b="unknown")
    assert not any(c["theirs"] in ("Ascendant", "Midheaven") for c in r["contacts"])
    assert not any(o["direction"] == "yours_in_theirs" for o in r["overlays"])


def test_uncertain_moon_contacts_are_labelled_as_such():
    """The Moon moves ~13° in a day; with no time, a tight Moon contact at noon
    may not exist at all. Not knowing the time can only take certainty away."""
    exact = compat.deep(HER, THEM, precision_b="exact")["contacts"]
    blind = compat.deep(HER, THEM, precision_b="unknown")["contacts"]
    assert {c["certainty"] for c in blind} <= {"certain", "likely", "depends on birth time"}
    moon = lambda cs: [c for c in cs if c["theirs"] == "Moon" and c["certainty"] == "certain"]  # noqa: E731
    assert len(moon(blind)) < len(moon(exact)) or not moon(exact)
    assert any(c["certainty"] != "certain" for c in blind if c["theirs"] == "Moon") or \
        len(moon(blind)) < len(moon(exact))


def test_every_contact_is_real_geometry():
    """No contact the charts don't contain — the report's whole promise."""
    a, b = natal(*HER["birth_ut"]), natal(*THEM["birth_ut"])
    from engine.synastry import ASPECTS, _sep
    angle = {n: ang for n, ang, _o in ASPECTS}
    back = {v: k for k, v in compat.NAME.items()}
    r = compat.deep(HER, THEM)
    for c in r["contacts"]:
        ya = back.get(c["yours"], c["yours"])
        tb = back.get(c["theirs"], c["theirs"])
        la = (a["planets"].get(ya) or a["angles"][ya])["lon"]
        lb = (b["planets"].get(tb) or b["angles"][tb])["lon"]
        assert abs(abs(_sep(la, lb) - angle[c["aspect"]]) - c["orb"]) < 0.01, c
        assert c["meaning"]


def test_generational_contacts_are_left_out():
    r = compat.deep(HER, THEM)
    outer = {"Uranus", "Neptune", "Pluto", "Chiron", "North Node"}
    assert not any(c["yours"] in outer and c["theirs"] in outer for c in r["contacts"])


def test_direction_is_kept():
    r = compat.deep(HER, THEM)
    for c in r["contacts"]:
        assert c["text"].startswith(f"Their {c['theirs']} ") and c["text"].endswith(f"your {c['yours']}")


def test_composite_midpoint_takes_the_short_way_round():
    assert compat._midpoint(350, 10) == pytest.approx(0)
    assert compat._midpoint(10, 350) == pytest.approx(0)
    assert compat._midpoint(100, 140) == pytest.approx(120)


def test_the_relationship_kind_changes_the_weighting():
    rom = compat.deep(HER, THEM, kind="romantic")["score"]
    work = compat.deep(HER, THEM, kind="work")["score"]
    assert rom["dimensions"] == work["dimensions"]
    assert rom["weights"] != work["weights"]


@pytest.mark.parametrize("relation,kind", [
    ("situationship, ~5 months", "romantic"), ("my older sister", "family"),
    ("roommate", "friend"), ("coworker on my team", "work"), ("", "romantic"),
    ("best friend from school", "friend"), ("my boss", "work")])
def test_kind_is_inferred_from_the_relation(relation, kind):
    assert compat.infer_kind(relation) == kind


def test_themes_are_judged_against_ordinary_pairs():
    """A fixed threshold called nearly every theme strong."""
    import random
    rng = random.Random(3)
    strong = total = 0
    for _ in range(40):
        p = lambda: {"birth_ut": [rng.randint(1988, 2004), rng.randint(1, 12),  # noqa: E731
                                  rng.randint(1, 28), rng.random() * 24, 41.88, -87.63]}
        for t in compat.deep(p(), p())["themes"]:
            total += 1
            strong += t["presence"] == "strong"
    assert 0.1 < strong / total < 0.45


def test_the_model_only_gets_facts_from_the_report():
    r = compat.deep(HER, THEM, precision_b="approximate")
    facts = compat.facts_for_model(r, "Dani")
    assert "Dani" in facts and "COMPOSITE" in facts and "CAVEAT" in facts


def test_narration_keeps_only_a_finished_reading():
    r = compat.deep(HER, THEM)

    class Block:
        type, text = "text", "A reading."

    def client(stop):
        class Msgs:
            @staticmethod
            def create(**k):
                return type("R", (), {"content": [Block()], "stop_reason": stop})()
        return type("C", (), {"messages": Msgs})()
    assert compat.narrate(r, "Dani", client=client("end_turn")) == "A reading."
    assert compat.narrate(r, "Dani", client=client("max_tokens")) is None
    assert compat.narrate(r, "Dani", client=client("refusal")) is None


def test_a_reading_that_invents_a_placement_is_rejected():
    facts = compat.facts_for_model(compat.deep(HER, THEM), "Dani")
    invented = next(s for s in ("Capricorn", "Virgo", "Libra", "Aries", "Taurus")
                    if s.lower() not in facts.lower())
    assert compat.ungrounded(f"Their Moon in {invented} explains it.", facts)
    assert compat.ungrounded("Their Venus in your 13th house.", facts)
    assert not compat.ungrounded("Their Saturn and your Venus make it serious.", facts)


# --------------------------------------------------------------------- API

def _add(c, **kw):
    body = {"name": "Dani", "relation": "roommate", "date": "2000-11-02",
            "time": "12:00", "city": "Chicago IL", **kw}
    return c.post("/api/connections", json=body)


def test_adding_someone_builds_the_deep_report(c):
    sign_in(c, "deep1@x.com"); onboard(c)
    row = _add(c, time_precision="approximate").json()
    assert row["kind"] == "friend" and row["time_precision"] == "approximate"
    assert len(row["overall_range"]) == 2 and row["themes"]
    d = c.get(f"/api/connections/{row['id']}/deep").json()
    assert d["person"]["name"] == "Dani"
    assert d["themes"] and d["composite"]["sun"] and d["balance"]["note"]


def test_changing_the_kind_rebuilds_the_report(c):
    sign_in(c, "deep2@x.com"); onboard(c)
    row = _add(c, kind="romantic").json()
    edited = c.patch(f"/api/connections/{row['id']}", json={"kind": "work"}).json()
    assert edited["kind"] == "work"
    assert c.get(f"/api/connections/{row['id']}/deep").json()["score"]["kind"] == "work"


def test_adding_a_time_later_narrows_the_range(c):
    sign_in(c, "deep3@x.com"); onboard(c)
    row = _add(c, time="").json()
    assert row["time_precision"] == "unknown"
    lo, hi = row["overall_range"]
    edited = c.patch(f"/api/connections/{row['id']}",
                     json={"time": "18:30", "time_precision": "exact"}).json()
    assert edited["time_precision"] == "exact"
    assert edited["overall_range"][0] == edited["overall_range"][1]
    assert hi - lo > 0


def test_bad_kind_and_precision_are_400s(c):
    sign_in(c, "deep4@x.com"); onboard(c)
    assert _add(c, kind="soulmate").status_code == 400
    assert _add(c, time_precision="vibes").status_code == 400


def test_forgetting_someone_removes_their_report(c):
    sign_in(c, "deep5@x.com"); onboard(c)
    uid = last_uid()
    row = _add(c).json()
    assert f"compat-{row['id']}" in store.keys(uid)
    c.delete(f"/api/connections/{row['id']}")
    assert f"compat-{row['id']}" not in store.keys(uid)


def test_the_chat_can_look_up_one_person(c):
    """get_synastry called compare() with the wrong arguments and errored on
    every call, so the chat could never discuss anyone specific."""
    sign_in(c, "deep6@x.com"); onboard(c)
    uid = last_uid()
    _add(c)
    out = tools.run("get_synastry", uid, server._natal(uid), {"name": "dani"})
    assert "error" not in out, out
    assert out["themes"] and out["composite"]["sun"] and "overall" in out["score"]


def test_the_reading_is_generated_once_and_kept(c, monkeypatch):
    sign_in(c, "deep7@x.com"); onboard(c)
    row = _add(c).json()
    calls = []
    monkeypatch.setattr(server, "DEMO", False)
    fun = {"headline": "Two fires.", "dynamic": "Written.", "sections": [],
           "green_flags": [], "watch_outs": []}
    from engine import compat_fun
    monkeypatch.setattr(compat_fun, "generate", lambda *a, **k: calls.append(1) or fun)
    assert c.post(f"/api/connections/{row['id']}/reading").json()["reading"] == "Written."
    second = c.post(f"/api/connections/{row['id']}/reading").json()
    assert second["reading"] == "Written." and second["fun"]["headline"] == "Two fires."
    assert len(calls) == 1
    deep = c.get(f"/api/connections/{row['id']}/deep").json()
    assert deep["reading"] == "Written." and deep["fun"]["headline"] == "Two fires."


# ------------------------------------------------ audit MEDIUM-2 / MEDIUM-1

MAYA = {"birth_ut": [2002, 3, 13, 14.333, 30.2672, -97.7431]}
JAKE_NOTIME = {"birth_ut": [2000, 11, 2, 19.0, 39.7392, -104.9903]}


def test_affection_never_says_no_venus_contacts_when_venus_contacts_exist():
    """Maya & Jake: 'No direct Venus contacts' beside Their Venus conjunction
    your Pluto, Their Jupiter sextile your Venus and Their Pluto trine your Venus
    — the Pluto, Jupiter and Saturn rules claim Venus contacts first."""
    seen = 0
    for b in range(0, 720, 5):
        other = {"birth_ut": [1985 + b % 17, 1 + b % 12, 1 + b % 28, (b % 24) * 1.0, 40.0, -74.0]}
        rep = compat.deep(MAYA, other)
        for t in rep["themes"]:
            planet = compat.THEME_PLANET.get(t["key"])
            touching = [c for c in rep["contacts"] if planet in (c["yours"], c["theirs"])]
            if planet and not t["contacts"] and touching:
                seen += 1
                assert t["presence"] == "faint"
                assert t["note"].startswith(f"{planet} is in play") and touching[0]["text"] in t["note"]
            if t["note"] and t["note"].startswith("No direct Venus"):
                assert not any("Venus" in (c["yours"], c["theirs"]) for c in rep["contacts"])
    assert seen, "no pair exercised the case"


def test_the_composite_sun_blurb_agrees_with_staying_power():
    for b in range(0, 360, 7):
        other = {"birth_ut": [1990 + b % 9, 1 + b % 12, 1 + b % 27, 12.0, 40.0, -74.0]}
        rep = compat.deep(MAYA, other)
        stay = rep["score"]["dimensions"]["staying"]
        assert "long haul" not in rep["composite"]["sun"]
        if rep["composite"]["signs"]["Sun"] in compat.DURABLE and stay < 50:
            assert f"Staying power scores {stay}" in rep["composite"]["sun"]


def test_their_chart_gives_his_rising_only_with_a_time_and_a_moon_range_without():
    rows = {"none": {**JAKE_NOTIME, "birth_time_known": False},
            "exact": {"birth_ut": [2000, 11, 3, 6.75, 39.7392, -104.9903], "birth_time_known": True}}
    none = compat.their_chart(rows["none"])
    assert "angles" not in none and "never name one" in none["note"]
    assert none["placements"]["Moon"]["sign"] == "Capricorn or Aquarius"
    assert none["placements"]["Moon"]["most_of_the_day"] == "Capricorn"
    assert "house" not in none["placements"]["Sun"]
    exact = compat.their_chart(rows["exact"])
    assert exact["angles"]["Ascendant"].endswith("Leo")
    assert exact["placements"]["Moon"]["at"].endswith("Aquarius") and exact["placements"]["Moon"]["house"]


def test_no_time_moon_contacts_carry_no_exact_orb():
    from engine import synastry as S
    a, b = compat.samples(MAYA["birth_ut"], "exact")[0], compat.samples(JAKE_NOTIME["birth_ut"], "unknown")[0]
    sc = S.score(a, b, time_known_b=False)
    for line in sc["drivers"] + sc["frictions"]:
        if line.startswith("their Moon"):
            assert "approximate" in line and "°" not in line


def test_a_composite_contact_never_says_your_and_their():
    """The composite belongs to both of them; a built line can't split it
    into "your" and "their"."""
    from engine.compat import KEYWORD, PAIR, _pair_key
    a, b = next((a, b) for a in KEYWORD for b in KEYWORD
                if a < b and _pair_key(a, b) not in PAIR)
    for asp in ("conjunction", "trine", "square"):
        line = compat.meaning(a, b, asp, shared=True)
        assert "Your " not in line and " their " not in line
        assert line.startswith("In the relationship itself")
    assert compat.meaning(a, b, "trine").startswith("Your ")


def test_a_green_flag_is_never_a_square_or_opposition():
    """
    The written fallback once sorted flags by their theme's tone, so a 'flowing'
    theme led by an opposition put "One of you can come across as critical of
    who the other is" under Green flags. Each contact is judged by its own aspect.
    """
    import random

    from engine import compat_fun
    rng = random.Random(7)
    seen = 0
    for _ in range(40):
        a = {"birth_ut": [rng.randint(1975, 2004), rng.randint(1, 12), rng.randint(1, 28),
                          rng.uniform(0, 23.9), 40.7, -74.0]}
        b = {"birth_ut": [rng.randint(1975, 2004), rng.randint(1, 12), rng.randint(1, 28),
                          rng.uniform(0, 23.9), 34.05, -118.24]}
        rep = compat.deep(a, b)
        firsts = {t["contacts"][0]["text"]: t["contacts"][0]["aspect"]
                  for t in rep["themes"] if t["contacts"]}
        out = compat_fun.fallback(rep, "Leo")
        for f in out["green_flags"] + ([out["give"]] if out.get("give") else []):
            seen += 1
            assert firsts[f["because"]] not in ("square", "opposition"), f
    assert seen > 20
