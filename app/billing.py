"""
Stripe, for buying Plus on the web.

Everything a purchase changes goes through engine/plan.grant, so the gate, the
analytics and the operator console see a Stripe subscription the same way they
see a hand-granted one. This file only translates: Checkout and the customer
portal on the way out, signed webhooks on the way back.

Off unless switched on. Billing is live only when the secret holds the Stripe
key and the webhook signing secret AND `billing` is "stripe": deploying this
code changes nothing for anyone until that last value is set on purpose,
because the moment Plus is buyable the free limits start to bite (plan.full_view).

No SDK: three form-encoded calls and an HMAC are less to ship in the Lambda
than the stripe package, and nothing here needs more.
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from engine import plan
from engine.store import load, save

API = "https://api.stripe.com/v1"
# Prices are found by lookup key, so a price can be replaced in the dashboard
# (new amount, new currency) without a deploy.
# The keys are the product ids in plan.PRODUCTS, the same ones the App Store uses.
LOOKUP = {v["plan"]: k for k, v in plan.PRODUCTS.items()}
# Where customer→account links and processed event ids live. Not under any
# account: deleting an account must not orphan a paying subscription's link
# before cancel() has run, and an event id must stay "seen" across accounts.
INDEX = "_billing"
# Plus is granted to the end of the paid period plus this much: Stripe renews
# at the boundary and its webhook can land minutes or hours later, and she
# must never drop to free in that gap. A cancellation still ends on time —
# that comes as its own event and sets free outright.
GRACE_DAYS = 3
TOLERANCE = 300                   # seconds a webhook signature stays valid
LIVE = ("active", "trialing", "past_due")   # past_due: Stripe is still retrying the card


class BillingError(Exception):
    pass


def enabled():
    return (os.environ.get("MYSIGN_BILLING") == "stripe"
            and bool(os.environ.get("STRIPE_SECRET_KEY"))
            and bool(os.environ.get("STRIPE_WEBHOOK_SECRET")))


def _call(method, path, data=None):
    body = urllib.parse.urlencode(_flatten(data or {})).encode() if data is not None else None
    url = f"{API}{path}"
    if method == "GET" and data:
        url, body = f"{url}?{body.decode()}", None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {os.environ['STRIPE_SECRET_KEY']}",
        "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        raise BillingError(f"stripe {e.code}: {msg}") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise BillingError(f"stripe unreachable: {e}") from None


def _flatten(d, prefix=""):
    """Stripe's form encoding: {"a": {"b": 1}, "l": [x]} → a[b]=1, l[0]=x."""
    out = []
    items = d.items() if isinstance(d, dict) else enumerate(d)
    for k, v in items:
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, (dict, list)):
            out += _flatten(v, key)
        elif isinstance(v, bool):
            out.append((key, "true" if v else "false"))
        elif v is not None:
            out.append((key, str(v)))
    return out


# ------------------------------------------------------------- on the way out

def _price_id(which):
    r = _call("GET", "/prices", {"lookup_keys": [LOOKUP[which]], "active": True})
    if not r.get("data"):
        raise BillingError(f"no active price with lookup key {LOOKUP[which]}")
    return r["data"][0]["id"]


def _customer(uid, email):
    """Her Stripe customer, created once and remembered both ways."""
    b = load(uid, "billing", None) or {}
    if b.get("customer"):
        return b["customer"]
    c = _call("POST", "/customers", {"email": email or None, "metadata": {"uid": uid}})
    save(uid, "billing", {**b, "customer": c["id"]})
    save(INDEX, f"cust-{c['id']}", {"uid": uid})
    return c["id"]


def checkout(uid, which, email, base):
    """
    A Checkout Session URL for Plus. The trial is offered once per person:
    a second subscription after cancelling starts billing straight away.
    """
    if which not in LOOKUP:
        raise BillingError(f"unknown plan {which!r}")
    b = load(uid, "billing", None) or {}
    sub = {"metadata": {"uid": uid}}
    if not b.get("trial_used"):
        sub["trial_period_days"] = plan.PRICES["trial_days"]
        # A card is taken up front; if it's gone when the trial ends, the
        # subscription cancels rather than running unpaid.
        sub["trial_settings"] = {"end_behavior": {"missing_payment_method": "cancel"}}
    s = _call("POST", "/checkout/sessions", {
        "mode": "subscription",
        "customer": _customer(uid, email),
        "client_reference_id": uid,
        "line_items": [{"price": _price_id(which), "quantity": 1}],
        "subscription_data": sub,
        "allow_promotion_codes": True,
        "success_url": f"{base}/app?plus=welcome",
        "cancel_url": f"{base}/app?plus=later",
    })
    return s["url"]


def portal(uid, base):
    """The Stripe-hosted page where she changes card, plan, or cancels."""
    b = load(uid, "billing", None) or {}
    if not b.get("customer"):
        raise BillingError("no billing account yet")
    s = _call("POST", "/billing_portal/sessions",
              {"customer": b["customer"], "return_url": f"{base}/app?settings=1"})
    return s["url"]


def cancel(uid):
    """
    Deleting her account ends any subscription on the spot: no one is billed
    for an account that no longer exists. Never raises; deletion must finish.
    """
    b = load(uid, "billing", None) or {}
    if not enabled() or not b.get("subscription"):
        return False
    try:
        _call("DELETE", f"/subscriptions/{b['subscription']}")
        return True
    except BillingError as e:
        from app import log
        log.error("billing.cancel_failed", exc=e, uid=uid)
        return False


# ------------------------------------------------------------- on the way back

