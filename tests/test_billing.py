"""
Stripe billing (app/billing.py): off until switched on, a signed webhook is the
only thing that changes a plan, and every way out of a subscription — cancel,
lapse, deleting the account — lands where it should. Stripe itself is faked:
_call is replaced by a recorder that answers like the API.
"""
import hashlib
import hmac
import json
import os
import pathlib
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-billing-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import billing, server
from engine import plan
from engine.store import load
from tests.test_app import last_uid, sign_in

WHSEC = "whsec_test"
PERIOD_END = 1893456000          # 2030-01-01 UTC


class FakeStripe:
    def __init__(self):
        self.calls, self.subs = [], {}

    def __call__(self, method, path, data=None):
        self.calls.append((method, path, data))
        if path == "/prices":
            return {"data": [{"id": f"price_{data['lookup_keys'][0]}"}]}
        if path == "/customers":
            return {"id": f"cus_{len(self.calls)}"}
        if path == "/checkout/sessions":
            return {"url": "https://checkout.stripe.test/s"}
        if path == "/billing_portal/sessions":
            return {"url": "https://billing.stripe.test/p"}
        if path.startswith("/subscriptions/"):
            sid = path.rsplit("/", 1)[1]
            if method == "DELETE":
                self.subs[sid]["status"] = "canceled"
            return self.subs[sid]
        raise AssertionError(path)

    def checkout_data(self):
        return [d for m, p, d in self.calls if p == "/checkout/sessions"][-1]


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("MYSIGN_BILLING", "stripe")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    monkeypatch.setitem(plan.PRICES, "available", True)
    fake = FakeStripe()
    monkeypatch.setattr(billing, "_call", fake)
    return fake


@pytest.fixture
def c():
    return TestClient(server.app)


def _user(c, email):
    sign_in(c, email)
    return last_uid()


def _sub(uid, sid="sub_1", status="active", customer="cus_9", **kw):
    return {"id": sid, "status": status, "customer": customer, "metadata": {"uid": uid},
            "current_period_end": PERIOD_END, **kw}


def _hook(c, fake, event_type, sub, eid=None, secret=WHSEC, t=None):
    fake.subs[sub["id"]] = sub
    body = json.dumps({"id": eid or f"evt_{time.time_ns()}", "type": event_type,
                       "data": {"object": sub}}).encode()
    t = t or int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return c.post("/api/billing/webhook", content=body, headers={"stripe-signature": f"t={t},v1={sig}"})


# ------------------------------------------------------------------ the switch

