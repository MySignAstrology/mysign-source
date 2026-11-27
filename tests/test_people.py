"""
team2/people: the "Best day to..." planner and the mutual reveal.

What these pin:
- every planner reason is re-derived from the ephemeris here: the transit,
  the point it crosses and the aspect must actually hold at that day's local
  noon — nothing in the week is invented;
- the ranking is deterministic and an intent reorders days only through the
  documented weights;
- no forecast language; days near usual say "about usual";
- the free slice never fakes a score and never touches the morning push;
- reveal: rounds are weekly with one nudge a week; the sky is the past
  week folded into dated stretches; the partner's answer is absent until both answered, the note never
  leaves its author, only mutually linked pairs can play, and unlinking or
  deleting the account removes the pair's reveals.
"""
import datetime as dt
import json
import re

from fastapi.testclient import TestClient

import app.server as server
from engine import compat, daily, plan, planner, store
from engine import reveal as RV
from engine.chart import positions
from tests.test_invites import invite, linked_row, person


def link(a, b):
    r = b.post(f"/api/invites/{invite(a)['token']}/accept", json={"share_chart": True})
    assert r.status_code == 200, r.text


def add_unlinked(c, name="Jake"):
    hits = c.get("/api/cities", params={"q": "Chicago"}).json()
    r = c.post("/api/connections", json={"name": name, "date": "1995-04-11", "time": "10:20",
                                         "city": hits[0]["id"]})
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ================================================================ planner

def _lon(chart, point):
    src = chart["angles"] if point in ("Ascendant", "Midheaven") else chart["planets"]
    return src[point]["lon"]


def test_every_week_reason_holds_in_the_ephemeris():
    """Re-check each reason against positions() at local noon: the named
    transit really makes the named aspect to the named point that day."""
    a = person("plan-eph@x.com", "Maya")
    cid = add_unlinked(a)
    uid = server.auth.uid_for("plan-eph@x.com", create=False)
    w = a.get(f"/api/connections/{cid}/plan", params={"intent": "talk"}).json()
    assert len(w["days"]) == 7
    # Rebuild the same natal and composite inputs the server used.
    rows = store.load(uid, "connections", [])
    row = next(r for r in rows if r["id"] == cid)
    prof = store.load(uid, "profile", {})
    rep = compat.report_for(uid, row)
    pm, pt = compat.my_precision(prof), compat.their_precision(row)
    charts = {"you": daily._natal(tuple(prof["birth_ut"]), pm),
              "them": daily._natal(tuple(row["birth_ut"]), pt)}
    tz = server._tz(uid)
    checked = 0
    for d in w["days"]:
        sky = positions(daily.local_noon_jd(dt.date.fromisoformat(d["date"]), tz))
        for r in d["reasons"]:
            t = r["target"]
            lon = (rep["composite"]["positions"][t["point"]] if t["chart"] == "composite"
                   else _lon(charts[t["chart"]], t["point"]))
            orb = daily.ORB[r["transit"]] if r["kind"] != "contact" else min(daily.ORB[r["transit"]], 3.0)
            got = daily._aspect(sky[r["transit"]]["lon"], lon, orb)
            assert got and got[0] == r["aspect"], (d["date"], r)
            assert abs(got[1] - r["orb"]) < 0.02
            assert r["lasts"]
            checked += 1
    assert checked, "a week with no reasons at all checks nothing"


def _days(deltas_and_reasons):
    start = dt.date(2026, 11, 2)
    out = []
    for i, (delta, reasons) in enumerate(deltas_and_reasons):
        d = (start + dt.timedelta(days=i)).isoformat()
        out.append({"date": d, "weekday": dt.date.fromisoformat(d).strftime("%A"),
                    "score": 60 + delta, "usual": 60, "delta": delta,
                    "label": planner.label(delta), "reasons": reasons})
    return out


def R(transit, weight, aspect="trine"):
    return {"transit": transit, "weight": weight, "aspect": aspect, "orb": 1.0, "kind": "pair",
            "target": {"chart": "composite", "point": "Moon"},
            "text": f"{transit} {aspect}s the relationship's Moon", "short": "x",
            "lasts": "lasts a few days"}


