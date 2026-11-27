"""
Circles and "Before you date".

What these pin, in the order the promises are made in the UI:
- every number in a circle is the same number the person's own page shows
  (daily.py), and every superlative is a stated formula over real scores;
- a linked friend's mood shows only while they share it, never on a card,
  and nobody's check-in text appears anywhere;
- a quick check writes nothing until "Save to People", and the card carries
  no birth date;
- without a birth time, Moon and angle contacts are left out, not guessed;
- one price list, no per-person or per-circle fees, no predictions.
"""
import datetime as dt
import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import app.server as server
from engine import compat, daily, plan, store
from engine import social as SOC
from engine.chart import SIGNS, positions
from tests.test_app import sign_in  # noqa: F401  (sets the dev-auth env first)
from tests.test_invites import invite


def person(email, name="Pat", date="1996-07-02", time_="21:15"):
    """test_invites.person, keeping the uid the sign-in returned."""
    c = TestClient(server.app)
    code = c.post("/api/auth/request", json={"email": email}).json()["dev_code"]
    c.uid = c.post("/api/auth/verify", json={"email": email, "code": code}).json()["uid"]
    c.post("/api/consent", json={"ai": True})
    r = c.post("/api/onboard", json={"name": name, "date": date, "time": time_, "city": "Chicago IL"})
    assert r.status_code == 200, r.text
    return c

# Predictions, not the noun: "Will against will" is a contact's meaning, "he will
# leave" is a forecast. Plus the sign-stereotype sentence the memory rule bans.
BANNED = re.compile(r"\b(?:he|she|they|it|you|this|things)\s+will\b"
                    r"|\bwill\s+(?:be|happen|cheat|leave|fall|end|go|break|work)\b"
                    r"|guaranteed|going to happen|because you'?re an? ", re.I)


def add(c, name, date, time_="", city="Chicago IL"):
    r = c.post("/api/connections", json={"name": name, "date": date, "time": time_, "city": city})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def three(c):
    return [add(c, "Theo Grant", "1996-07-02", "21:15"),
            add(c, "Sam", "1997-11-20"),
            add(c, "Leila", "2000-08-09", "16:30")]


def all_text(obj):
    return json.dumps(obj, ensure_ascii=False)


@pytest.fixture
def prices_live(monkeypatch):
    """Plus purchasable, so the free limits are enforced."""
    monkeypatch.setitem(plan.PRICES, "available", True)


# ===================================================================== circles

def test_circle_crud_and_validation():
    c = person("soc-crud@x.com", "Maya")
    ids = three(c)
    assert c.get("/api/circles").json()["circles"] == []
    bad = [({"name": "", "members": ids}, 400),
           ({"name": "x", "members": ids[:1]}, 400),                 # her plus one is a pair
           ({"name": "x", "members": [ids[0], "nope00000000"]}, 400),
           ({"name": "x", "members": ids + ["a"] * 6}, 422)]         # over eight
    for body, code in bad:
        assert c.post("/api/circles", json=body).status_code == code, body
    r = c.post("/api/circles", json={"name": "  the   girls ", "members": ids + [ids[0]]})
    assert r.status_code == 200
    circ = r.json()
    assert circ["name"] == "the girls" and circ["count"] == 3        # repeats dropped
    listed = c.get("/api/circles").json()
    assert [x["id"] for x in listed["circles"]] == [circ["id"]] and listed["people"] == 3
    assert c.delete(f"/api/circles/{circ['id']}").json() == {"ok": True}
    assert c.delete(f"/api/circles/{circ['id']}").status_code == 404
    assert c.get(f"/api/circles/{circ['id']}/today").status_code == 404


def test_circles_are_hers_alone():
    a, b = person("soc-own-a@x.com", "Maya"), person("soc-own-b@x.com", "Theo")
    circ = a.post("/api/circles", json={"name": "g", "members": three(a)}).json()
    assert b.get(f"/api/circles/{circ['id']}/today").status_code == 404
    assert b.get(f"/api/card/circle/{circ['id']}.svg").status_code == 404
    assert b.delete(f"/api/circles/{circ['id']}").status_code == 404


