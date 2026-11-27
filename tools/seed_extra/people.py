"""
team2/people dev seed: the mutual reveal in all three states, for the demo
accounts from tools/seed_demo.py.

    seed({"Maya": TestClient, "Theo": ..., "Sam": ..., "Leila": ..., "Jordan": ...})

After it runs, signed in as Maya:
- Home shows "Theo answered 'How's it going between us?' - your turn";
- Sam's card says Maya answered and is waiting on Sam;
- Jordan's card shows a finished reveal next to that day's sky.
The "Best day to..." planner needs no data: open any person.

Idempotent: answers go through the real endpoints; a reveal that already
finished answers 409 and is left as it is. Goes only through the API, so it
is itself a run of the workflow.
"""

# (who answers, about whom, feel, private note)
ANSWERS = [
    ("Theo", "Maya", "closer", None),            # Maya's turn
    ("Maya", "Sam", "steady", "busy week"),      # waiting on Sam
    ("Maya", "Jordan", "strained", None),        # revealed
    ("Jordan", "Maya", "steady", None),
]


def _cid(client, name):
    rows = client.get("/api/connections").json()["connections"]
    return next((r["id"] for r in rows if r["name"] == name and r.get("linked")), None)


def seed(clients):
    done = []
    for who, about, feel, note in ANSWERS:
        c = clients.get(who)
        cid = c and _cid(c, about)
        if not cid:
            continue                     # not linked in this seed; skip quietly
        r = c.post(f"/api/connections/{cid}/reveal", json={"feel": feel, "note": note or ""})
        if r.status_code not in (200, 409):
            raise RuntimeError(f"reveal {who}->{about}: {r.status_code} {r.text[:200]}")
        done.append(f"{who} -> {about}: {feel}")
    return done
