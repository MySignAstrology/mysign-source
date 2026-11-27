"""
The agent: its tools, and the loop that drives them.

The loop is tested against a fake client rather than the network — the mechanics
(pause on tool_use, execute, resume, one user message for all results) are what
break, and they are provable offline.
"""
import os
import pathlib
import sys
import tempfile
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-agent-"))

from engine import agent, tools
from engine.chart import natal
from engine.store import save

NAT = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)


# ------------------------------------------------------------------- tools

def test_every_tool_has_a_handler_and_a_strict_schema():
    for d in tools.DEFINITIONS:
        assert d["name"] in tools.HANDLERS, f"{d['name']} has no handler"
        assert d.get("strict") is True, f"{d['name']} is not strict"
        schema = d["input_schema"]
        assert schema["additionalProperties"] is False
        assert "required" in schema
        assert d["description"].strip(), f"{d['name']} has no description"


def test_get_sky_answers_about_another_day():
    """The question the old architecture had to decline."""
    r = tools.run("get_sky", "u", NAT, {"date": "2026-09-03"})
    assert r["weekday"] == "Thursday"
    assert r["aspects_to_her_chart"], "no aspects computed"
    assert all({"transiting", "aspect", "her", "orb"} <= set(a)
               for a in r["aspects_to_her_chart"])


def test_get_sky_works_for_the_past_too():
    r = tools.run("get_sky", "u", NAT, {"date": "2019-06-14"})
    assert r["date"] == "2019-06-14" and r["placements"]["Sun"]["at"]


def test_find_next_matches_a_direct_scan():
    """The tool that answers 'when does this end' has to be right."""
    import datetime as dt

    import swisseph as swe

    from engine.chart import jd_ut

    r = tools.run("find_next", "u", NAT,
                  {"transiting": "Mars", "natal_point": "Moon",
                   "aspect": "square", "from_date": "2026-08-27"})
    assert r["found"]

    moon = NAT["planets"]["Moon"]["lon"]
    from engine.ephemeris import FLAGS as flags
    start, prev, first = dt.date(2026, 8, 27), None, None
    for i in range(800):
        day = start + dt.timedelta(days=i)
        j = jd_ut(day.year, day.month, day.day, 12.0)
        lon = swe.calc_ut(j, swe.MARS, flags)[0][0]
        d = abs(lon - moon) % 360
        orb = abs(min(d, 360 - d) - 90)
        if prev is not None and orb > prev and prev < 1.0:
            first = (day - dt.timedelta(days=1)).isoformat()
            break
        prev = orb
    assert r["date"] == first, f"tool says {r['date']}, scan says {first}"


def test_get_lunations_finds_the_known_full_moon():
    r = tools.run("get_lunations", "u", NAT,
                  {"from_date": "2026-08-27", "days": 5})
    dates = [x["date"] for x in r["lunations"]]
    assert "2026-08-28" in dates          # verified in tests/test_astronomy.py
    assert all(1 <= x["her_house"] <= 12 for x in r["lunations"])


def test_search_memory_reaches_past_the_recent_window():
    uid = "agent-mem"
    save(uid, "episodes", [
        {"date": "2025-02-11", "mood": 3, "summary": "felt like this about Theo",
         "her_words": ["i hate how that came out"], "topics": ["dating"],
         "people": ["Theo"], "sky_state": {}},
        {"date": "2026-08-01", "mood": 8, "summary": "good day with friends",
         "her_words": [], "topics": ["friends"], "people": [], "sky_state": {}},
    ])
    r = tools.run("search_memory", uid, NAT, {"query": "theo", "limit": 5})
    assert r["found"] >= 1
    assert any("2025" in e["date"] for e in r["episodes"]), \
        "did not reach the old episode"


def test_synastry_without_a_saved_person_says_so():
    r = tools.run("get_synastry", "agent-nobody", NAT, {"name": "Theo"})
    assert "error" in r and "saved_people" in r


