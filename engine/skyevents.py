"""
Sky events — the big days, found by the ephemeris and read against her chart.

Retrograde stations, eclipses, new and full moons and the Sun's sign changes are
the days astrology apps get the most traffic, and the days generic copy does the
most damage ("Mercury retrograde: don't sign anything!"). Here every one of them
is a computed instant:

  stations     the moment a planet's longitudinal speed changes sign, found by
               bisecting the speed Swiss Ephemeris returns — not a table
  shadows      from the station degrees themselves: the pre-shadow starts when
               the planet first reaches the degree it will later turn direct
               at, the post-shadow ends when it gets back to the degree it
               turned retrograde at
  eclipses     swe.sol_eclipse_when_glob / swe.lun_eclipse_when, the same calls
               engine/accuracy.py holds against NASA's eclipse canon
  lunations    the Moon-Sun elongation crossing 0 and 180, bisected
  ingresses    the Sun crossing a multiple of 30 degrees

and then personalised with nothing but geometry: the house the event falls in
for her (only with an exact birth time — houses without one are a guess), the
natal points it aspects within ORB, and which of her people's charts it touches.

The sky part is the same for everyone, so it is computed once per process per
day (lru_cache) and per-user results are cached in the store by the caller.
That is the whole outage story: on a big day the work is a cache read.

Copy rules: calm and reflective. Nothing here predicts an event or tells her to
fear a date; BANNED is what tests/test_sky.py lints every generated string for.
"""
import datetime as dt
import functools
import hashlib

import swisseph as swe

from engine.chart import fmt, house_of
from engine.ephemeris import BASE, FLAGS

STATION_BODIES = (("Mercury", swe.MERCURY), ("Venus", swe.VENUS), ("Mars", swe.MARS))
ORB = 2.0                 # degrees: an event "touches" a natal point this close
LOOKBACK_DAYS = 160       # long enough to catch a Mars retrograde already under way
MAX_DAYS = 400
SHADOW_SEARCH_DAYS = 140  # Mars' shadow can run ~2 months either side
BIG = ("station_rx", "station_d", "solar_eclipse", "lunar_eclipse")
ASPECT_ANGLES = (("conjunction", 0), ("opposition", 180), ("square", 90),
                 ("trine", 120), ("sextile", 60))

# Never in anything this module writes. Fear and prophecy are the genre's habit
# and the opposite of what the app promises.
BANNED = ("disaster", "will go wrong", "don't sign", "do not sign", "doom", "beware",
          "guaranteed", "going to happen", "catastroph", "avoid signing", "will ")

HOUSE_MEANING = {
    1: "how you show up", 2: "money and what you value", 3: "everyday talk, texts and siblings",
    4: "home and family", 5: "romance, play and making things", 6: "work routines and health habits",
    7: "partners and one-to-one relationships", 8: "intimacy and shared resources",
    9: "travel, study and big beliefs", 10: "career and reputation",
    11: "friends and the groups you belong to", 12: "rest, retreat and what's private",
}

RETRO_REFLECT = {
    "Mercury": "It's a good stretch to re-read, revise and reconnect",
    "Venus": "It's a good stretch to revisit what, and who, you value",
    "Mars": "It's a good stretch to rework plans rather than rush new ones",
}


def _ut(j):
    y, m, d, h = swe.revjul(j, swe.GREG_CAL)
    base = dt.datetime(y, m, d, tzinfo=dt.timezone.utc)
    return (base + dt.timedelta(hours=h)).replace(microsecond=0)


def _jd(when):
    when = when.astimezone(dt.timezone.utc)
    return swe.julday(when.year, when.month, when.day,
                      when.hour + when.minute / 60 + when.second / 3600, swe.GREG_CAL)


def _lon(j, body):
    return swe.calc_ut(j, body, FLAGS)[0][0]


def _speed(j, body):
    return swe.calc_ut(j, body, FLAGS)[0][3]


def _wrap(x):
    return (x + 180) % 360 - 180


def _bisect(f, a, b, iters=30):
    """f changes sign between a and b; return the crossing. 30 halvings of a
    day is well under a second."""
    fa = f(a)
    for _ in range(iters):
        mid = (a + b) / 2
        fm = f(mid)
        if (fm < 0) == (fa < 0):
            a, fa = mid, fm
        else:
            b = mid
    return (a + b) / 2


def _where(lon):
    p = fmt(lon)
    return {"lon": round(lon % 360, 4), "sign": p["sign"], "deg": p["deg"], "label": p["label"]}


# ------------------------------------------------------------------ the sky

