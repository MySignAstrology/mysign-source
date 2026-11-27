"""
The App Review account: what a reviewer needs to see in one sitting.

Memory and the People trend lines take months of use to fill, and Apple rejects
what it can't see. This gives the one allowlisted address (app/auth.py
is_review_account) 100 days of check-ins and memory and 3 linked people, on the
same store and through the same code as anyone else. Only the mood numbers and
the words are made up. The sky on every day is computed, and each pattern on
screen passed memory.verify against these days.

Today stays empty so the reviewer can check in. Run it again before each
submission: it moves the dates up to yesterday and adds nothing twice.

    MYSIGN_REVIEW_EMAIL=review@... MYSIGN_REVIEW_CODE=... python tools/seed_review_account.py

Against a stack, set MYSIGN_TABLE and MYSIGN_SECRET_ARN too, and the address and
code come from the secret. Refuses an account it didn't seed itself.
"""
import contextlib
import datetime as dt
import io
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

with contextlib.redirect_stdout(io.StringIO()):
    import app.server as server

from app import auth
from engine import daily, funnel, unlocks
from engine import memory as M
from engine.store import load, save

MARK = "review_seed"
DAYS = 100
TZ = "America/New_York"
ME = {"name": "Alex", "date": "1994-05-21", "time": "08:40", "city": "Chicago, IL, US", "country": "US"}
PEOPLE = [
    {"name": "Maya", "date": "1995-10-02", "time": "19:20", "city": "Los Angeles, CA, US",
     "kind": "romantic", "relation": "together, two years"},
    {"name": "Theo", "date": "1993-02-14", "time": "", "city": "", "kind": "friend",
     "relation": "best friend"},
    {"name": "Sam", "date": "1990-07-30", "time": "", "city": "", "kind": "family",
     "relation": "sister"},
]
# A week with a shape, so the verifier has something true to find.
HEAVY = ("Sunday dread again. The week ahead felt like a lot.",
         "Quiet Sunday, but my head was already at work.",
         "Couldn't settle tonight. Too much waiting for Monday.",
         "Meant to rest. Spent the afternoon rereading my calendar.",
         "Nothing went wrong today and I still felt heavy.",
         "Told myself I'd stop checking work email on Sundays. I didn't.",
         "Went to bed early to make Monday come slower. It doesn't work.")
LIGHT = ("Good day. Walked home the long way.",
         "Finished the thing I'd been avoiding.",
         "Dinner with Maya. Laughed a lot.",
         "Long call with Theo. Felt like myself.",
         "Slow morning, and I didn't mind it.",
         "Maya found that old photo of us at the lake. We looked so young.",
         "Sam called just to tell me a joke. It was a bad joke.",
         "Got good news at work and told Maya first.",
         "Theo talked me out of a terrible idea. Again.",
         "Cooked something new. It was actually edible.",
         "Maya and I went the whole evening without our phones.",
         "Helped Sam move a couch up three flights. Worth it for the pizza.",
         "Ran into an old friend at the market. Stayed for coffee.",
         "Theo sent me that song and I played it the whole way home.",
         "Sat outside until it got dark. Needed that.")
MIDDLE = ("Ordinary day. Nothing to report.",
          "Busy at work, but it was fine.",
          "A bit tired. Early night.",
          "Long meeting that could have been an email.",
          "Got through the list. Not much else to say.",
          "Rainy, slow, fine.",
          "Maya's traveling this week. The place is quiet.",
          "Caught up on laundry and little else.")


def _day(date):
    """The same date always gets the same mood and words. The words go in turn,
    because Memory quotes them and the same line twice in a row reads as fake."""
    rng = random.Random(date.isoformat())
    turn = date.toordinal() // 7 if date.weekday() == 6 else date.toordinal()
    if date.weekday() == 6:
        return rng.choice((3, 3, 4, 4, 7)), HEAVY[turn % len(HEAVY)], ["work", "worry"]
    if rng.random() < 0.55:
        return rng.choice((7, 8, 8, 9)), LIGHT[turn % len(LIGHT)], ["friends"]
    return rng.choice((5, 6, 6)), MIDDLE[turn % len(MIDDLE)], ["work"]