def test_nothing_is_for_sale_until_billing_is_switched_on(c, monkeypatch):
    """Keys alone don't open the shop: `billing` must say "stripe" on purpose,
    because the free limits start to bite the moment Plus can be bought."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    monkeypatch.delenv("MYSIGN_BILLING", raising=False)
    assert billing.enabled() is False
    assert plan.PRICES["available"] is False
    _user(c, "off@b.com")
    assert c.post("/api/billing/checkout", json={"plan": "annual"}).status_code == 409
    assert c.post("/api/billing/webhook", content=b"{}").status_code == 404


# ------------------------------------------------------------------ checkout

def test_checkout_offers_the_trial_once(c, on):
    uid = _user(c, "trial@b.com")
    r = c.post("/api/billing/checkout", json={"plan": "monthly", "surface": "chat_cap"})
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.test")
    d = on.checkout_data()
    assert d["mode"] == "subscription" and d["client_reference_id"] == uid
    assert d["line_items"][0]["price"] == "price_mysign_plus_monthly"
    assert d["subscription_data"]["trial_period_days"] == plan.PRICES["trial_days"]
    assert d["subscription_data"]["metadata"]["uid"] == uid
    customer = d["customer"]

    # She tried it, cancelled; a second checkout bills from day one, same customer.
    assert _hook(c, on, "customer.subscription.created", _sub(uid, status="trialing", customer=customer, trial_start=1)).status_code == 200
    assert _hook(c, on, "customer.subscription.deleted",
                 _sub(uid, status="canceled", customer=customer, trial_start=1)).status_code == 200
    c.post("/api/billing/checkout", json={"plan": "annual"})
    d = on.checkout_data()
    assert "trial_period_days" not in d["subscription_data"]
    assert d["customer"] == customer
    assert d["line_items"][0]["price"] == "price_mysign_plus_annual"


def test_checkout_refuses_an_unknown_plan_and_a_second_subscription(c, on):
    uid = _user(c, "twice@b.com")
    assert c.post("/api/billing/checkout", json={"plan": "lifetime"}).status_code == 400
    plan.grant(uid, "plus", source="stripe")
    assert c.post("/api/billing/checkout", json={"plan": "monthly"}).status_code == 409


# ------------------------------------------------------------------ webhooks

def test_an_active_subscription_grants_plus_through_the_period_and_a_grace(c, on):
    uid = _user(c, "active@b.com")
    assert _hook(c, on, "customer.subscription.updated", _sub(uid)).status_code == 200
    p = plan.get(uid)
    assert p["tier"] == "plus" and p["source"] == "stripe"
    assert p["until"] == "2030-01-04"                     # period end + GRACE_DAYS
    assert "paid" in (load(uid, "funnel_seen", []) or [])


def test_the_period_end_is_found_on_the_item_in_newer_api_versions(c, on):
    uid = _user(c, "item@b.com")
    sub = _sub(uid)
    del sub["current_period_end"]
    sub["items"] = {"data": [{"current_period_end": PERIOD_END}]}
    _hook(c, on, "customer.subscription.updated", sub)
    assert plan.get(uid)["until"] == "2030-01-04"


def test_cancelling_returns_her_to_free(c, on):
    uid = _user(c, "cancel@b.com")
    _hook(c, on, "customer.subscription.created", _sub(uid))
    _hook(c, on, "customer.subscription.deleted", _sub(uid, status="canceled"))
    assert plan.get(uid)["tier"] == "free"


def test_a_stripe_cancel_never_takes_away_plus_someone_else_gave(c, on):
    """A pilot participant granted Plus by hand who once tried checkout keeps it."""
    uid = _user(c, "pilot@b.com")
    plan.grant(uid, "plus", source="operator", granted_by="ops")
    _hook(c, on, "customer.subscription.deleted", _sub(uid, status="canceled"))
    assert plan.get(uid)["tier"] == "plus"


def test_the_subscription_is_fetched_fresh_so_stale_events_cant_win(c, on):
    """Events about one subscription arrive out of order. The event carries a
    stale copy; what's applied is the subscription as Stripe has it now."""
    uid = _user(c, "order@b.com")
    on.subs["sub_1"] = _sub(uid, status="canceled")
    body = json.dumps({"id": "evt_stale", "type": "customer.subscription.updated",
                       "data": {"object": _sub(uid, status="active")}}).encode()
    t = int(time.time())
    sig = hmac.new(WHSEC.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    c.post("/api/billing/webhook", content=body, headers={"stripe-signature": f"t={t},v1={sig}"})
    assert plan.get(uid)["tier"] == "free"


def test_a_checkout_completion_links_the_customer_and_applies_the_subscription(c, on):
    uid = _user(c, "done@b.com")
    on.subs["sub_7"] = {"id": "sub_7", "status": "trialing", "customer": "cus_77",
                        "metadata": {}, "current_period_end": PERIOD_END, "trial_start": 1}
    session = {"id": "cs_1", "mode": "subscription", "subscription": "sub_7",
               "customer": "cus_77", "client_reference_id": uid}
    body = json.dumps({"id": "evt_cs", "type": "checkout.session.completed",
                       "data": {"object": session}}).encode()
    t = int(time.time())
    sig = hmac.new(WHSEC.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert c.post("/api/billing/webhook", content=body,
                  headers={"stripe-signature": f"t={t},v1={sig}"}).status_code == 200
    assert plan.get(uid)["tier"] == "plus"
    assert load(uid, "billing", {})["trial_used"] is True


def test_the_same_event_is_applied_once(c, on):
    uid = _user(c, "dup@b.com")
    _hook(c, on, "customer.subscription.updated", _sub(uid), eid="evt_same")
    n = len(on.calls)
    _hook(c, on, "customer.subscription.updated", _sub(uid), eid="evt_same")
    assert len(on.calls) == n                             # not fetched again


@pytest.mark.parametrize("secret,age", [("whsec_wrong", 0), (WHSEC, 3600)])
def test_a_bad_or_replayed_signature_changes_nothing(c, on, secret, age):
    uid = _user(c, f"sig{age}@b.com")
    r = _hook(c, on, "customer.subscription.updated", _sub(uid), secret=secret,
              t=int(time.time()) - age)
    assert r.status_code == 400
    assert plan.get(uid)["tier"] == "free"


def test_a_stripe_outage_answers_500_so_stripe_retries(c, on, monkeypatch):
    uid = _user(c, "down@b.com")

    def down(*a, **k):
        raise billing.BillingError("stripe unreachable")
    monkeypatch.setattr(billing, "_call", down)
    body = json.dumps({"id": "evt_down", "type": "customer.subscription.updated",
                       "data": {"object": _sub(uid)}}).encode()
    t = int(time.time())
    sig = hmac.new(WHSEC.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    r = c.post("/api/billing/webhook", content=body, headers={"stripe-signature": f"t={t},v1={sig}"})
    assert r.status_code == 500
    assert load("_billing", "evt-evt_down", None) is None   # not marked seen: the retry applies


# ------------------------------------------------------------------ leaving

def test_deleting_the_account_cancels_the_subscription(c, on):
    uid = _user(c, "gone@b.com")
    _hook(c, on, "customer.subscription.created", _sub(uid, sid="sub_gone"))
    assert c.delete("/api/account").json()["deleted"] is True
    assert ("DELETE", "/subscriptions/sub_gone", None) in on.calls
    # Stripe's own cancellation event for it then finds no account and does nothing.
    assert _hook(c, on, "customer.subscription.deleted", on.subs["sub_gone"]).status_code == 200


def test_settings_offers_manage_only_to_someone_with_a_billing_account(c, on):
    _user(c, "manage@b.com")
    assert c.get("/api/state").json()["plan"]["manage"] is False
    c.post("/api/billing/checkout", json={"plan": "monthly"})
    assert c.get("/api/state").json()["plan"]["manage"] is True
    assert c.post("/api/billing/portal").json()["url"].startswith("https://billing.stripe.test")


# ------------------------------------------------------------------ reminder

def test_the_trial_reminder_the_paywall_promises_is_sent(c, on, monkeypatch):
    sent = []

    class SES:
        def send_email(self, **kw):
            sent.append(kw)
    import boto3
    monkeypatch.setattr(boto3, "client", lambda name, **kw: SES())
    uid = _user(c, "remind@b.com")
    monkeypatch.setenv("MYSIGN_FROM_EMAIL", "hello@mysignapp.com")   # after sign-in, which would mail too
    sub = _sub(uid, status="trialing", trial_start=1, trial_end=PERIOD_END,
               items={"data": [{"price": {"unit_amount": 999, "recurring": {"interval": "month"}}}]})
    _hook(c, on, "customer.subscription.trial_will_end", sub)
    assert len(sent) == 1
    body = sent[0]["Message"]["Body"]["Text"]["Data"]
    assert sent[0]["Destination"]["ToAddresses"] == ["remind@b.com"]
    assert "$9.99 a month" in body and "January 1" in body and "Cancel before" in body

    # Already cancelled: no reminder.
    sent.clear()
    _hook(c, on, "customer.subscription.trial_will_end", {**sub, "cancel_at_period_end": True})
    assert sent == []


def test_signature_check_matches_stripes_scheme():
    body, t = b'{"a":1}', 1_700_000_000
    sig = hmac.new(b"whsec_k", f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    assert billing.verify(body, f"t={t},v1=bad,v1={sig}", "whsec_k", now=t + 10)
    assert not billing.verify(body, f"t={t},v1={sig}", "whsec_k", now=t + 301)
    assert not billing.verify(body + b" ", f"t={t},v1={sig}", "whsec_k", now=t)
    assert not billing.verify(body, "garbage", "whsec_k", now=t)


def test_form_encoding_is_stripes_bracket_style():
    assert billing._flatten({"line_items": [{"price": "p", "quantity": 1}],
                             "subscription_data": {"metadata": {"uid": "u1"}},
                             "allow_promotion_codes": True, "skip": None}) == [
        ("line_items[0][price]", "p"), ("line_items[0][quantity]", "1"),
        ("subscription_data[metadata][uid]", "u1"), ("allow_promotion_codes", "true")]
