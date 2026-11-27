"""
Launch-readiness regressions — each one a bug found by testing the app as a new
user, or by the pre-launch audit, and fixed. Kept together so the list of what
went wrong stays readable.
"""
import datetime as dt
import os
import pathlib
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-launch-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import server
from engine import plan, quota, store, thread
from engine.store import load
from tests.test_app import last_uid, onboard, sign_in


@pytest.fixture
def c():
    return TestClient(server.app)


def _send(c, text):
    with c.stream("POST", "/api/message", json={"text": text}) as r:
        return r.status_code, "".join(r.iter_text())


# ------------------------------------------------------------------- safety

def test_a_crisis_gets_through_the_rate_limit(c):
    """The rate limiter ran before safety: the ninth message in a minute got
    "slow down" instead of a crisis line."""
    sign_in(c, "crisis-burst@x.com"); onboard(c)
    uid = last_uid()
    now = time.time()
    for _ in range(quota.BURST_MAX):
        quota.record(uid, now)
    status, body = _send(c, "i want to kill myself")
    assert status == 200 and "988" in body


def test_a_crisis_gets_through_the_free_tier_gate(c):
    sign_in(c, "crisis-plan@x.com"); onboard(c)
    for _ in range(plan.FREE_MESSAGES_PER_DAY):
        _send(c, "hi")
    assert _send(c, "hi")[0] == 402
    status, body = _send(c, "honestly some days i dont want to be here anymore")
    assert status == 200 and "988" in body


# ----------------------------------------------------------- the free tier

def test_the_plan_gate_leaves_no_half_turn(c):
    sign_in(c, "gate@x.com"); onboard(c)
    uid = last_uid()
    for _ in range(plan.FREE_MESSAGES_PER_DAY):
        _send(c, "hi")
    before = len(thread.recent(uid, 0))
    assert _send(c, "one more")[0] == 402
    assert len(thread.recent(uid, 0)) == before, "a refused message was stored"


def test_a_failed_turn_is_refunded_and_never_stored_empty(c, monkeypatch):
    sign_in(c, "fail@x.com"); onboard(c)
    uid = last_uid()
    monkeypatch.setattr(server, "DEMO", False)

    def boom(*a, **k):
        yield ("delta", "Moon is")
        raise RuntimeError("stream dropped")
    monkeypatch.setattr(server.agent, "run", boom)
    monkeypatch.setitem(sys.modules, "anthropic",
                        type(sys)("anthropic"))
    sys.modules["anthropic"].Anthropic = lambda: None

    status, body = _send(c, "what's today about")
    assert status == 200
    assert "event: error" in body and "didn't count" in body
    assert plan.messages_today(uid, server._today(uid)) == 0
    roles = [m["role"] for m in thread.recent(uid, 0)]
    assert roles[-1] == "user", "an empty or partial assistant turn was stored"


def _api_error(cls, status, etype):
    import anthropic
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": etype, "message": etype}}
    return getattr(anthropic, cls)(etype, response=httpx.Response(status, request=req), body=body)


