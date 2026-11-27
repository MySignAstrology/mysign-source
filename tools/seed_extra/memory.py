"""
Dev seed for team2/memory: pattern unlocks and relationship-aware chat.

    from tools.seed_extra import memory
    memory.seed({"Maya": maya_client, "Theo": theo_client, ...})

Dev only. It writes episodes straight into the LOCAL file store, because a
pattern needs ~90 days of check-ins and nobody is going to type them. Each
episode's sky_state is computed by the server's own _sky()/_sky_state() for
that real date, so the pattern verify() finds is about the real sky — only the
moods are invented, and they are invented on purpose to contain one strong,
checkable pattern (low mood on Moon-in-Scorpio days) and nothing else planted.

What it leaves behind, for a human to try:
  Maya   90 days logged; one verified pattern waiting to unlock (Memory tab
         shows the reveal once); notes about Theo across several weeks, so
         "how are things with Theo?" in chat quotes her with dates.
  Leila  30 days logged: the progress meter, with an honest count of candidates
         still being tested and no claims.
  Theo   a private check-in note about Maya that must never reach Maya's chat.

Idempotent: episodes are keyed by date, notes by date, and the unlock's seen
flag is reset so the ceremony can be watched again after re-seeding.
"""
import datetime as dt
import os

PATTERN_SIGN = "Scorpio"
THEO_NOTES = [
    (40, "Theo went quiet again after I asked about the weekend"),
    (19, "Theo called and we talked for two hours, it felt easy"),
    (6, "Theo cancelled last minute and I felt stupid for caring"),
]


def _uid(client):
    from app import auth
    return auth.verify(client.cookies.get(auth.COOKIE, ""))


def _refuse_prod():
    if os.environ.get("MYSIGN_TABLE") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        raise SystemExit("Refusing: seed_extra.memory writes the local file store only.")


def _episodes(uid, days, plant=True, people_notes=()):
    """`days` past days ending yesterday, each with the real sky for that date."""
    import app.server as server
    from engine import memory as M

    today = server._today(uid)
    notes = {(today - dt.timedelta(days=n)).isoformat(): t for n, t in people_notes}
    for i in range(days, 0, -1):
        d = today - dt.timedelta(days=i)
        sky = server._sky_state(server._sky(uid, d))
        # A fixed, deterministic mood: low on the planted condition, otherwise a
        # spread that includes some ordinary low days so the base rate isn't 0.
        if plant and sky["moon_sign"] == PATTERN_SIGN:
            mood = 3
        else:
            mood = (6, 7, 8, 5, 7, 4, 6, 8, 7, 6, 3, 7)[i % 12]
        topics = ["work"] if d.weekday() < 5 and i % 3 else []
        text = notes.get(d.isoformat())
        people = ["Theo"] if text else []
        M.upsert_episode(uid, {
            "date": d.isoformat(), "mood": mood,
            "summary": text or ("long day" if mood < 5 else "an ordinary day"),
            "her_words": [text] if text else [], "people": people, "topics": topics,
            "decision": None, "tier": "green", "sky_state": sky,
            "extraction": "heuristic", "person_ids": M.person_ids(uid, people)})


def seed(clients):
    _refuse_prod()
    from engine import daily
    from engine import memory as M
    from engine.store import load, save

    maya = clients.get("Maya")
    if maya is not None:
        uid = _uid(maya)
        _episodes(uid, 90, people_notes=THEO_NOTES)
        survivors = M.weekly_pattern_job(uid)
        save(uid, "patterns_run", dt.date.today().isoformat())
        save(uid, "pattern-seen", None)          # watch the unlock again
        # Her own check-in about Theo, in her store.
        theo = next((r for r in load(uid, "connections", []) or []
                     if r.get("name") == "Theo"), None)
        if theo:
            key = f"pulse-{theo['id']}"
            day = dt.date.today() - dt.timedelta(days=2)
            save(uid, key, daily.record_checkin(load(uid, key, {}), day, "strained",
                                                "he went quiet when I brought up the trip"))
        print(f"  memory: Maya has 90 days and {len(survivors)} verified pattern(s)")

    theo_c = clients.get("Theo")
    if theo_c is not None:
        tuid = _uid(theo_c)
        maya_row = next((r for r in load(tuid, "connections", []) or []
                         if r.get("name") == "Maya"), None)
        if maya_row:
            key = f"pulse-{maya_row['id']}"
            save(tuid, key, daily.record_checkin(load(tuid, key, {}), dt.date.today(),
                                                 "steady", "THEO-PRIVATE: not sure about us"))

    leila = clients.get("Leila")
    if leila is not None:
        luid = _uid(leila)
        _episodes(luid, 30, plant=False)
        print("  memory: Leila has 30 days — progress meter, no patterns yet")
