"""
The public compatibility check: no account, nothing about either person kept,
and a shared link or card that can't leak a birthday or be edited.
Run with: python -m pytest tests/test_check.py -q
"""
import base64
import datetime as dt
import json
import os
import tempfile

import pytest

os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-check-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("MYSIGN_TABLE", None)

from fastapi.testclient import TestClient

import app.server as server
from engine import check, compat, funnel, store
from tests.test_invites import person

A, B = "1996-07-22", "1995-11-03"
BODY = {"date_a": A, "date_b": B, "name_a": "Sam", "name_b": "Theo"}
SECRET = b"test-secret"


@pytest.fixture
def c():
    return TestClient(server.app, follow_redirects=False)


def _count(step):
    return funnel._counts(dt.date.today()).get(step, 0)


def _years_ago(n):
    t = dt.date.today()
    return dt.date(t.year - n, t.month, 1).isoformat()


# ------------------------------------------------------------------ the score
def test_it_is_the_same_engine_as_people():
    ua, ub = [1996, 7, 22, 12.0, 0.0, 0.0], [1995, 11, 3, 12.0, 0.0, 0.0]
    deep = compat.deep({"birth_ut": ua}, {"birth_ut": ub}, precision_a="unknown",
                       precision_b="unknown")["score"]
    got = check.read((1996, 7, 22), (1995, 11, 3))
    assert got["score"] == deep["overall"] and got["band"] == deep["band"]
    assert [got["low"], got["high"]] == deep["overall_range"]


def test_the_order_of_the_two_people_does_not_change_the_number():
    one, two = check.read((1996, 7, 22), (1995, 11, 3)), check.read((1995, 11, 3), (1996, 7, 22))
    assert one["score"] == two["score"] and one["band"] == two["band"]


def test_a_friend_is_weighed_differently_and_an_ex_as_the_romance_it_was():
    a, b = (1996, 7, 22), (1995, 11, 3)
    assert check.read(a, b, "ex")["score"] == check.read(a, b, "romantic")["score"]
    assert check.read(a, b, "ex")["line"] != check.read(a, b, "romantic")["line"]
    assert check.read(a, b, "friend")["kind"] == "friend"
    assert check.read(a, b, "nonsense")["kind"] == "romantic"


def test_every_kind_has_a_line_for_every_band_that_fits_the_card():
    bands = {"rare", "strong", "promising", "mixed", "effortful", "hard going"}
    assert set(check.LINES) == set(check.KINDS)
    for lines in check.LINES.values():
        assert set(lines) == bands
    for text in [*(t for ls in check.LINES.values() for t in ls.values()), check.SAME_SKY]:
        assert len(text) <= 90


def test_the_same_birthday_says_the_number_cannot_tell_them_apart():
    got = check.read((1990, 2, 1), (1990, 2, 1))
    assert got["same_sky"] and got["line"] == check.SAME_SKY and got["why"] == []


def test_why_only_claims_what_holds_across_the_whole_day():
    got = check.read((1996, 7, 22), (1995, 11, 3))
    full = compat.deep({"birth_ut": [1996, 7, 22, 12.0, 0.0, 0.0]},
                       {"birth_ut": [1995, 11, 3, 12.0, 0.0, 0.0]},
                       precision_a="unknown", precision_b="unknown")["contacts"]
    certain = {x["text"] for x in full if x["certainty"] == "certain"}
    assert got["why"] and all(w["text"] in certain for w in got["why"])


# ----------------------------------------------------------------- the route
def test_it_needs_no_account_and_stays_out_of_caches(c):
    r = c.post("/api/vs", json=BODY)
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert "set-cookie" not in r.headers
    assert r.json()["names"] == ["Sam", "Theo"] and r.json()["url"].endswith(r.json()["token"])


