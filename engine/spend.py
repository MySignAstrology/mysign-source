"""
A daily budget for every model call that isn't the chat.

engine/quota.py caps /api/message. Nothing capped the other routes that call
the model, and one of them — the written compatibility read, Sonnet with a
16k budget — could be asked for again and again: PATCH the person with an
empty body (which rebuilt the report and dropped the cached read), POST the
read, repeat; or add, read, delete, repeat. A free account could spend
hundreds of dollars an hour (audit H1).

So each such route claims from a per-user budget for the UTC day before it
calls the model, and when the budget is spent it answers without the model —
the authored fallback, the itemised report, the heuristic memory pass — and
says so in plain words. Nothing about the person is lost; only the prose waits
until tomorrow.

Buckets, because they fail differently and a busy day in one must not starve
another:

  compat     a written read for one person. The only one with a natural
             loop (a new person, or a changed birth time, is a new read).
  placement  a sun / moon / rising long read. Three points, rewritten as her
             data crosses a mark, so a handful a day is generous.
  natal      the reading itself. Cached for the life of the chart; this only
             bounds retries after a failure and tradition switches.
  memory     a finished day turned into an episode, plus the weekly pattern
             job. Bounded by days she chatted; the cap is a backstop.

Atomic counters in the store (one item per user per bucket per UTC day), the
same mechanism as quota.py, so parallel requests can't slip past and a cold
start doesn't reset anything. UTC rather than her local day on purpose: the
local day follows a client-sent zone (see quota.py and audit L3).
"""
import time

from engine.store import counter, incr

DAY = 86400

DAILY = {                 # Plus
    "compat": 15,
    "placement": 9,
    "natal": 6,
    "memory": 12,
}
# Free: enough to see what the written reads are, not a way to run up a bill.
FREE_DAILY = {
    "compat": 3,
    "placement": 3,
    "natal": 3,
    "memory": 6,
}

# What she reads when a bucket is spent. Not "limit reached": the reading she
# came for is already on the screen, itemised; only the prose is waiting.
MESSAGES = {
    "compat": "You've opened a lot of new readings today. The written part for this "
              "one comes tomorrow; every number and detail below is already here.",
    "placement": "That's a lot of long reads for one day. This one gets written "
                 "tomorrow; everything below is its substance.",
    "natal": "Your full written reading is taking a breather today. Everything "
             "below comes from your chart and is complete.",
    "memory": None,      # nothing to tell her: the heuristic pass still writes the day
}


def _key(bucket, now):
    return f"spend-{bucket}-{int(now // DAY)}"


def claim(uid, bucket, now=None):
    """True if this call may go to the model, recording it; False when today's
    budget for the bucket is spent, or her month's AI cost is past her plan's
    line (engine/costs.py) — and nothing is recorded."""
    from engine import costs, plan
    now = now or time.time()
    tier = plan.get(uid)["tier"]
    cap = (DAILY if tier == "plus" else FREE_DAILY)[bucket]
    if not costs.extras_allowed(uid, tier, now):
        from app import log
        log.emit("spend.capped", uid=uid, bucket=bucket, cap="month", tier=tier)
        return False
    n = incr(uid, _key(bucket, now), "n", limit=cap, ttl=int(now) + 2 * DAY)
    if n is None:
        from app import log
        # A metric, not an error: one person hitting a cap is fine; the alarm is
        # on the model spend itself (engine/meter.py -> ModelSpendUSD).
        log.emit("spend.capped", uid=uid, bucket=bucket, cap=cap, tier=tier)
        return False
    return True


def refund(uid, bucket, now=None):
    """Give the claim back when the model was never reached (no key, demo)."""
    now = now or time.time()
    incr(uid, _key(bucket, now), "n", by=-1, ttl=int(now) + 2 * DAY)


def used(uid, bucket, now=None):
    return counter(uid, _key(bucket, now or time.time()), "n")


def message(bucket):
    return MESSAGES.get(bucket)