def stations(j0, j1):
    """Every station of Mercury, Venus and Mars between two JDs."""
    out = []
    for name, body in STATION_BODIES:
        j, prev = j0, _speed(j0, body)
        while j < j1:
            nxt = _speed(j + 1, body)
            if (prev < 0) != (nxt < 0):
                t = _bisect(lambda x, b=body: _speed(x, b), j, j + 1)
                out.append({"kind": "station_rx" if prev > 0 else "station_d",
                            "body": name, "jd": t, **_where(_lon(t, body))})
            j, prev = j + 1, nxt
    return out


def _cross_back(body, j_from, target, limit=SHADOW_SEARCH_DAYS):
    """Walking back from j_from, the last time the planet sat at `target`
    while moving direct (the start of the pre-shadow)."""
    f = lambda x: _wrap(_lon(x, body) - target)   # noqa: E731
    j = j_from
    for _ in range(limit):
        if f(j - 1) < 0 <= f(j):
            return _bisect(f, j - 1, j)
        j -= 1
    return None


def _cross_fwd(body, j_from, target, limit=SHADOW_SEARCH_DAYS):
    """Walking forward from j_from, the first time the planet is back at
    `target` (the end of the post-shadow)."""
    f = lambda x: _wrap(_lon(x, body) - target)   # noqa: E731
    j = j_from
    for _ in range(limit):
        if f(j) < 0 <= f(j + 1):
            return _bisect(f, j, j + 1)
        j += 1
    return None


def retro_cycles(j0, j1):
    """Station retrograde paired with the station direct that ends it, plus
    both shadow edges, for every cycle that overlaps [j0, j1]."""
    st = stations(j0 - LOOKBACK_DAYS, j1 + 60)
    cycles = []
    for i, s in enumerate(st):
        if s["kind"] != "station_rx":
            continue
        end = next((x for x in st[i + 1:] if x["body"] == s["body"] and x["kind"] == "station_d"), None)
        if not end:
            continue
        body = dict(STATION_BODIES)[s["body"]]
        pre = _cross_back(body, s["jd"], end["lon"])
        post = _cross_fwd(body, end["jd"], s["lon"])
        lo, hi = pre or s["jd"], post or end["jd"]
        if hi < j0 or lo > j1:
            continue
        cycles.append({"body": s["body"], "rx": s, "d": end,
                       "pre_shadow_jd": pre, "post_shadow_jd": post})
    return cycles


def lunations(j0, j1):
    out = []
    step = 0.25
    j = j0

    def elong(x):
        return (swe.calc_ut(x, swe.MOON, FLAGS)[0][0] - swe.calc_ut(x, swe.SUN, FLAGS)[0][0]) % 360

    prev = elong(j)
    while j < j1:
        nxt = elong(j + step)
        for target, kind in ((0, "new_moon"), (180, "full_moon")):
            a, b = _wrap(prev - target), _wrap(nxt - target)
            if a < 0 <= b and abs(a - b) < 90:
                t = _bisect(lambda x, tg=target: _wrap(elong(x) - tg), j, j + step)
                out.append({"kind": kind, "body": "Moon", "jd": t, **_where(_lon(t, swe.MOON))})
        j, prev = j + step, nxt
    return out


_SOLAR_TYPES = ((swe.ECL_TOTAL, "total"), (swe.ECL_ANNULAR, "annular"),
                (getattr(swe, "ECL_ANNULAR_TOTAL", 32), "hybrid"), (swe.ECL_PARTIAL, "partial"))
_LUNAR_TYPES = ((swe.ECL_TOTAL, "total"), (swe.ECL_PARTIAL, "partial"),
                (swe.ECL_PENUMBRAL, "penumbral"))


def _etype(flag, table):
    return next((name for bit, name in table if flag & bit), "partial")


def eclipses(j0, j1):
    """Solar and lunar eclipses between two JDs: greatest eclipse, type, and
    where in the zodiac (the Sun's degree for a solar one, the Moon's for a
    lunar one)."""
    out = []
    j = j0
    while True:
        flag, tret = swe.sol_eclipse_when_glob(j, BASE, 0, False)
        t = tret[0]
        if t > j1:
            break
        out.append({"kind": "solar_eclipse", "body": "Sun", "jd": t,
                    "type": _etype(flag, _SOLAR_TYPES), **_where(_lon(t, swe.SUN))})
        j = t + 1
    j = j0
    while True:
        flag, tret = swe.lun_eclipse_when(j, BASE, 0, False)
        t = tret[0]
        if t > j1:
            break
        out.append({"kind": "lunar_eclipse", "body": "Moon", "jd": t,
                    "type": _etype(flag, _LUNAR_TYPES), **_where(_lon(t, swe.MOON))})
        j = t + 1
    return out