@pytest.mark.parametrize("call,bad", [
    ("find_next", {"transiting": "Xena", "natal_point": "Moon", "aspect": "square"}),
    ("find_next", {"transiting": "Mars", "natal_point": "Moon", "aspect": "quintile"}),
    ("find_next", {"transiting": "Mars", "natal_point": "Nessus", "aspect": "square"}),
    ("get_sky", {"date": "not-a-date"}),
])
def test_bad_input_returns_a_readable_error_not_a_crash(call, bad):
    r = tools.run(call, "u", NAT, bad)
    assert "error" in r, f"{call}{bad} did not report an error"


def test_an_unknown_tool_is_reported_not_raised():
    assert "error" in tools.run("summon_mercury", "u", NAT, {})


# -------------------------------------------------------------------- loop

class _Blk(dict):
    __getattr__ = dict.get


def _fake_client(script):
    """script: list of (chunks, stop_reason, content)."""
    class Stream:
        def __init__(self, chunks, final):
            self.text_stream, self._f = chunks, final
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self): return self._f

    class Messages:
        def __init__(self): self.turn = 0; self.calls = []
        def stream(self, **kw):
            self.calls.append(kw)
            chunks, stop, content = script[min(self.turn, len(script) - 1)]
            self.turn += 1
            return Stream(chunks, types.SimpleNamespace(
                stop_reason=stop, content=content))

    return type("C", (), {"messages": Messages()})()


def _collect(events):
    out = {"delta": [], "tool": [], "tool_result": [], "done": None}
    for kind, payload in events:
        if kind == "done":
            out["done"] = payload
        else:
            out.setdefault(kind, []).append(payload)
    return out


def test_the_loop_pauses_executes_and_resumes():
    client = _fake_client([
        ([""], "tool_use",
         [_Blk(type="tool_use", id="t1", name="get_sky", input={"date": "2026-09-03"})]),
        (["Thursday is worse."], "end_turn", []),
    ])
    got = _collect(agent.run(client, "SYS", "BLOCK", "what about thursday?",
                             [], "u", NAT, tools))
    assert got["tool"][0]["name"] == "get_sky"
    assert got["tool_result"][0]["ok"] is True
    assert got["done"]["text"] == "Thursday is worse."
    assert [t["name"] for t in got["done"]["tools_used"]] == ["get_sky"]
    assert got["done"]["stopped"] == "end_turn"


def test_tool_results_go_back_in_one_message():
    """Splitting them trains the model out of parallel calls."""
    client = _fake_client([
        ([""], "tool_use", [
            _Blk(type="tool_use", id="a", name="get_sky", input={"date": "2026-09-03"}),
            _Blk(type="tool_use", id="b", name="get_natal", input={}),
        ]),
        (["done"], "end_turn", []),
    ])
    list(agent.run(client, "SYS", "B", "q", [], "u", NAT, tools))
    second = client.messages.calls[1]["messages"]
    user_msgs = [m for m in second if m["role"] == "user"]
    results = [b for m in user_msgs for b in m["content"]
               if isinstance(b, dict) and b.get("type") == "tool_result"]
    assert len(results) == 2
    holders = [m for m in user_msgs
               if isinstance(m["content"], list)
               and any(b.get("type") == "tool_result" for b in m["content"])]
    assert len(holders) == 1, "results were split across messages"


def test_grounding_carries_the_briefing_and_every_tool_result():
    """Without this, every tool-assisted answer scores as a hallucination."""
    client = _fake_client([
        ([""], "tool_use",
         [_Blk(type="tool_use", id="t1", name="get_sky", input={"date": "2026-09-03"})]),
        (["ok"], "end_turn", []),
    ])
    got = _collect(agent.run(client, "SYS", "THE-BRIEFING", "q", [], "u", NAT, tools))
    g = got["done"]["grounding"]
    assert "THE-BRIEFING" in g
    assert "2026-09-03" in g and "get_sky" in g