def verify(payload, header, secret=None, now=None):
    """Stripe's v1 signature: HMAC-SHA256 of "<t>.<body>", within TOLERANCE."""
    secret = secret or os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    try:
        pairs = [p.strip().split("=", 1) for p in header.split(",") if "=" in p]
        t = int(next(v for k, v in pairs if k == "t"))
        sigs = [v for k, v in pairs if k == "v1"]
    except Exception:
        return False
    if abs((now or time.time()) - t) > TOLERANCE:
        return False
    want = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(want, s) for s in sigs)


def _period_end(sub):
    # Newer API versions moved the period onto the subscription item.
    end = sub.get("current_period_end")
    if not end:
        items = (sub.get("items") or {}).get("data") or [{}]
        end = items[0].get("current_period_end")
    return end


def _uid_for(obj):
    uid = (obj.get("metadata") or {}).get("uid") or obj.get("client_reference_id")
    if not uid and obj.get("customer"):
        uid = (load(INDEX, f"cust-{obj['customer']}", None) or {}).get("uid")
    return uid


def _sync(sub):
    """Make her plan match one subscription object, whatever event carried it."""
    uid = _uid_for(sub)
    if not uid or not load(uid, "account", None):
        return None                               # deleted account, or not ours
    b = load(uid, "billing", None) or {}
    b.update(customer=sub.get("customer") or b.get("customer"),
             subscription=sub["id"], status=sub.get("status"),
             cancel_at_period_end=bool(sub.get("cancel_at_period_end")))
    if sub.get("trial_start") or sub.get("status") == "trialing":
        b["trial_used"] = True
    save(uid, "billing", b)
    if b.get("customer"):
        save(INDEX, f"cust-{b['customer']}", {"uid": uid})
    if sub.get("status") in LIVE:
        end = _period_end(sub)
        until = (dt.datetime.fromtimestamp(end, dt.timezone.utc).date()
                 + dt.timedelta(days=GRACE_DAYS)).isoformat() if end else None
        after = plan.provider_update(uid, "stripe", True, until=until)
        try:
            from engine import funnel
            funnel.hit(uid, "paid")              # once per person, like the console's grant
        except Exception:
            pass
        return after
    # canceled, unpaid, incomplete_expired: back to free, unless something
    # other than Stripe (the operator console, an Apple subscription) gives Plus.
    return plan.provider_update(uid, "stripe", False)


def handle(event):
    """
    One webhook event. Idempotent: Stripe retries, and the same event id is
    applied once. Events about one subscription can arrive out of order, so a
    subscription event is never trusted as is — the subscription is fetched
    fresh and that is what's applied.
    """
    eid = event.get("id", "")
    if eid and load(INDEX, f"evt-{eid}", None):
        return "duplicate"
    kind, obj = event.get("type", ""), (event.get("data") or {}).get("object") or {}
    sub_id = None
    if kind == "checkout.session.completed" and obj.get("mode") == "subscription":
        sub_id = obj.get("subscription")
        uid = _uid_for(obj)
        if uid and obj.get("customer"):
            save(INDEX, f"cust-{obj['customer']}", {"uid": uid})
    elif kind.startswith("customer.subscription."):
        sub_id = obj.get("id")
    elif kind in ("invoice.paid", "invoice.payment_failed"):
        sub_id = obj.get("subscription") or ((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription")
    result = "ignored"
    if sub_id:
        sub = _call("GET", f"/subscriptions/{sub_id}")
        _sync(sub)
        result = "applied"
        # Three days before the trial bills (Stripe's timing), the reminder the
        # paywall promises — unless she has already cancelled.
        if kind == "customer.subscription.trial_will_end":
            result = "reminded" if remind(sub) else result
    if eid:
        save(INDEX, f"evt-{eid}", 1, ttl=int(time.time()) + 30 * 86400)
    return result


def remind(sub):
    """The trial-ending email. True if one was sent."""
    uid = _uid_for(sub)
    acct = load(uid, "account", None) if uid else None
    if (not acct or not acct.get("email") or sub.get("status") != "trialing"
            or sub.get("cancel_at_period_end") or not os.environ.get("MYSIGN_FROM_EMAIL")):
        return False
    end = sub.get("trial_end") or _period_end(sub)
    day = dt.datetime.fromtimestamp(end, dt.timezone.utc).strftime("%B %-d") if end else "soon"
    price = ((((sub.get("items") or {}).get("data") or [{}])[0]).get("price") or {})
    amount = f"${price['unit_amount'] / 100:.2f}" if price.get("unit_amount") else "your plan's price"
    every = {"month": "month", "year": "year"}.get((price.get("recurring") or {}).get("interval"), "period")
    from app.edge import public_url
    text = (f"Your My Sign Plus trial ends on {day}. After that it's {amount} a {every}, "
            f"charged to the card you added.\n\nWant to keep it? Nothing to do.\n"
            f"Don't want it? Cancel before {day} and you won't be charged: "
            f"{public_url()}/app?settings=1 → Your plan → Manage.\n\n— My Sign")
    import boto3
    extra = ({"ConfigurationSetName": os.environ["MYSIGN_SES_CONFIG_SET"]}
             if os.environ.get("MYSIGN_SES_CONFIG_SET") else {})
    try:
        boto3.client("ses").send_email(
            **extra, Source=os.environ["MYSIGN_FROM_EMAIL"],
            Destination={"ToAddresses": [acct["email"]]},
            Message={"Subject": {"Data": f"Your Plus trial ends {day}"},
                     "Body": {"Text": {"Data": text}}})
        return True
    except Exception as e:
        from app import log
        log.error("billing.remind_failed", exc=e, uid=uid)
        return False