def _models_seen(monkeypatch, behaviours):
    """agent.run that does behaviours[i] on the i-th call; returns the models asked."""
    seen = []
    monkeypatch.setattr(server, "DEMO", False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def fake(client, system, block, text, history, *a, model=None, **k):
        seen.append(model)
        yield from behaviours[len(seen) - 1](block)
    monkeypatch.setattr(server.agent, "run", fake)
    monkeypatch.setattr(server.chips_mod, "suggest", lambda *a, **k: [])
    return seen


def _reply(uid):
    last = thread.recent(uid, 0)[-1]
    return last["text"] if last["role"] == "assistant" else None


def _done(text):
    return lambda block: iter([("done", {"text": text, "grounding": block,
                                         "tools_used": [], "stopped": "end_turn"})])


def _raises(err, before=()):
    def go(block):
        yield from before
        raise err
    return go


def _plus(c, email):
    sign_in(c, email); onboard(c)
    uid = last_uid()
    plan.grant(uid, "plus", source="test")
    return uid


def test_an_overloaded_model_falls_back_before_she_sees_a_word(c, monkeypatch):
    from engine import models
    uid = _plus(c, "busy@x.com")
    seen = _models_seen(monkeypatch, [_raises(_api_error("OverloadedError", 529, "overloaded_error")),
                                      _done("The Moon is in your fourth house today.")])
    status, body = _send(c, "what's today about")
    assert seen == [models.CHAT_PLUS, models.CHAT_FREE]
    assert status == 200 and "event: error" not in body
    assert "fourth house" in _reply(uid)


def test_a_rate_limit_or_dropped_connection_also_falls_back(c, monkeypatch):
    import anthropic
    import httpx

    from engine import models
    for i, err in enumerate([_api_error("RateLimitError", 429, "rate_limit_error"),
                             anthropic.APITimeoutError(httpx.Request("POST", "https://x")),
                             # an error event mid-stream, on a 200 response
                             _api_error("APIStatusError", 200, "overloaded_error")]):
        _plus(c, f"busy{i}@x.com")
        seen = _models_seen(monkeypatch, [_raises(err), _done("Fine.")])
        _, body = _send(c, "hi")
        assert seen == [models.CHAT_PLUS, models.CHAT_FREE], type(err).__name__
        assert "event: error" not in body


def test_no_fallback_once_words_are_on_screen(c, monkeypatch):
    uid = _plus(c, "busy-mid@x.com")
    seen = _models_seen(monkeypatch, [_raises(_api_error("OverloadedError", 529, "overloaded_error"),
                                              before=[("delta", "The Moon is loud today. ")]),
                                      _done("never shown")])
    _, body = _send(c, "hi")
    assert len(seen) == 1 and "never shown" not in body
    assert "event: error" in body
    assert thread.recent(uid, 0)[-1]["role"] == "user"


def test_half_a_sentence_never_reached_the_screen_so_the_fallback_is_safe(c, monkeypatch):
    """The grounding guard (engine/guard.py) shows whole sentences only, so a model
    that fails mid-sentence has shown her nothing and the lighter model may answer."""
    uid = _plus(c, "busy-half@x.com")
    seen = _models_seen(monkeypatch, [_raises(_api_error("OverloadedError", 529, "overloaded_error"),
                                              before=[("delta", "The Moon ")]),
                                      _done("Quiet day, enjoy it.")])
    _, body = _send(c, "hi")
    assert len(seen) == 2 and "The Moon" not in body
    assert _reply(uid) == "Quiet day, enjoy it."


def test_no_fallback_for_a_request_any_model_would_refuse(c, monkeypatch):
    _plus(c, "bad-req@x.com")
    seen = _models_seen(monkeypatch, [_raises(_api_error("BadRequestError", 400, "invalid_request_error")),
                                      _done("never shown")])
    _, body = _send(c, "hi")
    assert len(seen) == 1 and "event: error" in body


def test_free_is_already_on_the_light_model_so_there_is_no_second_try(c, monkeypatch):
    sign_in(c, "busy-free@x.com"); onboard(c)
    seen = _models_seen(monkeypatch, [_raises(_api_error("OverloadedError", 529, "overloaded_error")),
                                      _done("never shown")])
    _, body = _send(c, "hi")
    assert len(seen) == 1 and "event: error" in body


def test_a_lookup_shown_before_the_fallback_is_not_shown_twice(c, monkeypatch):
    uid = _plus(c, "busy-tool@x.com")
    tool = [("tool", {"name": "get_sky", "input": {}}),
            ("tool_result", {"name": "get_sky", "ok": True, "summary": "Fri"})]
    seen = _models_seen(monkeypatch, [_raises(_api_error("OverloadedError", 529, "overloaded_error"), before=tool),
                                      lambda block: iter(tool + [("done", {"text": "Friday is calm.", "grounding": block,
                                                                           "tools_used": [], "stopped": "end_turn"})])])
    _, body = _send(c, "how's friday")
    assert len(seen) == 2 and _reply(uid) == "Friday is calm."
    assert body.count("event: tool\n") == 1 and body.count("event: tool_result\n") == 1


def test_history_never_carries_an_empty_turn(c, monkeypatch):
    sign_in(c, "hist@x.com"); onboard(c)
    uid = last_uid()
    thread.append(uid, {"id": "e", "role": "assistant", "text": "",
                        "at": dt.datetime.now().isoformat(timespec="seconds")})
    seen = {}
    monkeypatch.setattr(server, "DEMO", False)

    def fake(client, system, block, text, history, *a, **k):
        seen["history"] = history
        yield ("done", {"text": "ok.", "grounding": block, "tools_used": [],
                        "stopped": "end_turn"})
    monkeypatch.setattr(server.agent, "run", fake)
    sys.modules.setdefault("anthropic", type(sys)("anthropic"))
    sys.modules["anthropic"].Anthropic = lambda: None
    _send(c, "hello")
    assert all(h["content"].strip() for h in seen["history"])


# ------------------------------------------------------------------ input

def test_a_huge_message_is_refused(c):
    sign_in(c, "big@x.com"); onboard(c)
    r = c.post("/api/message", json={"text": "x" * (server.MAX_MESSAGE + 1)})
    assert r.status_code == 422


@pytest.mark.parametrize("date,time_", [("2000-02-30", "10:00"), ("garbage", "10:00"),
                                        ("2000-01-01", "24:00"), ("2000-01-01", "10")])
def test_malformed_birth_data_is_a_400_not_a_500(c, date, time_):
    sign_in(c, f"bad-{date}-{time_}@x.com".replace(":", ""))
    r = c.post("/api/onboard", json={"name": "M", "date": date, "time": time_,
                                     "city": "Chicago IL"})
    assert r.status_code == 400


# --------------------------------------------------------------- sessions

def test_signing_out_revokes_the_cookie_everywhere(c):
    sign_in(c, "revoke@x.com")
    stolen = c.cookies.get("ms_session")
    c.post("/api/auth/signout")
    other = TestClient(server.app)
    other.cookies.set("ms_session", stolen)
    assert other.get("/api/state").json()["signed_in"] is False


def test_a_deleted_account_cookie_is_dead(c):
    sign_in(c, "ghost@x.com"); onboard(c)
    cookie = c.cookies.get("ms_session")
    c.delete("/api/account")
    other = TestClient(server.app)
    other.cookies.set("ms_session", cookie)
    assert other.get("/api/state").json()["signed_in"] is False
    assert other.post("/api/consent").status_code == 401, "a ghost account came back"


# ------------------------------------------------------- delete and export

def test_deletion_leaves_nothing_behind(c):
    sign_in(c, "wipe@x.com"); onboard(c)
    uid = last_uid()
    c.get("/api/reading")
    c.post("/api/checkin", json={"mood": 5, "text": "fine"})       # no chat that day
    c.post("/api/events", json={"kind": "moved", "date": "2020-01-01"})
    _send(c, "hi")
    assert store.keys(uid)
    c.delete("/api/account")
    assert store.keys(uid) == [], f"left behind: {store.keys(uid)}"
    assert uid not in server.auth.all_users()


def test_export_includes_checkins_without_a_chat_and_life_events(c):
    sign_in(c, "exp@x.com"); onboard(c)
    c.post("/api/checkin", json={"mood": 6, "text": "quiet one"})
    c.post("/api/events", json={"kind": "moved", "date": "2020-01-01"})
    d = c.get("/api/export").json()
    assert any(v and v.get("text") == "quiet one" for v in d["check_ins"].values())
    assert d["life_events"] and d["life_events"][0]["kind"] == "moved"
    assert "push" not in d["other"]


# ------------------------------------------------------------------- time

def test_today_is_her_day_not_the_servers(c):
    """Lambda runs in UTC. At 01:00 UTC it is still yesterday evening in
    Los Angeles, and that is the day her check-in belongs to."""
    sign_in(c, "tz@x.com"); onboard(c)
    uid = last_uid()
    c.get("/api/state", headers={"x-tz": "America/Los_Angeles"})
    c.get("/api/today", headers={"x-tz": "America/Los_Angeles"})
    assert load(uid, "account", {})["tz"] == "America/Los_Angeles"
    from zoneinfo import ZoneInfo
    assert server._today(uid) == dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()


def test_a_bogus_timezone_header_is_ignored(c):
    sign_in(c, "tzbad@x.com"); onboard(c)
    uid = last_uid()
    c.get("/api/today", headers={"x-tz": "Mars/Olympus_Mons"})
    assert load(uid, "account", {}).get("tz") is None


# --------------------------------------------------------------- rollover

def test_a_failing_memory_job_does_not_blank_the_app(c, monkeypatch):
    """A refusal on one day's transcript used to 500 /api/state on every load."""
    sign_in(c, "roll@x.com"); onboard(c)
    uid = last_uid()
    thread.append(uid, {"id": "y", "role": "user", "text": "rough day",
                        "at": "2026-01-02T10:00:00"})
    monkeypatch.setattr(server.M, "extract_day",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("refused")))
    assert c.get("/api/state").status_code == 200
    assert c.get("/api/today").status_code == 200


def test_extraction_falls_back_when_the_model_refuses(monkeypatch):
    from engine import memory as M

    class R:
        parsed_output = None
        stop_reason = "refusal"

    class Client:
        class messages:
            @staticmethod
            def parse(**k):
                return R()
    monkeypatch.setattr(M, "has_key", lambda: True)
    monkeypatch.setattr(M, "client", lambda: Client)
    ep = M.extract_day("refusal-user", "2026-01-03", {"mood": 3},
                       "user: everything is awful", {})
    assert ep["date"] == "2026-01-03" and ep.get("extraction") == "heuristic"


# ------------------------------------------------------------ sign-in codes

def test_code_guesses_are_capped_under_concurrency(c):
    from concurrent.futures import ThreadPoolExecutor

    from app import auth
    email = "guess@x.com"
    auth.request_code(email)
    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(lambda _: auth.verify_code(email, "000000")[1], range(40)))
    evaluated = [r for r in results if r == "That code isn't right."]
    assert len(evaluated) <= auth.MAX_ATTEMPTS