def test_a_failing_tool_is_returned_as_an_error_not_a_crash():
    client = _fake_client([
        ([""], "tool_use",
         [_Blk(type="tool_use", id="t1", name="get_sky", input={"date": "nope"})]),
        (["I couldn't look that up."], "end_turn", []),
    ])
    got = _collect(agent.run(client, "SYS", "B", "q", [], "u", NAT, tools))
    assert got["tool_result"][0]["ok"] is False
    second = client.messages.calls[1]["messages"]
    err = [b for m in second if m["role"] == "user" and isinstance(m["content"], list)
           for b in m["content"] if b.get("type") == "tool_result"]
    assert err and err[0].get("is_error") is True


def test_asking_about_someone_never_saved_is_quiet_not_a_failure():
    # The model looks up a name she never saved. The UI should drop the step
    # (no "nothing there"), the saved message should not list it, and the
    # model still gets the error with her saved names.
    client = _fake_client([
        ([""], "tool_use",
         [_Blk(type="tool_use", id="t1", name="get_person_day", input={"name": "Zed"})]),
        (["I don't have Zed saved yet."], "end_turn", []),
    ])
    got = _collect(agent.run(client, "SYS", "B", "how's Zed today?", [], "quiet-u", NAT, tools))
    r = got["tool_result"][0]
    assert r["ok"] is False and r["quiet"] is True
    assert got["done"]["tools_used"][0]["quiet"] is True
    second = client.messages.calls[1]["messages"]
    err = [b for m in second if m["role"] == "user" and isinstance(m["content"], list)
           for b in m["content"] if b.get("type") == "tool_result"]
    assert "saved_people" in err[0]["content"]


def test_a_real_tool_failure_is_not_quiet():
    client = _fake_client([
        ([""], "tool_use",
         [_Blk(type="tool_use", id="t1", name="get_sky", input={"date": "nope"})]),
        (["I couldn't look that up."], "end_turn", []),
    ])
    got = _collect(agent.run(client, "SYS", "B", "q", [], "u", NAT, tools))
    assert "quiet" not in got["tool_result"][0]


def test_the_loop_cannot_run_forever():
    """A loop that can call tools indefinitely is a bill, not a feature."""
    client = _fake_client([
        ([""], "tool_use",
         [_Blk(type="tool_use", id="x", name="get_natal", input={})]),
    ])
    got = _collect(agent.run(client, "SYS", "B", "q", [], "u", NAT, tools,
                             max_iterations=3))
    assert got["done"]["stopped"] == "max_iterations"
    assert len(got["tool"]) == 3


def test_the_tool_list_is_stable_for_prompt_caching():
    """Tools render before system and messages — a reordered list breaks the
    cached prefix on every request."""
    a = [d["name"] for d in tools.DEFINITIONS]
    b = [d["name"] for d in tools.DEFINITIONS]
    assert a == b and len(a) == len(set(a))


# ------------------------------------------------- demo mode looks things up

def test_demo_mode_runs_the_real_tool_for_another_day(monkeypatch):
    """
    The model is absent in demo mode; the engine is not. A question about
    another day should run the actual tool and answer from the actual sky —
    otherwise the tool path ships untested by anything but a fake client.
    """
    import re as _re

    from fastapi.testclient import TestClient

    import app.server as server
    from tests.test_app import onboard, sign_in

    c = TestClient(server.app)
    sign_in(c, "demotool@x.com")
    onboard(c)
    with c.stream("POST", "/api/message",
                  json={"text": "is thursday a bad day to talk to him"}) as r:
        body = "".join(r.iter_text())

    assert "event: tool" in body, "no lookup happened"
    assert "get_sky" in body
    reply = "".join(_re.findall(r'"t": "(.*?)"', body))
    assert "Thursday" in reply, f"the answer is not about Thursday: {reply[:120]}"


