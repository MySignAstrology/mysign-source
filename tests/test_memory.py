"""
team2/memory: pattern unlock moments and relationship-aware chat.

The rules pinned here are the product's promises:
- nothing unlocks that memory.verify() did not return, and every number on
  the reveal is verify()'s;
- each pattern unlocks once, and again only if it dropped out and came back;
- before the first pattern, progress is a count — never a claim;
- what she said about a person is hers, dated, deletable, and kept apart
  from what she said about anyone else;
- a linked person's own check-in words never reach her chat.
"""
import datetime as dt
import json
import re

import pytest
from fastapi.testclient import TestClient

import app.server as server
from engine import daily, personmem, tools, unlocks
from engine import memory as M
from engine.store import load, save
from tests.test_app import last_uid, onboard, sign_in

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra",
         "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]


def user(email):
    c = TestClient(server.app)
    sign_in(c, email)
    assert onboard(c).status_code == 200
    return c, last_uid()


def plant(uid, days=90, sign="Scorpio", strong=True, end=None):
    """
    Synthetic episodes: the Moon sign cycles through the zodiac every ~2.5
    days, and the mood is low on `sign` days when `strong`. Other days carry a
    fixed spread with some lows, so the base rate is not zero.
    """
    end = end or dt.date.today()
    eps = []
    for i in range(days, 0, -1):
        d = end - dt.timedelta(days=i)
        ms = SIGNS[(i * 2 // 5) % 12]
        mood = 3 if (strong and ms == sign) else (6, 7, 8, 5, 7, 4, 6, 8, 7, 6, 3, 7)[i % 12]
        eps.append({"date": d.isoformat(), "mood": mood, "summary": "a day",
                    "her_words": [], "people": [], "topics": [],
                    "sky_state": {"moon_phase": "waxing gibbous", "moon_sign": ms,
                                  "aspect_keys": [], "active_houses": []}})
    save(uid, "episodes", eps)
    return eps


CLAIMY = re.compile(r"you logged|pattern holds|because you", re.I)


# ------------------------------------------------------------- unlocks

def test_a_non_verified_proposal_never_unlocks():
    c, uid = user("unl-fake@x.com")
    plant(uid, strong=False)
    # A dict that looks like a pattern but that verify() would never return:
    # the data has no Aries effect at all.
    fake = {"claim": "On days the Moon is in Aries, you logged a mood below 5.",
            "condition": {"kind": "moon_sign", "value": "Aries", "window_days": 0},
            "outcome": {"kind": "mood_below", "value": "5"},
            "support": "9 of the last 9", "lift": 9.0, "p_value": 0.0001,
            "rate": 1.0, "base_rate": 0.1, "confidence": "high", "n_tests": 1}
    fake["id"] = unlocks.pattern_id(fake)
    save(uid, "patterns", [fake])
    assert c.get("/api/memory/unlocks").json() == {"unlocks": []}
    assert c.post(f"/api/memory/unlocks/{fake['id']}/seen").status_code == 404
    assert c.get(f"/api/card/pattern/{fake['id']}.svg").status_code == 404
    assert load(uid, unlocks.SEEN, None) in (None, {})
    assert c.get("/api/memory/today").json()["heads_up"] == []


def test_the_weekly_job_finds_the_planted_pattern_and_the_reveal_says_its_numbers():
    c, uid = user("unl-real@x.com")
    plant(uid)
    survivors = M.weekly_pattern_job(uid)          # no key: code proposes
    assert survivors, "the planted pattern should survive Bonferroni"
    top = survivors[0]
    assert top["condition"] == {"kind": "moon_sign", "value": "Scorpio", "window_days": 0}
    assert top["n_tests"] > 1 and top["id"] == unlocks.pattern_id(top)

    got = c.get("/api/memory/unlocks").json()["unlocks"]
    card = next(u for u in got if u["id"] == top["id"])
    # Every number is verify()'s.
    assert card["support"] == top["support"]
    assert card["lift"] == top["lift"] and card["p_value"] == top["p_value"]
    hit, of = card["hit"], card["of"]
    assert f"{hit} of the last {of}" == top["support"]
    # The headline says the lift in words, from verify()'s number; no p-value on it.
    assert unlocks.lift_words(top["lift"]) in card["text"] and "p<" not in card["text"]
    assert "you logged" in card["text"] and "pattern" in card["title"]
    assert not re.search(r"because you'?re an? \w+", card["text"], re.I)
    # The dates are the days the condition AND outcome held, recounted.
    eps = {e["date"]: e for e in load(uid, "episodes", [])}
    assert len(card["dates"]) == min(hit, 12)
    for d in card["dates"]:
        assert eps[d]["sky_state"]["moon_sign"] == "Scorpio" and eps[d]["mood"] < 5


def test_each_pattern_unlocks_once_and_again_only_after_dropping_out():
    c, uid = user("unl-once@x.com")
    plant(uid)
    pid = M.weekly_pattern_job(uid)[0]["id"]
    assert c.post(f"/api/memory/unlocks/{pid}/seen").status_code == 200
    assert all(u["id"] != pid for u in c.get("/api/memory/unlocks").json()["unlocks"])

    # The same pattern on a re-run does not re-fire.
    M.weekly_pattern_job(uid)
    assert all(u["id"] != pid for u in c.get("/api/memory/unlocks").json()["unlocks"])

    # It drops out (her data no longer shows it) ...
    plant(uid, strong=False)
    assert all(p["id"] != pid for p in M.weekly_pattern_job(uid))
    assert pid not in (load(uid, unlocks.SEEN, {}) or {})
    # ... and comes back: it unlocks again, with the numbers true now.
    plant(uid)
    M.weekly_pattern_job(uid)
    assert any(u["id"] == pid for u in c.get("/api/memory/unlocks").json()["unlocks"])


def test_progress_before_the_first_pattern_is_a_count_never_a_claim():
    c, uid = user("unl-prog@x.com")
    m = c.get("/api/memory").json()
    assert m["patterns"] == []
    assert m["progress"]["days_logged"] == 0 and m["progress"]["target"] == 90
    assert m["progress"]["candidates_tracking"] == 0
    assert not CLAIMY.search(json.dumps(m["progress"]))

    plant(uid, days=25, strong=False)
    m = c.get("/api/memory").json()
    assert m["patterns"] == [] and m["progress"]["days_logged"] == 25
    assert isinstance(m["progress"]["candidates_tracking"], int)
    # Nothing in the payload names a candidate.
    assert not CLAIMY.search(json.dumps(m))
    assert c.get("/api/memory/unlocks").json() == {"unlocks": []}


def test_today_gets_a_heads_up_when_the_sky_matches_a_verified_pattern():
    c, uid = user("unl-today@x.com")
    sign = server._sky_state(server._sky(uid, server._today(uid)))["moon_sign"]
    plant(uid, sign=sign, end=server._today(uid))
    M.weekly_pattern_job(uid)
    h = c.get("/api/memory/today").json()["heads_up"]
    assert h, "today's Moon sign is the planted condition"
    assert h[0]["text"].startswith("Heads-up from your own check-ins")
    assert h[0]["support"] in h[0]["text"] and "you logged" in h[0]["text"]
    assert h[0]["pushable"] == (h[0]["confidence"] in ("medium", "high"))


def test_pattern_share_card_has_numbers_and_no_dates():
    c, uid = user("unl-card@x.com")
    eps = plant(uid)
    pid = M.weekly_pattern_job(uid)[0]["id"]
    r = c.get(f"/api/card/pattern/{pid}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg")
    assert "FOUND IN MY OWN DATA" in r.text and "Scorpio" in r.text
    for e in eps:
        assert e["date"] not in r.text
    assert c.get("/api/card/pattern/not-an-id.svg").status_code == 404


@pytest.mark.parametrize("kind,value", [
    ("moon_phase", "full moon"), ("moon_sign", "Leo"), ("weekday", "Monday"),
    ("transit_aspect", "Mars-square-Venus"), ("house_transit", "7"), ("tag", "sleep"),
    ("tag_day_before", "sleep")])
def test_condition_copy_never_reads_as_a_sign_trait(kind, value):
    c = {"kind": kind, "value": value, "window_days": 0}
    for o in ({"kind": "mood_below", "value": "5"}, {"kind": "topic_present", "value": "work"}):
        text = unlocks.claim_for(c, o)
        assert "you logged" in text
        assert not re.search(r"because you'?re|you are an? \w+|as an? \w+,", text, re.I)
        assert not re.search(r"\d", unlocks.condition_words(c).replace(value, "")), text


def test_a_legacy_pattern_without_n_tests_does_not_unlock_until_the_job_stamps_it():
    """Re-verifying at n_tests=1 would be uncorrected alpha: a bar the weekly
    job never set. It stays shut until the job re-derives it."""
    c, uid = user("unl-legacy@x.com")
    sign = server._sky_state(server._sky(uid, server._today(uid)))["moon_sign"]
    plant(uid, sign=sign, end=server._today(uid))
    survivors = M.weekly_pattern_job(uid)
    assert survivors
    legacy = [{k: v for k, v in p.items() if k != "n_tests"} for p in survivors]
    save(uid, "patterns", legacy)
    save(uid, unlocks.SEEN, None)
    assert unlocks.reverify(uid, legacy[0]) is None
    assert c.get("/api/memory/unlocks").json() == {"unlocks": []}
    assert c.get("/api/memory/today").json()["heads_up"] == []
    assert c.post(f"/api/memory/unlocks/{survivors[0]['id']}/seen").status_code == 404
    # The next weekly run stamps the count, and it unlocks at the corrected bar.
    M.weekly_pattern_job(uid)
    got = c.get("/api/memory/unlocks").json()["unlocks"]
    assert any(u["id"] == survivors[0]["id"] for u in got)
    assert all(u["tests_corrected_for"] > 1 for u in got)


# ------------------------------------------------------------- people

def add(c, name, date="1997-11-03"):
    r = c.post("/api/connections", json={"name": name, "relation": "talking", "date": date,
                                         "time": "08:15", "city": "Chicago IL"})
    assert r.status_code == 200, r.text
    return r.json()


def note(uid, date, people, words):
    M.upsert_episode(uid, {"date": date, "mood": 5, "summary": words, "her_words": [words],
                           "people": people, "topics": [], "sky_state": {},
                           "person_ids": M.person_ids(uid, people)})


def reply_of(c, text):
    with c.stream("POST", "/api/message", json={"text": text}) as r:
        body = "".join(r.iter_text())
    out = []
    for m in re.finditer(r"^data: (.*)$", body, re.M):
        d = json.loads(m.group(1))
        if "t" in d:
            out.append(d["t"])
    return body, "".join(out)


def test_heuristic_extraction_tags_episodes_with_the_persons_id():
    c, uid = user("pm-tag@x.com")
    jake = add(c, "Jake")
    ep = M.extract_day_heuristic(uid, "2026-10-01", {}, "user: Jake ghosted me again", {})
    assert ep["person_ids"] == [jake["id"]]


def test_search_memory_by_person_does_not_cross_contaminate():
    c, uid = user("pm-cross@x.com")
    add(c, "Jake")
    add(c, "Ben", date="1995-02-02")
    note(uid, "2026-09-03", ["Jake"], "Jake went quiet after I asked about Friday")
    note(uid, "2026-09-10", ["Ben"], "Ben made me laugh the whole drive")
    nat = server._natal(uid)
    j = tools.run("search_memory", uid, nat, {"query": "", "person": "Jake"})
    b = tools.run("search_memory", uid, nat, {"query": "", "person": "Ben"})
    assert [n["text"] for n in j["notes"]] == ["Jake went quiet after I asked about Friday"]
    assert [n["text"] for n in b["notes"]] == ["Ben made me laugh the whole drive"]
    assert "error" in tools.run("search_memory", uid, nat, {"query": "", "person": "Nobody"})


CREDIT = r"\b(Thank|Blame) (?:the )?(Moon|Sun|Mercury|Venus|Mars|Jupiter|Saturn)\b"


# The real ephemeris on fixed days, so today's sky can't decide what gets checked.
@pytest.mark.parametrize("day", ["2026-11-03", "2026-10-30"])
def test_demo_chat_quotes_her_note_with_its_date_and_a_computed_transit(monkeypatch, day):
    today = dt.date.fromisoformat(day)
    monkeypatch.setattr(server, "_today", lambda uid: today)
    c, uid = user(f"pm-demo-{day}@x.com")
    jake = add(c, "Jake")
    said = (today - dt.timedelta(days=9)).isoformat()
    note(uid, said, ["Jake"], "Jake went quiet after I asked about Friday")
    body, text = reply_of(c, "what's going on with jake")
    assert "Jake went quiet after I asked about Friday" in text
    d = dt.date.fromisoformat(said)
    assert f"{d.strftime('%b')} {d.day}" in text
    f = personmem.facts(uid, jake, today, server._tz(uid))
    # It talks like a friend, not an ephemeris: no aspect words, no scores. But every
    # planet it credits or blames is one engine/daily computed for this pair, in the
    # direction it computed.
    assert not re.search(r"\b(conjoins|squares|trines|sextiles|opposes|orb|°)\b", text), text
    assert f"against your usual {f['usual']}" not in text
    named = re.findall(CREDIT, text)
    computed = {("Thank" if r["pulls"] == "easier" else "Blame", r["transit"])
                for r in f["today"]["reasons"]}
    assert len(named) <= 1 and set(named) <= computed, text
    assert "event: tool" in body and "get_person_day" in body
    assert not re.search(r"\bwill\b|going to happen|guaranteed", text, re.I)


# ---- demo_reply on hand-built facts: no chart, no ephemeris, no clock.
def reason(transit, pulls):
    return {"text": f"{transit} text", "transit": transit, "aspect": "square",
            "lasts": "passes by tomorrow", "pulls": pulls}


def person_facts(delta, *reasons):
    return {"person": {"name": "Jake"}, "usual": 50, "she_said": [], "best_next": None,
            "today": {"score": 50 + delta, "delta": delta, "mood": daily.mood(delta),
                      "reasons": list(reasons)}}


@pytest.mark.parametrize("transit,said", [("Moon", "the Moon"), ("Sun", "the Sun"),
                                          ("Mercury", "Mercury"), ("Venus", "Venus"),
                                          ("Mars", "Mars")])
@pytest.mark.parametrize("pulls,verb", [("easier", "Thank"), ("harder", "Blame")])
def test_demo_reply_names_the_planet_in_its_direction_and_grammar(transit, said, pulls, verb):
    text = personmem.demo_reply(person_facts(0, reason(transit, pulls)))
    assert f"{verb} {said}:" in text
    assert re.findall(CREDIT, text) == [(verb, transit)]


@pytest.mark.parametrize("delta,verb,planet", [(6, "Thank", "Venus"), (-6, "Blame", "Moon")])
def test_demo_reply_credits_a_reason_that_points_the_way_the_day_does(delta, verb, planet):
    # The strongest single reason can pull against the day's total.
    first, second = (("Moon", "harder"), ("Venus", "easier")) if delta > 0 else \
        (("Venus", "easier"), ("Moon", "harder"))
    text = personmem.demo_reply(person_facts(delta, reason(*first), reason(*second)))
    assert re.findall(CREDIT, text) == [(verb, planet)], text


@pytest.mark.parametrize("delta,pulls", [(6, "harder"), (-6, "easier")])
def test_demo_reply_credits_nothing_when_every_reason_pulls_against_the_day(delta, pulls):
    text = personmem.demo_reply(person_facts(delta, reason("Moon", pulls)))
    assert re.findall(CREDIT, text) == [], text


@pytest.mark.parametrize("delta", range(-15, 16))
def test_demo_reply_opening_never_argues_with_the_mood(delta):
    text = personmem.demo_reply(person_facts(delta))
    mood = daily.mood(delta)
    how = ("a little off" if mood in ("a bit tender", "a tense day") else
           "better than usual" if mood in ("a warm day", "easier than usual") else
           "pretty normal")
    assert text.startswith(f"You and Jake are {how} today, so it's {mood}."), text


def test_the_question_being_answered_is_never_quoted_back_as_a_memory():
    # Her question lands in today's episode as her own words. It must not come
    # back as "On <today> you said: should Daniel and I make it official?"; a
    # real note about him still does, even one with a question mark in it.
    c, uid = user("pm-selfq@x.com")
    add(c, "Daniel")
    said = (dt.date.today() - dt.timedelta(days=3)).isoformat()
    note(uid, said, ["Daniel"], "Daniel invited me to his sister's birthday?? what are we")
    note(uid, server._today(uid).isoformat(), ["Daniel"], "should Daniel and I make it official?")
    _, text = reply_of(c, "should Daniel and I make it official?")
    assert "you said: \"should Daniel" not in text
    assert "what are we" in text


def test_the_demo_reply_never_quotes_the_question_back_as_logged():
    # Demo mode lifted the first quote in the context — which was the question
    # being answered, via today's check-in and episode — and closed it with a
    # stray period: 'You logged this before: "should I text him back?."'
    verdict = {"band": "high", "max_sentences": 4,
               "headlines": [{"transit": "Mars", "aspect": "opposition",
                              "natal": "Ascendant", "orb": 0.1}]}
    ask = "should I text him back?"
    block = (f'CHECK-IN (09:12): mood 5/10 — "{ask}"\n'
             f'  2026-10-30 · mood 5 · {ask} — "{ask}"\n'
             f'  2026-10-28 · mood 4 · a day — "Should I Text Him Back?"\n')
    body = server._demo_body(ask, verdict, block)
    assert "logged this before" not in body
    assert '?."' not in body

    # A real note still comes back, in her own punctuation...
    block += '  2026-10-21 · mood 3 · a day — "he left me on read for two days?!"\n'
    body = server._demo_body(ask, verdict, block)
    assert 'You logged this before: "he left me on read for two days?!"' in body
    # ...and one without an ending gets exactly one period.
    body = server._demo_body(ask, verdict, '  2026-10-21 · mood 3 · x — "he went quiet again"')
    assert 'You logged this before: "he went quiet again."' in body

    # End to end: the same question asked twice is never "logged before".
    c, uid = user("pm-demo-q@x.com")
    note(uid, server._today(uid).isoformat(), [], ask)
    _, text = reply_of(c, ask)
    assert f'logged this before: "{ask}' not in text
    assert '?."' not in text


def test_a_linked_persons_checkin_words_never_reach_her_chat():
    a, auid = user("pm-link-a@x.com")
    b = TestClient(server.app)
    sign_in(b, "pm-link-b@x.com")
    b.post("/api/onboard", json={"name": "Theo", "date": "1996-07-02", "time": "21:15",
                                 "city": "Chicago IL"})
    buid = last_uid()
    inv = a.post("/api/invites").json()
    assert b.post(f"/api/invites/{inv['token']}/accept",
                  json={"share_chart": True, "share_mood": True}).status_code == 200
    # Theo writes about Maya, in HIS store, and checks in with a private note.
    b.post("/api/checkin", json={"mood": 7, "text": "THEO-DAY-SECRET"})
    maya_row = next(r for r in load(buid, "connections", []) if r.get("linked_uid") == auid)
    b.post(f"/api/connections/{maya_row['id']}/checkin",
           json={"feel": "strained", "note": "THEO-PAIR-SECRET"})

    facts, their = server._person_context(auid, "how is theo", server._today(auid))
    blob = personmem.as_block(facts) + json.dumps(their)
    assert "THEO" not in blob.replace("Theo", "")          # neither secret
    assert facts["their_shared_mood_today"] == 7            # the number only
    body, _ = reply_of(a, "how is theo")
    assert "SECRET" not in body


def test_forgetting_a_note_removes_it_from_retrieval_immediately():
    c, uid = user("pm-forget@x.com")
    jake = add(c, "Jake")
    note(uid, "2026-09-03", ["Jake"], "Jake went quiet after I asked about Friday")
    c.post(f"/api/connections/{jake['id']}/checkin",
           json={"feel": "strained", "note": "he forgot my birthday"})
    people = c.get("/api/memory/people").json()["people"]
    notes = {n["text"]: n for n in people[0]["notes"]}
    assert set(notes) == {"Jake went quiet after I asked about Friday", "he forgot my birthday"}

    for t in notes:
        r = c.delete(f"/api/memory/people/{jake['id']}/notes", params={"key": notes[t]["key"]})
        assert r.status_code == 200
    assert c.get("/api/memory/people").json()["people"] == []
    nat = server._natal(uid)
    assert tools.run("search_memory", uid, nat, {"query": "", "person": "Jake"})["found"] == 0
    # Gone from the store, not just hidden.
    raw = json.dumps(load(uid, "episodes", [])) + json.dumps(load(uid, f"pulse-{jake['id']}", {}))
    assert "went quiet" not in raw and "birthday" not in raw
    _, text = reply_of(c, "what about jake")
    assert "went quiet" not in text and "birthday" not in text
    assert c.delete(f"/api/memory/people/{jake['id']}/notes",
                    params={"key": "quote:2026-09-03:deadbeef"}).status_code == 404


def test_a_message_naming_no_saved_person_takes_the_old_path():
    c, uid = user("pm-none@x.com")
    add(c, "Jake")
    assert personmem.mentioned(uid, "should i answer him") == []
    assert [r["name"] for r in personmem.mentioned(uid, "JAKE again")] == ["Jake"]


def test_the_tool_schema_stays_strict_with_the_person_filter():
    d = next(t for t in tools.DEFINITIONS if t["name"] == "search_memory")
    assert "person" in d["input_schema"]["properties"]
    assert d["input_schema"]["required"] == ["query"]


def test_daily_reason_text_is_what_personmem_cites():
    """The demo reply's mechanism sentence is daily's reason text verbatim."""
    c, uid = user("pm-cite@x.com")
    jake = add(c, "Jake")
    f = personmem.facts(uid, jake, server._today(uid), server._tz(uid))
    prof = load(uid, "profile", {})
    d = daily.for_pair(prof, jake, server.compat.report_for(uid, jake),
                       server._today(uid), server._tz(uid))
    assert [r["text"] for r in f["today"]["reasons"]] == [r["text"] for r in d["reasons"]]
    assert f["today"]["score"] == d["score"]


def test_get_person_day_is_a_tool_with_her_words_and_computed_transits():
    c, uid = user("pm-tool@x.com")
    add(c, "Jake Moss")
    note(uid, (dt.date.today() - dt.timedelta(days=5)).isoformat(), ["Jake"],
         "Jake went quiet after I asked about Friday")
    nat = server._natal(uid)
    out = tools.run("get_person_day", uid, nat, {"name": "jake"}, today=server._today(uid))
    assert out["person"]["name"] == "Jake Moss" and out["today"]["reasons"] is not None
    assert [n["text"] for n in out["she_said"]] == ["Jake went quiet after I asked about Friday"]
    assert "never predict" in out["rule"]
    assert "error" in tools.run("get_person_day", uid, nat, {"name": "Nobody"})


def test_keyed_chat_gets_person_facts_as_a_tool_result_not_system_context(monkeypatch):
    """
    With a key, the person facts used to be appended to the context block, so
    an aspect the model cited could not be checked against any tool result.
    They now arrive as a closed get_person_day tool_use / tool_result exchange,
    and the grounding the accuracy check runs against carries them as such.
    """
    import types

    import anthropic

    c, uid = user("pm-keyed@x.com")
    jake = add(c, "Jake")
    said = (dt.date.today() - dt.timedelta(days=9)).isoformat()
    note(uid, said, ["Jake"], "Jake went quiet after I asked about Friday")
    f = personmem.facts(uid, jake, server._today(uid), server._tz(uid))
    reason = f["today"]["reasons"][0]["text"] if f["today"]["reasons"] else None

    calls = []

    class Stream:
        def __enter__(self):
            return self

        def __init__(self):
            self.text_stream = ["Here is what I see about Jake."]

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return types.SimpleNamespace(stop_reason="end_turn", content=[])

    class Client:
        def __init__(self, *a, **k):
            self.messages = types.SimpleNamespace(
                stream=lambda **kw: calls.append(kw) or Stream())

    monkeypatch.setattr(server, "DEMO", False)
    monkeypatch.setattr(anthropic, "Anthropic", Client)
    graded = []
    monkeypatch.setattr(server.accuracy, "record_reply",
                        lambda text, grounding, *a, **k: graded.append(grounding) or (True, []))
    body, text = reply_of(c, "what's going on with jake")
    assert text == "Here is what I see about Jake."
    assert "event: tool" in body and "get_person_day" in body

    msgs = calls[0]["messages"]
    system = json.dumps(calls[0]["system"])
    uses = [b for m in msgs if m["role"] == "assistant" and isinstance(m["content"], list)
            for b in m["content"] if b.get("type") == "tool_use"]
    results = [b for m in msgs if m["role"] == "user" and isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_result"]
    assert [u["name"] for u in uses] == ["get_person_day"]
    assert [r["tool_use_id"] for r in results] == [u["id"] for u in uses]
    payload = json.loads(results[0]["content"])
    assert payload["person"]["name"] == "Jake"
    assert any(n["text"] == "Jake went quiet after I asked about Friday"
               for n in payload["she_said"])
    if reason:
        assert reason in results[0]["content"]
    # The person facts are not in the context block nor the system prompt —
    # only the tool result. (Her episode can still be retrieved into context by
    # the ordinary memory path; that is her day, not the person block.)
    context = [m["content"] for m in msgs if m["role"] == "user" and isinstance(m["content"], str)]
    for t in context + [system]:
        assert "<person>" not in t and '"she_said"' not in t and '"next_days"' not in t
        if reason:
            assert reason not in t
    # Her message is the last turn, after the closed exchange.
    assert msgs[-1] == {"role": "user", "content": "what's going on with jake"}
    assert graded and "[get_person_day]" in graded[0] and "Jake went quiet" in graded[0]
    stored = [m for m in server.thread.recent(uid, 5) if m["role"] == "assistant"][-1]
    assert stored["tools"] == ["get_person_day"]


def test_forgetting_a_chat_line_redacts_it_from_the_thread():
    """The confirm says it is deleted from what I remember; the thread goes
    back to the model as history, so the line has to go from there too."""
    c, uid = user("pm-forget-chat@x.com")
    jake = add(c, "Jake")
    reply_of(c, "Jake cancelled on me again tonight")
    notes = c.get("/api/memory/people").json()["people"][0]["notes"]
    chat = next(n for n in notes if n["source"] == "chat")
    assert chat["text"] == "Jake cancelled on me again tonight"
    r = c.delete(f"/api/memory/people/{jake['id']}/notes", params={"key": chat["key"]})
    assert r.status_code == 200
    from engine import thread
    msgs = thread.recent(uid, 50)
    assert "cancelled on me" not in json.dumps(msgs)
    assert any(m["role"] == "user" and m["text"] == personmem.REMOVED for m in msgs)
    assert all(n["source"] != "chat"
               for p in c.get("/api/memory/people").json()["people"] for n in p["notes"])


def test_forgetting_a_note_also_scrubs_replies_that_quoted_it():
    c, uid = user("pm-forget-quote@x.com")
    jake = add(c, "Jake")
    said = (dt.date.today() - dt.timedelta(days=9)).isoformat()
    note(uid, said, ["Jake"], "Jake went quiet after I asked about Friday")
    _, text = reply_of(c, "what's going on with jake")
    assert "Jake went quiet" in text
    n = c.get("/api/memory/people").json()["people"][0]["notes"][0]
    assert c.delete(f"/api/memory/people/{jake['id']}/notes",
                    params={"key": n["key"]}).status_code == 200
    from engine import thread
    assert "went quiet" not in json.dumps(thread.recent(uid, 50))


# --------------------------------------------------------- her own tags

def test_a_tag_the_night_before_can_become_a_verified_pattern():
    """The day after she tags sleep, her mood drops. The tag day itself is
    ordinary, so only the day-after condition can clear the verifier."""
    _c, uid = user("tag-sleep@x.com")
    eps = plant(uid, strong=False)
    for i, e in enumerate(eps):
        e["tags"] = ["sleep"] if i % 6 == 0 else ["work"] if i % 2 else []
        if i % 6 == 1:
            e["mood"] = 2                            # the morning after
    save(uid, "episodes", eps)

    same_day = M.Proposal(claim="x", condition=M.Condition(kind="tag", value="sleep", window_days=0),
                          outcome_kind="mood_below", outcome_value="4")
    day_after = M.Proposal(claim="x", condition=M.Condition(kind="tag_day_before", value="sleep", window_days=0),
                           outcome_kind="mood_below", outcome_value="4")
    assert M.verify(uid, same_day) is None           # the tag day itself is ordinary
    v = M.verify(uid, day_after)
    assert v and v["lift"] > 1.5

    survivors = M.weekly_pattern_job(uid)            # no key: code proposes tags too
    found = [p for p in survivors if p["condition"]["kind"] == "tag"]   # same day: nothing
    assert found == [] or all(p["condition"]["kind"] != "tag" for p in found)
    found = [p for p in survivors if p["condition"]["kind"] == "tag_day_before"]
    assert found and found[0]["condition"]["value"] == "sleep"
    assert found[0]["claim"].startswith("On the day after you tag sleep, you logged")


def test_episodes_keep_the_check_in_tags():
    _c, uid = user("tag-ep@x.com")
    ep = M.extract_day_heuristic(uid, "2026-11-01", {"mood": 3, "text": "rough", "tags": ["sleep"]},
                                 "", {"moon_phase": "new moon", "moon_sign": "Leo"})
    assert ep["tags"] == ["sleep"]


def test_a_day_after_tag_pattern_gives_todays_heads_up():
    _c, uid = user("tag-heads@x.com")
    pat = {"claim": "On the day after you tag sleep, you logged a mood below 5.",
           "condition": {"kind": "tag_day_before", "value": "sleep", "window_days": 0},
           "outcome": {"kind": "mood_below", "value": "5"}}
    save(uid, "patterns", [pat])
    today = dt.date(2026, 11, 11)
    stub = {"date": today.isoformat()}
    assert M.fires_today(uid, {}, stub) == []
    save(uid, "checkin-2026-11-10", {"mood": 4, "tags": ["sleep"]})
    assert M.fires_today(uid, {}, stub) == [pat]



def test_a_tag_is_never_verified_against_its_own_echo():
    """Tags are written into her check-in message and topics are extracted from
    it: "tagged work -> a day about work" held 10 of 10 on pure noise."""
    _c, uid = user("tag-echo@x.com")
    eps = plant(uid, strong=False)
    for i, e in enumerate(eps):
        e["tags"] = ["work"] if i % 3 == 0 else []
        e["topics"] = ["work"] if i % 3 == 0 else []
    save(uid, "episodes", eps)
    echo = M.Proposal(claim="x", condition=M.Condition(kind="tag", value="work", window_days=0),
                      outcome_kind="topic_present", outcome_value="work")
    assert M.verify(uid, echo) is None
    assert not [p for p in M.weekly_pattern_job(uid)
                if p["condition"]["kind"].startswith("tag") and p["outcome"]["kind"] == "topic_present"]


def test_tag_patterns_are_measured_against_tagged_days_only():
    """She tags only on her low days: against all days that looks like the tag
    'causes' lows; against the days she was tagging, it doesn't."""
    _c, uid = user("tag-base@x.com")
    eps = plant(uid, strong=False)
    for i, e in enumerate(eps):
        if i % 4 == 0:
            e["mood"], e["tags"] = 3, ["sleep"]
        elif i % 4 == 1:
            e["mood"], e["tags"] = 3, ["work"]
        else:
            e["tags"] = []
    save(uid, "episodes", eps)
    p = M.Proposal(claim="x", condition=M.Condition(kind="tag", value="sleep", window_days=0),
                   outcome_kind="mood_below", outcome_value="5")
    assert M.verify(uid, p) is None
