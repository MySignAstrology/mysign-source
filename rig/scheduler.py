"""
The 7:30am push and the nightly write.

  python -m rig.scheduler morning     # cron: 0,15,30,45 * * * *  (fires per-tz)
  python -m rig.scheduler nightly     # cron: 0 4 * * *

Morning drafts a push for anyone whose local clock just passed 07:30 and queues
it. It does not send — the operator still approves. Nightly runs extraction over
the day's thread, and on Sundays re-runs the pattern job.
"""
import datetime as dt
import sys
from zoneinfo import ZoneInfo

from engine import memory as M
from engine.store import load, save
from rig import agent
from rig.app import enqueue
from rig.sky import sky_for


def morning(window_min=15):
    for p in load("_study", "participants", []):
        if not p.get("consented"):
            continue
        now = dt.datetime.now(ZoneInfo(p["tz"]))
        target = now.replace(hour=7, minute=30, second=0, microsecond=0)
        if not (0 <= (now - target).total_seconds() < window_min * 60):
            continue
        if load(p["user_id"], f"pushed-{now.date()}", None):
            continue

        d = agent.draft(p["user_id"], p["birth"], sky_for(p),
                        checkin={"text": "", "time": "07:30"},
                        history=[], surface="push", today=now.date())
        enqueue(p["user_id"], d["text"], "push", d["context_block"], d["verdict"])
        save(p["user_id"], f"pushed-{now.date()}", True)
        print(f"queued push for {p['name']} — salience {d['verdict']['band']}")


def nightly():
    today = dt.date.today()
    for p in load("_study", "participants", []):
        uid = p["user_id"]
        thread = [m for m in load(uid, "thread", []) if m["at"][:10] == today.isoformat()]
        if not thread:
            continue
        transcript = "\n".join(f"{m['role']}: {m['text']}" for m in thread)
        checkin = load(uid, f"checkin-{today}", {}) or {}
        sky = sky_for(p, today)
        M.extract_day(uid, today.isoformat(), checkin, transcript, {
            "moon_phase": sky["moon"]["phase"], "moon_sign": sky["moon_sign"],
            "aspect_keys": [f"{a['transit']}-{a['aspect']}-{a['natal']}"
                            for a in sky["aspects_to_natal"]],
            "active_houses": sky["active_houses"]})
        print(f"extracted {p['name']} — {len(thread)} messages")

        if today.weekday() == 6:                       # Sunday
            kept = M.weekly_pattern_job(uid)
            print(f"  patterns surviving verification: {len(kept)}")


if __name__ == "__main__":
    {"morning": morning, "nightly": nightly}[sys.argv[1]]()
