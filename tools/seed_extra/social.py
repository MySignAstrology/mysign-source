"""
Seed for Circles and "Before you date" (team2/social).

    seed(clients)   # clients: {"Maya": TestClient, "Theo": ..., ...}, signed in,
                    # as tools/seed_demo.py leaves them (Maya linked to the rest)

Gives Maya a circle, "the girls", of three of her people (linked and not), so
the group's day, the week's superlatives and the share card have something to
show the moment she signs in. Gives Theo a second circle of his own. "Before
you date" needs no seed - it stores nothing by design - but Jordan, who has
only Maya, sees the Home prompt for it.

Idempotent: a circle with the same name is left alone.
"""


def _ok(r, what):
    if r.status_code >= 400:
        raise RuntimeError(f"{what} failed: {r.status_code} {r.text[:200]}")
    return r.json()


def _circle(c, name, want):
    have = {x["name"] for x in _ok(c.get("/api/circles"), "circles")["circles"]}
    if name in have:
        return None
    rows = _ok(c.get("/api/connections"), "connections")["connections"]
    by_name = {r["name"]: r["id"] for r in rows}
    ids = [by_name[n] for n in want if n in by_name]
    if len(ids) < 2:
        return None
    return _ok(c.post("/api/circles", json={"name": name, "members": ids}), f"circle {name}")


def seed(clients):
    made = []
    maya = clients.get("Maya")
    if maya and _circle(maya, "the girls", ["Leila", "Sam", "Jordan", "Theo"]):
        made.append("Maya: the girls")
    theo = clients.get("Theo")
    if theo:
        # Theo is linked to Maya only; give him two people he typed in himself.
        rows = {r["name"] for r in _ok(theo.get("/api/connections"), "connections")["connections"]}
        hits = _ok(theo.get("/api/cities", params={"q": "London"}), "city")
        for name, date in (("Priya", "1997-04-11"), ("Kai", "1995-12-02")):
            if name not in rows:
                _ok(theo.post("/api/connections", json={"name": name, "date": date, "time": "",
                                                        "city": hits[0]["id"]}), f"add {name}")
        if _circle(theo, "flatmates", ["Maya", "Priya", "Kai"]):
            made.append("Theo: flatmates")
    return made