def test_intents_reorder_only_through_documented_weights():
    days = _days([(2, [R("Mercury", 0.9)]), (3, [R("Venus", 0.9)]), (0, []),
                  (-6, [R("Mars", -0.9, "square")]), (1, []), (0, []), (1, [])])
    talk = planner.plan("talk", days, "Jake")
    date = planner.plan("date", days, "Jake")
    space = planner.plan("space", days, "Jake")
    assert talk["best_day"]["date"] == "2026-11-02"      # Mercury day
    assert date["best_day"]["date"] == "2026-11-03"      # Venus day
    assert space["best_day"]["date"] == "2026-11-05"     # the hard-Mars day
    # The fit is exactly the documented formula.
    assert talk["days"][0]["fit"] == round(2 + planner.SCALE * 0.9 * 1.5, 2)
    # Ask: a hard Mars counts extra against, an easy one does not count for.
    assert planner.fit("ask", {"delta": 0, "reasons": [R("Mars", 0.5)]}) == 0
    assert planner.fit("ask", {"delta": 0, "reasons": [R("Mars", -0.5, "square")]}) < -4


def test_ranking_is_deterministic_and_ties_go_to_the_earlier_day():
    days = _days([(1, []), (1, []), (1, []), (1, []), (1, []), (1, []), (1, [])])
    a = planner.rank("talk", days)
    b = planner.rank("talk", list(reversed(days)))
    assert [d["date"] for d in a] == [d["date"] for d in b] == sorted(d["date"] for d in days)


def test_a_flat_week_says_so_instead_of_crowning_a_day():
    p = planner.plan("date", _days([(1, [])] * 7), "Jake")
    assert not p["standout"]
    assert "No day this week stands out" in p["summary"]
    assert all(d["label"] == "about usual" for d in p["days"])


def test_two_intents_differ_on_a_real_pair():
    """Over a few real pairs, intents must not all collapse to one ranking."""
    a = person("plan-two@x.com", "Maya")
    cid = add_unlinked(a)
    tops = {i: a.get(f"/api/connections/{cid}/plan", params={"intent": i}).json()["best_day"]["date"]
            for i in planner.INTENTS}
    assert len(set(tops.values())) >= 2, tops
    # And the same request twice gives the same answer.
    assert a.get(f"/api/connections/{cid}/plan", params={"intent": "talk"}).json()["best_day"]["date"] == tops["talk"]


def test_planner_copy_never_predicts():
    a = person("plan-lint@x.com", "Maya")
    cid = add_unlinked(a)
    for i in planner.INTENTS:
        p = a.get(f"/api/connections/{cid}/plan", params={"intent": i}).json()
        texts = [p["summary"], p["verb"], p["listens_to"]] + [d["line"] for d in p["days"]] \
            + [d["label"] for d in p["days"]]
        for t in texts:
            assert not planner.PREDICTIVE.search(t), t
        for d in p["days"]:
            if abs(d["delta"]) < daily.STEADY:
                assert d["label"] == "about usual"


def test_bad_intent_and_unknown_person():
    a = person("plan-bad@x.com", "Maya")
    cid = add_unlinked(a)
    assert a.get(f"/api/connections/{cid}/plan", params={"intent": "marry"}).status_code == 400
    assert a.get("/api/connections/nope/plan").status_code == 404


def test_free_plan_gets_the_best_day_only_once_billing_exists(monkeypatch):
    a = person("plan-free@x.com", "Maya")
    cid = add_unlinked(a)
    full = a.get(f"/api/connections/{cid}/plan", params={"intent": "talk"}).json()
    assert full["plus_required"] is False and len(full["days"]) == 7   # no gating yet
    monkeypatch.setitem(plan.PRICES, "available", True)
    free = a.get(f"/api/connections/{cid}/plan", params={"intent": "talk"}).json()
    assert free["plus_required"] is True and free["days"] is None
    # The day and its score are the real ones — the free slice is not a teaser number.
    assert free["best_day"] == full["best_day"]
    assert "reasons" not in json.dumps(free)
    # The morning push is never on this path: the cron does not read the planner.
    import app.cron as cron
    with open(cron.__file__) as fh:
        assert "planner" not in fh.read()


def test_plus_sees_the_week_when_billing_exists(monkeypatch):
    a = person("plan-plus@x.com", "Maya")
    cid = add_unlinked(a)
    monkeypatch.setitem(plan.PRICES, "available", True)
    monkeypatch.setattr(plan, "get", lambda uid: {"tier": "plus"})
    p = a.get(f"/api/connections/{cid}/plan", params={"intent": "date"}).json()
    assert p["plus_required"] is False and len(p["days"]) == 7
    assert plan.PRICES["monthly"]["price"] == "$9.99"                  # no new price anywhere


