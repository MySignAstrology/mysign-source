"""
Mood: the richer check-in, the Mood tab's summary, and the free reply to the
check-in that turns it into the day's first conversation.
"""
import datetime as dt
import os
import tempfile

import pytest

os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-test-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("MYSIGN_TABLE", None)
os.environ.pop("AWS_LAMBDA_FUNCTION_NAME", None)

from fastapi.testclient import TestClient

import app.server as server
from engine import mood


@pytest.fixture
def c():
    return TestClient(server.app)


def sign_in(c, email):
    code = c.post("/api/auth/request", json={"email": email}).json()["dev_code"]
    c.post("/api/auth/verify", json={"email": email, "code": code})
    c.post("/api/consent", json={"ai": True})
    c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14",
                                 "time": "04:42", "city": "Chicago IL"})
    return c


# ------------------------------------------------------------------ engine

def test_quadrant_from_mood_and_energy():
    assert mood.quadrant(8, "high") == "high_pleasant"
    assert mood.quadrant(3, "low") == "low_unpleasant"
    assert mood.quadrant(6, "low") == "low_pleasant"
    assert mood.quadrant(5, None) is None


def test_clean_keeps_only_known_values_capped():
    e, f, t = mood.clean("sideways", ["Anxious", "anxious", "made up", "drained", "sad", "calm"],
                         ["work", "nonsense", "sleep"])
    assert e is None
    assert f == ["anxious", "drained", "sad"]          # known, de-duplicated, at most three
    assert t == ["work", "sleep"]


def test_every_word_has_a_meaning():
    for q, ws in mood.FEELINGS.items():
        assert len(ws) == 8, q
        assert all(w and m for w, m in ws)


def test_reflect_text_says_the_check_in():
    t = mood.reflect_text({"mood": 3, "energy": "low", "feelings": ["drained", "lonely"],
                           "tags": ["work"], "text": "long week"})
    assert t == "Checking in: 3/10 — drained, lonely. It's about work. long week"
    assert mood.reflect_text({"mood": 7}) == "Checking in: 7/10."


def _days(today, moods_tags):
    return [((today - dt.timedelta(days=i)).isoformat(), {"mood": m, "tags": tg})
            for i, (m, tg) in enumerate(moods_tags)]


def test_summary_weeks_and_no_streak():
    today = dt.date(2026, 11, 10)
    rows = [((today - dt.timedelta(days=i)).isoformat(), {"mood": 6}) for i in range(0, 14, 2)]
    s = mood.summary(rows, today)
    assert s["count"] == 7 and s["recent14"] == 7
    assert s["this_week"] == 6.0 and s["this_week_n"] == 4
    assert s["last_week_n"] == 3
    assert "streak" not in s
    assert s["days_to_patterns"] == 83


def test_tag_gap_needs_three_days_each_side_and_a_real_gap():
    today = dt.date(2026, 11, 10)
    rows = _days(today, [(8, ["outdoors"]), (7, ["outdoors"]), (8, ["outdoors"]),
                         (4, ["work"]), (3, ["work"]), (4, ["work"])])
    s = mood.summary(rows, today)
    assert [g["tag"] for g in s["lifts"]] == ["outdoors"]
    assert s["lifts"][0]["n_with"] == 3 and s["lifts"][0]["n_without"] == 3
    assert [g["tag"] for g in s["drags"]] == ["work"]
    # Two days with the tag is not enough to say anything.
    s = mood.summary(rows[:5], today)
    assert not any(g["tag"] == "outdoors" for g in s["lifts"])


def test_untagged_days_dont_count_against_a_tag():
    today = dt.date(2026, 11, 10)
    rows = _days(today, [(8, ["outdoors"])] * 3 + [(2, [])] * 5 + [(6, ["work"])] * 3)
    s = mood.summary(rows, today)
    lift = s["lifts"][0]
    assert lift["tag"] == "outdoors" and lift["n_without"] == 3 and lift["without"] == 6.0


# --------------------------------------------------------------------- api

def test_checkin_keeps_feelings_and_tags(c):
    sign_in(c, "moodrich@x.com")
    r = c.post("/api/checkin", json={"mood": 3, "energy": "low", "feelings": ["drained", "bogus"],
                                     "tags": ["sleep"], "text": "rough night"}).json()
    assert r["feelings"] == ["drained"] and r["tags"] == ["sleep"] and r["energy"] == "low"
    assert c.get("/api/state").json()["checkin"]["feelings"] == ["drained"]
    s = c.get("/api/mood").json()
    assert s["count"] == 1 and s["calendar"][0]["feelings"] == ["drained"]
    assert s["words"][0]["word"] == "drained" and s["words"][0]["means"]