def _ok(r, what):
    if r.status_code >= 400:
        sys.exit(f"{what} failed: {r.status_code} {r.text[:200]}")
    return r.json()


def _feel(cid, date):
    roll = random.Random(f"{cid}{date.isoformat()}").random()
    return "closer" if roll < 0.4 else "steady" if roll < 0.85 else "strained"


def seed(today=None):
    email = os.environ.get("MYSIGN_REVIEW_EMAIL", "").strip().lower()
    if not auth.is_review_account(email):
        sys.exit("Set MYSIGN_REVIEW_EMAIL and MYSIGN_REVIEW_CODE, or the secret's review_email and review_code.")
    today = today or dt.date.today()

    known = auth.uid_for(email, create=False)
    if known and (load(known, "account", {}) or {}).get("consented_at") and not load(known, MARK, None):
        sys.exit("That address already belongs to an account this tool didn't seed.")
    uid = auth.uid_for(email)
    save(uid, MARK, True)
    # Not a signup, a consent or anything else a funnel should count.
    save(uid, "funnel_seen", list(funnel.STEPS))

    headers = {"authorization": f"Bearer {auth.sign(uid, auth.session_version(uid))}", "x-tz": TZ}
    # This calls the app directly, so it stands in for CloudFront at OriginGuard.
    if os.environ.get("MYSIGN_ORIGIN_TOKEN"):
        headers["x-origin-auth"] = os.environ["MYSIGN_ORIGIN_TOKEN"]
    c = TestClient(server.app, headers=headers)
    with contextlib.redirect_stdout(io.StringIO()):
        _ok(c.post("/api/consent", json={"ai": True}), "consent")
        if not _ok(c.get("/api/state"), "state").get("profile"):
            _ok(c.post("/api/onboard", json=ME), "onboarding")
        have = {p["name"] for p in _ok(c.get("/api/connections"), "people").get("connections", [])}
        for person in PEOPLE:
            if person["name"] not in have:
                _ok(c.post("/api/connections", json=person), f"adding {person['name']}")
        rows = _ok(c.get("/api/connections"), "people").get("connections", [])

    # Not a purchase, so it skips plan.grant and the subscription events. A
    # reviewer who hits the free limit stops reviewing.
    save(uid, "plan", {"tier": "plus", "source": "review", "until": None, "granted_by": "seed_review_account",
                       "changed_at": dt.datetime.now().isoformat(timespec="seconds")})

    dates = [today - dt.timedelta(days=n) for n in range(DAYS, 0, -1)]
    pulses = {r["id"]: load(uid, f"pulse-{r['id']}", {}) for r in rows}
    for date in dates:
        iso = date.isoformat()
        mood, words, topics = _day(date)
        save(uid, f"checkin-{iso}", {"mood": mood, "text": words, "time": "21:10"})
        M.upsert_episode(uid, {"date": iso, "mood": mood, "summary": words, "her_words": [words],
                               "people": [], "person_ids": [], "topics": topics, "decision": None,
                               "tier": "green", "extraction": "review_seed",
                               "sky_state": server._sky_state(server._sky(uid, date))})
        for cid in pulses:
            pulses[cid] = daily.record_checkin(pulses[cid], date, _feel(cid, date), at=f"{iso}T21:15:00+00:00")
    for cid, log in pulses.items():
        save(uid, f"pulse-{cid}", log)

    # The code's proposals rather than the model's: same verifier, same gates,
    # and the seed comes out the same every run.
    patterns = M.weekly_pattern_job(uid, props=unlocks.candidates(uid))
    save(uid, "patterns_run", today.isoformat())
    # One unlock moment. Several in a row is a queue of sheets to dismiss.
    for p in patterns[1:]:
        unlocks.mark_seen(uid, p["id"])
    return {"uid": uid, "people": len(rows), "days": len(dates), "patterns": patterns}


if __name__ == "__main__":
    done = seed()
    print(f"{done['days']} days, {done['people']} people, {len(done['patterns'])} patterns")
    for p in done["patterns"][:5]:
        print(f"  {p['claim']} ({p['support']}, p={p['p_value']})")