# ================================================================ reveal

def pair(tag):
    a, b = person(f"rv-{tag}-a@x.com", "Maya"), person(f"rv-{tag}-b@x.com", "Theo")
    link(a, b)
    return a, b, linked_row(a, "Theo")["id"], linked_row(b, "Maya")["id"]


def test_nothing_of_theirs_until_both_answered():
    a, b, a_cid, b_cid = pair("hide")
    r = a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "strained", "note": "he forgot dinner"})
    assert r.status_code == 200
    mine = r.json()
    assert mine["state"] == "waiting" and mine["theirs"] is None
    assert mine["mine"] == {"feel": "strained", "note": "he forgot dinner"}
    # Theo sees that Maya answered — the invitation — and nothing about what.
    t = b.get(f"/api/connections/{b_cid}/reveal")
    raw = t.text
    assert t.json()["state"] == "your_turn" and t.json()["they_answered"] is True
    assert "strained" not in raw and "dinner" not in raw
    assert b.get("/api/reveals/waiting").json()["waiting"] == [{"id": b_cid, "name": "Maya"}]
    # And Maya's view before Theo answers holds no hint of Theo at all.
    raw_a = a.get(f"/api/connections/{a_cid}/reveal").text
    assert '"theirs":null' in raw_a.replace(" ", "")
    # Theo answers; now both see both, and the sky that day, with dates.
    done = b.post(f"/api/connections/{b_cid}/reveal", json={"feel": "closer", "note": "felt fine to me"}).json()
    assert done["state"] == "revealed"
    assert done["theirs"] == {"feel": "strained"} and done["mine"]["feel"] == "closer"
    assert "together" in done
    sky = done["sky"]
    # The past week, ending the day the reveal happened — never ahead of it.
    assert (dt.date.fromisoformat(sky["to"]) - dt.date.fromisoformat(sky["from"])).days == 6
    assert sky["to"] <= server._today(server.auth.uid_for("rv-hide-b@x.com", create=False)).isoformat()
    assert len(sky["reasons"]) <= 3
    for r_ in sky["reasons"]:
        assert sky["from"] <= r_["from"] <= r_["to"] <= sky["to"] and r_["lasts"] and r_["text"]
    seen_a = a.get(f"/api/connections/{a_cid}/reveal").json()
    assert seen_a["theirs"] == {"feel": "closer"}
    assert b.get("/api/reveals/waiting").json()["waiting"] == []


def test_the_note_never_reaches_the_partner(monkeypatch):
    sent = []
    monkeypatch.setattr(server.push, "send", lambda uid, title, body, url="/", **_: sent.append(
        (uid, title, body, url)) or {"sent": 1})
    a, b, a_cid, b_cid = pair("note")
    a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "steady", "note": "SECRETWORD"})
    b.post(f"/api/connections/{b_cid}/reveal", json={"feel": "steady"})
    for path in (f"/api/connections/{b_cid}/reveal", "/api/reveals/waiting",
                 "/api/connections", "/api/people/today", f"/api/connections/{b_cid}/today"):
        assert "SECRETWORD" not in b.get(path).text, path
    assert all("SECRETWORD" not in json.dumps(s) for s in sent)
    # One push, to Theo, naming Maya and nothing she chose, deep-linked to her row.
    assert len(sent) == 1
    _, _, body, url = sent[0]
    assert "Maya answered" in body and "your turn" in body and "steady" not in body
    assert url == f"/app?p={b_cid}"
    assert not re.search(r"\$|Plus|upgrade|unlock", body)


def test_push_once_per_week_even_if_she_changes_her_answer(monkeypatch):
    sent = []
    monkeypatch.setattr(server.push, "send", lambda *a, **k: sent.append(a) or {"sent": 1})
    a, _b, a_cid, _ = pair("once")
    for f in ("steady", "closer", "strained"):
        a.post(f"/api/connections/{a_cid}/reveal", json={"feel": f})
    assert len(sent) == 1


def test_a_revealed_round_cannot_be_rewritten():
    a, b, a_cid, b_cid = pair("lock")
    a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "steady"})
    b.post(f"/api/connections/{b_cid}/reveal", json={"feel": "closer"})
    assert a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "strained"}).status_code == 409


