"""
The personality read and the fun compatibility read.

The model is stubbed: these check the plumbing that makes the output safe to
show — structured output requested, every line grounded, invented placements
dropped rather than shown — and that demo mode still fills the same shape.
"""
import json
from types import SimpleNamespace

from engine import compat_fun, interpret, profile
from engine.chart import natal

CHICAGO = [1999, 3, 14, 10.7, 41.8781, -87.6298]
LAGOS = [1998, 7, 2, 14.0, 6.5244, 3.3792]


class Stub:
    """Records the request and returns a canned structured response."""
    def __init__(self, payload):
        self.payload, self.seen = payload, {}
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.seen = kw
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


def _item(text, because):
    return {"text": text, "because": because}


def test_the_profile_asks_for_structured_output_and_drops_invented_lines():
    n = natal(*CHICAGO)
    a = interpret.analyze(n)
    moon = n["planets"]["Moon"]["sign"]
    wrong = "Leo" if moon != "Leo" else "Virgo"
    payload = {"tagline": "Cool head, fast mouth.", "vibe": "You arrive already mid-sentence.",
               "superpower": _item("You read a room in seconds.", f"Moon in {moon}"),
               "toxic_trait": _item("You leave on read and call it boundaries.", f"Moon in {wrong}"),
               "in_love": _item("You fall fast.", "Venus in " + n["planets"]["Venus"]["sign"]),
               "when_mad": _item("Silence, then a plan.", "Mars in " + n["planets"]["Mars"]["sign"]),
               "at_work": _item("You debate for sport.", "Mercury in " + n["planets"]["Mercury"]["sign"]),
               "misread": _item("Cold? No. Busy thinking.", f"Moon in {moon}"),
               "needs": _item("Room to think.", f"Moon in {moon}"),
               "green_flags": [_item("Remembers everything.", f"Moon in {moon}")],
               "red_flags": [_item("Chiron energy.", "Chiron in your 13th house")]}
    stub = Stub(payload)
    out = profile.generate(n, a, True, name="Maya", client=stub)
    fmt = stub.seen["output_config"]["format"]
    assert fmt["type"] == "json_schema" and "tagline" in fmt["schema"]["required"]
    assert out["tagline"] == "Cool head, fast mouth."
    assert out["toxic_trait"] is None, "a Moon in the wrong sign must not be shown"
    assert out["superpower"]["because"] == f"Moon in {moon}"
    assert out["red_flags"] == [], "a 13th house must not be shown"


def test_the_profile_falls_back_without_a_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    n = natal(*CHICAGO)
    r = profile.build(n, interpret.analyze(n), True)
    assert not r["generated"] and r["tagline"]
    keys = [s["key"] for s in r["sections"]]
    assert {"in_love", "when_mad", "toxic_trait"} <= set(keys)
    assert all(s["because"] for s in r["sections"])


def test_compat_fun_reads_both_charts_and_keeps_owners_straight():
    mine, theirs = natal(*CHICAGO), natal(*LAGOS)
    lines = compat_fun._person_lines("your", mine, True) + compat_fun._person_lines("Theo's", theirs, True)
    assert f"your Venus in {mine['planets']['Venus']['sign']}" in lines
    assert f"Theo's Mars in {theirs['planets']['Mars']['sign']}" in lines


def test_platonic_pairs_never_get_a_date():
    report = {"score": {"kind": "friend"}}
    out = compat_fun.shape({"headline": "x", "dynamic": "y",
                            "chemistry": _item("a", "b"), "date": _item("c", "d")}, report)
    titles = [s["title"] for s in out["sections"]]
    assert "Your perfect date" not in titles and "Best thing to do together" in titles
