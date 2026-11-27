"""
The morning job.

    python -m app.cron morning        # locally
    handler(event, context)           # on Lambda, from EventBridge

Runs every fifteen minutes and pushes to whoever is past 07:30 in their own
timezone and has not had today's push yet. Idempotent per user per day, so a
duplicate firing is harmless.

The quiet-day rule matters more here than anywhere else in the product. Roughly
40% of days score low, and a notification that says "nothing is happening" every
third morning is how the whole thing becomes something she swipes away. On those
days she gets a bare check-in prompt instead — the ritual without the invented
significance.
"""
import contextlib
import datetime as dt
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from engine import meter, models

meter.install_lazy()    # the push lines cost money too (engine/meter.py)

HOUR, MINUTE = 7, 30
# Due from 07:30 until 09:30 local, not just the first 15-minute tick. A user
# deferred by the time budget, or missed by a failed run, is picked up by the
# next one instead of losing the day. The stamp keeps it to one push.
WINDOW = dt.timedelta(hours=2)

# Each due user costs a model call of several seconds. Serially, one busy
# timezone slot overruns the 300s Lambda timeout and silently drops the tail.
WORKERS = 8
BUDGET_S = 300          # the Lambda timeout; used when there is no context to ask
RESERVE_S = 30          # stop starting users with this much left


def _compose(uid, verdict, block, name):
    """Model-written when we can reach it, deterministic when we can't."""
    if verdict["band"] == "low":
        return "My Sign", "It's a quiet day in your chart. How are you doing?"

    top = verdict["headlines"][0] if verdict["headlines"] else None
    fallback = (f"{top['transit']} is on your {top['natal']} today."
                if top else "Something in the sky is active for you today.")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "My Sign", fallback

    from app import log
    from engine import costs
    costs.current_uid.set(uid)             # her push is charged to her month
    try:
        import anthropic
        # A bounded timeout and one retry: a hung call must not eat the budget
        # every other user in this run is sharing.
        r = anthropic.Anthropic(timeout=60, max_retries=1).messages.create(
            model=models.PUSH, max_tokens=2000,   # thinking is on; 300 could truncate
            output_config={"effort": "low"},
            messages=[{"role": "user", "content":
                       "Write the morning notification. ONE sentence, under 140 "
                       "characters. Say what today may feel like for her first, "
                       "then name the planet behind it. No greeting, no emoji, no "
                       "question. It shows on a lock screen anyone nearby can "
                       "read: never mention her mood, anything she wrote, or any "
                       "person by name — only the sky. Return only the sentence.\n\n"
                       f"<context>\n{block}\n</context>"}],
        )
        # Anything but a clean finish — a refusal, a max_tokens cut mid-sentence —
        # is not something to put on her lock screen.
        if getattr(r, "stop_reason", None) != "end_turn":
            log.emit("cron.compose.fallback", uid=uid, stop_reason=getattr(r, "stop_reason", None))
            return "My Sign", fallback
        text = "".join(getattr(b, "text", "") for b in (r.content or [])
                       if getattr(b, "type", None) == "text").strip()
        if not text:
            log.emit("cron.compose.fallback", uid=uid, stop_reason="empty")
            return "My Sign", fallback
        return "My Sign", text[:180]
    except Exception as e:
        log.error("cron.compose.failed", exc=e, uid=uid)
        return "My Sign", fallback


def _people_line(uid, day):
    """team2/habit: (sentence, cid) or None. Never fatal — a person line that
    fails to compute costs only the person line, never her morning."""
    from app import log
    from app.server import morning_people_line
    try:
        return morning_people_line(uid, day)
    except Exception as e:
        log.error("cron.people_line.failed", exc=e, uid=uid)
        return None


def _due(local, at=None):
    """Due from her chosen time (07:30 unless she picked another) until the
    window closes, in her own zone."""
    hh, mm = (int(x) for x in at.split(":")) if at else (HOUR, MINUTE)
    target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target <= local < target + WINDOW


def _stamp(local):
    # The user's local date, never the server's: Lambda runs in UTC, and for
    # Sydney 07:30 is the previous UTC day.
    return f"pushed-{local.date()}"