def test_only_linked_pairs_and_valid_feels():
    a = person("rv-unl@x.com", "Maya")
    cid = add_unlinked(a)
    assert a.get(f"/api/connections/{cid}/reveal").status_code == 400
    assert a.post(f"/api/connections/{cid}/reveal", json={"feel": "steady"}).status_code == 400
    a2, _b2, a_cid, _ = pair("feel")
    assert a2.post(f"/api/connections/{a_cid}/reveal", json={"feel": "furious"}).status_code == 400


def test_one_sided_link_cannot_start_a_reveal():
    """Unlinking is mutual now (audit H4), so a one-sided link only exists in
    data from before that; made here by hand, it still can't start a reveal."""
    a, _b, a_cid, b_cid = pair("oneside")
    theo = server.auth.uid_for("rv-oneside-b@x.com", create=False)
    store.save(theo, "connections",                               # Theo's row for Maya, gone
               [r for r in store.load(theo, "connections", []) if r["id"] != b_cid])
    assert a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "steady"}).status_code == 400


def test_dropping_a_linked_person_unlinks_both_sides():
    a, b, a_cid, b_cid = pair("mutual")
    assert b.delete(f"/api/connections/{b_cid}").status_code == 200   # Theo drops Maya
    assert a.get(f"/api/connections/{a_cid}").status_code == 404      # gone for Maya too
    assert a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "steady"}).status_code == 404


def _reveal_keys():
    return store.keys(RV.NS)


def test_unlinking_deletes_pending_and_past_reveals():
    a, b, a_cid, b_cid = pair("unlink")
    a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "steady"})
    b.post(f"/api/connections/{b_cid}/reveal", json={"feel": "closer"})       # a past round
    keys = [k for k in _reveal_keys() if k.startswith("rv-")]
    assert keys
    before = len(keys)
    a.delete(f"/api/connections/{a_cid}")
    assert len([k for k in _reveal_keys() if k.startswith("rv-")]) == before - 1


def test_deleting_the_account_deletes_reveals():
    a, _b, a_cid, _ = pair("acct")
    a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "strained", "note": "x"})
    before = len([k for k in _reveal_keys() if k.startswith("rv-")])
    assert a.delete("/api/account").status_code == 200
    assert len([k for k in _reveal_keys() if k.startswith("rv-")]) == before - 1


def test_rounds_are_weekly_keyed_by_the_first_answerers_week():
    mon = dt.date(2026, 11, 2)                        # ISO 2026-W45, a Monday
    rec, wk, first = RV.answer(None, "u1", mon, "closer", now=1000.0)
    assert first and wk == "2026-W45" and rec["rounds"][wk]["day"] == "2026-11-02"
    # Thursday, same week: the partner joins the same round — weekly, not daily.
    rec, wk2, first2 = RV.answer(rec, "u2", dt.date(2026, 11, 5), "steady", now=1000.0 + 3 * 86400)
    assert wk2 == wk and not first2
    assert RV.view(rec, "u2", "u1", dt.date(2026, 11, 5), now=1000.0 + 3 * 86400)["state"] == "revealed"
    # Later that same week the revealed round stands; no second round opens.
    sat = dt.date(2026, 11, 7)
    assert RV.view(rec, "u1", "u2", sat, now=1000.0 + 5 * 86400)["state"] == "revealed"
    try:
        RV.answer(rec, "u1", sat, "strained", now=1000.0 + 5 * 86400)
        raise AssertionError("a revealed week was rewritten")
    except PermissionError:
        pass
    # The next Monday is a new week and a new round.
    rec, wk3, first3 = RV.answer(rec, "u1", dt.date(2026, 11, 9), "steady", now=1000.0 + 7 * 86400)
    assert wk3 == "2026-W46" and first3
    assert RV.history(rec, "u1", "u2") == [{"week": "2026-W45", "day": "2026-11-02",
                                           "mine": "closer", "theirs": "steady"}]
    # History never carries a note.
    assert "note" not in json.dumps(RV.history(rec, "u1", "u2"))


