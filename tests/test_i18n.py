"""
Her language for the server's own words (audit MEDIUM-6): Lucía wrote only in
Spanish, the model answered in Spanish, and the app's cap message, starters and
compatibility report around it were English.
"""
import json

import pytest
from fastapi.testclient import TestClient

import app.server as server
from engine import compat, i18n, plan
from engine.store import load, save
from tests.test_app import last_uid, onboard, sign_in


@pytest.fixture
def c():
    return TestClient(server.app)


def _send(c, text):
    with c.stream("POST", "/api/message", json={"text": text}) as r:
        return r.status_code, "".join(r.iter_text())


def test_language_is_read_from_her_messages_only_when_clear():
    assert i18n.detect("hola, no hablo ingles. mi novio y yo peleamos mucho esta semana") == "es"
    assert i18n.detect("gracias. una pregunta mas por favor") == "es"
    assert i18n.detect("what is my moon sign and why") == "en"
    for unclear in ("ok", "jake", "jajaja", "Marco", "lol ok"):
        assert i18n.detect(unclear) is None, unclear


def test_the_free_cap_answers_in_her_language(c):
    sign_in(c, "lucia-cap@x.com"); onboard(c)
    for _ in range(plan.FREE_MESSAGES_PER_DAY):
        _send(c, "hola, mi novio y yo peleamos mucho esta semana")
    status, body = _send(c, "gracias. una pregunta mas por favor")
    assert status == 402 and json.loads(body)["detail"] == i18n.text("cap", "es")
    assert load(last_uid(), "profile", {})["lang"] == "es"
    # One clearly English message switches back.
    status, body = _send(c, "ok what is my moon sign then")
    assert status == 402 and "That's three for today" in json.loads(body)["detail"]


def test_reading_starters_follow_her_language(c):
    sign_in(c, "lucia-read@x.com"); onboard(c)
    en = c.get("/api/reading").json()["starters"]
    prof = load(last_uid(), "profile", {})
    save(last_uid(), "profile", {**prof, "lang": "es"})
    es = c.get("/api/reading").json()["starters"]
    assert len(es) == len(en) and all(s.startswith("¿") for s in es), es


def test_legacy_starters_lose_internal_names_and_the_angle_square():
    got = i18n.starters(["What is the Mars–TrueNode tension actually about?",
                         "What is the Ascendant–Midheaven tension actually about?"], "en")
    assert got == ["What is the tension between my Mars and my North Node actually about?"]


def test_the_compatibility_report_speaks_spanish_where_the_server_wrote_it():
    rep = compat.deep({"birth_ut": [1990, 5, 4, 0.5, 20.67, -103.35]},
                      {"birth_ut": [1993, 11, 8, 13.17, 20.67, -103.35]}, name_b="Marco")
    es = i18n.compat_report(rep, "es")
    titles = {t["key"]: t["title"] for t in es["themes"]}
    assert titles.get("communication", "Comunicación") == "Comunicación"
    assert all(t["presence"] in i18n.PRESENCE_ES.values() for t in es["themes"])
    assert es["composite"]["sun"] in [v + x for v in i18n.COMPOSITE_SUN_ES.values()
                                      for x in ("",)] or "permanencia" in es["composite"]["sun"]
    assert rep["themes"][0]["title"] != es["themes"][0]["title"]       # the original is untouched
    assert i18n.compat_report(rep, "en") is rep
