"""
The conversational voice (v2): follow-ups on what she said was coming, the
session boundary that decides when to ask, suggested replies, and which model
answers whom. Run with: python -m pytest tests/test_conversation.py -q
"""
import datetime as dt
import json
import os
import re
import tempfile

import pytest

os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-conv-"))
os.environ["MYSIGN_DEV_AUTH"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("MYSIGN_TABLE", None)

from fastapi.testclient import TestClient

import app.server as server
from engine import chips, context, models, voice
from engine import memory as M
from engine.store import load, save
from tests.test_app import last_uid, onboard, sign_in


@pytest.fixture
def c():
    return TestClient(server.app)


def _events(body):
    return [(m.group(1), json.loads(m.group(2)))
            for m in re.finditer(r"event: (\w+)\ndata: (.+)", body)]


def _send(c, text):
    with c.stream("POST", "/api/message", json={"text": text}) as r:
        return _events("".join(r.iter_text()))


# ------------------------------------------------------------- open loops

TUE = dt.date(2026, 10, 27)


def test_a_loop_is_asked_about_the_day_after_it_happens_not_before():
    uid = "loop-timing"
    M.add_loops(uid, TUE.isoformat(), [
        {"what": "interview at the agency", "when": "2026-10-30", "ask": "So, the agency?", "kind": "event"},
        {"what": "dinner with theo", "when": "2026-10-27", "ask": "How was dinner?", "kind": "event"}])
    assert [loop["what"] for loop in M.due_loops(uid, TUE)] == []              # never the same day
    assert [loop["what"] for loop in M.due_loops(uid, TUE + dt.timedelta(1))] == ["dinner with theo"]
    # The interview is on Friday: asking that morning would be asking too soon.
    assert "interview at the agency" not in [loop["what"] for loop in M.due_loops(uid, dt.date(2026, 10, 30))]
    assert "interview at the agency" in [loop["what"] for loop in M.due_loops(uid, dt.date(2026, 10, 31))]


def test_a_loop_is_raised_on_two_days_at_most_then_retired():
    uid = "loop-retire"
    M.add_loops(uid, TUE.isoformat(), [{"what": "the talk with mum", "when": None,
                                        "ask": "Did you talk to your mum?", "kind": "worry"}])
    # An undated worry waits two days (memory.UNDATED_WAIT), then gets two.
    assert not M.due_loops(uid, TUE + dt.timedelta(1))
    for i in (2, 3):
        day = TUE + dt.timedelta(i)
        due = M.due_loops(uid, day)
        assert due, f"not offered on day {i}"
        M.mark_raised(uid, [loop["id"] for loop in due], day)
        assert not M.due_loops(uid, day), "offered twice on the same day"
    assert not M.due_loops(uid, TUE + dt.timedelta(4)), "nagging: offered a third day"


def test_a_stale_loop_is_dropped():
    uid = "loop-stale"
    M.add_loops(uid, TUE.isoformat(), [{"what": "the party", "when": "2026-10-28",
                                        "ask": "How was the party?", "kind": "event"}])
    assert M.due_loops(uid, dt.date(2026, 10, 29))
    assert not M.due_loops(uid, dt.date(2026, 11, 20))


def test_the_same_thing_said_twice_is_one_loop():
    uid = "loop-dedupe"
    item = {"what": "Dentist on Thursday", "when": None, "ask": "Dentist?", "kind": "event"}
    M.add_loops(uid, TUE.isoformat(), [item])
    M.add_loops(uid, TUE.isoformat(), [dict(item, what="dentist on thursday")])
    assert len(load(uid, "loops", [])) == 1


# ----------------------------------------------- audit HIGH-4 (Nora's loops)

def test_near_duplicate_loops_are_one_loop():
    uid = "loop-fuzzy"
    M.add_loops(uid, "2026-10-21", [{"what": "final round interview at Fieldwork design studio",
                                     "when": "2026-10-28", "ask": "So how did the Fieldwork final round go?",
                                     "kind": "event"}])
    M.add_loops(uid, "2026-10-28", [{"what": "interview at Fieldwork with Priya Raman",
                                     "when": "2026-10-28", "ask": "So how did the Fieldwork interview go?",
                                     "kind": "event"}])
    assert len(load(uid, "loops", [])) == 1
    # Already stored twice (before this fix): briefed once.
    save(uid, "loops", load(uid, "loops", []) + [dict(load(uid, "loops", [])[0], id="dup00001",
                                                       what="interview at Fieldwork with Priya Raman")])
    assert len(M.due_loops(uid, dt.date(2026, 10, 29))) == 1
    # A different thing on the same day is not a duplicate.
    M.add_loops(uid, "2026-10-21", [{"what": "dinner with Dana", "when": "2026-10-28",
                                     "ask": "How was dinner?", "kind": "event"}])
    assert len(load(uid, "loops", [])) == 3


def test_an_undated_decision_never_comes_due_before_the_event_it_hangs_on():
    """'possible offer and negotiating' (when: null) came due the day after she
    said it — a week before the interview the offer depended on."""
    uid = "loop-offer"
    M.add_loops(uid, "2026-10-21", [
        {"what": "final round interview at Fieldwork", "when": "2026-10-28",
         "ask": "How did it go?", "kind": "event"},
        {"what": "possible offer and negotiating with Dana's advice", "when": None,
         "ask": "Did an offer come through?", "kind": "decision"}])
    for d in range(22, 29):
        assert not M.due_loops(uid, dt.date(2026, 10, d)), d
    assert {x["kind"] for x in M.due_loops(uid, dt.date(2026, 10, 29))} == {"event", "decision"}
    uid = "loop-decide"
    M.add_loops(uid, "2026-10-21", [{"what": "whether to end things with Jake", "when": None,
                                     "ask": "Where did you land on Jake?", "kind": "decision"}])
    assert not M.due_loops(uid, dt.date(2026, 10, 22)) and not M.due_loops(uid, dt.date(2026, 10, 23))
    assert M.due_loops(uid, dt.date(2026, 10, 24))


def test_only_the_loop_the_reply_asked_about_is_marked_raised():
    uid = "loop-raised"
    M.add_loops(uid, "2026-10-21", [
        {"what": "final round interview at Fieldwork", "when": "2026-10-28",
         "ask": "How did the Fieldwork interview go?", "kind": "event"},
        {"what": "possible offer and negotiating", "when": "2026-10-28",
         "ask": "Did an offer come through?", "kind": "decision"}])
    ids = [x["id"] for x in load(uid, "loops", [])]
    M.mark_raised(uid, ids, dt.date(2026, 10, 29),
                  reply="Hey. So how did the Fieldwork final round go? You were shaking beforehand.")
    raised = {x["what"]: x["raised"] for x in load(uid, "loops", [])}
    assert raised == {"final round interview at Fieldwork": ["2026-10-29"],
                      "possible offer and negotiating": []}


def test_a_loop_she_answered_is_resolved_and_never_asked_again(monkeypatch):
    """Nora said how the interview went; both copies stayed open and were
    briefed again the next morning."""
    uid = "loop-resolve"
    M.add_loops(uid, "2026-10-21", [{"what": "final round interview at Fieldwork", "when": "2026-10-28",
                                     "ask": "How did the Fieldwork interview go?", "kind": "event"}])
    lid = load(uid, "loops", [])[0]["id"]
    assert lid in M._open_lines(uid)
    assert M.resolve_loops(uid, [lid, "nope"], "2026-10-29") == [lid]
    assert load(uid, "loops", [])[0]["status"] == "resolved"
    assert not M.due_loops(uid, dt.date(2026, 10, 30))
    # The demo extraction resolves it too: asked that day, and her line names it.
    uid = "loop-resolve-demo"
    save(uid, "profile", {"name": "Nora", "people": []})
    M.add_loops(uid, "2026-10-21", [{"what": "final round interview at Fieldwork", "when": "2026-10-28",
                                     "ask": "How did the Fieldwork interview go?", "kind": "event"}])
    M.mark_raised(uid, [load(uid, "loops", [])[0]["id"]], "2026-10-29")
    M.extract_day_heuristic(uid, "2026-10-29", {}, "assistant: How did the Fieldwork interview go?\n"
                            "user: the fieldwork interview was weird, priya was warm", {})
    assert load(uid, "loops", [])[0]["status"] == "resolved"


def test_the_extractor_is_shown_the_open_loops_and_can_close_them():
    assert "{open_loops}" in M.EXTRACT_PROMPT
    assert "resolved" in M.Extraction.model_fields


def test_demo_extraction_finds_what_is_coming():
    items = M.loops_heuristic(["my interview is on friday and i'm terrified",
                               "nothing much happened"], TUE)
    assert len(items) == 1 and items[0]["when"] == "2026-10-30"


# ------------------------------------------------------- the context block

def _ctx(uid, session, voice_name="v2"):
    from engine.chart import natal
    nat = natal(1999, 3, 14, 10.7, 41.88, -87.63)
    sky = {"aspects_to_natal": [], "upcoming": [], "positions": {}, "moon": {}}
    save(uid, "profile", {"name": "Maya", "people": []})
    return context.build(uid, nat, sky, {"mood": 6}, today=TUE + dt.timedelta(1),
                         session=session, news=["Theo (linked) checked in today: 8/10"],
                         voice_name=voice_name)


def test_follow_ups_open_a_conversation_and_never_interrupt_one():
    uid = "ctx-follow"
    M.add_loops(uid, TUE.isoformat(), [{"what": "dinner with theo", "when": "2026-10-27",
                                        "ask": "How was dinner?", "kind": "event"}])
    block, verdict = _ctx(uid, "first today")
    assert "FOLLOW UP" in block and "dinner with theo" in block
    assert verdict["follow_ups"]
    block, verdict = _ctx(uid, "continuing")
    assert "FOLLOW UP" not in block and not verdict["follow_ups"]


def test_v2_briefs_a_rhythm_and_v1_keeps_the_hard_cap():
    block, _ = _ctx("ctx-len", "continuing", "v2")
    assert "LENGTH:" in block and "MAX SENTENCES" not in block
    assert "NEWS" in block and "Theo (linked)" in block
    block, _ = _ctx("ctx-len1", "first today", "v1")
    assert "MAX SENTENCES" in block
    assert "FOLLOW UP" not in block and "NEWS" not in block and "SESSION" not in block


def test_the_v1_prompt_is_kept_and_selectable():
    assert "Keep the conversation going" in voice.system_prompt("v2")
    assert "Keep the conversation going" not in voice.system_prompt("v1")
    assert "Stay under the cap" in voice.system_prompt("v1")


def test_the_v2_prompt_keeps_the_rules_that_make_it_trustworthy():
    p = voice.system_prompt("v2")
    for rule in ("That block is your only source of", "Never assert a trait from her sign",
                 "Drop the persona completely", "CRISIS LINE", "say plainly that you're an AI",
                 "No\n  hooks", "Never say you miss her"):
        assert rule.replace("\n  ", " ") in p.replace("\n  ", " "), f"lost: {rule!r}"
    # The crisis line comes from her country in the block, not a US number in
    # the prompt (audit H5).
    assert "988" not in p


# ------------------------------------------------------------- the routes

def test_plus_talks_to_sonnet_and_free_to_haiku():
    assert models.chat_model("plus") == models.SONNET
    assert models.chat_model("free") == models.HAIKU
    assert models.chat_model(None) == models.HAIKU
    assert models.fallback_kwargs(models.SONNET)["fallbacks"] == "default"
    assert models.fallback_kwargs(models.HAIKU) == {}


def test_no_call_is_left_on_opus():
    import pathlib
    root = pathlib.Path(__file__).parent.parent
    hits = [str(p.relative_to(root)) for d in ("engine", "app", "tools")
            for p in (root / d).rglob("*.py")
            if "claude-opus" in p.read_text() and p.name != "models.py"]
    assert not hits, f"hard-coded Opus in {hits}"


# ------------------------------------------------------------------- chips

def test_chips_are_short_distinct_and_in_her_voice():
    got = chips._clean(["  Wait, is he a Scorpio?  ", "wait, is he a scorpio?", "x",
                        "a" * 80, "ok honestly not great", "what's the deep cut", "fourth"])
    assert got == ["Wait, is he a Scorpio?", "ok honestly not great", "what's the deep cut"]
    assert chips.heuristic("Answer tomorrow. What do you want him to feel?")
    assert chips.suggest("hi", "") == []


# --------------------------------------------------------------- end to end

def test_a_reply_ends_on_a_question_and_offers_chips(c):
    sign_in(c, "conv1@x.com"); onboard(c)
    ev = _send(c, "should i answer him")
    names = [e for e, _ in ev]
    assert "chips" in names and names.index("chips") < names.index("done")
    chips_ = dict(ev)["chips"]["chips"]
    assert 2 <= len(chips_) <= 3
    text = "".join(d["t"] for e, d in ev if e == "delta")
    assert text.rstrip().endswith("?")
    # Kept with the reply, so reopening the app shows them again.
    thread = c.get("/api/state").json()["thread"]
    assert thread[-1]["role"] == "assistant" and thread[-1]["chips"] == chips_


def test_no_chips_on_a_crisis_turn(c):
    sign_in(c, "conv2@x.com"); onboard(c)
    ev = _send(c, "i want to kill myself")
    assert "chips" not in [e for e, _ in ev]


def test_yesterdays_plan_is_asked_about_first_thing_today(c, monkeypatch):
    sign_in(c, "conv3@x.com"); onboard(c)
    uid = last_uid()
    today = server._today(uid)
    M.add_loops(uid, (today - dt.timedelta(days=1)).isoformat(),
                [{"what": "dinner with theo", "when": None,
                  "ask": "So how was dinner with Theo?", "kind": "event"}])
    ev = _send(c, "hi")
    text = "".join(d["t"] for e, d in ev if e == "delta")
    assert "dinner with theo" in text.lower()
    # Raised once today; the next message in the same conversation doesn't repeat it.
    assert load(uid, "loops", [])[0]["raised"] == [today.isoformat()]
    ev = _send(c, "ok and what about work")
    assert "dinner with theo" not in "".join(d["t"] for e, d in ev if e == "delta").lower()


def test_session_boundaries():
    now = dt.datetime(2026, 10, 28, 14, 0)
    uid = "sess"
    from engine import thread
    assert server._session(uid, now) == "first today"
    thread.append(uid, {"id": "a", "role": "user", "text": "hi", "at": "2026-10-28T13:30:00"})
    assert server._session(uid, now) == "continuing"
    assert server._session(uid, now + dt.timedelta(hours=4)) == "back after a break"
    assert server._session(uid, now + dt.timedelta(days=1)) == "first today"


# ------------------------------------------------------ audit MEDIUM-4 (voice)

def _say_back(uid, replies, day):
    from engine import thread
    for i, r in enumerate(replies):
        thread.append(uid, {"id": f"r{i}", "role": "assistant", "text": r, "tier": "green",
                            "at": f"{day.isoformat()}T10:0{i}:00"})


def test_two_question_endings_in_a_row_brief_a_statement_next():
    day = TUE + dt.timedelta(1)
    _say_back("rhythm-q", ["Mars squares your Moon (a tense angle). How's work?",
                           "Saturn is on your Venus. Did he text?"], day)
    block, _ = _ctx("rhythm-q", "continuing")
    assert "QUESTION: skip" in block
    _say_back("rhythm-noq", ["Fine. What now?", "Quiet day. Rest."], day)
    block, _ = _ctx("rhythm-noq", "continuing")
    assert "QUESTION: skip" not in block


def test_terms_explained_earlier_today_are_listed_once():
    day = TUE + dt.timedelta(1)
    _say_back("rhythm-g", ["Pluto hace una cuadratura (una tensión de 90 grados) a tu ascendente.",
                           "Mars squares your Moon (a tense angle)."], day)
    block, _ = _ctx("rhythm-g", "continuing")
    line = next(x for x in block.splitlines() if x.startswith("ALREADY EXPLAINED"))
    assert "square" in line and "rising sign" in line and "trine" not in line
    # Yesterday's conversation does not count.
    _say_back("rhythm-old", ["Mars squares your Moon (a tense angle)."], TUE)
    block, _ = _ctx("rhythm-old", "continuing")
    assert "ALREADY EXPLAINED" not in block


def test_the_limiter_keeps_the_first_sentences_and_the_closing_question():
    from engine import voice
    reply = ("Pluto squares your rising. It is 0.07° from exact. Mars is also on your Pluto. "
             "That part passes soon. It's a push, not a verdict.\n\nWhat keeps repeating in the fights?")
    lim = voice.Limiter(3)
    shown = "".join(lim.feed(reply[i:i + 7]) for i in range(0, len(reply), 7))
    stored, tail = lim.finish(reply)
    assert shown + tail == stored
    assert " ".join(stored.split()) == ("Pluto squares your rising. It is 0.07° from exact. "
                                        "Mars is also on your Pluto. What keeps repeating in the fights?")
    assert "\n\nWhat keeps" in stored
    # Under the ceiling, untouched.
    lim = voice.Limiter(5)
    short = "One. Two? "
    assert "".join(lim.feed(ch) for ch in short) == short and lim.finish(short) == ("One. Two?", "")
    assert voice.sentence_limit(3, "por que peleamos tanto") == 6
    assert voice.sentence_limit(3, "is he into me") == 3


# ------------------------------------------------------ audit MEDIUM-3 (chips)

class _FakeChips:
    def __init__(self, replies=None, fail=False):
        self.replies, self.fail, self.prompts = replies or [], fail, []
        self.messages = self

    def parse(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        if self.fail:
            raise RuntimeError("boom")
        return type("R", (), {"parsed_output": chips.Chips(replies=self.replies)})()


def test_no_chips_after_a_goodbye():
    fake = _FakeChips(["what time is good to show up?"])
    for bye in ("ok. night", "yeah. ok. thank you, goodnight", "ok going to bed. thanks",
                "gracias amiga. buenas noches", "ok wish me luck"):
        assert chips.suggest(bye, "Goodnight. Sleep well.", client=fake) == [], bye
    assert not fake.prompts
    # "night" inside an ordinary message is not a goodbye.
    assert chips.suggest("he texted me last night", "What did he say?", client=_FakeChips(["hm"]))


def test_chips_are_asked_for_reactions_not_facts_and_know_who_is_who():
    save("chips-who", "connections", [{"name": "Jess", "relation": "best friend"}])
    fake = _FakeChips(["hm, why do you ask", "an absurdly long chip that goes well past seven words"])
    got = chips.suggest("jess and i had a tiff", "What was it about?", client=fake,
                        uid="chips-who", lang="es")
    assert got == ["hm, why do you ask"]
    p = fake.prompts[0]
    assert "Jess (best friend)" in p and "Spanish" in p
    assert "Never state a fact about her life" in p and "first chip answers it" not in p


def test_a_failed_chip_call_shows_no_canned_chips():
    assert chips.suggest("hi", "How's work?", client=_FakeChips(fail=True)) == []


def test_the_block_says_when_the_moon_changes_sign_in_her_day():
    # Sydney, 30 Oct 2026: the Moon leaves Gemini for Cancer in her afternoon.
    got = context.moon_change(dt.date(2026, 10, 30), "Australia/Sydney")
    assert got and got[:2] == ("Gemini", "Cancer"), got
    assert context.orb_word(0.1) == "exact" and "not 'almost exact'" in context.orb_word(1.26)


def test_state_records_where_she_is_before_rolling_the_day_over(c):
    sign_in(c, "tz-state@x.com"); onboard(c)
    c.get("/api/state", headers={"X-Tz": "Asia/Tokyo"})
    assert load(last_uid(), "account", {})["tz"] == "Asia/Tokyo"


def test_lunations_also_give_the_last_new_and_full_moon():
    from engine import tools
    from engine.chart import natal
    nat = natal(1997, 12, 1, 17.25, -33.87, 151.21)
    r = tools.get_lunations("u", nat, "2026-10-30", days=10)
    kinds = {x["kind"]: x["date"] for x in r["previous"]}
    assert set(kinds) == {"new moon", "full moon"} and kinds["full moon"] == "2026-10-26"


def test_the_limiter_lets_a_just_opened_paragraph_finish():
    """Cut on 'Tampoco es todo pelea.' the reply hung on a lead-in."""
    from engine import voice
    reply = ("Uno. Dos. Tres.\n\nCuatro. Cinco. Seis.\n\nTampoco es todo pelea. Él te busca. "
             "Y tú a él. Y otra cosa. Y otra más.")
    lim = voice.Limiter(7)
    shown = "".join(lim.feed(reply[i:i + 4]) for i in range(0, len(reply), 4))
    stored, tail = lim.finish(reply)
    assert shown.strip().endswith("Y tú a él.") and stored == shown.strip() and tail == ""
