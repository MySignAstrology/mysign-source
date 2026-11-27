"""
Paging.

The engineering guidelines require paging on-call for every critical failure.
My Sign has a category the rest of the estate does not: a **red-tier safety
event** — someone in crisis. That is not a system failure, so it does not fit
the DLQ-alarm pattern, but it is the most time-sensitive thing this product can
produce. Same mechanism, different trigger.

Never let paging break the request it is reporting on. Every call here swallows
its own failures — a crisis reply must reach the user whether or not PagerDuty
is reachable.
"""
import functools
import json
import os
import urllib.error
import urllib.request

EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
TIMEOUT = 4


def _never_raises(fn):
    """
    Paging must not be able to break the thing it is reporting on.

    _send guards the network call, but a narrow except list only covers the
    failures I thought of. This covers the ones I did not — a serialization
    error, an exception type from deep in http.client, anything. A crisis reply
    reaches the user whether or not PagerDuty is reachable.
    """
    @functools.wraps(fn)
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            from app import log
            log.error("alert.failed", exc=e, fn=fn.__name__)
            return False
    return wrapped


def _send(payload):
    key = os.environ.get("PAGERDUTY_ROUTING_KEY")
    if not key:
        from app import log
        log.emit("alert.unrouted", level="error",
                 component=payload["payload"].get("component"),
                 severity=payload["payload"].get("severity"))
        return False
    body = json.dumps({**payload, "routing_key": key}).encode()
    req = urllib.request.Request(EVENTS_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status in (200, 202)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        # Losing the page is bad. Losing the user's reply because paging failed
        # would be worse.
        from app import log
        log.error("alert.send.failed", exc=e)
        return False


@_never_raises
def red_tier(uid, message_excerpt, kind="self_harm", severity="critical"):
    """
    Someone is in crisis. Page — unless it is App Review's demo account testing
    the crisis path, which is logged and not paged.

    The caller decides whether this message is worth a page at all
    (engine/episode.py): once per crisis episode per severity per half hour,
    and again only when it gets worse. Forty red messages in a burst used to be
    forty critical triggers. The dedup key is per user, so a re-page on
    escalation lands on the same PagerDuty incident instead of opening another.

    The excerpt is deliberately short and the full message is not sent — enough
    to triage, not a copy of someone's worst moment in a third-party incident
    tracker.
    """
    from app import auth
    if uid and uid == auth.review_uid():
        from app import log
        log.emit("alert.review_account", uid=uid)
        return False
    return _send({
        "event_action": "trigger",
        "dedup_key": f"mysign-red-{uid}",
        "payload": {
            "summary": f"My Sign: red-tier safety event ({kind}) for {uid}",
            "severity": severity,
            "source": "mysign",
            "component": "safety",
            "custom_details": {
                "user": uid,
                "kind": kind,
                "excerpt": (message_excerpt or "")[:120],
                "action": "A red reply was sent. Review the thread now.",
            },
        },
    })


@_never_raises
def age_notice(uid):
    """
    She said she is under 18. Not a page: the reply has gone, chat is paused
    and nothing is deleted. Someone should look at the account within a day —
    an info-severity event, with its own dedup key so it never merges into a
    crisis incident.
    """
    from app import auth
    if uid and uid == auth.review_uid():
        return False
    return _send({
        "event_action": "trigger",
        "dedup_key": f"mysign-age-{uid}",
        "payload": {
            "summary": f"My Sign: user {uid} said she is under 18; chat paused",
            "severity": "info",
            "source": "mysign",
            "component": "safety",
            "custom_details": {"user": uid,
                               "action": "Review the thread. The account is age-flagged, not deleted."},
        },
    })


@_never_raises
def failure(component, summary, details=None):
    """Ordinary critical failure — a push that could not send, a job that died."""
    return _send({
        "event_action": "trigger",
        "dedup_key": f"mysign-{component}",
        "payload": {"summary": f"My Sign: {summary}", "severity": "error",
                    "source": "mysign", "component": component,
                    "custom_details": details or {}},
    })