def test_nothing_about_either_person_is_stored(c, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(store, "save", lambda *a, **k: seen.append(a))
    calls = []
    real = store.incr
    monkeypatch.setattr(store, "incr", lambda *a, **k: calls.append(a) or real(*a, **k))
    assert c.post("/api/vs", json=BODY).status_code == 200
    assert seen == []
    assert [a[0] for a in calls] == ["_study"] and A not in str(calls)


@pytest.mark.parametrize("field", ["date_a", "date_b"])
def test_both_people_must_be_adults(c, field):
    r = c.post("/api/vs", json={**BODY, field: _years_ago(16)})
    assert r.status_code == 403 and "18" in r.json()["detail"]


@pytest.mark.parametrize("bad", ["nope", "2031-01-01", "1800-01-01", "1996-13-40"])
def test_a_bad_date_is_a_plain_400(c, bad):
    assert c.post("/api/vs", json={**BODY, "date_b": bad}).status_code == 400


@pytest.mark.parametrize("typed,kept", [
    ("Theo", "Theo"), ("  Theo Smith ", "Theo"), ("<script>alert(1)</script>", ""),
    ("Anne-Marie", "Anne-Marie"), ("José", "José"), ("O'Brien", "O'Brien"), ("🔥", ""), ("", ""),
    ("x" * 60, "x" * 20), ("http://evil.example", ""), ("प्रिया", "प्रिया"),
])
def test_a_name_is_one_word_of_letters(typed, kept):
    assert check.first_name(typed) == kept


# ------------------------------------------------------------------ the link
def test_the_token_never_holds_a_birth_date(c):
    token = c.post("/api/vs", json=BODY).json()["token"]
    raw = base64.urlsafe_b64decode(token.split(".")[0] + "==").decode()
    assert set(json.loads(raw)) == {"a", "b", "s", "r", "k"}
    for piece in (A, B, "1996", "1995"):
        assert piece not in raw and piece not in token


def test_a_token_cannot_be_edited_into_a_better_score():
    got = check.read((1996, 7, 22), (1995, 11, 3))
    token = check.sign(got, "Sam", "Theo", SECRET)
    assert check.verify(token, SECRET)["score"] == got["score"]
    body, sig = token.split(".")
    forged = base64.urlsafe_b64encode(json.dumps(
        {"a": "Sam", "b": "Theo", "s": 99, "r": "rare", "k": "romantic"}).encode()).rstrip(b"=").decode()
    assert check.verify(f"{forged}.{sig}", SECRET) is None
    assert check.verify(token, b"another-secret") is None
    for junk in ("", "nope", "a.b.c", "x" * 500, None, f"{body}.", "é.é", f"{body}.é"):
        assert check.verify(junk, SECRET) is None


def test_the_preview_shows_the_result_and_counts_nothing(c):
    d = c.post("/api/vs", json=BODY).json()
    before = _count("check_opened")
    r = c.get(f"/api/vs/r/{d['token']}")
    assert r.status_code == 200 and "noindex" in r.text and "no-store" in r.headers["cache-control"]
    assert f"Sam &amp; Theo got {d['score']} on My Sign" in r.text
    assert f"/check?r={d['token']}" in r.text and A not in r.text
    assert _count("check_opened") == before


@pytest.mark.parametrize("token", ["not.real", "%C3%A9.%C3%A9", "a.%C3%A9"])
def test_a_bad_link_still_lands_on_the_page(c, token):
    r = c.get(f"/api/vs/r/{token}")
    assert r.status_code == 200 and "Are you two compatible?" in r.text and "?r=" not in r.text
    assert c.get(f"/api/vs/result/{token}").status_code == 404
    assert c.get(f"/api/vs/card/{token}.svg").status_code == 404


def test_opening_is_counted_and_a_bad_token_is_a_404(c):
    d = c.post("/api/vs", json=BODY).json()
    before = _count("check_opened")
    got = c.get(f"/api/vs/result/{d['token']}").json()
    assert got["names"] == ["Sam", "Theo"] and got["score"] == d["score"] and got["line"] == d["line"]
    assert _count("check_opened") == before + 1
    assert c.get("/api/vs/result/not.real").status_code == 404


def test_the_card_shows_the_result_and_no_birth_data(c):
    d = c.post("/api/vs", json=BODY).json()
    r = c.get(f"/api/vs/card/{d['token']}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg")
    assert "Sam &amp; Theo" in r.text and f">{d['score']}<" in r.text and "mysignapp.com" in r.text
    assert A not in r.text and B not in r.text
    assert c.get("/api/vs/card/not.real.svg").status_code == 404


def test_a_card_without_names_still_reads(c):
    d = c.post("/api/vs", json={"date_a": A, "date_b": B}).json()
    assert d["names"] == ["", ""]
    assert "You &amp; Them" in c.get(f"/api/vs/card/{d['token']}.svg").text
    assert "We got" in c.get(f"/api/vs/r/{d['token']}").text


def test_the_funnel_counts_made_and_shared(c):
    made, shared = _count("check_made"), _count("check_shared")
    c.post("/api/vs", json=BODY)
    assert c.post("/api/vs/shared").status_code == 200
    assert (_count("check_made"), _count("check_shared")) == (made + 1, shared + 1)


def test_a_refused_check_is_not_counted(c):
    made = _count("check_made")
    c.post("/api/vs", json={**BODY, "date_b": _years_ago(16)})
    assert _count("check_made") == made


# ---------------------------------------------------------------- the handoff
def test_saving_from_the_check_counts_once_and_a_normal_add_does_not():
    u = person("check-handoff@example.com")
    before = _count("check_joined")
    plain = u.post("/api/connections", json={"name": "Ana", "date": B})
    assert plain.status_code == 200 and _count("check_joined") == before
    for name in ("Theo", "Bea"):
        got = u.post("/api/connections", json={"name": name, "date": B, "source": "check"})
        assert got.status_code == 200
    assert _count("check_joined") == before + 1


def _profile_people(u):
    return u.get("/api/export").json()["profile"].get("people")


def test_a_nameless_person_saves_but_is_not_learned_as_someone_named_them():
    u = person("check-nameless@example.com")
    assert u.post("/api/connections", json={"name": "Them", "date": B, "source": "check"}).status_code == 200
    assert "Them" not in str(_profile_people(u))
    assert u.post("/api/connections", json={"name": "Theo", "date": B, "source": "check"}).status_code == 200
    assert "Theo" in str(_profile_people(u))


# ------------------------------------------------------------------- the page
def test_the_page_is_served_and_indexed(c):
    r = c.get("/check")
    assert r.status_code == 200 and "<title>Are you two compatible?" in r.text
    assert 'name="robots"' not in r.text
    assert 'href="https://mysignapp.com/check"' in r.text


def test_the_page_says_what_it_keeps_and_who_it_is_for(c):
    text = c.get("/check").text
    assert "We don’t save either birthday" in text and "18 and over" in text
    assert "/check" in c.get("/privacy").text


def test_the_page_never_puts_a_date_in_a_url(c):
    text = c.get("/check").text
    assert "date_a=" not in text and "encodeURIComponent(a)" not in text
    assert "last.url" in text and "last.token" in text
