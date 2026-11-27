"""
The sky-event heads-up: one push before a retrograde station or an eclipse.

    python -m app.cron sky_events

Separate from the morning push on purpose. That one is the daily ritual and is
never gated or padded; this one is opt-in (Settings on the Chart tab), ships
dark behind MYSIGN_SKY_EVENT_PUSH=1, and sends at most one push per event per
person, ever — stamped by event id, the same claim-before-send pattern the
morning job uses. Lead time: three days before a station (enough to plan a
quieter week if she wants to), one day before an eclipse.

Copy is calm and carries no price, plan or upsell; tests/test_sky.py lints it.
"""
import datetime as dt
import os
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

LEAD = {"station_rx": dt.timedelta(days=3), "station_d": dt.timedelta(days=3),
        "solar_eclipse": dt.timedelta(days=1), "lunar_eclipse": dt.timedelta(days=1)}
# Not in the middle of the night: a heads-up days ahead can wait for morning.
AWAKE = (9, 21)

# The morning job's budget pattern (app/cron.py). A cold _sky_events_for is a
# year of ephemeris per user; serially, one busy run overruns the Lambda
# timeout and the tail is dropped without a trace. A user deferred here is
# picked up by the next run — the event-id stamp still holds it to one push.
WORKERS = 4
BUDGET_S = 300
RESERVE_S = 30


def enabled():
    return os.environ.get("MYSIGN_SKY_EVENT_PUSH") == "1"


def _until(delta):
    """In words, like the app's countdown: "in 2 days", not "in 1d 23h"."""
    h = int(delta.total_seconds() // 3600)
    if h >= 36:
        d = round(delta.total_seconds() / 86400)
        return "in about a week" if d >= 7 else f"in {d} days"
    if h >= 20:
        return "tomorrow"
    return f"in {h} hours" if h >= 2 else "in about an hour"


# What it means for her, first; the same sentences as the app's sky cards.
FOR_YOU = {
    "solar_eclipse": "A turning point around {a}: a new chapter can start here.",
    "lunar_eclipse": "A turning point around {a}: something may come to an end or a head.",
    "station_rx": "Time to slow down and revisit {a}.",
    "station_d": "What you've been revisiting around {a} can move forward again.",
}


def compose(e, now):
    """What it means for her, then when; under 140 characters where the names allow."""
    t = dt.datetime.fromisoformat(e["utc"].replace("Z", "+00:00"))
    when = f"{e['title']} {_until(t - now)}."
    lead = FOR_YOU.get(e["kind"])
    if lead and e.get("house_meaning"):
        body = f"{lead.format(a=e['house_meaning'])} {when}"
    else:
        where = f", in your {_ord(e['her_house'])} house" if e.get("her_house") else f" in {e['sign']}"
        body = f"{e['title']} {_until(t - now)}{where}."
    if e.get("people"):
        body += f" It touches {e['people'][0]['name']} too."
    return "My Sign", body


def _ord(n):
    from engine.skyevents import _ord as o
    return o(n)


def due(payload, now):
    """The first big event inside its lead window, or None."""
    for e in payload.get("events") or []:
        lead = LEAD.get(e["kind"])
        if not lead:
            continue
        t = dt.datetime.fromisoformat(e["utc"].replace("Z", "+00:00"))
        if dt.timedelta(0) < t - now <= lead:
            return e
    return None


def one(uid, now, remaining=None):
    """Never raises; returns what happened for this user."""
    from app import log, push
    from app.server import _profile, _sky_events_for
    from engine.store import load, save
    if remaining is not None and remaining() < RESERVE_S:
        return "deferred"
    try:
        prof = _profile(uid)
        if not prof or not (load(uid, "sky-prefs", {}) or {}).get("event_push"):
            return "off"
        if not push.has_device(uid):
            return "no-device"
        local = now.astimezone(ZoneInfo(prof.get("tz") or "UTC"))
        if not AWAKE[0] <= local.hour < AWAKE[1]:
            return "asleep"
        e = due(_sky_events_for(uid, 7, now=now), now)
        if not e:
            return "nothing"
        stamp = f"skypush-{e['id']}"
        if load(uid, stamp, None):
            return "sent-already"
        save(uid, stamp, {"at": now.isoformat(timespec="seconds")})
        title, body = compose(e, now)
        push.send(uid, title, body, url="/app?sky=events", kind="sky")
        log.emit("sky.pushed", uid=uid, kind=e["kind"])
        return "pushed"
    except Exception as err:
        log.emit("sky.push.failed", level="error", uid=uid, error_type=type(err).__name__)
        return "failed"


def _candidate(uid):
    """
    The two cheap reads, before any computing: a device to push to, and the
    opt-in. Most users have neither, and _sky_events_for is the expensive part —
    there is no reason to compute a sky nobody will be told about.
    """
    from app import push
    from engine.store import load
    try:
        return push.has_device(uid) and \
            bool((load(uid, "sky-prefs", {}) or {}).get("event_push"))
    except Exception:
        return False


def run(now=None, remaining=None, workers=WORKERS):
    """
    remaining: callable returning seconds left in this invocation (the Lambda
    context's clock); defaults to a wall clock against BUDGET_S started here.
    """
    if not enabled():
        return {"enabled": False, "pushed": 0}
    from app import auth, log
    now = now or dt.datetime.now(dt.timezone.utc)
    if remaining is None:
        started = time.monotonic()

        def remaining():
            return BUDGET_S - (time.monotonic() - started)

    due = [uid for uid in auth.all_users() if _candidate(uid)]
    outcomes = []
    if due:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(due)))) as pool:
            outcomes = list(pool.map(lambda u: one(u, now, remaining), due))
    deferred = outcomes.count("deferred")
    if deferred:
        log.emit("sky.push.deferred", level="warning", count=deferred)
    return {"enabled": True, "pushed": outcomes.count("pushed"), "deferred": deferred}
