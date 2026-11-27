"""
Signup → onboarded → came back → paid. Counts, never people.

Item 99. The same rule as the logs: shape, not content. Nothing here can be
joined back to a person — the per-user flag that stops double counting is a
private boolean on their own record, and the aggregate is a daily bucket of
integers. There is nothing to export and nothing to breach.

Both halves are atomic. The day's bucket is a counter item (store.incr), so two
signups in the same instant both count; it used to be read-modify-write, and one
of them was lost. Buckets written before that change are plain items, and the
report adds them in. The once-ness is a per-user counter with a limit of one,
so two requests racing on the same step can't both count it.

Each step is also an aggregate `funnel.<step>` server event in the analytics
lake — no uid, no device id, the same promise as the bucket.
"""
import datetime as dt

from engine import store
from engine.store import load, save

STEPS = ("signed_up", "consented", "onboarded", "returned_day2", "first_message",
         "week_one", "invite_created", "invite_accepted", "card_sent", "card_opened",
         "card_joined", "check_made", "check_shared", "check_opened", "check_joined",
         "paywall_intent", "paid")


ONCE = "funnel_once"


def _bucket(day=None):
    return f"funnel-{(day or dt.date.today()).isoformat()}"


def hit(uid, step, day=None):
    """
    Count `step` for `uid`, once. The once-ness lives on the user's record;
    the count lives in the day's bucket. Neither references the other.
    """
    if step not in STEPS:
        raise ValueError(step)
    seen = load(uid, "funnel_seen", []) or []
    if step in seen:
        return False
    if store.incr(uid, ONCE, step, limit=1) is None:
        return False                 # a concurrent request got there first
    # The list is what the dashboard's stage column and the export read. It is
    # still read-modify-write, so `seen()` also reads the once-counter.
    save(uid, "funnel_seen", seen + [step])
    store.incr("_study", _bucket(day), step)
    _emit(step)
    return True


def tally(step, day=None):
    """Count `step` with nobody attached: someone opening a link has no account
    yet, so there is no record to hold a once-flag. The caller keeps it once."""
    if step not in STEPS:
        raise ValueError(step)
    store.incr("_study", _bucket(day), step)
    _emit(step)


def seen(uid):
    """Every step counted for `uid`, from either record."""
    return set(load(uid, "funnel_seen", []) or []) | set(store.counters(uid, ONCE))


def _emit(step):
    try:
        from app import activity, events, forward
        if not forward.stream():
            return
        ev = activity.server_event(f"funnel.{step}", None, {})
        forward.send([events.envelope(ev, source="server")])
    except Exception:                # analytics never breaks a signup
        pass


def _counts(day):
    """A day's bucket: the atomic counter item plus any legacy plain item."""
    key = _bucket(day)
    out = dict(store.counters("_study", key))
    for k, v in (load("_study", key, {}) or {}).items():
        if isinstance(v, int):
            out[k] = out.get(k, 0) + v
    return out


def report(days=30, today=None):
    """Totals over the window plus the per-day rows, for the operator."""
    today = today or dt.date.today()
    rows, totals = [], dict.fromkeys(STEPS, 0)
    for i in range(days):
        d = today - dt.timedelta(days=i)
        b = _counts(d)
        if b:
            rows.append({"date": d.isoformat(), **{s: b.get(s, 0) for s in STEPS}})
            for s in STEPS:
                totals[s] += b.get(s, 0)
    conv = {}
    if totals["signed_up"]:
        for s in STEPS[1:]:
            conv[s] = round(totals[s] / totals["signed_up"], 3)
    return {"days": days, "totals": totals, "conversion_from_signup": conv,
            "by_day": list(reversed(rows))}
