"""
The couples' public story: the researched snapshot, and the weekly job that
adds to it. The job's output is model output from the open web, so what's
tested here is the mechanical gate in front of the page.
"""
import json
from types import SimpleNamespace

import pytest

from engine import couples as C
from engine import couples_research as R
from engine import couples_watch
from engine.store import load, save


@pytest.fixture(autouse=True)
def fresh_store():
    save(C.STORE, "story_pending", None)
    for c in C.couples():
        save(C.STORE, f"story-{R.key(c)}", None)
        save(C.STORE, f"read-{c['slug']}", None)
    yield


def _c(slug="taylor-swift-and-travis-kelce"):
    return C.by_slug(slug)


# ------------------------------------------------------------- the snapshot

def test_every_story_item_is_sourced_and_on_topic():
    snap = R._snapshot()["couples"]
    keys = {R.key(c) for c in C.couples()}
    assert set(snap) <= keys, "a story for a couple we don't list"
    for k, s in snap.items():
        for m in s.get("milestones", []):
            assert m["event"] in R.EVENTS, (k, m)
            assert R._date_ok(m["date"]), (k, m)
            assert m["sources"] and all(u.startswith("https://") for u in m["sources"]), (k, m)
            assert R._clean(m["text"]), (k, m["text"])
        if s.get("met"):
            assert s["met"]["sources"] and R._clean(s["met"]["text"]), k


def test_most_couples_have_a_story():
    have = [c for c in C.couples() if (R.story(c) or {}).get("milestones")]
    assert len(have) >= 0.8 * len(C.couples())


def test_the_story_reaches_the_page_and_the_read():
    c = _c("barack-obama-and-michelle-obama")
    d = C.detail(c["slug"])
    assert d["story"]["milestones"]
    facts = couples_watch.facts(c)
    assert "MILESTONE " in facts and "married" in facts


# --------------------------------------------------------------- the gate

def _m(**kw):
    return {"date": "2026-07-03", "event": "married", "text": "They married in Rhode Island.",
            "sources": ["https://people.com/a", "https://apnews.com/b"], **kw}


SEEN = {"https://people.com/a", "https://apnews.com/b", "https://www.tmz.com/c",
        "https://people.com/d"}


def test_two_outlets_publish_one_waits_for_a_person():
    ok, review, rej = R.validate([_m(), _m(date="2026-08", event="public",
                                           sources=["https://people.com/a", "https://people.com/d"])],
                                 SEEN, [])
    assert len(ok) == 1 and len(review) == 1 and not rej


@pytest.mark.parametrize("bad,why", [
    (_m(sources=["https://people.com/never-returned"]), "no verified source"),
    (_m(sources=["https://www.tmz.com/c"]), "no verified source"),
    (_m(text="They married months after announcing a pregnancy."), "off-limits"),
    (_m(text="A source told the outlet they wed quietly."), "off-limits"),
    (_m(event="feud"), "event"),
    (_m(date="next June"), "date"),
    (_m(date="2099-06"), "date"),
])
def test_the_gate_rejects(bad, why):
    ok, review, rej = R.validate([bad], SEEN, [])
    assert not ok and not review and rej[0]["why"] == why


def test_known_milestones_are_not_added_twice():
    _ok, _r, rej = R.validate([_m()], SEEN, [{"date": "2026-07-03", "event": "married"}])
    assert rej[0]["why"] == "duplicate"


# ------------------------------------------------------------- the weekly job

class SearchStub:
    """A web-searching response: a search result block, then the JSON."""
    def __init__(self, items, urls=SEEN, pause_first=False):
        self.items, self.urls, self.calls, self.pause = items, urls, [], pause_first
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        results = SimpleNamespace(type="web_search_tool_result",
                                  content=[SimpleNamespace(url=u) for u in self.urls])
        if self.pause and len(self.calls) == 1:
            return SimpleNamespace(stop_reason="pause_turn", content=[results])
        return SimpleNamespace(stop_reason="end_turn", content=[
            results, SimpleNamespace(type="text", text=json.dumps({"milestones": self.items}))])


def test_research_uses_web_search_on_reputable_domains_and_resumes_a_pause():
    stub = SearchStub([_m(date="2026-09-01", event="public")], pause_first=True)
    ok, _r, _x = R.research(_c(), stub)
    assert len(stub.calls) == 2, "pause_turn must be resumed"
    tool = stub.calls[0]["tools"][0]
    assert tool["type"] == "web_search_20260209"
    # unreachable outlets in allowed_domains 400 the whole call; the gate filters instead
    assert "allowed_domains" not in tool
    # web search results carry citations, which structured outputs reject (400)
    assert "format" not in stub.calls[0]["output_config"]
    prompt = stub.calls[0]["messages"][0]["content"]
    assert "Never include" in prompt and "rumours" in prompt
    assert ok and ok[0]["event"] == "public"


def test_the_run_publishes_holds_and_rotates():
    two = _m(date="2026-10-01", event="engaged")
    one = _m(date="2026-10-02", event="separated", sources=["https://people.com/a"])
    stub = SearchStub([two, one])
    out = R.run(limit=2, client=stub)
    assert len(out["researched"]) == 2 and out["held_for_review"] == 2
    first = C.by_slug(out["researched"][0])
    assert any(m["date"] == "2026-10-01" for m in R.story(first)["milestones"])
    assert not any(m["date"] == "2026-10-02" for m in R.story(first)["milestones"])
    # next run picks different couples: the ones just done aren't due yet
    again = R.run(limit=2, client=stub)
    assert not set(again["researched"]) & set(out["researched"])
    # a person approves the single-source one
    k = next(k for k, v in load(C.STORE, "story_pending", {}).items() if v["slug"] == first["slug"])
    assert R.approve(k)
    assert any(m["date"] == "2026-10-02" for m in R.story(first)["milestones"])
    assert k not in (load(C.STORE, "story_pending", {}) or {})


def test_couples_who_have_both_died_are_not_researched():
    due = R._due(__import__("datetime").datetime.now())
    assert due and all(not (c["people"][0].get("death_date") and c["people"][1].get("death_date"))
                       for c in due)
    assert due[0]["group"] == "now", "current couples first"


def test_no_key_means_no_research():
    assert R.run()["skipped"] == "no model key"


def test_a_new_milestone_rewrites_the_read():
    c = _c()
    before = couples_watch._version(c)
    R.publish(c, [_m(date="2027-03-01", event="separated")])
    assert couples_watch._version(c) != before


def test_the_json_is_found_at_the_end_of_a_chatty_reply():
    text = ('I searched and found one new thing. {"note": 1}\n'
            'Here is the result:\n{\n  "milestones": [{"date": "2026-09-01", "event": "public", '
            '"text": "x", "sources": []}]\n}')
    assert R._milestones(text)[0]["date"] == "2026-09-01"
    assert R._milestones("nothing new, sorry") is None
    assert R._milestones('{"milestones": "nope"}') is None
