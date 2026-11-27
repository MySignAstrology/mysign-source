"""
The free reading on the website: public, stores nothing, can't claim what the
birth details don't support. Run with: python -m pytest tests/test_free_reading.py -q
"""
import datetime as dt
import os
import tempfile

import pytest

os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-free-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("MYSIGN_TABLE", None)

from fastapi.testclient import TestClient

import app.server as server
from app import cities
from engine import corpus, freeread, profile, store
from engine.chart import natal, with_time


@pytest.fixture
def c():
    return TestClient(server.app)


def _city():
    return cities.search("Chicago")[0]["id"]


def test_it_needs_no_account_and_stays_out_of_caches(c):
    r = c.post("/api/free-reading", json={"date": "1996-07-22", "time": "18:40", "city": _city()})
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    body = r.json()
    assert "set-cookie" not in r.headers
    assert len(body["sections"]) == 3 and body["sections"][0]["key"] == "toxic_trait"
    assert any("rising" in s for s in body["signs"])



def test_locked_sections_are_named_and_their_text_is_never_sent(c):
    body = c.post("/api/free-reading", json={"date": "1996-07-22", "city": _city()}).json()
    assert body["locked"] and "text" not in str(body["locked"])
    shown = {s["title"] for s in body["sections"]}
    assert not shown & set(body["locked"])
    assert shown | set(body["locked"]) == {t for _, t in profile.SECTIONS}


def test_no_birth_time_means_no_rising_sign(c):
    body = c.post("/api/free-reading", json={"date": "1996-07-22", "city": _city()}).json()
    assert body["time_known"] is False
    assert not any("rising" in s for s in body["signs"])


def test_a_moon_it_cannot_vouch_for_is_never_claimed():
    # 14 Mar 1999, no time: find a day the Moon changes sign and check the output.
    day = dt.date(2000, 1, 1)
    seen = 0
    for i in range(60):
        d = day + dt.timedelta(days=i)
        chart = with_time(natal(d.year, d.month, d.day, 12, 41.88, -87.63), False)
        out = freeread.build(chart, False, (d.year, d.month, d.day))
        if not any("Moon" in s for s in out["signs"]):
            seen += 1
            assert "misread" not in [s["key"] for s in out["sections"]]
    assert seen, "no sign-change day in the window: the test proved nothing"


def test_under_18_is_refused_the_same_way_as_signup(c):
    today = dt.date.today()
    r = c.post("/api/free-reading", json={"date": f"{today.year - 16}-01-01", "city": _city()})
    assert r.status_code == 403 and "18" in r.json()["detail"]


def test_bad_input_is_a_clear_400(c):
    assert c.post("/api/free-reading", json={"date": "nope", "city": _city()}).status_code == 400
    assert c.post("/api/free-reading", json={"date": "1996-07-22", "city": "zzzz-nowhere"}).status_code == 400


def test_absurd_numbers_are_a_400_not_a_server_error(c):
    # A 500 here would let a stranger trip the error alarm with a few requests.
    assert c.post("/api/free-reading", json={"date": "99999999999999-1-1", "city": _city()}).status_code == 400
    assert c.post("/api/free-reading", json={"date": "1996-07-22", "time": "99999999999:0",
                                             "city": _city()}).status_code == 400


def test_nothing_about_the_birth_details_is_stored_or_logged(c, monkeypatch, capsys):
    writes, events = [], []
    monkeypatch.setattr(store, "save", lambda *a, **k: writes.append(a))
    monkeypatch.setattr(server, "save", lambda *a, **k: writes.append(a))
    real = server.activity.track_anon
    monkeypatch.setattr(server.activity, "track_anon",
                        lambda *a, **k: (events.append((a, k)), real(*a, **k))[1])
    r = c.post("/api/free-reading", json={"date": "1996-07-22", "time": "18:40", "city": _city()})
    assert r.status_code == 200
    assert events, "the reading was never counted, so this proved nothing"
    seen = str(writes) + str(events) + "".join(capsys.readouterr())
    assert "1996" not in seen and "18:40" not in seen


def test_every_sign_has_a_toxic_trait_short_enough_to_post():
    assert set(profile.TOXIC_TRAITS) == set(corpus.SIGNS)
    for line in profile.TOXIC_TRAITS.values():
        assert len(line) <= 60


def test_the_free_reading_leads_with_the_line_for_her_sun(c):
    r = c.post("/api/free-reading", json={"date": "1996-07-22", "time": "18:40", "city": "Austin, TX"}).json()
    assert r["sections"][0]["key"] == "toxic_trait"
    assert r["sections"][0]["text"] == profile.TOXIC_TRAITS[r["signs"][0].split()[0]]


def test_a_birthday_alone_gets_one_line_and_no_city_is_needed(c):
    r = c.post("/api/free-glimpse", json={"date": "1996-07-10"})
    assert r.status_code == 200
    body = r.json()
    assert body["signs"] == ["Cancer Sun"] and body["section"]["text"]
    assert r.headers["cache-control"] == "no-store"


def test_a_birthday_on_a_sign_change_gets_no_line_rather_than_a_guess(c):
    # The Sun enters Cancer on 21 June, so which sign it is depends on the hour.
    body = c.post("/api/free-glimpse", json={"date": "1996-06-21"}).json()
    assert body == {"signs": [], "section": None}


def test_the_glimpse_refuses_the_young_and_the_malformed(c):
    today = dt.date.today()
    assert c.post("/api/free-glimpse", json={"date": f"{today.year - 16}-01-01"}).status_code == 403
    assert c.post("/api/free-glimpse", json={"date": "nope"}).status_code == 400
