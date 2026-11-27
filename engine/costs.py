"""
What each person costs us in AI, in dollars, and the lines that keep every
account profitable.

engine/meter.py prices every model response. Until now that only went to the
logs, so the alarm could see the total but nothing could see one person. Here
each response is also added to her own month and to everyone's day, and three
lines are drawn from those numbers:

  Plus premium   Plus talks to the stronger model (Sonnet) until her month's
                 AI cost reaches PLUS_PREMIUM_USD. After that the conversation
                 carries on, still unlimited, on the lighter model (Haiku, about
                 a twentieth of the price). She is never cut off for talking.
  Plus ceiling   Past PLUS_CEILING_USD in a month — an account being farmed,
                 not a person — the daily fair-use limit drops to
                 PLUS_DAILY_AFTER_CEILING and the long written extras wait for
                 next month (the authored versions still show).
  Free ceiling   Free is three AI questions a day (engine/plan.py). The extras
                 that call a model (the written reads) stop at FREE_CEILING_USD
                 a month and show their authored versions.

Why these numbers. After Stripe's fee (2.9% + 30c) monthly Plus nets about
$9.40 and annual about $4.83 a month. Worst case for one Plus account is the
premium budget, plus the ceiling's slack, plus a month at the reduced daily
limit on Haiku — about $4.30 — so even the heaviest annual subscriber covers
her AI. A typical one costs well under a dollar.

And one line for everyone at once: if the whole app's AI spend for the UTC day
passes MYSIGN_DAILY_BUDGET_USD, every conversation moves to the lighter model
and free extras pause, until midnight UTC. The CloudWatch alarm still fires;
this is what happens while someone looks.

Crisis turns are counted but never limited by any of this (engine/redreply.py
runs on its own model whatever it costs).

Counters are integer micro-dollars in the store, so parallel calls add up
exactly and a cold start loses nothing.
"""
import contextvars
import datetime as dt
import os
import time

from engine.store import counter, incr

PLUS_PREMIUM_USD = float(os.environ.get("MYSIGN_PLUS_PREMIUM_USD", "2.50"))
PLUS_CEILING_USD = float(os.environ.get("MYSIGN_PLUS_CEILING_USD", "3.50"))
PLUS_DAILY_AFTER_CEILING = 30          # messages a UTC day, past the ceiling
FREE_CEILING_USD = float(os.environ.get("MYSIGN_FREE_CEILING_USD", "0.50"))
GLOBAL = "_costs"                      # store id for the whole app's day

# Who the current request is for, so a model call deep in the engine is charged
# to her without every function passing a uid. Set per request by the server
# (from the session cookie) and per user by the cron.
current_uid = contextvars.ContextVar("current_uid", default=None)

_MICRO = 1_000_000


def _month(now=None):
    return dt.datetime.fromtimestamp(now or time.time(), dt.timezone.utc).strftime("%Y%m")


def _day(now=None):
    return int((now or time.time()) // 86400)


def add(usd, uid=None, now=None):
    """Charge one model response. Never raises: metering must not break a reply."""
    try:
        micro = max(0, round(usd * _MICRO))
        if not micro:
            return
        now = now or time.time()
        uid = uid or current_uid.get()
        if uid:
            incr(uid, f"cost-{_month(now)}", "u", by=micro, ttl=int(now) + 70 * 86400)
        incr(GLOBAL, f"cost-day-{_day(now)}", "u", by=micro, ttl=int(now) + 3 * 86400)
    except Exception:
        pass


def month_usd(uid, now=None):
    return counter(uid, f"cost-{_month(now)}", "u") / _MICRO


def today_usd(now=None):
    return counter(GLOBAL, f"cost-day-{_day(now)}", "u") / _MICRO


def global_over(now=None):
    cap = os.environ.get("MYSIGN_DAILY_BUDGET_USD")
    return bool(cap) and today_usd(now) >= float(cap)


def premium_chat(uid, tier, now=None):
    """Whether this turn may use the stronger model."""
    return tier == "plus" and not global_over(now) and month_usd(uid, now) < PLUS_PREMIUM_USD


def daily_message_cap(uid, tier, default, now=None):
    """The fair-use messages-per-day for her right now."""
    if tier == "plus" and month_usd(uid, now) >= PLUS_CEILING_USD:
        return min(default, PLUS_DAILY_AFTER_CEILING)
    return default


def extras_allowed(uid, tier, now=None):
    """Whether a long written extra (a model-written read) may call the model."""
    if tier == "plus":
        return month_usd(uid, now) < PLUS_CEILING_USD
    return not global_over(now) and month_usd(uid, now) < FREE_CEILING_USD


def summary(uid, tier, now=None):
    """For the operator console: where she stands this month."""
    spent = month_usd(uid, now)
    return {"month_usd": round(spent, 4), "tier": tier,
            "premium_left_usd": round(max(0.0, PLUS_PREMIUM_USD - spent), 4) if tier == "plus" else None,
            "over_ceiling": spent >= (PLUS_CEILING_USD if tier == "plus" else FREE_CEILING_USD)}
