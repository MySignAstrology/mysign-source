"""
What she is on, and what that lets her do.

Provider-agnostic on purpose. Whether billing is Stripe or Apple's IAP is item
79 on the road map and it is not decided; this is the state both would write
into. Deciding the provider later must not mean rewriting the gate.

The rule from the spec, unchanged: paywall the conversation, never the morning
push. The ritual builds the habit; the habit is what converts. Gating the push
would be the single most expensive mistake available.
"""
import datetime as dt
import time

from engine.quota import DAILY_MAX
from engine.store import counter, incr, load, save

FREE_MESSAGES_PER_DAY = 3
PLANS = ("free", "plus")

# People are what she comes back for, so the free plan keeps enough of them to
# make the daily ranking worth opening — five — and Plus keeps everyone. Today's
# score and headline for those five are never gated; Plus sells the why and the
# when, not the daily hit.
FREE_PEOPLE = 5

# One honest price list, shown the same everywhere it appears. Annual is the
# default because it is the better deal for her, not because it is the bigger
# number. The trial is real days, with a reminder before it bills.
PRICES = {
    "monthly": {"price": "$9.99", "period": "month"},
    "annual": {"price": "$59.99", "period": "year", "note": "$5/mo, save 49%"},
    "trial_days": 7,
    "available": False,          # flips when a billing provider is wired (item 79)
    # What the one price buys, beside the price rather than in it, so every
    # place that shows the price list shows the same honest list of what's in
    # it. Each line names its free allowance too: a feature list that only
    # says what Plus has reads as if free has none of it.
    "features": (
        "Unlimited AI conversation, on our stronger model (free: three questions a day)",
        "Everyone in your People (free: your first five)",
        "Circles: your first circle is free, and Plus lets you have as many as you like",
        "Before you date: the three strongest links between your charts, and the best day this week",
        "The why behind every best day, and the whole week ahead (free: the best day and its score)",
        "Every written deep read, as often as you like (free: a few a day)",
    ),
}

# The two plans, side by side, in one place. Settings, the paywall and the
# terms all read this, so what we say each plan includes can't drift from
# what the code enforces (the numbers come from the constants that enforce
# them). "Always free" is the spec's rule: the habit is never paywalled.
FAIR_USE = (f"Unlimited means for your own personal use: up to {DAILY_MAX} messages a day. "
            "After very heavy use in a month, replies may come from a lighter model "
            "until the month resets.")
TIERS = {
    "always_free": (
        "Your daily reading and morning notification",
        "Daily check-ins and your mood patterns",
        "Your birth chart and full natal reading",
        "Today's score and headline for everyone you've saved",
    ),
    "free": (
        f"{FREE_MESSAGES_PER_DAY} AI questions a day",
        f"Your first {FREE_PEOPLE} people",
        "1 circle",
        "Best-day planner: the best day and its score",
        "Before you date: the score and the strongest link",
        "A few written deep reads a day",
    ),
    "plus": (
        "Unlimited AI conversation, on our stronger model",
        "Unlimited people and circles",
        "Best-day planner: the why, and the whole week",
        "Before you date: the three strongest links and the best day",
        "Every written deep read, as often as you like",
    ),
    "fair_use": FAIR_USE,
}

# What can be bought, by the id every provider knows it by: the Stripe price
# lookup key and the App Store product id are the same string on purpose, so
# this is the one table that maps a purchase to a plan, whoever took the money.
# The amounts stay in PRICES above (the annual price changes there, and in the
# dashboards; native/ios/App/App/MySign.storekit is checked against it).
PRODUCTS = {
    "mysign_plus_monthly": {"tier": "plus", "plan": "monthly"},
    "mysign_plus_annual": {"tier": "plus", "plan": "annual"},
}
# Who can sell Plus. A plan change from one of these goes through
# provider_update, never straight to grant, so two subscriptions on one
# account are seen as two.
PROVIDERS = ("stripe", "apple")

PEOPLE_CAP_MESSAGE = ("Your first five people are free. With Plus you can add everyone, "
                      "and see who is easiest to be around three days ahead.")


