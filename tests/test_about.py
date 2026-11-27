"""
"About you": hobbies, interests, skills and passions, asked after the birth
form and editable in Settings.

Checks the limits hold on the server (not just in the app), her own words are
kept but tamed, the chart hints point at her real placements, the chat sees
her words and never a sign name from this block, and export/delete cover it.
"""
import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import server
from engine import about
from engine.chart import SIGNS
from tests.test_app import last_uid, onboard, sign_in


@pytest.fixture
def c():
    return TestClient(server.app)


def test_clean_keeps_catalog_ids_and_tames_her_own_words():
    out = about.clean({"hobbies": ["hiking", "Hiking", "  rock <b>climbing</b>\n  ", ""],
                       "interests": [], "dream": "x" * 500})
    assert out["hobbies"] == ["hiking", "rock bclimbing/b"]
    assert len(out["dream"]) == about.DREAM_MAX
    assert out["skills"] == [] and out["passions"] == []


def test_clean_dedupes_case_insensitively():
    out = about.clean({"skills": ["Fixing bikes", "fixing BIKES", "listening"]})
    assert out["skills"] == ["Fixing bikes", "listening"]


@pytest.mark.parametrize("step", about.STEPS)
def test_each_list_is_capped(step):
    ids = [o["id"] for o in about.CATALOG[step]][: about.LIMITS[step] + 1]
    with pytest.raises(ValueError, match="up to"):
        about.clean({step: ids})
    about.clean({step: ids[:-1]})


def test_her_own_words_are_capped_per_list():
    with pytest.raises(ValueError, match="of your own"):
        about.clean({"hobbies": ["a1", "b2", "c3", "d4"]})


def test_every_option_has_a_planet_and_an_element():
    for step in about.STEPS:
        ids = [o["id"] for o in about.CATALOG[step]]
        assert len(ids) == len(set(ids)), step
        for o in about.CATALOG[step]:
            assert o["planet"] in about.PLANETS and o["element"] in about.ELEMENTS
            assert len(o["label"]) <= about.LABEL_MAX
    # The deck needs a line on every card.
    assert all(o.get("line") for o in about.CATALOG["hobbies"])
    assert len(about.CATALOG["hobbies"]) > about.LIMITS["hobbies"]


def test_mix_compares_her_days_with_her_chart():
    chart = {"elements": {"fire": 10, "earth": 20, "air": 60, "water": 10}}
    same = about.mix({"interests": ["science", "tech"]}, chart)
    assert same["lived"] == "air" and same["built"] == "air" and "so does" in same["text"]
    other = about.mix({"hobbies": ["hiking", "running"]}, chart)
    assert other["lived"] == "fire" and "balanced" in other["text"]
    assert sum(other["life"].values()) == 100
    # Her own words carry no element, so they can't make a mix on their own.
    assert about.mix({"hobbies": ["bouldering"]}, chart) is None


def test_context_lines_are_her_words_without_any_sign():
    a = {"hobbies": ["hiking", "Bouldering"], "interests": ["psychology"], "skills": [],
         "passions": ["helping"], "dream": "Teach kids to swim"}
    lines = about.context_lines(a)
    text = "\n".join(lines)
    assert "does: Hiking, Bouldering" in text and "into: Psychology" in text
    assert "good at" not in text, "an empty list is left out"
    assert "Teach kids to swim" in text
    assert not any(s in text for s in SIGNS), "this block must never carry a placement"
    assert about.context_lines({}) == [] and about.context_lines(None) == []


def test_api_round_trip_hints_settings_and_chat(c):
    sign_in(c, "about-api@x.com")
    assert c.get("/api/about").status_code == 400            # no chart yet
    onboard(c)
    uid = last_uid()

    r = c.get("/api/about").json()
    assert r["about"] is None and r["mix"] is None
    assert r["limits"] == about.LIMITS and "hobbies" in r["catalog"]
    jup = r["chart"]["planets"]["Jupiter"]
    assert jup["text"].startswith(f"Jupiter in {jup['sign']}") and "house" in jup["text"]
    assert sum(r["chart"]["elements"].values()) in (99, 100, 101)
    assert c.get("/api/today").json()["about_nudge"] is True

    r = c.put("/api/about", json={"hobbies": ["hiking", "Bouldering"], "interests": ["psychology"],
                                  "passions": ["helping"], "dream": "Teach kids to swim",
                                  "source": "onboard"})
    assert r.status_code == 200
    body = r.json()
    assert body["about"]["hobbies"] == ["hiking", "Bouldering"] and body["about"]["updated_at"]
    assert body["mix"]["chart"] and body["mix"]["text"]
    assert c.get("/api/today").json()["about_nudge"] is False
    assert c.get("/api/state").json()["about_done"] is True

    bad = c.put("/api/about", json={"passions": [o["id"] for o in about.CATALOG["passions"]][:4]})
    assert bad.status_code == 400 and "Pick up to 3" in bad.json()["detail"]
    assert c.get("/api/about").json()["about"]["passions"] == ["helping"], "a bad save changes nothing"

    block, _ = server.context.build(uid, server._natal(uid), server._sky(uid),
                                    {"text": "", "mood": 5}, today=dt.date.today())
    assert "ABOUT HER" in block and "does: Hiking, Bouldering" in block

    exp = c.get("/api/export").json()
    assert exp["about"]["dream"] == "Teach kids to swim"


def test_not_now_stops_the_home_card_but_keeps_settings(c):
    sign_in(c, "about-later@x.com")
    onboard(c)
    assert c.post("/api/about/later").json() == {"ok": True}
    assert c.get("/api/today").json()["about_nudge"] is False
    # A later save from Settings keeps her "not now".
    c.put("/api/about", json={"skills": ["listening"]})
    assert c.get("/api/about").json()["about"]["nudge_off"] is True


def test_about_needs_consent_and_goes_with_the_account(c):
    assert TestClient(server.app).get("/api/about").status_code == 401
    assert TestClient(server.app).put("/api/about", json={}).status_code == 401
    sign_in(c, "about-del@x.com")
    onboard(c)
    c.put("/api/about", json={"hobbies": ["hiking"]})
    uid = last_uid()
    assert server.load(uid, "about", None)
    assert c.delete("/api/account").json() == {"deleted": True}
    assert server.load(uid, "about", None) is None