def test_circle_today_is_daily_py_number_for_number():
    c = person("soc-today@x.com", "Maya")
    ids = three(c)
    circ = c.post("/api/circles", json={"name": "the girls", "members": ids}).json()
    d = c.get(f"/api/circles/{circ['id']}/today").json()
    assert d["count"] == 3 and {p["id"] for p in d["ranked"]} == set(ids)
    for p in d["ranked"]:
        own = c.get(f"/api/connections/{p['id']}/today").json()
        assert (p["score"], p["delta"], p["mood"]) == (own["score"], own["delta"], own["mood"])
    # Ranked by daily.rank_people's order: most moved first.
    keys = [(-abs(p["delta"]), -p["score"]) for p in d["ranked"]]
    assert keys == sorted(keys)
    assert d["in_sync"]["score"] == max(p["score"] for p in d["ranked"])
    # The Moon's sign is the ephemeris's, at her local noon.
    tz = server._tz(_uid(c))
    moon = positions(daily.local_noon_jd(dt.date.fromisoformat(d["date"]), tz))["Moon"]["lon"]
    assert d["moon"]["sign"] == SIGNS[int(moon // 30)]
    assert d["moon"]["text"].startswith(f"Moon in {d['moon']['sign']}")
    # Every superlative states its number.
    for s in d["week"]:
        assert re.search(r"\d", s["says"]), s
    assert not BANNED.search(all_text(d))


def _uid(c):
    return c.uid


def _rows(c):
    return {r["id"]: r for r in store.load(_uid(c), "connections", [])}


def test_every_pair_is_scored_not_only_hers():
    """n members: n pairs with her, n*(n-1)/2 between them, each by the same
    compat.deep + daily.day_score the rest of the app uses."""
    c = person("soc-pairs@x.com", "Maya")
    ids = three(c) + [add(c, "Ana", "1991-03-03")]
    circ = c.post("/api/circles", json={"name": "g", "members": ids}).json()
    d = c.get(f"/api/circles/{circ['id']}/today").json()
    n = len(ids)
    assert d["pair_count"] == len(d["pairs"]) == n + n * (n - 1) // 2
    keys = {frozenset((p["a"]["id"], p["b"]["id"])) for p in d["pairs"]}
    assert len(keys) == len(d["pairs"])                         # no pair twice
    # Hers are the numbers on each member's row.
    by = {p["id"]: p for p in d["ranked"]}
    for p in (p for p in d["pairs"] if p["yours"]):
        r = by[p["b"]["id"]]
        assert (p["score"], p["delta"], p["mood"]) == (r["score"], r["delta"], r["mood"])
    # Theirs, recomputed here from the stored rows.
    rows, tz = _rows(c), server._tz(_uid(c))
    today = dt.date.fromisoformat(d["date"])
    members = [p for p in d["pairs"] if not p["yours"]]
    assert len(members) == n * (n - 1) // 2
    for p in members:
        a, b = rows[p["a"]["id"]], rows[p["b"]["id"]]
        pa, pb = compat.their_precision(a), compat.their_precision(b)
        rep = compat.deep({"birth_ut": a["birth_ut"]}, {"birth_ut": b["birth_ut"]},
                          kind=SOC.PAIR_KIND, precision_a=pa, precision_b=pb, today=today)
        s = daily.day_score(rep, daily._natal(tuple(a["birth_ut"]), pa),
                            daily._natal(tuple(b["birth_ut"]), pb), today, tz,
                            moon_mine=pa != "unknown", moon_theirs=pb != "unknown")
        assert (p["score"], p["usual"], p["delta"], p["mood"]) == (
            s["score"], rep["score"]["overall"], s["delta"], s["mood"])
    assert [p["score"] for p in d["pairs"]] == sorted((p["score"] for p in d["pairs"]),
                                                      reverse=True)
    assert not BANNED.search(all_text(d))


def test_pair_superlatives_are_superlatives_over_pairs():
    c = person("soc-pairsup@x.com", "Maya")
    ids = three(c)
    circ = c.post("/api/circles", json={"name": "g", "members": ids}).json()
    d = c.get(f"/api/circles/{circ['id']}/today").json()
    rows, tz = _rows(c), server._tz(_uid(c))
    today = dt.date.fromisoformat(d["date"])
    weeks = {}
    for p in d["pairs"]:
        if p["yours"]:
            t = c.get(f"/api/connections/{p['b']['id']}/today").json()
            past = [s for s in t["series"] if s["date"] <= t["date"]][-7:]
        else:
            a, b = rows[p["a"]["id"]], rows[p["b"]["id"]]
            past = SOC.pair_week(a["birth_ut"], compat.their_precision(a), b["birth_ut"],
                                 compat.their_precision(b), today, tz)["series"]
        assert len(past) == 7
        weeks[p["id"]] = {"name": f"{p['a']['name']} & {p['b']['name']}", "series": past}
    assert SOC.pair_superlatives(weeks) == d["pair_week"]
    assert d["pair_week"][0]["key"] == "in_sync" and d["pair_week"][0]["label"] == "Most in sync pair"
    assert {s["key"] for s in d["pair_week"]} <= {"in_sync", "steadiest", "most_charged"}


def test_pairs_are_cached_per_day_and_follow_changes():
    c = person("soc-paircache@x.com", "Maya")
    ids = three(c)
    circ = c.post("/api/circles", json={"name": "g", "members": ids}).json()
    first = c.get(f"/api/circles/{circ['id']}/today").json()["pairs"]
    key = f"circlepairs-{circ['id']}"
    cached = store.load(_uid(c), key, None)
    assert cached and len(cached["pairs"]) == 3
    assert "Theo" not in json.dumps(cached)                     # names applied at read time
    assert c.get(f"/api/circles/{circ['id']}/today").json()["pairs"] == first
    # Initials come from the same cache.
    ini = c.get(f"/api/card/circle/{circ['id']}.svg?initials=1").text
    assert "Theo" not in ini and "T." in ini
    # A member gone means a different set of pairs, never the stale one.
    c.delete(f"/api/connections/{ids[2]}")
    after = c.get(f"/api/circles/{circ['id']}/today").json()
    assert after["pair_count"] == 2 + 1
    c.delete(f"/api/circles/{circ['id']}")
    assert store.load(_uid(c), key, None) is None


def test_circle_card_has_the_pair_grid():
    c = person("soc-paircard@x.com", "Maya Rose")
    ids = three(c)
    circ = c.post("/api/circles", json={"name": "the girls", "members": ids}).json()
    d = c.get(f"/api/circles/{circ['id']}/today").json()
    svg = c.get(f"/api/card/circle/{circ['id']}.svg").text
    assert "EVERY PAIR, TODAY" in svg and "Maya" in svg and "Rose" not in svg
    for p in d["pairs"]:
        assert f">{p['score']}</text>" in svg
    for birth in ("1996", "1997-11-20", "Chicago", "21:15", "2000-08-09"):
        assert birth not in svg


def test_week_matches_daily_series():
    """The superlatives are computed from exactly the scores daily.series gives."""
    c = person("soc-week@x.com", "Maya")
    ids = three(c)
    circ = c.post("/api/circles", json={"name": "g", "members": ids}).json()
    d = c.get(f"/api/circles/{circ['id']}/today").json()
    full = {p["id"]: c.get(f"/api/connections/{p['id']}/today").json() for p in d["ranked"]}
    weeks = {}
    for cid, t in full.items():
        past = [s for s in t["series"] if s["date"] <= t["date"]][-7:]
        weeks[cid] = {"name": t["person"]["name"].split()[0], "series": past}
    assert SOC.superlatives(weeks) == d["week"]


def test_superlatives_pick_the_right_winners():
    days = [f"2026-10-{n:02d}" for n in range(19, 26)]

    def w(name, scores):
        assert len(scores) == len(days)            # what zip(strict=True) checked; that's 3.10+
        return {"name": name, "series": [{"date": d, "score": s, "delta": 0}
                                         for d, s in zip(days, scores)]}
    weeks = {"a": w("Ana", [60, 60, 60, 61, 60, 60, 60]),      # steady
             "b": w("Bea", [70, 72, 71, 70, 73, 72, 70]),      # warm
             "c": w("Cat", [40, 60, 35, 65, 45, 70, 30])}      # swings
    got = {s["key"]: s for s in SOC.superlatives(weeks)}
    assert got["warmest"]["name"] == "Bea" and got["warmest"]["says"] == "averaged 71 over 7 days"
    assert got["steadiest"]["name"] == "Ana"
    assert got["most_charged"]["name"] == "Cat" and got["most_charged"]["value"] > 10
    # Best day: highest mean across all three. 2026-10-24 = (60+72+70)/3 = 67.3.
    assert got["best_day"]["date"] == "2026-10-24" and got["best_day"]["name"] == "Saturday"
    assert "averaged 67" in got["best_day"]["says"]


def test_no_most_charged_when_nobody_moved():
    days = [f"2026-10-{n:02d}" for n in range(19, 26)]
    flat = lambda n, v: {"name": n, "series": [{"date": d, "score": v, "delta": 0} for d in days]}  # noqa: E731
    keys = [s["key"] for s in SOC.superlatives({"a": flat("Ana", 50), "b": flat("Bea", 51)})]
    assert "most_charged" not in keys
    # Ties break by name, every time.
    got = SOC.superlatives({"b": flat("Bea", 50), "a": flat("Ana", 50)})
    assert got[0]["name"] == "Ana" and got[1]["name"] == "Ana"


def test_removed_member_drops_out():
    c = person("soc-drop@x.com", "Maya")
    ids = three(c)
    circ = c.post("/api/circles", json={"name": "g", "members": ids}).json()
    c.delete(f"/api/connections/{ids[1]}")
    assert c.get("/api/circles").json()["circles"][0]["count"] == 2
    assert c.get(f"/api/circles/{circ['id']}/today").json()["count"] == 2


def test_one_free_circle_then_plus(prices_live):
    c = person("soc-gate@x.com", "Maya")
    ids = three(c)
    assert c.post("/api/circles", json={"name": "one", "members": ids}).status_code == 200
    r = c.post("/api/circles", json={"name": "two", "members": ids[:2]})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "plus_circles"
    plan.grant(_uid(c), "plus", "test")
    assert c.post("/api/circles", json={"name": "two", "members": ids[:2]}).status_code == 200


def test_no_gate_while_plus_cannot_be_bought():
    assert plan.PRICES["available"] is False
    c = person("soc-nogate@x.com", "Maya")
    ids = three(c)
    for n in range(3):
        assert c.post("/api/circles", json={"name": f"c{n}", "members": ids}).status_code == 200


def test_linked_mood_only_when_shared_and_never_on_the_card():
    a, b = person("soc-mood-a@x.com", "Maya"), person("soc-mood-b@x.com", "Theodora Quill")
    b.post(f"/api/invites/{invite(a)['token']}/accept", json={"share_chart": True, "share_mood": True})
    b.post("/api/checkin", json={"mood": 8, "text": "SECRET-NOTE-xyz about my week"})
    theo = next(r for r in a.get("/api/connections").json()["connections"] if r["linked"])
    others = [add(a, "Sam", "1997-11-20"), add(a, "Leila", "2000-08-09", "16:30")]
    circ = a.post("/api/circles", json={"name": "crew", "members": [theo["id"], *others]}).json()

    d = a.get(f"/api/circles/{circ['id']}/today").json()
    row = next(p for p in d["ranked"] if p["id"] == theo["id"])
    assert row["their_day"]["mood"] == 8
    svg = a.get(f"/api/card/circle/{circ['id']}.svg").text
    assert "SECRET-NOTE" not in all_text(d)
    for leak in ("SECRET-NOTE", "8/10", "feeling"):           # the card never shows a mood
        assert leak not in svg
    assert "Theodora" in svg and "Quill" not in svg             # first names only

    b.post("/api/profile/share-mood", json={"on": False})
    d = a.get(f"/api/circles/{circ['id']}/today").json()
    row = next(p for p in d["ranked"] if p["id"] == theo["id"])
    assert row["their_day"] == {"private": True}
    assert "SECRET-NOTE" not in all_text(d)


def test_circle_card_initials_and_no_birth_data():
    c = person("soc-card@x.com", "Maya")
    ids = three(c)
    circ = c.post("/api/circles", json={"name": "the girls", "members": ids}).json()
    r = c.get(f"/api/card/circle/{circ['id']}.svg?initials=1")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    svg = r.text
    assert "the girls" in svg and "T." in svg and "Theo" not in svg and "Leila" not in svg
    for birth in ("1996", "1997-11-20", "Chicago", "21:15"):
        assert birth not in svg
    assert 'width="1080" height="1350"' in svg


def test_deleting_her_account_takes_the_circles():
    c = person("soc-del@x.com", "Maya")
    uid = _uid(c)
    c.post("/api/circles", json={"name": "g", "members": three(c)})
    assert store.load(uid, "circles", None)
    c.delete("/api/account")
    assert store.load(uid, "circles", None) is None


# ============================================================ before you date

def _snapshot():
    root = pathlib.Path(store.ROOT)
    return {str(p): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}


def test_quick_check_writes_nothing():
    c = person("soc-q-none@x.com", "Maya")
    before = _snapshot()
    r = c.post("/api/check", json={"name": "Jakeperson Smith", "date": "1997-11-20"})
    assert r.status_code == 200
    c.post("/api/card/check.svg", json={"name": "Jakeperson", "date": "1997-11-20"})
    after = _snapshot()
    changed = {p for p in after if before.get(p) != after[p]}
    for p in changed:
        txt = pathlib.Path(p).read_text(errors="ignore")
        assert "Jakeperson" not in txt and "1997-11-20" not in txt, p
    assert c.get("/api/connections").json()["count"] == 0


def test_save_makes_an_ordinary_person():
    c = person("soc-q-save@x.com", "Maya")
    out = c.post("/api/check", json={"name": "Jake", "date": "1997-11-20", "time": "21:15",
                                     "city": "London, GB"}).json()
    s = out["save"]
    assert s["city"] and s["time"] == "21:15"
    r = c.post("/api/connections", json={**s, "time": s["time"] or ""})
    assert r.status_code == 200 and r.json()["name"] == "Jake"
    assert c.get("/api/connections").json()["count"] == 1


def test_numbers_are_compat_and_daily():
    c = person("soc-q-real@x.com", "Maya")
    uid = _uid(c)
    out = c.post("/api/check", json={"name": "Jake", "date": "1997-11-20"}).json()
    prof = store.load(uid, "profile", None)
    ut = [1997, 11, 20, 12.0, 0.0, 0.0]
    tz = server._tz(uid)
    today = dt.datetime.now(tz).date()
    rep = compat.deep({"birth_ut": prof["birth_ut"]}, {"birth_ut": ut}, kind="romantic",
                      precision_a=compat.my_precision(prof), precision_b="unknown",
                      name_b="Jake", today=today)
    assert out["score"] == rep["score"]["overall"]
    assert out["score_range"] == rep["score"]["overall_range"]
    assert out["tier"] == rep["score"]["band"]
    real = {(x["theirs"], x["aspect"], x["yours"]) for x in rep["contacts"]}
    for ct in out["top_contacts"]:
        assert (ct["theirs"], ct["aspect"], ct["yours"]) in real
    mine = daily._natal(tuple(prof["birth_ut"]), compat.my_precision(prof))
    theirs = daily._natal(tuple(ut), "unknown")
    scores = [daily.day_score(rep, mine, theirs, today + dt.timedelta(days=i), tz, "Jake",
                              moon_theirs=False)["score"] for i in range(7)]
    assert out["best_day_this_week"]["score"] == max(scores)


def test_date_only_suppresses_moon_and_angles():
    c = person("soc-q-prec@x.com", "Maya")
    seen_moon = False
    for date in ("1997-11-20", "1990-01-05", "2001-04-17", "1995-09-09", "1999-12-31"):
        out = c.post("/api/check", json={"name": "Jake", "date": date}).json()
        assert out["precision"] == "date"
        assert "birthday-only read" in out["confidence"]
        assert all(ct["theirs"] not in ("Moon", "Ascendant", "Midheaven")
                   for ct in out["top_contacts"])
        full = c.post("/api/check", json={"name": "Jake", "date": date, "time": "03:00",
                                          "city": "London, GB"}).json()
        assert full["precision"] == "exact"
        seen_moon |= any(ct["theirs"] == "Moon" for ct in full["top_contacts"])
    # A time without a place is set aside, and she is told.
    out = c.post("/api/check", json={"name": "Jake", "date": "1997-11-20", "time": "21:15"}).json()
    assert out["precision"] == "date" and out["notes"] and out["save"]["time"] is None


def test_quick_check_validation():
    c = person("soc-q-bad@x.com", "Maya")
    future = (dt.date.today() + dt.timedelta(days=30)).isoformat()
    for body in ({"name": " ", "date": "1997-11-20"}, {"name": "J", "date": "nope"},
                 {"name": "J", "date": future}, {"name": "J", "date": "1997-11-20", "time": "25:99"},
                 {"name": "J", "date": "1997-11-20", "city": "Xqzzyville Nowhere"},
                 {"name": "J", "date": "1997-11-20", "kind": "enemy"}):
        assert c.post("/api/check", json=body).status_code == 400, body
    fresh = TestClient(server.app)
    assert fresh.post("/api/check", json={"name": "J", "date": "1997-11-20"}).status_code == 401


def test_free_sees_one_contact_plus_sees_three(prices_live):
    c = person("soc-q-gate@x.com", "Maya")
    out = c.post("/api/check", json={"name": "Jake", "date": "1997-11-20"}).json()
    assert out["plus_required"] and len(out["top_contacts"]) <= 1
    assert out["best_day_this_week"] is None and "score" in out
    plan.grant(_uid(c), "plus", "test")
    out = c.post("/api/check", json={"name": "Jake", "date": "1997-11-20"}).json()
    assert out["full"] and "plus_required" not in out and len(out["top_contacts"]) == 3


def test_price_list_is_unchanged():
    """The prices are pinned; the list of what they buy may grow, and circles
    are on it — with the free allowance said in the same line."""
    prices = {k: v for k, v in plan.PRICES.items() if k != "features"}
    assert prices == {"monthly": {"price": "$9.99", "period": "month"},
                      "annual": {"price": "$59.99", "period": "year",
                                 "note": "$5/mo, save 49%"},
                      "trial_days": 7, "available": False}
    circles = [f for f in plan.PRICES["features"] if "ircle" in f]
    assert circles == ["Circles: your first circle is free, and Plus lets you have as many as you like"]
    assert not any("$" in f for f in plan.PRICES["features"])      # no second price list


def test_full_view_is_one_rule(prices_live):
    c = person("soc-fullview@x.com", "Maya")
    assert plan.full_view(_uid(c)) is False
    plan.grant(_uid(c), "plus", "test")
    assert plan.full_view(_uid(c)) is True


def test_check_card_has_no_birth_data_and_needs_post():
    c = person("soc-q-card@x.com", "Maya")
    body = {"name": "Jake Smith", "date": "1997-11-20", "time": "21:15", "city": "London, GB"}
    r = c.post("/api/card/check.svg", json=body)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    svg = r.text
    for leak in ("1997", "11-20", "21:15", "London", "Smith"):
        assert leak not in svg, leak
    assert "Jake" in svg and "Maya" in svg
    svg = c.post("/api/card/check.svg", json={**body, "initials": True}).text
    assert "Jake" not in svg and "J." in svg
    assert c.get("/api/card/check.svg").status_code == 405       # never in a URL


def test_copy_never_predicts():
    c = person("soc-q-lint@x.com", "Maya")
    for date in ("1997-11-20", "1990-01-05", "2001-04-17"):
        out = c.post("/api/check", json={"name": "Jake", "date": date}).json()
        text = " ".join([out["confidence"], *out["caveats"], *out["notes"]]
                        + [f"{x['text']} {x['meaning']}" for x in out["top_contacts"]]
                        + [json.dumps(out["best_day_this_week"])])
        assert not BANNED.search(text), BANNED.search(text)
        assert "Jake is a" not in text and "he is a" not in text.lower()