def get(uid):
    p = load(uid, "plan", None) or {}
    tier = p.get("tier", "free")
    until = p.get("until")
    if tier == "plus" and until and until < dt.date.today().isoformat():
        tier = "free"                      # lapsed; the record stays for history
    return {"tier": tier, "source": p.get("source"), "until": until,
            "granted_by": p.get("granted_by")}


# Plus through the App Store (app/apple.py), set at startup beside
# PRICES["available"]. Kept apart from it on purpose: the web client reads
# PRICES["available"] as "Stripe's checkout is open" and draws its buy buttons
# from it, and the App Store being open must not draw those.
APP_STORE = {"available": False}


def for_sale():
    """
    Whether Plus can be bought anywhere: on the web (Stripe) or in the iPhone
    app (Apple). The free limits bite from the moment either shop opens, and
    not before — a gate with nothing to buy is just a wall.
    """
    return bool(PRICES["available"] or APP_STORE["available"])


def full_view(uid):
    """
    Whether she sees Plus depth. Plus sells the why; until there is a way to
    pay (for_sale) everyone sees everything — a gate with nothing
    to buy is just a wall. One definition, so no feature can drift into
    gating early on its own.
    """
    return not for_sale() or get(uid)["tier"] == "plus"


def grant(uid, tier, source, until=None, granted_by=None):
    """
    Set the plan. Called by a billing webhook once one exists, and by the
    operator console for pilot participants until then. `source` is
    "apple" | "google" | "stripe" | "web" from billing, "operator" by hand;
    it rides on the subscription_* analytics events as is.
    """
    if tier not in PLANS:
        raise ValueError(f"unknown tier {tier!r}")
    before = get(uid)
    rec = {"tier": tier, "source": source, "until": until,
           "granted_by": granted_by,
           "changed_at": dt.datetime.now().isoformat(timespec="seconds")}
    save(uid, "plan", rec)
    after = get(uid)
    # subscription_started / _renewed / _canceled, here so every way a plan
    # changes (a billing webhook, the operator console) is counted the same.
    try:
        from app import activity
        activity.track_plan(uid, before, after, source)
    except Exception:                # analytics never breaks a purchase
        pass
    return after


def _live(entry, today=None):
    until = (entry or {}).get("until")
    return bool(entry) and (not until or until >= (today or dt.date.today().isoformat()))


def providers(uid):
    """The billing providers with a subscription running on this account now."""
    held = load(uid, "entitlements", None) or {}
    return [p for p in PROVIDERS if _live(held.get(p))]


