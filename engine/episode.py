"""
A crisis episode: red is sticky.

Red used to be a property of one message. A free user said "i want to die",
got the canned reply, said "yeah i am" (classified green, billed), then
"tonight. i already decided" — and got the paywall, because that message on its
own reads as amber. Her next message, "please answer me", got the paywall too.

So a red message opens an episode, and for STICKY_HOURS after the last red one:

- the plan cap (402) and the burst limiter (429) do not apply to her;
- the classifier is told she was flagged, and sees the thread;
- the normal chat model is told too, so it stays plain.

Paging belongs to the episode, not the message. Forty red messages in a burst
used to send forty critical PagerDuty triggers. Now each severity pages at most
once per PAGE_WINDOW, and a page only goes again inside the window when the
episode gets worse — a plan, means, someone else in danger.
"""
import datetime as dt
import time

from engine.store import counter, incr, load, save

STICKY_HOURS = 24
PAGE_WINDOW = 30 * 60      # seconds

KEY = "crisis_episode"

# PagerDuty severity by kind. A minor's age is a notice for ops, not a page at
# 3am; an overdose happening now is the most urgent thing this product sees.
SEVERITY = {
    "self_harm_means": "critical", "other_emergency": "critical",
    "self_harm": "critical", "abuse": "error",
    "eating_disorder": "warning", "medical": "warning", "minor": "info",
}
# Rank decides whether a later message in the window is worth another page.
# Means and a third party in danger rank above plain self-harm on purpose:
# disclosing them is the escalation the audit asked to re-page on.
_RANK = {"self_harm_means": 4, "other_emergency": 4, "self_harm": 3, "abuse": 2,
         "eating_disorder": 1, "medical": 1, "minor": 0}


def severity(kind, figurative=False):
    """Hyperbole about herself is still red, but it is not a 3am page."""
    if figurative and kind == "self_harm":
        return "warning"
    return SEVERITY.get(kind, "critical")


def _rank(kind, figurative=False):
    return 1 if figurative and kind == "self_harm" else _RANK.get(kind, 3)


def _iso(now):
    return dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds")


def active(uid, now=None):
    """The open episode, or None. Open = a red message in the last STICKY_HOURS."""
    ep = load(uid, KEY, None)
    if not ep or not ep.get("last_red_ts"):
        return None
    now = now or time.time()
    return ep if now - ep["last_red_ts"] < STICKY_HOURS * 3600 else None


def for_classifier(ep):
    """The episode the classifier and the chat block are told about: none
    when every red so far was figurative. The gates stay lifted either way."""
    return ep if ep and not ep.get("figurative_only") else None


def record_red(uid, kind, figurative=False, now=None):
    """
    A red message. Opens or extends the episode, and says whether to page.
    Returns (episode, page_severity or None).

    The page decision is an atomic counter per rank per window, so forty
    parallel requests cannot all decide they are first.
    """
    now = now or time.time()
    ep = active(uid, now) or {"started": _iso(now), "kind": kind, "reds": 0}
    ep["reds"] = ep.get("reds", 0) + 1
    # Hyperbole about herself ("this exam is killing me lmao") is still red,
    # but an episode of nothing else must not make every later joke red too.
    ep["figurative_only"] = bool(ep.get("figurative_only", True) and figurative)
    ep["last_red"], ep["last_red_ts"] = _iso(now), now
    # The episode's kind is its worst so far: a later "medical" message does
    # not turn an overdose episode into a medication question.
    if _rank(kind, figurative) >= _RANK.get(ep.get("kind"), 0) or ep["reds"] == 1:
        ep["kind"] = kind
    save(uid, KEY, ep, ttl=int(now) + STICKY_HOURS * 3600 + 86400)
    return ep, _page_slot(uid, kind, figurative, now)


def _page_slot(uid, kind, figurative, now):
    window = int(now // PAGE_WINDOW)
    rank = _rank(kind, figurative)
    ttl = int(now) + 2 * PAGE_WINDOW
    # Something at least as bad already paged this window: that incident is open.
    if any(counter(uid, f"redpage-{r}-{window}", "n") for r in range(rank, 5)):
        return None
    if incr(uid, f"redpage-{rank}-{window}", "n", limit=1, ttl=ttl) is None:
        return None
    return severity(kind, figurative)