def test_demo_mode_does_not_invent_a_date_from_a_vague_question():
    from app.server import _demo_lookup_date
    for vague in ("should i answer him", "why do i feel like this",
                  "what about later", "is it a good time"):
        assert _demo_lookup_date(vague) is None, vague
    assert _demo_lookup_date("what about thursday") is not None
    assert _demo_lookup_date("what happened on 2019-06-14") == "2019-06-14"


# ------------------------------------------------- find_next, honestly

def _fn(t, n, a, today):
    import datetime as dt
    return tools.run("find_next", "u", NAT,
                     {"transiting": t, "natal_point": n, "aspect": a},
                     today=dt.date.fromisoformat(today))


def test_find_next_counts_from_the_conversation_day_not_the_server_clock():
    r = _fn("Mars", "Moon", "square", "2026-08-27")
    explicit = tools.run("find_next", "u", NAT,
                         {"transiting": "Mars", "natal_point": "Moon",
                          "aspect": "square", "from_date": "2026-08-27"})
    assert r["date"] == explicit["date"]


def test_find_next_refines_fast_planets_to_the_hour():
    """A daily grid put Mercury 0.9° off a trine it actually perfected."""
    r = _fn("Mercury", "Venus", "trine", "2026-08-22")
    assert r["passes"][0]["date"] == "2026-08-23"
    assert r["passes"][0]["exact"] and r["passes"][0]["orb"] < 0.1


def test_find_next_reports_when_the_current_contact_ends():
    """'When does this end' is what the tool is for, and it used to not say."""
    r = _fn("Mercury", "Venus", "trine", "2026-08-22")
    assert r["now"]["in_effect"]
    assert r["ends"] == "2026-08-24", "the end of THIS contact, not the last pass of the year"


def test_find_next_does_not_skip_the_moon():
    """The Moon squares a point about every two weeks; a daily grid missed most."""
    r = _fn("Moon", "Sun", "square", "2026-08-22")
    gaps = [b["days_away"] - a["days_away"] for a, b in zip(r["passes"], r["passes"][1:5])]
    assert gaps and all(12 <= g <= 17 for g in gaps), gaps


def test_find_next_never_calls_a_near_miss_exact():
    for t, n, a in [("Mercury", "Venus", "trine"), ("Neptune", "Mercury", "conjunction"),
                    ("Moon", "Sun", "square"), ("Saturn", "Sun", "square")]:
        for p in _fn(t, n, a, "2026-08-22")["passes"]:
            assert p["exact"] == (p["orb"] <= tools.EXACT_ORB)


# ------------------------------------------------------ memory retrieval

def test_memory_search_matches_across_word_endings():
    """The model searched "overexplaining", found nothing, and told her she had
    never mentioned it — she had, as "overexplain"."""
    from engine import retrieval
    eps = [{"date": "2026-07-29", "summary": "overexplained to Theo again",
            "her_words": ["why do i always overexplain"]}]
    for q in ("overexplaining", "over-explaining myself", "overexplains"):
        assert retrieval.relevant(q, eps, floor=0.2), q


NORA_EPS = [
    {"date": "2026-10-21", "mood": 4, "summary": "Final round interview next week at Fieldwork; anxious. Sister Dana says negotiate hard if an offer comes.",
     "her_words": ["im already spiraling", "i hate negotiating"], "people": ["Dana", "Priya Raman"],
     "topics": ["work", "interview", "negotiation"]},
    {"date": "2026-10-22", "mood": 6, "summary": "Mock interview with Dana went better than expected.",
     "her_words": ["it went better than i expected"], "people": ["Dana"], "topics": ["work", "mock interview"]},
    {"date": "2026-10-23", "mood": None, "summary": "Bought a green blazer.", "her_words": [], "people": [],
     "topics": ["clothes"]},
    {"date": "2026-09-01", "mood": 7, "summary": "Weekend away.", "her_words": [], "people": [],
     "topics": ["travel"]},
]