def ingresses(j0, j1):
    out = []
    j, prev = j0, int(_lon(j0, swe.SUN) // 30)
    while j < j1:
        nxt = int(_lon(j + 1, swe.SUN) // 30)
        if nxt != prev:
            edge = nxt * 30.0
            t = _bisect(lambda x, e=edge: _wrap(_lon(x, swe.SUN) - e), j, j + 1)
            out.append({"kind": "ingress", "body": "Sun", "jd": t, **_where(edge + 1e-6)})
        j, prev = j + 1, nxt
    return out


TITLES = {
    "station_rx": "{body} turns retrograde", "station_d": "{body} turns direct",
    "solar_eclipse": "{type} solar eclipse", "lunar_eclipse": "{type} lunar eclipse",
    "new_moon": "New moon", "full_moon": "Full moon", "ingress": "The Sun enters {sign}",
}


def _id(e):
    return f"{e['kind']}-{e['body']}-{_ut(e['jd']).strftime('%Y%m%dT%H%M')}"


@functools.lru_cache(maxsize=16)
def sky_events(start_iso, days):
    """
    Every event from 00:00 UT on start_iso for `days` days, plus the retrograde
    cycles that overlap it. The same for every user, so cached per process; the
    argument is a date string so the cache key changes at most once a day.
    """
    s = dt.date.fromisoformat(start_iso)
    j0 = swe.julday(s.year, s.month, s.day, 0.0, swe.GREG_CAL)
    j1 = j0 + days
    cycles = retro_cycles(j0, j1)
    evs = list(stations(j0, j1))
    ecl = eclipses(j0, j1)
    # A solar eclipse IS a new moon (a lunar one a full moon); listing both
    # twice the same day reads like two events. Keep the eclipse, say so.
    for lu in lunations(j0, j1):
        twin = next((e for e in ecl if abs(e["jd"] - lu["jd"]) < 0.5), None)
        if twin:
            twin["lunation"] = lu["kind"].replace("_", " ")
        else:
            evs.append(lu)
    evs += ecl + ingresses(j0, j1)
    for c in cycles:
        for key in ("rx", "d"):
            st = c[key]
            if j0 <= st["jd"] <= j1:
                twin = next((e for e in evs if e["kind"] == st["kind"] and e["body"] == st["body"]
                             and abs(e["jd"] - st["jd"]) < 0.01), None)
                if twin:
                    twin["cycle"] = c
    for e in evs:
        e["id"] = _id(e)
        e["title"] = TITLES[e["kind"]].format(body=e["body"], sign=e["sign"],
                                              type=(e.get("type") or "").capitalize())
    evs.sort(key=lambda e: e["jd"])
    return tuple(evs), tuple(cycles)


# ---------------------------------------------------------- her and her people

def natal_points(chart, time_known):
    """The points a time can be trusted for. Without a birth time the Moon can
    be six degrees off and the angles anywhere, so they are left out rather than
    matched against a 2-degree orb they cannot honestly meet."""
    pts = {k: v["lon"] for k, v in chart["planets"].items() if k != "TrueNode"}
    if time_known:
        pts.update({k: v["lon"] for k, v in chart["angles"].items()})
    else:
        pts.pop("Moon", None)
    return pts


def touches(lon, points, orb=ORB):
    hits = []
    for name, p in points.items():
        for aspect, angle in ASPECT_ANGLES:
            d = abs(abs(_wrap(lon - p)) - angle)
            if d <= orb:
                hits.append({"point": name, "aspect": aspect, "orb": round(d, 2)})
    return sorted(hits, key=lambda h: h["orb"])


def _reflect(e):
    """One optional, calm line: what the event is. A prompt to notice, never a
    forecast. The house travels separately (her_house, house_meaning) so the
    UI can say it once rather than twice."""
    k = e["kind"]
    if k == "station_rx":
        return f"{e['body']} appears to slow and move backward (retrograde). {RETRO_REFLECT[e['body']]}."
    if k == "station_d":
        return f"{e['body']} moves forward again, so what you revisited can move ahead at your pace."
    if k in ("solar_eclipse", "new_moon"):
        return "This is a fresh-start point in the Moon's monthly cycle, if you want one."
    if k in ("lunar_eclipse", "full_moon"):
        return "This is the peak of the Moon's monthly cycle, a natural moment to notice what's come to light."
    return "The Sun moves into a new sign, and a new season begins."


def _ord(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _iso(j):
    return _ut(j).isoformat().replace("+00:00", "Z")


def _local(j, tz):
    return _ut(j).astimezone(tz)


def personalise(e, nat, time_known, people, tz):
    """One event, read against her chart and her people's."""
    house = house_of(e["lon"], nat["house_cusps"]) if time_known else None
    mine = touches(e["lon"], natal_points(nat, time_known))
    who = []
    for p in people:
        hits = touches(e["lon"], p["points"])
        if hits:
            who.append({"cid": p["cid"], "name": p["name"], "points": hits[:3]})
    who.sort(key=lambda w: (-len(w["points"]), w["points"][0]["orb"], w["name"]))
    loc = _local(e["jd"], tz)
    out = {k: e[k] for k in ("id", "kind", "body", "title", "sign", "deg", "label", "lon")}
    out.update({"utc": _iso(e["jd"]), "local": loc.isoformat(), "local_date": loc.date().isoformat(),
                "her_house": house, "house_meaning": HOUSE_MEANING.get(house),
                "touches": mine[:4], "people": who[:3], "reflect": _reflect(e),
                "big": e["kind"] in BIG})
    for k in ("type", "lunation"):
        if e.get(k):
            out[k] = e[k]
    c = e.get("cycle")
    if c:
        out["shadow"] = _shadow(c, tz)
    return out


def _shadow(c, tz):
    def at(j):
        return _iso(j) if j else None
    return {"pre_shadow_start": at(c["pre_shadow_jd"]), "station_rx": at(c["rx"]["jd"]),
            "station_d": at(c["d"]["jd"]), "post_shadow_end": at(c["post_shadow_jd"]),
            "rx_label": c["rx"]["label"], "d_label": c["d"]["label"]}


def active(cycles, now, tz, people_for):
    """What is going on right now: a planet in its shadow or retrograde, with
    which day of how many. `people_for(lon_a, lon_b)` names who it touches."""
    jn = _jd(now)
    out = []
    for c in cycles:
        lo = c["pre_shadow_jd"] or c["rx"]["jd"]
        hi = c["post_shadow_jd"] or c["d"]["jd"]
        if not lo <= jn <= hi:
            continue
        if jn < c["rx"]["jd"]:
            phase, a, b = "pre_shadow", lo, c["rx"]["jd"]
        elif jn <= c["d"]["jd"]:
            phase, a, b = "retrograde", c["rx"]["jd"], c["d"]["jd"]
        else:
            phase, a, b = "post_shadow", c["d"]["jd"], hi
        # Calendar days in her zone, counted inclusively: the station day is day 1.
        d0, d1 = _local(a, tz).date(), _local(b, tz).date()
        day_n = (now.astimezone(tz).date() - d0).days + 1
        total = (d1 - d0).days + 1
        who = people_for(c["rx"]["lon"], c["d"]["lon"])
        out.append({"body": c["body"], "phase": phase, "day": day_n, "of": total,
                    "since": _iso(a), "until": _iso(b), "people": who,
                    "shadow": _shadow(c, tz), "banner": _banner(c["body"], phase, day_n, total, who)})
    return out


# What a retrograde means for her, said first; the planet and the count follow.
RX_FOR_YOU = {"Mercury": "A good week to re-read before you send",
              "Venus": "A good time to rethink what you want in love",
              "Mars": "A time to plan more and push less"}


def _banner(body, phase, day_n, total, who):
    name = who[0]["name"] if who else None
    if phase == "retrograde":
        line = f"{body} is retrograde, day {day_n} of {total}"
        lead = RX_FOR_YOU.get(body)
        if lead and body == "Mercury" and name:
            lead = f"A good week to re-read texts with {name}"
        if lead:
            return f"{lead}. {line}"
        return line + (f", and it touches {name}'s chart" if name else "")
    if phase == "pre_shadow":
        return f"{body} is slowing toward its retrograde (the pre-shadow), day {day_n} of {total}"
    return f"{body} is retracing ground after its retrograde (the post-shadow), day {day_n} of {total}"


def signature(*parts):
    """A short hash of whatever the personal part depends on, so a new birth
    time or a new person simply misses the cache instead of needing to be
    remembered by every write path."""
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:12]


def lint(text):
    low = (text or "").lower()
    return [b for b in BANNED if b in low]


def strings(payload):
    """Every human-readable string in a payload, for the copy lint."""
    out = []
    for e in (payload.get("events") or []) + (payload.get("active") or []):
        out += [e.get("title", ""), e.get("reflect", ""), e.get("banner", "")]
    if payload.get("banner"):
        out.append(payload["banner"].get("text", ""))
    return [s for s in out if s]

