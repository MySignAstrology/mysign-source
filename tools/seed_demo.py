"""
Sample people for testing My Sign locally - the whole social loop.

Creates real accounts in the LOCAL data folder through the app's own API
(sign-in code, consent, onboarding, invites, check-ins), so seeding is itself a
run of the workflow. Safe to run again: existing accounts are reused and links
are not duplicated.

    MYSIGN_DATA=./data-dev .venv/bin/python tools/seed_demo.py

Then open http://localhost:8421, sign in as any address below; in dev mode the
six-digit code is shown on screen. Never point this at production: it refuses
unless MYSIGN_DEV_AUTH is on and the store is the local file store.
"""
import contextlib
import io
import os
import sys

os.environ.setdefault("MYSIGN_DATA", "./data-dev")
os.environ["MYSIGN_DEV_AUTH"] = "1"
if os.environ.get("MYSIGN_TABLE") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    sys.exit("Refusing: this seeds the local file store only.")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

with contextlib.redirect_stdout(io.StringIO()):
    import app.server as server

BASE = os.environ.get("MYSIGN_BASE", "http://localhost:8421")

# name, email, birth date, time ("" = unknown), city query, country, today's mood,
# shares mood with linked people
MAYA = ("Maya", "maya@demo.test", "1998-03-14", "04:42", "Chicago", "US", 6, True)
FRIENDS = [
    ("Theo",   "theo@demo.test",   "1996-07-02", "21:15", "London",   "GB", 8,    True),
    ("Sam",    "sam@demo.test",    "1997-11-20", "",      "Lisbon",   "PT", 4,    True),
    ("Leila",  "leila@demo.test",  "2000-08-09", "16:30", "Paris",    "FR", 9,    False),
    ("Jordan", "jordan@demo.test", "1995-01-30", "09:10", "New York", "US", None, True),
]


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def ok(r, what):
    if r.status_code >= 400:
        sys.exit(f"{what} failed: {r.status_code} {r.text[:200]}")
    return r.json()


def account(name, email, date, time_, city, cc, mood, share):
    c = TestClient(server.app)
    code = ok(quiet(c.post, "/api/auth/request", json={"email": email}), "code")["dev_code"]
    ok(quiet(c.post, "/api/auth/verify", json={"email": email, "code": code}), "sign-in")
    quiet(c.post, "/api/consent", json={"ai": True})
    st = ok(quiet(c.get, "/api/state"), "state")
    if not st.get("onboarded"):
        hits = ok(quiet(c.get, "/api/cities", params={"q": city}), "city")
        pick = next((h for h in hits if h["id"].endswith("|" + cc)), hits[0])
        ok(quiet(c.post, "/api/onboard", json={"name": name, "date": date, "time": time_,
                                               "city": pick["id"]}), f"onboard {name}")
    if mood is not None:
        ok(quiet(c.post, "/api/checkin", json={"mood": mood, "text": f"{name}'s private note"}),
           "check-in")
    ok(quiet(c.post, "/api/profile/share-mood", json={"on": share}), "share-mood")
    return c


def main():
    print(f"Seeding into {os.environ['MYSIGN_DATA']} ...")
    maya = account(*MAYA)
    clients = {"Maya": maya}
    linked = {r["name"] for r in ok(quiet(maya.get, "/api/connections"), "list")["connections"]
              if r.get("linked")}
    for f in FRIENDS:
        friend = account(*f)
        clients[f[0]] = friend
        if f[0] in linked:
            continue
        inv = ok(quiet(maya.post, "/api/invites"), "invite")
        ok(quiet(friend.post, f"/api/invites/{inv['token']}/accept",
                 json={"share_chart": True, "share_mood": f[7]}), f"accept {f[0]}")
    # Someone Maya entered herself - no account, so no shared mood.
    names = {r["name"] for r in ok(quiet(maya.get, "/api/connections"), "list")["connections"]}
    if "Mom" not in names:
        hits = ok(quiet(maya.get, "/api/cities", params={"q": "Chicago"}), "city")
        ok(quiet(maya.post, "/api/connections", json={"name": "Mom", "relation": "mom",
               "date": "1970-05-22", "time": "", "city": hits[0]["id"]}), "add Mom")

    # Each feature brings its own sample data (tools/seed_extra/<feature>.py,
    # a seed(clients) per module), so the demo accounts show every feature.
    import importlib
    import pkgutil

    import tools.seed_extra as extras
    for mod in sorted(m.name for m in pkgutil.iter_modules(extras.__path__)):
        try:
            made = quiet(importlib.import_module(f"tools.seed_extra.{mod}").seed, clients)
            print(f"  seeded {mod}: {made if made else 'ok'}")
        except Exception as e:                      # one feature's sample must not stop the rest
            print(f"  seed {mod} FAILED: {e}")

    fresh = ok(quiet(maya.post, "/api/invites"), "fresh invite")
    theo_c = TestClient(server.app)
    code = ok(quiet(theo_c.post, "/api/auth/request", json={"email": "theo@demo.test"}), "code")["dev_code"]
    quiet(theo_c.post, "/api/auth/verify", json={"email": "theo@demo.test", "code": code})
    theo_inv = ok(quiet(theo_c.post, "/api/invites"), "theo invite")

    people = ok(quiet(maya.get, "/api/people/today"), "people today")
    print(f"\nAccounts (sign in at {BASE}; the dev code shows on screen):")
    for n, e, *_rest in [MAYA, *FRIENDS]:
        print(f"  {n:<7} {e}")
    print("\nMaya's people today:")
    for p in people["ranked"]:
        d = p.get("their_day") or {}
        feel = ("private" if d.get("private") else f"feeling {d['mood']}/10" if d.get("mood")
                else "no check-in yet" if p.get("linked") else "entered by Maya")
        print(f"  {p['name']:<7} today {p['score']:>3} ({p['delta']:+d})  {feel}")
    print("\nTry the invite as a brand-new person (use a new email, e.g. you+1@demo.test):")
    print(f"  {BASE}/?invite={fresh['token']}")
    print(f"Or join Theo's sky:\n  {BASE}/?invite={theo_inv['token']}")


if __name__ == "__main__":
    main()
