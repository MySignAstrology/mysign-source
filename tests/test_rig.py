"""
Pilot rig tests. Run with: python -m pytest tests/test_rig.py -q

These existed once and were deleted as collateral in ec2a9e1, a commit about
moving an engine script. The rig went untested afterwards and the safety module
quietly drifted into a stale copy of engine/safety.py — no pre-filter, an eager
client, no fail-closed path — so a missing API key took the whole webhook down.

What is covered here is the part that would hurt someone: the webhook must never
5xx, a crisis must be caught without the network, and a transport or model
outage must still put something in front of a human.
"""
import datetime as dt
import os
import tempfile

import pytest

os.environ["MYSIGN_DATA"] = tempfile.mkdtemp(prefix="mysign-rig-")
for k in ("ANTHROPIC_API_KEY", "TWILIO_SID", "TWILIO_TOKEN", "TWILIO_FROM",
          "MYSIGN_TABLE", "OPERATOR_SMS"):
    os.environ.pop(k, None)

from fastapi.testclient import TestClient

import rig.app as R
from engine.store import load, save
from rig import safety

PHONE = "+13125550142"


@pytest.fixture
def c():
    save("_study", "participants", [{
        "user_id": "p_test", "name": "Maya", "phone": PHONE,
        "tz": "America/Chicago",
        "birth": [1999, 3, 14, 10.7, 41.8781, -87.6298],
        "birth_local": "1999-03-14 04:42 America/Chicago",
        "enrolled_at": dt.date.today().isoformat(), "consented": True,
    }])
    save("_study", "queue", [])
    return TestClient(R.app, raise_server_exceptions=False)


def queue():
    return load("_study", "queue", [])


# ------------------------------------------------------------------ safety

def test_rig_safety_is_the_engine_implementation():
    """One copy. The drift between two copies is what broke the rig."""
    from engine import safety as engine_safety
    assert safety.classify is engine_safety.classify
    assert safety.prefilter is engine_safety.prefilter
    assert safety.CRISIS_REPLY is engine_safety.CRISIS_REPLY


@pytest.mark.parametrize("msg", [
    "i want to kill myself",
    "i don't want to be here anymore",
    "he hit me again",
])
def test_crisis_is_caught_without_a_network(msg):
    """No API key is set in this module. Red still has to land."""
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert safety.classify(msg).tier == "red"


def test_ordinary_message_stays_green_without_a_network():
    assert safety.classify("should i answer him or not").tier == "green"


# ----------------------------------------------------------------- webhook

def test_unenrolled_numbers_get_silence(c):
    r = c.post("/sms", data={"From": "+15559998888", "Body": "hello?"})
    assert r.status_code == 204
    assert queue() == []


def test_webhook_never_5xxs_when_the_model_is_unreachable(c):
    """
    Twilio retries a 5xx. A crash here does not just drop the reply, it
    re-runs the handler and doubles the queue.
    """
    r = c.post("/sms", data={"From": PHONE, "Body": "ok theo texted. should i answer"})
    assert r.status_code == 204, r.text


def test_a_failed_draft_still_reaches_the_operator(c):
    """An outage must leave a human something to act on, not nothing."""
    c.post("/sms", data={"From": PHONE, "Body": "why do i keep waking up at 4am"})
    items = queue()
    assert len(items) == 1
    item = items[0]
    assert item["kind"].endswith("DRAFT-FAILED")
    assert item["status"] == "pending"
    assert "why do i keep waking up at 4am" in item["context_block"]


def test_crisis_survives_a_dead_transport(c):
    """
    Twilio is unconfigured here, so the canned reply cannot go out. The webhook
    still must not 5xx, and the crisis must not vanish — it goes in the queue
    flagged for a human instead.
    """
    r = c.post("/sms", data={"From": PHONE, "Body": "i don't want to be here anymore"})
    assert r.status_code == 204
    items = queue()
    assert len(items) == 1
    assert items[0]["kind"] == "crisis/SEND-FAILED"
    assert items[0]["draft"] == safety.CRISIS_REPLY


def test_a_crisis_never_gets_an_astrology_draft(c):
    """
    Not a keyword sniff — CRISIS_REPLY itself says "no transits", and asserting
    on vocabulary would fail on the correct copy. What matters is that the model
    was never asked to write this one: nothing queued is a drafted reply, and
    the text is the canned response verbatim.
    """
    c.post("/sms", data={"From": PHONE, "Body": "i want to kill myself"})
    items = queue()
    assert items
    for item in items:
        assert not item["kind"].startswith("reply/")
        assert item["draft"] == safety.CRISIS_REPLY


def test_inbound_is_recorded_even_when_the_draft_fails(c):
    c.post("/sms", data={"From": PHONE, "Body": "hey"})
    thread = load("p_test", "thread", [])
    assert thread and thread[-1]["role"] == "user" and thread[-1]["text"] == "hey"