def test_plain_checkin_still_works(c):
    sign_in(c, "moodplain@x.com")
    r = c.post("/api/checkin", json={"mood": 6}).json()
    assert r["mood"] == 6 and "feelings" not in r


def test_mood_needs_sign_in(c):
    assert c.get("/api/mood").status_code == 401
    assert c.get("/api/mood/vocab").status_code == 200


def _say(c, **body):
    with c.stream("POST", "/api/message", json=body) as r:
        return r.status_code, "".join(r.iter_text())


def test_checkin_reply_is_free_and_once_a_day(c):
    sign_in(c, "moodreflect@x.com")
    assert _say(c, text="x", checkin=True)[0] == 409          # no check-in yet
    c.post("/api/checkin", json={"mood": 3, "energy": "low", "feelings": ["drained"]})
    code, body = _say(c, text="ignored", checkin=True)
    assert code == 200 and "event: done" in body
    thread = c.get("/api/state").json()["thread"]
    assert thread[0]["text"].startswith("Checking in: 3/10 — drained")
    assert _say(c, text="x", checkin=True)[0] == 409          # once a day
    # Checking in again doesn't buy another free reply.
    c.post("/api/checkin", json={"mood": 5})
    assert _say(c, text="x", checkin=True)[0] == 409
    # And it didn't spend one of the free plan's three.
    for _ in range(3):
        assert _say(c, text="what about today")[0] == 200
    assert _say(c, text="one more")[0] == 402


# ---------------------------------------------------------- the next batch

def test_line_reaction_is_free_once_and_written_by_the_server(c):
    sign_in(c, "moodline@x.com")
    assert c.get("/api/today").json()["line_reacted"] is False
    line = c.get("/api/today").json()["line"]["text"]
    assert _say(c, text="x", line="nah")[0] == 400
    code, body = _say(c, text="ignored", line="no")
    assert code == 200 and "event: done" in body
    first = c.get("/api/state").json()["thread"][0]["text"]
    assert first == f'About today\'s line, "{line}": not really, no.'
    assert c.get("/api/today").json()["line_reacted"] is True
    assert _say(c, text="x", line="yes")[0] == 409
    for _ in range(3):                                   # still three typed ones
        assert _say(c, text="tell me more")[0] == 200
    assert _say(c, text="one more")[0] == 402


def test_helped_is_kept_and_listed_after_a_low_day():
    today = dt.date(2026, 11, 10)
    rows = [((today - dt.timedelta(days=1)).isoformat(), {"mood": 3}),
            (today.isoformat(), {"mood": 6, "helped": "a long walk with Sam"})]
    s = mood.summary(rows, today)
    assert s["helped"] == [{"date": "2026-11-10", "text": "a long walk with Sam", "mood": 6, "before": 3}]
    t = mood.reflect_text({"mood": 6, "helped": "a long walk"})
    assert t.endswith("What helped since yesterday: a long walk")


def test_checkin_saves_helped(c):
    sign_in(c, "moodhelped@x.com")
    r = c.post("/api/checkin", json={"mood": 6, "helped": "  slept nine hours "}).json()
    assert r["helped"] == "slept nine hours"


def test_week_recap_and_monday_looks_back():
    tue = dt.date(2026, 11, 10)                   # a Tuesday
    rows = [("2026-11-09", {"mood": 4, "feelings": ["drained"], "tags": ["work"]}),
            ("2026-11-10", {"mood": 8, "feelings": ["hopeful"], "tags": ["friends"]}),
            ("2026-11-05", {"mood": 2})]          # last week
    w = mood.summary(rows, tue)["week"]
    assert w["days"] == 2 and w["avg"] == 6.0 and w["best"]["mood"] == 8
    assert w["best"]["tags"] == ["friends"] and set(w["words"]) == {"drained", "hopeful"}
    mon = dt.date(2026, 11, 9)
    w = mood.summary(rows[2:], mon + dt.timedelta(days=0))["week"]
    assert w["from"] == "2026-11-05" and w["days"] == 1


def test_word_growth_only_after_the_first_month():
    today = dt.date(2026, 11, 10)
    start = today - dt.timedelta(days=60)
    rows = [(start.isoformat(), {"mood": 5, "feelings": ["sad"]}),
            ((start + dt.timedelta(days=40)).isoformat(), {"mood": 6, "feelings": ["calm", "hopeful"]})]
    s = mood.summary(rows, today)
    assert s["vocab_first_month"] == 1 and s["vocab_size"] == 3
    assert mood.summary(rows[:1], start + dt.timedelta(days=10))["vocab_first_month"] is None