def _search(monkeypatch, query, today=None):
    import datetime as dt

    from engine import memory
    monkeypatch.setattr(memory, "all_episodes", lambda uid: list(NORA_EPS))
    return tools.search_memory("u", None, query, today=today or dt.date(2026, 10, 23))


def test_a_this_week_mood_search_returns_the_week_not_one_lexical_hit(monkeypatch):
    """search_memory('mood…') matched only the day tagged 'mood'; the model then
    took back the 4 it had quoted, and Dana's advice with it (audit HIGH-2)."""
    r = _search(monkeypatch, "her mood this week")
    dates = [e["date"] for e in r["episodes"]]
    assert {"2026-10-21", "2026-10-22", "2026-10-23"} <= set(dates)
    assert "2026-09-01" not in dates
    assert r["dates"] == {"from": "2026-10-16", "to": "2026-10-23"}


def test_every_search_carries_the_last_week_and_says_a_miss_is_not_proof(monkeypatch):
    r = _search(monkeypatch, "skydiving")
    assert r["found"] == 0 and r["no_match_for_this_query"]
    assert {e["date"] for e in r["last_7_days"]} == {"2026-10-21", "2026-10-22", "2026-10-23"}
    assert "not evidence it never happened" in r["note"]


def test_synonyms_and_stems_find_what_she_said_in_other_words(monkeypatch):
    for q in ("what did dana say about negotiation", "how was i feeling", "the offer"):
        r = _search(monkeypatch, q)
        assert "2026-10-21" in [e["date"] for e in r["episodes"]], q


def test_a_named_day_is_searched_by_date(monkeypatch):
    for q in ("what did i say on the 22nd", "22 October", "october 22nd", "2026-10-22"):
        r = _search(monkeypatch, q)
        assert r["episodes"][0]["date"] == "2026-10-22", q


def test_context_recalls_what_her_message_is_about():
    """Retrieval keyed only off the check-in, so a question asked in chat never
    reached the day she first said it."""
    import datetime as dt

    from engine import context
    uid = "recall-msg"
    save(uid, "profile", {"name": "M", "people": []})
    save(uid, "episodes", [{"date": "2026-07-29", "mood": 4,
                            "summary": "overexplained to Theo again",
                            "her_words": ["why do i always overexplain"],
                            "topics": ["dating"], "people": ["Theo"], "sky_state": {}}])
    sky = {"aspects_to_natal": [], "upcoming": [], "moon": {}, "active_houses": []}
    day = dt.date(2026, 8, 22)
    without, _ = context.build(uid, NAT, sky, {"mood": 5, "text": ""}, today=day)
    with_msg, _ = context.build(uid, NAT, sky, {"mood": 5, "text": ""}, today=day,
                                message="why do i always overexplain myself")
    assert "2026-07-29" not in without
    assert "2026-07-29" in with_msg


def test_the_conversation_so_far_is_a_cache_breakpoint_and_effort_follows_the_tier():
    """Audit MEDIUM-8: only the system prompt was cached, and every green turn
    thought at medium effort before its first word."""
    from engine import models
    seen = []

    class Stream:
        text_stream = iter(["ok."])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return types.SimpleNamespace(stop_reason="end_turn", content=[])

    class Client:
        class messages:
            @staticmethod
            def stream(**kw):
                seen.append(kw)
                return Stream()

    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]
    list(agent.run(Client, "sys", "block", "and now?", history, "u", {}, tools,
                   model=models.HAIKU, effort=models.chat_effort("green")))
    sent = seen[0]["messages"]
    assert sent[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in str(sent[-1]) and history[1]["content"] == "hey"
    assert seen[0]["output_config"]["effort"] == "low"
    assert models.chat_effort("amber") == models.CHAT_EFFORT == "medium"
