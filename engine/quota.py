"""
Ceilings on the endpoint that spends money.

Sign-in is rate limited; the model endpoint was not. A signed-in session — hers,
or a stolen cookie — could call an agent loop with a six-iteration tool budget as
fast as the network allowed, and the first anyone would know is the bill.

Two limits, because they fail differently:

  burst   too many messages too fast. Almost always a bug, a double-tap, or a
          script. Refused with a retry-after; nothing is lost.
  daily   a real person having a hard day, or an account being farmed. Refused
          softly, because telling someone in distress to go away at message
          sixty is a product decision, not a technical one — see below.

Windows are stored per user, so a limit survives a Lambda cold start rather than
resetting whenever the container recycles. They are atomic counters in fixed
windows (this minute, this UTC day) rather than a list of timestamps rewritten on
every message: the list was read-modify-write, so N parallel requests all read
the same count and all got through.
"""
import time

from engine.store import counter, incr

BURST_MAX = 8            # messages
BURST_WINDOW = 60        # seconds
DAILY_MAX = 120          # messages per UTC day
DAY = 86400


def _keys(now):
    return f"quota-m-{int(now // BURST_WINDOW)}", f"quota-d-{int(now // DAY)}"


def _burst_refusal(now):
    return {"reason": "burst",
            "retry_after": int(BURST_WINDOW - now % BURST_WINDOW) + 1,
            "message": "Slow down a second — I'm still catching up."}


def _daily_refusal(now):
    return {"reason": "daily",
            "retry_after": int(DAY - now % DAY) + 1,
            # Not "you have hit your limit". Someone who sends a hundred and
            # twenty messages in a day is usually not abusing anything.
            "message": "That's a lot for one day, and I don't think "
                       "more of me is what helps right now. I'll be "
                       "here tomorrow."}


def check(uid, now=None):
    """Returns (allowed, info) without recording anything."""
    now = now or time.time()
    m, d = _keys(now)
    burst, day = counter(uid, m, "n"), counter(uid, d, "n")
    if burst >= BURST_MAX:
        return False, _burst_refusal(now)
    if day >= DAILY_MAX:
        return False, _daily_refusal(now)
    return True, {"burst": burst, "today": day}


def claim(uid, now=None, burst=True, daily_max=DAILY_MAX):
    """
    Check and record in one atomic step per window. Returns (allowed, info);
    a refusal records nothing in the window that refused it.

    burst=False: count the message but never refuse it for speed — someone in
    an open crisis episode (engine/episode.py) types fast, and "slow down" is
    the wrong answer to her.

    daily_max: lower than DAILY_MAX only for an account past its month's cost
    ceiling (engine/costs.py).
    """
    now = now or time.time()
    m, d = _keys(now)
    if incr(uid, m, "n", limit=BURST_MAX if burst else None,
            ttl=int(now) + 2 * BURST_WINDOW) is None:
        return False, _burst_refusal(now)
    n = incr(uid, d, "n", limit=daily_max, ttl=int(now) + 2 * DAY)
    if n is None:
        return False, _daily_refusal(now)
    return True, {"today": n}


def record(uid, now=None):
    now = now or time.time()
    m, d = _keys(now)
    incr(uid, m, "n", ttl=int(now) + 2 * BURST_WINDOW)
    return incr(uid, d, "n", ttl=int(now) + 2 * DAY)


def usage(uid, now=None):
    now = now or time.time()
    m, d = _keys(now)
    return {"last_minute": counter(uid, m, "n"), "today": counter(uid, d, "n"),
            "burst_max": BURST_MAX, "daily_max": DAILY_MAX}