def test_a_short_bridge_only_across_the_week_boundary():
    sun = dt.date(2026, 11, 8)                        # last day of 2026-W45
    t0 = 50_000.0
    rec, wk, _ = RV.answer(None, "u1", sun, "closer", now=t0)
    assert wk == "2026-W45"
    # The partner is already on Monday (W46) a few hours later: same round.
    rec, wk2, first2 = RV.answer(rec, "u2", dt.date(2026, 11, 9), "steady", now=t0 + 6 * 3600)
    assert wk2 == wk and not first2
    # Reverse clocks: she opened W46 on her Monday, he is still on Sunday (W45).
    rec, wk, _ = RV.answer(None, "u1", dt.date(2026, 11, 9), "closer", now=t0)
    rec, wk2, _ = RV.answer(rec, "u2", sun, "steady", now=t0 + 3600)
    assert wk == wk2 == "2026-W46"
    # But a round opened early last week is not joinable this week: it lapsed.
    rec, wk, _ = RV.answer(None, "u1", dt.date(2026, 11, 3), "strained", now=t0)
    later = t0 + 6 * 86400
    rec, wk2, first2 = RV.answer(rec, "u2", dt.date(2026, 11, 9), "steady", now=later)
    assert wk2 == "2026-W46" and first2
    assert RV.view(rec, "u1", "u2", dt.date(2026, 11, 9), now=later)["state"] == "your_turn"


def test_one_nudge_per_week_per_pair(monkeypatch):
    sent = []
    monkeypatch.setattr(server.push, "send", lambda *a, **k: sent.append(a) or {"sent": 1})
    a, _b, a_cid, _ = pair("weekly")
    a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "steady"})
    assert len(sent) == 1
    uid_a = server.auth.uid_for("rv-weekly-a@x.com", create=False)
    uid_b = server.auth.uid_for("rv-weekly-b@x.com", create=False)
    k = RV.key(uid_a, uid_b)
    rec = store.load(RV.NS, k, None)
    wk = RV.week_of(server._today(uid_a))
    assert rec["nudged_week"] == wk
    # Even if the round is cleared and reopened inside the same week (a
    # bridged round, a reset), Theo is not pushed twice that week.
    rec["rounds"] = {}
    store.save(RV.NS, k, rec)
    a.post(f"/api/connections/{a_cid}/reveal", json={"feel": "closer"})
    assert len(sent) == 1
    # A new week may nudge again.
    assert RV.should_nudge(rec, "2099-W01")


def _sd(date, reasons):
    return {"date": date, "reasons": reasons}


def _r(text, weight, lasts="lasts a few days"):
    return {"text": text, "short": "s", "lasts": lasts, "transit": "Venus", "aspect": "trine",
            "weight": weight}


def test_reveal_sky_folds_reasons_into_date_ranges():
    days = [_sd("2026-11-02", [_r("Venus trines the relationship's Moon", 0.3)]),
            _sd("2026-11-03", [_r("Venus trines the relationship's Moon", 0.5),
                               _r("Mars squares your Venus", -0.9)]),
            _sd("2026-11-04", [_r("Venus trines the relationship's Moon", 0.4, "fading"),
                               _r("Mars squares your Venus", -0.7)]),
            _sd("2026-11-05", [_r("Mercury sextiles Theo's Sun", 0.1)]),
            _sd("2026-11-06", [_r("Moon conjoins your Moon", 0.05)]),
            _sd("2026-11-07", [_r("Venus trines the relationship's Moon", 0.2)]),
            _sd("2026-11-08", [])]
    got = RV.sky_ranges(days)
    assert [g["text"] for g in got] == ["Mars squares your Venus",
                                        "Venus trines the relationship's Moon",
                                        "Venus trines the relationship's Moon"]
    assert (got[0]["from"], got[0]["to"], got[0]["weight"]) == ("2026-11-03", "2026-11-04", -0.9)
    # Consecutive days are one stretch; a gap starts a new one.
    assert (got[1]["from"], got[1]["to"], got[1]["weight"], got[1]["lasts"]) == \
        ("2026-11-02", "2026-11-04", 0.5, "fading")
    assert (got[2]["from"], got[2]["to"]) == ("2026-11-07", "2026-11-07")
    assert {"text", "lasts", "from", "to"} <= set(got[0])
    assert RV.sky_ranges([_sd("2026-11-02", [])]) == []


def test_the_week_endpoint_is_gone():
    a = person("plan-noweek@x.com", "Maya")
    cid = add_unlinked(a)
    assert a.get(f"/api/connections/{cid}/week").status_code in (404, 405)


def test_anonymous_requests_are_refused():
    anon = TestClient(server.app)
    assert anon.get("/api/connections/x/plan").status_code in (401, 403)
    assert anon.post("/api/connections/x/reveal", json={"feel": "steady"}).status_code in (401, 403)