def _one(uid, prof, local, now, remaining, style="line"):
    """One user, start to finish. Never raises — the caller is a thread pool."""
    from app import log, push
    from app.server import _natal, _sky
    from engine import context
    from engine.store import load, save

    if remaining() < RESERVE_S:
        return {"uid": uid, "status": "deferred"}

    stamp, day = _stamp(local), local.date()
    sending = False
    try:
        # Claim before composing, not after sending. The compose is the slow part,
        # and a stamp written only after the send leaves that whole window open
        # for an overlapping or retried run to push the same person twice. With
        # load/save this is not atomic, but the race shrinks from seconds to
        # milliseconds; a conditional write would close it.
        if load(uid, stamp, None):
            return {"uid": uid, "status": "skipped"}
        # The morning after a crisis is not the morning for an astrology line
        # (or "yesterday was hard" on the lock screen). The chat has her
        # resources; the push waits until the episode closes.
        from engine import episode
        if episode.active(uid):
            log.emit("cron.morning.held", uid=uid, reason="episode")
            return {"uid": uid, "status": "held"}
        save(uid, stamp, {"at": now.isoformat(timespec="seconds"), "state": "claimed"})

        checkin = load(uid, f"checkin-{day}", None) or {"text": "", "mood": None}
        block, verdict = context.build(uid, _natal(uid), _sky(uid, day),
                                       checkin, today=day, surface="push")
        # team2/habit: when today moved one of her people, the push names them
        # and the transit doing it — deterministic, from engine/daily.py, so it
        # needs no model call and can't invent a mechanism. Otherwise the style
        # she chose: the line of the day (default) or her own sky.
        named = _people_line(uid, day)
        url = "/app"
        if named:
            (title, body), url = ("My Sign", named[0]), f"/app?p={named[1]}"
        elif style == "sky":
            title, body = _compose(uid, verdict, block, prof.get("name"))
        else:
            # The line of the day — the same one Today leads with (engine/lines.py
            # picks deterministically per user and day, so they always agree).
            # The transit that chose it is the title; the line is the body.
            from engine import lines
            quiet = verdict.get("band") == "low"
            heads = [h for h in verdict.get("headlines") or []
                     if all(h.get(k) for k in ("transit", "aspect", "natal"))]
            ln = lines.for_user(heads, day.isoformat(), uid, quiet=quiet or not heads)
            # The line's own reason, as a title. A low-salience day can still
            # have a real transit behind its line (engine/lines.py), so "quiet"
            # is only the fallback when the line gives no reason at all.
            reason = lines.sky_of(ln.get("why")) or ""
            title = reason[:1].upper() + reason[1:] if reason else "A quiet sky today"
            # The morning after a low check-in, the title remembers it. Same
            # line underneath; the sky reason is a tap away in the app.
            from engine import mood as mood_mod
            yday = load(uid, f"checkin-{day - dt.timedelta(days=1)}", None)
            title = mood_mod.after_low_title(yday) or title
            body = ln.get("text", "")
        if not body:
            raise ValueError("empty notification")

        # From here the claim stays even if every device fails: some may already
        # have it, and a missed morning is better than a second one.
        sending = True
        out = push.send(uid, title, body, url=url, kind="person" if named else "morning")
        save(uid, stamp, {"at": now.isoformat(timespec="seconds"), "state": "sent",
                          "band": verdict["band"], "body": body, "url": url,
                          "named": bool(named), **out},
             ttl=int(time.time()) + 3 * 86400)
        log.emit("cron.pushed", uid=uid, band=verdict["band"],
                 devices=out["sent"], pruned=out["pruned"], failed=out.get("failed", 0))
        return {"uid": uid, "status": "pushed", "band": verdict["band"], **out}
    except Exception as e:
        log.emit("cron.user.failed", level="error", uid=uid, error_type=type(e).__name__)
        if not sending:
            # Nothing went out, so release the claim and let the next run retry.
            with contextlib.suppress(Exception):
                save(uid, stamp, None)
        return {"uid": uid, "status": "failed"}


def morning(now=None, remaining=None, workers=WORKERS):
    """
    now: the UTC instant to evaluate (injectable for tests).
    remaining: callable returning seconds left in this invocation; defaults to a
    wall clock against BUDGET_S started here.
    """
    from app import auth, log, push
    from app.server import _profile
    from engine.store import load

    now = now or dt.datetime.now(dt.timezone.utc)
    if remaining is None:
        started = time.monotonic()
        def remaining():
            return BUDGET_S - (time.monotonic() - started)

    due = []
    for uid in auth.all_users():
        try:
            prof = _profile(uid)
            if not prof or not push.has_device(uid):
                continue
            acct = load(uid, "account", {}) or {}
            # Where she is now (sent by her browser), not where she was born.
            local = now.astimezone(ZoneInfo(acct.get("tz") or prof.get("tz")
                                            or "America/New_York"))
            if acct.get("push_off"):
                continue
            if _due(local, acct.get("push_time")) and not load(uid, _stamp(local), None):
                due.append((uid, prof, local, acct.get("push_style") or "line"))
        except Exception as e:      # e.g. a garbage tz in one profile
            log.emit("cron.user.failed", level="error", uid=uid, error_type=type(e).__name__)

    outcomes = []
    if due:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(due)))) as pool:
            futures = [pool.submit(_one, uid, prof, local, now, remaining, style)
                       for uid, prof, local, style in due]
            outcomes = [f.result() for f in futures]

    results = [o for o in outcomes if o["status"] == "pushed"]
    deferred = sum(o["status"] == "deferred" for o in outcomes)
    failed = sum(o["status"] == "failed" for o in outcomes)
    if deferred:
        log.emit("cron.deferred", level="warning", count=deferred)
    return {"pushed": len(results), "deferred": deferred, "failed": failed,
            "results": results}


def handler(event, context=None):
    """
    EventBridge entrypoint. This function is invoked directly rather than
    through the web adapter, so it has to load its own secrets — importing
    app.server would drag in FastAPI for nothing.
    """
    from app.bootstrap import load_secrets
    load_secrets()
    remaining = None
    if context is not None and hasattr(context, "get_remaining_time_in_millis"):
        def remaining():
            return context.get_remaining_time_in_millis() / 1000
    # team2/sky: the opt-in event heads-up runs on its own schedule entry.
    if isinstance(event, dict) and event.get("job") == "sky_events":
        from app import skypush
        return skypush.run(remaining=remaining)
    # Weekly: re-read celebrity couples' status from Wikidata, write any
    # missing reads, research new milestones (engine/couples_watch.py).
    if isinstance(event, dict) and event.get("job") == "couples_watch":
        from engine import couples_watch
        return couples_watch.run(remaining=remaining)
    return morning(remaining=remaining)


if __name__ == "__main__":
    job = sys.argv[1] if len(sys.argv) > 1 else "morning"
    def sky_events():       # team2/sky
        from app import skypush
        return skypush.run()
    def couples_watch():
        from engine import couples_watch as cw
        return cw.run()
    print({"morning": morning, "sky_events": sky_events, "couples_watch": couples_watch}[job]())
