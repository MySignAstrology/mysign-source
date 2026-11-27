"""Today's computed sky for one participant. Cached per user per day —
the ephemeris is deterministic, so there is no reason to recompute it."""
import datetime as dt

from engine.chart import SIGNS, aspects, house_of, jd_ut, moon_phase, natal, positions
from engine.store import load, save

_natal_cache = {}


def natal_for(p):
    if p["user_id"] not in _natal_cache:
        b = p["birth"]                        # [y, m, d, hour_ut, lat, lon]
        _natal_cache[p["user_id"]] = natal(*b)
    return _natal_cache[p["user_id"]]


def sky_for(p, day=None):
    day = day or dt.date.today()
    key = f"sky-{day}"
    cached = load(p["user_id"], key, None)
    if cached:
        return cached

    nat = natal_for(p)
    j = jd_ut(day.year, day.month, day.day, 17.0)
    tpos = positions(j)
    sky = {
        "aspects_to_natal": aspects(tpos, nat),
        "moon": moon_phase(j),
        "moon_sign": SIGNS[int(tpos["Moon"]["lon"] // 30)],
        "active_houses": sorted({house_of(v["lon"], nat["house_cusps"])
                                 for v in tpos.values()}),
        "upcoming": p.get("upcoming", []),
    }
    save(p["user_id"], key, sky)
    return sky
