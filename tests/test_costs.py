"""
The plans and the money lines (engine/costs.py, engine/plan.py): free is three
AI questions a day; Plus is unlimited and never loses us money; one person's
cost lands on her own month; and the whole app has a daily ceiling.
"""
import os
import pathlib
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-costs-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import server
from engine import costs, meter, models, plan, quota, spend
from tests.test_app import last_uid, onboard, sign_in

NOW = 1_900_000_000.0


@pytest.fixture
def c():
    return TestClient(server.app)


def _uid(name):
    return f"cost-{name}-{time.time_ns()}"


# ------------------------------------------------------------------ the ledger

def test_a_response_is_charged_to_whoever_the_request_is_for():
    uid = _uid("ledger")
    tok = costs.current_uid.set(uid)
    try:
        usd = meter.record(type("R", (), {"model": models.SONNET,
                                          "usage": {"input_tokens": 1000, "output_tokens": 500}})())
    finally:
        costs.current_uid.reset(tok)
    assert usd == pytest.approx((1000 * 2 + 500 * 10) / 1e6)
    assert costs.month_usd(uid) == pytest.approx(usd, abs=1e-6)


def test_months_are_separate_and_the_app_day_adds_everyone():
    a, b = _uid("a"), _uid("b")
    before = costs.today_usd(NOW)
    costs.add(0.40, a, now=NOW)
    costs.add(0.10, b, now=NOW)
    costs.add(9.00, a, now=NOW + 40 * 86400)            # next month
    assert costs.month_usd(a, NOW) == pytest.approx(0.40)
    assert costs.month_usd(a, NOW + 40 * 86400) == pytest.approx(9.00)
    assert costs.today_usd(NOW) == pytest.approx(before + 0.50)


def test_the_ledger_never_breaks_a_reply(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store down")
    monkeypatch.setattr(costs, "incr", boom)
    costs.add(1.0, "anyone")                              # no exception


# ------------------------------------------------------------------ Plus lines

def test_plus_uses_the_best_model_until_the_premium_budget_then_carries_on():
    uid = _uid("plus")
    assert costs.premium_chat(uid, "plus", NOW)
    costs.add(costs.PLUS_PREMIUM_USD, uid, now=NOW)
    assert not costs.premium_chat(uid, "plus", NOW)
    # Still unlimited: the daily fair-use limit is untouched until the ceiling.
    assert costs.daily_message_cap(uid, "plus", quota.DAILY_MAX, NOW) == quota.DAILY_MAX
    assert not costs.premium_chat(_uid("free"), "free", NOW)        # free never gets premium


def test_past_the_ceiling_plus_slows_to_fair_use_and_extras_wait():
    uid = _uid("farm")
    costs.add(costs.PLUS_CEILING_USD, uid, now=NOW)
    assert costs.daily_message_cap(uid, "plus", quota.DAILY_MAX, NOW) == costs.PLUS_DAILY_AFTER_CEILING
    assert not costs.extras_allowed(uid, "plus", NOW)


def test_the_worst_plus_month_still_covers_its_ai_on_the_annual_plan():
    """The numbers behind the lines: premium, the ceiling's slack, then a full
    month at the reduced limit on the lighter model, against annual net."""
    haiku_per_msg = (3000 * 0.10 + 600 * 0.50) / 1e6 * 1.5        # generous: thinking + tools
    tail = costs.PLUS_DAILY_AFTER_CEILING * 31 * haiku_per_msg
    worst = costs.PLUS_CEILING_USD + tail
    annual_net_month = (59.99 * (1 - 0.029) - 0.30) / 12
    assert worst < annual_net_month


# ------------------------------------------------------------------ free lines

def test_free_extras_stop_at_the_free_ceiling():
    uid = _uid("freeextra")
    assert costs.extras_allowed(uid, "free", NOW)
    costs.add(costs.FREE_CEILING_USD, uid, now=NOW)
    assert not costs.extras_allowed(uid, "free", NOW)


def test_free_written_reads_get_the_smaller_daily_allowance():
    uid = _uid("freeread")
    got = sum(spend.claim(uid, "compat") for _ in range(spend.DAILY["compat"]))
    assert got == spend.FREE_DAILY["compat"] < spend.DAILY["compat"]
    plus = _uid("plusread")
    plan.grant(plus, "plus", source="operator")
    assert sum(spend.claim(plus, "compat") for _ in range(spend.DAILY["compat"] + 2)) == spend.DAILY["compat"]


def test_free_is_three_ai_questions_a_day(c):
    sign_in(c, f"three{time.time_ns()}@b.com")
    onboard(c)
    codes = []
    for i in range(4):
        with c.stream("POST", "/api/message", json={"text": f"what's my week like {i}"}) as r:
            codes.append(r.status_code)
            "".join(r.iter_text())
    assert codes == [200, 200, 200, 402]


# ------------------------------------------------------------------ the whole app

def test_the_app_wide_daily_budget_moves_everyone_to_the_lighter_model(monkeypatch):
    uid = _uid("global")
    monkeypatch.setenv("MYSIGN_DAILY_BUDGET_USD", "1")
    monkeypatch.setattr(costs, "today_usd", lambda now=None: 1.01)
    assert costs.global_over()
    assert not costs.premium_chat(uid, "plus")
    assert not costs.extras_allowed(uid, "free")
    assert costs.extras_allowed(uid, "plus")       # paid extras still within her own lines
    monkeypatch.delenv("MYSIGN_DAILY_BUDGET_USD")
    assert not costs.global_over()                  # unset: no app-wide line


def test_a_plus_member_past_premium_is_answered_by_the_lighter_model(c, monkeypatch):
    sign_in(c, f"lighter{time.time_ns()}@b.com")
    onboard(c)
    uid = last_uid()
    plan.grant(uid, "plus", source="operator")
    seen = []
    real = models.chat_model
    monkeypatch.setattr(models, "chat_model", lambda t: seen.append(t) or real(t))
    with c.stream("POST", "/api/message", json={"text": "how's today"}) as r:
        "".join(r.iter_text())
    costs.add(costs.PLUS_PREMIUM_USD, uid)
    with c.stream("POST", "/api/message", json={"text": "and tomorrow"}) as r:
        assert r.status_code == 200
        "".join(r.iter_text())
    assert seen == ["plus", "free"]


# ------------------------------------------------------------------ one definition

def test_the_plans_are_defined_once_and_match_what_is_enforced(c):
    t = plan.TIERS
    assert f"{plan.FREE_MESSAGES_PER_DAY} AI questions a day" in t["free"]
    assert f"Your first {plan.FREE_PEOPLE} people" in t["free"]
    assert str(quota.DAILY_MAX) in t["fair_use"]
    sign_in(c, f"tiers{time.time_ns()}@b.com")
    assert c.get("/api/state").json()["plan"]["tiers"]["plus"] == list(t["plus"])
    terms = (pathlib.Path(server.STATIC) / "terms.html").read_text()
    assert f"up to {quota.DAILY_MAX} messages a day" in terms
    assert f"{plan.FREE_MESSAGES_PER_DAY} AI questions a day" in terms
    assert plan.PRICES["monthly"]["price"] in terms and plan.PRICES["annual"]["price"] in terms
