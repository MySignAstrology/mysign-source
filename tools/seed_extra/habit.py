"""
team2/habit demo data: the named-person morning push, the widget, and
"ask your mom", visible with the seed_demo accounts.

    seed(clients)       # clients: {"Maya": TestClient, "Theo": ..., "Sam": ...}

or on its own, after tools/seed_demo.py has made the accounts:

    MYSIGN_DATA=./data-dev .venv/bin/python tools/seed_extra/habit.py

What it sets up (idempotent — run it as often as you like):
- Maya keeps "Mention my people in the morning notification" on, and the
  script prints the exact line her 07:30 push would carry today (or says the
  sky moved nobody enough, which is the honest answer some days).
- Sam was seeded without a birth time, so Sam gets one live "ask your mom"
  link. Open it in a private window — no account — to play the parent. A
  second run reuses the live link rather than revoking it.
"""
import contextlib
import io
import os
import sys

BASE = os.environ.get("MYSIGN_BASE", "http://localhost:8421")


def _ok(r, what):
    if r.status_code >= 400:
        raise RuntimeError(f"{what} failed: {r.status_code} {r.text[:200]}")
    return r.json()


def seed(clients):
    from app import server
    out = {}
    maya = clients.get("Maya")
    if maya is not None:
        _ok(maya.post("/api/profile/push-people", json={"on": True}), "push-people")
        uid = _uid_of(maya)
        with contextlib.redirect_stdout(io.StringIO()):
            line = server.morning_people_line(uid) if uid else None
            w = _ok(maya.get("/api/widget"), "widget")
        out["maya_push"] = line[0] if line else None
        out["maya_push_link"] = f"/?p={line[1]}" if line else None
        out["widget_top"] = w.get("top") and f"{w['top']['name']} {w['top']['score']} ({w['top']['delta']:+d})"

    sam = clients.get("Sam")
    if sam is not None:
        st = _ok(sam.get("/api/birthtime/request"), "bt status")
        if st["time_known"]:
            out["sam_link"] = None          # someone already answered it; nothing to ask
        else:
            tok = (st.get("pending") or {}).get("token") or _ok(
                sam.post("/api/birthtime/request"), "bt request")["token"]
            out["sam_link"] = f"{BASE}/bt/{tok}"
    return out


def _uid_of(c):
    """The signed-in uid, read from the session cookie the app itself issued."""
    from app import auth
    data = auth._claims(c.cookies.get(auth.COOKIE) or "")
    return data and data.get("u")


def _clients():
    from fastapi.testclient import TestClient

    from app import server
    out = {}
    for name, email in (("Maya", "maya@demo.test"), ("Sam", "sam@demo.test")):
        c = TestClient(server.app)
        code = c.post("/api/auth/request", json={"email": email}).json()["dev_code"]
        c.post("/api/auth/verify", json={"email": email, "code": code})
        out[name] = c
    return out


if __name__ == "__main__":
    os.environ.setdefault("MYSIGN_DATA", "./data-dev")
    os.environ["MYSIGN_DEV_AUTH"] = "1"
    if os.environ.get("MYSIGN_TABLE") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        sys.exit("Refusing: this seeds the local file store only.")
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    with contextlib.redirect_stdout(io.StringIO()):
        cl = _clients()
    r = seed(cl)
    print("Maya's morning push today:", r.get("maya_push") or "(nobody moved enough - her own sky line instead)")
    if r.get("maya_push_link"):
        print("  deep link:", r["maya_push_link"])
    print("Maya's widget, top person:", r.get("widget_top"))
    print("Sam's 'ask your mom' link (open with no account):", r.get("sam_link") or "(Sam's time is already known)")