def test_milestones_count_check_ins_and_never_go_backwards():
    assert mood.milestones(0) == {"reached": [], "next": {"n": 1, "label": "first check-in", "to_go": 1}}
    m = mood.milestones(15)
    assert [x["n"] for x in m["reached"]] == [1, 7, 14]
    assert m["next"] == {"n": 30, "label": "a month in colour", "to_go": 15}
    assert mood.milestones(120)["next"] is None


def test_mood_api_marks_new_and_full_moons(c):
    sign_in(c, "moodmoons@x.com")
    c.post("/api/checkin", json={"mood": 5})
    moons = c.get("/api/mood").json()["moons"]
    assert moons and set(moons.values()) <= {"full", "new"}
    assert len(moons) >= 2                       # 35 days always hold at least two


def test_week_card_has_numbers_and_never_her_words(c):
    code = c.post("/api/auth/request", json={"email": "moodcard@x.com"}).json()["dev_code"]
    uid = c.post("/api/auth/verify", json={"email": "moodcard@x.com", "code": code}).json()["uid"]
    c.post("/api/consent", json={"ai": True})
    c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14", "time": "04:42", "city": "Chicago IL"})
    assert c.get("/api/card/week.svg").status_code == 404     # nothing this week yet
    from engine.store import save as _save
    today = server._today(uid)
    for back, m in ((1, 3), (2, 8)):               # always two in the recap week
        _save(uid, f"checkin-{today - dt.timedelta(days=back)}",
              {"mood": m, "feelings": ["drained"], "text": "a secret thing"})
    r = c.get("/api/card/week.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg")
    assert "my week," in r.text and "DRAINED" not in r.text and "drained" in r.text
    assert "secret" not in r.text                  # nothing she typed
    assert "AN 8" in r.text                        # her best day, said properly


# -------------------------------------------------------- the fourth batch

def test_a_note_is_written_on_a_good_day_and_given_back_on_a_hard_one():
    notes = mood.add_note([], "2026-11-01", 8, "  You got through March.  Remember that. ")
    assert notes[0]["text"] == "You got through March. Remember that." and notes[0]["given"] is None
    with pytest.raises(ValueError):
        mood.add_note(notes, "2026-11-02", 5, "not a good day")
    assert mood.note_for(notes, "2026-11-05", 6) == (None, notes)        # not low: nothing
    note, notes = mood.note_for(notes, "2026-11-05", 3)
    assert note["text"].startswith("You got through March") and notes[0]["given"] == "2026-11-05"
    again, notes = mood.note_for(notes, "2026-11-05", 3)                 # same day: same note
    assert again["date"] == "2026-11-01"
    assert mood.note_for(notes, "2026-11-09", 2)[0] is None             # given once


def test_note_api_needs_a_good_check_in_and_returns_it_on_a_low_day(c):
    code = c.post("/api/auth/request", json={"email": "moodnote@x.com"}).json()["dev_code"]
    uid = c.post("/api/auth/verify", json={"email": "moodnote@x.com", "code": code}).json()["uid"]
    c.post("/api/consent", json={"ai": True})
    c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14", "time": "04:42", "city": "Chicago IL"})
    assert c.post("/api/mood/note", json={"text": "hi"}).status_code == 400   # no check-in yet
    c.post("/api/checkin", json={"mood": 8})
    assert c.post("/api/mood/note", json={"text": "You are allowed to rest."}).json()["saved"]
    assert c.get("/api/mood/note").json() == {"note": None, "written_today": True}
    # Move the note into the past, then have a hard day.
    from engine.store import load as _load
    from engine.store import save as _save
    notes = _load(uid, "mood-notes", [])
    notes[0]["date"] = "2026-01-01"
    _save(uid, "mood-notes", notes)
    c.post("/api/checkin", json={"mood": 2})
    got = c.get("/api/mood/note").json()["note"]
    assert got["text"] == "You are allowed to rest." and got["date"] == "2026-01-01"


def test_year_grid_and_card(c):
    code = c.post("/api/auth/request", json={"email": "moodyear@x.com"}).json()["dev_code"]
    uid = c.post("/api/auth/verify", json={"email": "moodyear@x.com", "code": code}).json()["uid"]
    c.post("/api/consent", json={"ai": True})
    c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14", "time": "04:42", "city": "Chicago IL"})
    assert c.get("/api/card/year.svg").status_code == 404
    from engine.store import save as _save
    today = server._today(uid)
    for back in range(8):
        d = today - dt.timedelta(days=back)
        if d.year == today.year:
            _save(uid, f"checkin-{d}", {"mood": 3 + back % 6, "text": "private words"})
    yr = c.get("/api/mood").json()["year"]
    assert yr and all(k.startswith(str(today.year)) for k in yr)
    if len(yr) >= 7:
        r = c.get("/api/card/year.svg")
        assert r.status_code == 200 and "in colour" in r.text and "private" not in r.text


