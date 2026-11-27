"""
Dev seed for sky events (team2/sky).

Sky events need no invented data: they are the real sky, read against the demo
accounts' real birth data and the people seed_demo.py already linked. This only
turns on the opt-in event heads-up for Maya (so the toggle shows checked when
the server runs with MYSIGN_SKY_EVENT_PUSH=1) and warms each account's
per-day events cache, so the first tap on the Home banner is instant.

    seed(clients)   # clients: {"Maya": TestClient, "Theo": ..., ...}

Idempotent: the preference is a plain set, the cache a recomputable read.
"""


def seed(clients):
    out = {}
    maya = clients.get("Maya")
    if maya is not None:
        r = maya.post("/api/sky/prefs", json={"event_push": True})
        out["maya_event_push"] = r.status_code == 200 and r.json().get("event_push")
    for name, c in clients.items():
        r = c.get("/api/sky/events")
        if r.status_code == 200:
            d = r.json()
            out[name] = {"banner": (d.get("banner") or {}).get("text"),
                         "events": len(d.get("events") or [])}
    return out