def provider_update(uid, provider, live, until=None, product=None):
    """
    What one billing provider says about her subscription, and the plan that
    follows from all of them together.

    Each provider's word is kept on its own line, so one ending never takes
    away what the other still pays for, and an account paying twice is known
    to be paying twice: the plan is one grant, the two subscriptions behind it
    are both on record, and `billing.double_subscription` is logged for a
    person to act on. Plus that neither provider gave (the operator console)
    is left alone when a subscription ends, as before.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    held = load(uid, "entitlements", None) or {}
    # A subscription granted before this ledger existed is only on the plan
    # record. It goes on its own line first, or the other provider ending
    # would look like the last one ending.
    before = load(uid, "plan", None) or {}
    if before.get("tier") == "plus" and before.get("source") in PROVIDERS:
        held.setdefault(before["source"], {"until": before.get("until"), "product": None})
    was = [p for p in PROVIDERS if _live(held.get(p))]
    if live:
        held[provider] = {"until": until, "product": product}
    else:
        held.pop(provider, None)
    save(uid, "entitlements", held)
    now = [p for p in PROVIDERS if _live(held.get(p))]
    if len(now) > 1 and len(was) < 2:
        try:
            from app import log
            log.emit("billing.double_subscription", level="warning", uid=uid, providers=",".join(now))
        except Exception:
            pass
    if now:
        # Whoever the plan already names, while they are still paying, so a
        # second subscription doesn't flip the record with every webhook.
        source = before.get("source") if before.get("source") in now else (
            provider if provider in now else now[0])
        untils = [held[p].get("until") for p in now]
        return grant(uid, "plus", source=source,
                     until=None if None in untils else max(untils))
    if get(uid)["source"] in (None, *PROVIDERS):
        return grant(uid, "free", source=provider)
    return get(uid)


def may_add_person(uid, count):
    """
    Whether one more saved person fits her plan. Only new adds are gated: anyone
    already over five from before the cap keeps every person they saved — a
    limit introduced later must never take something away.
    """
    # A cap with nothing to buy is just a wall. Until a billing provider is
    # wired (for_sale: Stripe or the App Store), the cap is not enforced; the
    # paywall copy and the intent logging still ship, so demand is measured
    # from day one.
    if not for_sale():
        return True
    return get(uid)["tier"] == "plus" or count < FREE_PEOPLE


def _cap_day(uid, today):
    """
    The day the free cap is counted against: her local date, but never one
    earlier than a date she has already been counted on (audit L3).

    Her local date follows the zone the client sends (X-Tz), which is right for
    "today" and the morning push, and which anyone can set. Hopping between
    UTC+14 and UTC-11 put three local dates in reach at once — three days'
    free messages in a burst. Counting a step back in time against the latest
    date already used closes that: going forward only borrows a day she then
    doesn't get later, so the total over time never grows. A traveller going
    west simply keeps the later date until her clock reaches it.
    """
    today = today or dt.date.today()
    latest = load(uid, "msgcount-latest", None) if uid else None
    if latest and today.isoformat() < latest:
        return dt.date.fromisoformat(latest)
    if uid and latest != today.isoformat():
        save(uid, "msgcount-latest", today.isoformat(), ttl=int(time.time()) + 3 * 86400)
    return today


def _count_key(today):
    return f"msgcount-{(today or dt.date.today()).isoformat()}"


def messages_today(uid, today=None):
    return counter(uid, _count_key(_cap_day(uid, today)), "n")


def count_message(uid, today=None):
    return incr(uid, _count_key(_cap_day(uid, today)), "n", ttl=int(time.time()) + 3 * 86400)


def refund_message(uid, today=None):
    """A turn that failed should not cost her one of her three."""
    return incr(uid, _count_key(_cap_day(uid, today)), "n", by=-1,
                ttl=int(time.time()) + 3 * 86400)


def claim_message(uid, today=None):
    """
    may_message and count_message as one atomic step. Separately they raced:
    three parallel requests on a free account all read "two used" and all got
    through. Returns (allowed, info) like may_message.
    """
    p = get(uid)
    ttl = int(time.time()) + 3 * 86400
    today = _cap_day(uid, today)
    if p["tier"] == "plus":
        n = incr(uid, _count_key(today), "n", ttl=ttl)
        return True, {"tier": "plus", "used": n, "limit": None}
    n = incr(uid, _count_key(today), "n", limit=FREE_MESSAGES_PER_DAY, ttl=ttl)
    if n is None:
        return may_message(uid, today)          # the refusal, with its copy
    return True, {"tier": "free", "used": n, "limit": FREE_MESSAGES_PER_DAY}


def _cap_message(uid):
    from engine import i18n
    return i18n.text("cap", i18n.lang_of(uid))


def may_message(uid, today=None):
    """
    Returns (allowed, info). Free accounts get FREE_MESSAGES_PER_DAY; Plus is
    bounded only by the abuse quota. Reading, check-in and the morning push are
    never gated and never pass through here.
    """
    p = get(uid)
    used = messages_today(uid, today)
    if p["tier"] == "plus":
        return True, {"tier": "plus", "used": used, "limit": None}
    if used < FREE_MESSAGES_PER_DAY:
        return True, {"tier": "free", "used": used, "limit": FREE_MESSAGES_PER_DAY}
    return False, {
        "tier": "free", "used": used, "limit": FREE_MESSAGES_PER_DAY,
        "paywall": True,
        # Not "upgrade to continue". The morning reading and the check-in are
        # hers either way, and the copy has to say so first.
        # No mention of a paid tier until one can actually be bought: App Review
        # rejects references to purchases that don't exist (2.1, 3.1.1).
        # In her language when her messages have shown it (engine/i18n).
        "message": _cap_message(uid),
    }