def test_period_is_a_tag():
    assert mood.clean(None, [], ["period"])[2] == ["period"]


# ------------------------------------------------------------- audit fixes

def test_a_crisis_note_in_a_second_check_in_is_still_heard(c):
    from engine import episode
    code = c.post("/api/auth/request", json={"email": "moodred2@x.com"}).json()["dev_code"]
    uid = c.post("/api/auth/verify", json={"email": "moodred2@x.com", "code": code}).json()["uid"]
    c.post("/api/consent", json={"ai": True})
    c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14", "time": "04:42", "city": "Chicago IL"})
    c.post("/api/checkin", json={"mood": 6, "text": "ok day"})
    assert _say(c, text="x", checkin=True)[0] == 200
    r = c.post("/api/checkin", json={"mood": 1, "text": "honestly i want to die"}).json()
    assert r["red"] is True and r["reflected"] is True
    assert episode.active(uid)                          # opened at check-in, before any reply
    code, body = _say(c, text="x", checkin=True)        # the crisis reply is never refused
    assert code == 200 and '"tier": "red"' in body


def test_the_free_reply_cannot_be_claimed_twice_and_a_refusal_keeps_it(c, monkeypatch):
    sign_in(c, "moodfree2@x.com")
    c.post("/api/checkin", json={"mood": 5})
    from engine import quota
    monkeypatch.setattr(quota, "claim", lambda *a, **k: (False, {"reason": "burst", "message": "Slow down.", "retry_after": 5}))
    assert _say(c, text="x", checkin=True)[0] == 429
    monkeypatch.undo()
    assert _say(c, text="x", checkin=True)[0] == 200    # the refusal didn't spend it
    assert _say(c, text="x", checkin=True)[0] == 409


def test_a_failed_free_reply_refunds_nothing_and_can_be_retried(c, monkeypatch):
    sign_in(c, "moodfail@x.com")
    c.post("/api/checkin", json={"mood": 5})
    # Out of demo mode with no key, the model call fails: the failed-turn path.
    monkeypatch.setattr(server, "DEMO", False)
    code, body = _say(c, text="x", checkin=True)
    assert code == 200 and '"failed": true' in body
    code, body = _say(c, text="x", checkin=True)       # the claim was released
    assert code == 200
    monkeypatch.setattr(server, "DEMO", True)
    for _ in range(3):
        assert _say(c, text="hello")[0] == 200          # still exactly three typed:
    assert _say(c, text="one more")[0] == 402           # no refund below zero


def test_helped_lists_only_what_followed_a_low_day():
    today = dt.date(2026, 11, 10)
    rows = [("2026-11-08", {"mood": 9}), ("2026-11-09", {"mood": 8, "helped": "not after a low day"}),
            ("2026-11-10", {"mood": 6, "helped": "a walk"})]
    assert mood.summary(rows, today)["helped"] == []
    rows[1] = ("2026-11-09", {"mood": 3})
    assert [h["text"] for h in mood.summary(rows, today)["helped"]] == ["a walk"]


def test_a_crisis_in_what_helped_is_heard(c):
    from engine import episode
    code = c.post("/api/auth/request", json={"email": "moodhelpred@x.com"}).json()["dev_code"]
    uid = c.post("/api/auth/verify", json={"email": "moodhelpred@x.com", "code": code}).json()["uid"]
    c.post("/api/consent", json={"ai": True})
    c.post("/api/onboard", json={"name": "Maya", "date": "1999-03-14", "time": "04:42", "city": "Chicago IL"})
    r = c.post("/api/checkin", json={"mood": 3, "helped": "nothing. i want to die"}).json()
    assert r["red"] is True and episode.active(uid)


def test_the_line_reaction_is_claimed_once_even_after_a_failure_releases_it(c, monkeypatch):
    sign_in(c, "moodlinefail@x.com")
    c.get("/api/today")
    monkeypatch.setattr(server, "DEMO", False)              # no key: the turn fails
    code, body = _say(c, text="x", line="yes")
    assert code == 200 and '"failed": true' in body
    monkeypatch.setattr(server, "DEMO", True)
    assert _say(c, text="x", line="yes")[0] == 200          # released, so it can be retried
    assert _say(c, text="x", line="no")[0] == 409


def test_note_for_survives_a_legacy_note_without_a_date():
    notes = [{"mood": 8, "text": "old one"}, {"date": "2026-11-01", "mood": 9, "text": "keep going", "given": None}]
    note, notes = mood.note_for(notes, "2026-11-05", 3)
    assert note["text"] in ("old one", "keep going")
