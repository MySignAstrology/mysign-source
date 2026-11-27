"""
Sharing and the honest edge of the free plan: the pair card, the five-person
cap, and the price list. Every word on a card comes from the engine, and no one
else's birth data ever leaves on one.
"""
import pytest
from fastapi.testclient import TestClient

import app.server as server
from engine import cards, daily, plan
from tests.test_app import last_uid, onboard, sign_in


@pytest.fixture
def c():
    return TestClient(server.app)


PEOPLE = [("Theo", "1997-06-02"), ("Sam", "1995-11-19"), ("Ari", "2000-01-08"),
          ("Jordan", "1998-04-23"), ("Noor", "1996-09-30"), ("Rae", "2001-02-14")]


def _add(c, name, date, **kw):
    return c.post("/api/connections", json={"name": name, "date": date, "time": "09:15",
                                            "city": "Chicago IL", **kw})


# ------------------------------------------------------------------ pair card

def test_pair_card_shows_names_and_todays_numbers_never_birth_data(c):
    sign_in(c, "pair@x.com"); onboard(c)
    row = _add(c, "<Theo & Co>", "1997-06-02").json()
    r = c.get(f"/api/card/pair/{row['id']}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "my-sign-us-today.svg" in r.headers["content-disposition"]
    svg = r.text
    assert "Maya &amp; &lt;Theo" in svg and "<Theo" not in svg
    for leak in ("1997", "06-02", "09:15", "Chicago"):
        assert leak not in svg, leak
    d = c.get(f"/api/connections/{row['id']}/today").json()
    assert f">{d['score']}<" in svg and d["mood"] in svg
    assert "#0F0B0D" in svg and "Instrument" in svg       # Velvet Sky, not the old ink


def test_pair_card_initials_hide_the_name_everywhere(c):
    sign_in(c, "pair2@x.com"); onboard(c)
    row = _add(c, "Theodore Lane", "1997-06-02").json()
    svg = c.get(f"/api/card/pair/{row['id']}.svg?initials=1").text
    assert "T." in svg and "Theodore" not in svg and "Lane" not in svg


def test_pair_card_needs_her_own_connection(c):
    sign_in(c, "pair3@x.com"); onboard(c)
    assert c.get("/api/card/pair/nope.svg").status_code == 404
    assert TestClient(server.app).get("/api/card/pair/nope.svg").status_code == 401


def test_pair_card_headline_is_the_engines():
    d = {"date": "2026-10-28", "score": 71, "usual": 62, "delta": 9, "mood": "easier than usual",
         "reasons": [{"weight": 1.0, "short": "Venus is warming your Moon"}]}
    line = daily.headline(d, "Maya")
    svg = cards.pair_card("Me", "Maya", "WEDNESDAY · OCTOBER 28", 71, 9, d["mood"], line)
    assert "▲ 9" in svg and "Things are easier with Maya today" in svg
    assert cards.pair_card("A" * 40, "B", "", 50, -6, "a bit tender", "")\
        .count("A" * 17 + "…") == 1


def test_solo_cards_are_velvet_sky(c):
    sign_in(c, "velvet@x.com"); onboard(c)
    for path in ("/api/card/chart.svg", "/api/card/today.svg"):
        svg = c.get(path).text
        assert "#0F0B0D" in svg and "#D9A94E" not in svg and "Bodoni" not in svg


# ------------------------------------------------------------- people cap

@pytest.fixture
def billing_live(monkeypatch):
    # The cap only binds once Plus can actually be bought.
    monkeypatch.setitem(plan.PRICES, "available", True)


def test_no_wall_while_plus_cannot_be_bought(c):
    sign_in(c, "nowall@x.com"); onboard(c)
    assert plan.PRICES["available"] is False
    for name, date in PEOPLE:
        assert _add(c, name, date).status_code == 200, name


def test_free_plan_keeps_five_people_then_asks_honestly(c, billing_live):
    sign_in(c, "cap@x.com"); onboard(c)
    for name, date in PEOPLE[:plan.FREE_PEOPLE]:
        assert _add(c, name, date).status_code == 200, name
    r = _add(c, *PEOPLE[5])
    assert r.status_code == 402
    det = r.json()["detail"]
    assert det["code"] == "plus_people" and det["prices"]["annual"]["price"] == "$59.99"
    assert c.get("/api/connections").json()["count"] == plan.FREE_PEOPLE   # nothing half-saved

    plan.grant(last_uid(), "plus", "test")
    assert _add(c, *PEOPLE[5]).status_code == 200


def test_people_over_the_cap_from_before_are_kept(c, billing_live):
    sign_in(c, "grand@x.com"); onboard(c)
    plan.grant(last_uid(), "plus", "test")
    for name, date in PEOPLE:
        assert _add(c, name, date).status_code == 200
    plan.grant(last_uid(), "free", "test")             # lapsed: nobody is taken away
    listed = c.get("/api/connections").json()
    assert listed["count"] == 6
    assert _add(c, "Ivy", "1999-12-01").status_code == 402
    # Deleting down under five frees a slot again.
    for row in listed["connections"][:2]:
        c.delete(f"/api/connections/{row['id']}")
    assert _add(c, "Ivy", "1999-12-01").status_code == 200


def test_state_carries_one_honest_price_list(c):
    sign_in(c, "price@x.com"); onboard(c)
    p = c.get("/api/state").json()["plan"]
    assert p["free_people"] == 5 and p["prices"]["trial_days"] == 7
    assert p["prices"]["monthly"]["price"] == "$9.99"
    assert p["prices"]["available"] is False


def test_paywall_intent_is_counted_and_says_it_is_not_for_sale(c):
    sign_in(c, "intent@x.com"); onboard(c)
    r = c.post("/api/plan/intent", json={"plan": "annual", "surface": "people_cap"})
    assert r.status_code == 200 and "isn't available" in r.json()["message"]
    assert c.post("/api/plan/intent", json={"plan": "lifetime", "surface": "x"}).status_code == 400
    assert c.post("/api/plan/intent", json={"plan": "annual", "surface": "<b>"}).status_code == 400
    assert plan.get(last_uid())["tier"] == "free"
