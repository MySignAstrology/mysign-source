"""Minimal natal + transit engine. Swiss Ephemeris — see engine/ephemeris.py for
which data it runs on."""
import json
import sys

import swisseph as swe

from engine import tradition
from engine.ephemeris import FLAGS

SIGNS = ["Aries","Taurus","Gemini","Cancer","Leo","Virgo",
         "Libra","Scorpio","Sagittarius","Capricorn","Aquarius","Pisces"]

BODIES = [("Sun",swe.SUN),("Moon",swe.MOON),("Mercury",swe.MERCURY),("Venus",swe.VENUS),
          ("Mars",swe.MARS),("Jupiter",swe.JUPITER),("Saturn",swe.SATURN),
          ("Uranus",swe.URANUS),("Neptune",swe.NEPTUNE),("Pluto",swe.PLUTO),
          ("TrueNode",swe.TRUE_NODE),("Chiron",swe.CHIRON)]

ASPECTS = [("conjunction",0,3),("sextile",60,3),("square",90,3),
           ("trine",120,3),("opposition",180,3)]

def jd_ut(y,m,d,h_ut):
    return swe.julday(y,m,d,h_ut,swe.GREG_CAL)

def local_noon_jd(day, tz=None):
    """
    Julian day of midday on `day` where she is. "Today's sky" was a fixed
    17:00 UT — 1am the next morning in Manila, with the Moon some seven degrees
    from where it is at her midday, against a three-degree orb.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo
    try:
        zone = ZoneInfo(tz) if tz else _dt.timezone.utc
    except Exception:
        zone = _dt.timezone.utc
    u = _dt.datetime(day.year, day.month, day.day, 12, tzinfo=zone).astimezone(_dt.timezone.utc)
    return jd_ut(u.year, u.month, u.day, u.hour + u.minute / 60)


def fmt(lon):
    # Truncated to the arcminute, the convention for a reason: rounding the
    # minutes alone printed "29°60' Taurus", and rounding the whole position
    # would print a planet still in Taurus as "0°00' Gemini".
    lon = lon % 360
    s = int(lon // 30)
    d, m = divmod(int((lon - s * 30) * 60), 60)
    return {"sign": SIGNS[s], "deg": round(min(lon - s * 30, 29.99), 2),
            "label": f"{d}°{m:02d}' {SIGNS[s]}"}

def positions(jd):
    out = {}
    for name, code in BODIES:
        try:
            xx, _ = swe.calc_ut(jd, code, FLAGS)
        except Exception:
            continue
        p = fmt(xx[0]); p["lon"] = round(xx[0],4); p["speed"] = round(xx[3],4)
        # The mean node always moves backwards and the true node nearly always
        # does, so "Rx" on it is its ordinary state, not news. Never flag it.
        p["retrograde"] = xx[3] < 0 and name != "TrueNode"
        out[name] = p
    return out

def house_of(lon, cusps):
    for i in range(12):
        a = cusps[i]; b = cusps[(i+1) % 12]
        if a <= b:
            if a <= lon < b: return i+1
        else:
            if lon >= a or lon < b: return i+1
    return None

# What the polar fallback is called wherever the house system is named. Saying
# "Placidus" over Porphyry cusps was the app describing a chart it had not drawn.
POLAR_FALLBACK = {
    "system": "porphyry", "label": "Porphyry",
    "why": "Placidus and Koch houses cannot be drawn inside the polar circles — "
           "some degrees of the zodiac never rise there — so this chart's houses "
           "use Porphyry, which is defined at every latitude and keeps the same "
           "Ascendant and Midheaven.",
}


def natal(y,m,d,h_ut,lat,lon_geo,houses=None):
    jd = jd_ut(y,m,d,h_ut)
    asked = tradition.house_system(houses)
    used = {"system": asked, "label": tradition.HOUSE_SYSTEMS[asked]["label"],
            "fallback": False}
    try:
        cusps, ascmc = swe.houses(jd, lat, lon_geo, tradition.house_code(houses))
    except Exception:
        # Placidus and Koch are undefined inside the polar circles — someone
        # born in Tromsø got a 500 at signup. Porphyry is defined everywhere
        # and uses the same Ascendant and Midheaven. It is said, not hidden:
        # every surface that names the house system reads `houses_used`.
        cusps, ascmc = swe.houses(jd, lat, lon_geo, b"O")
        used = {**POLAR_FALLBACK, "requested": asked, "fallback": True}
    pos = positions(jd)
    for k,v in pos.items():
        v["house"] = house_of(v["lon"], cusps)
    angles = {"Ascendant": {**fmt(ascmc[0]), "lon": round(ascmc[0],4)},
              "Midheaven": {**fmt(ascmc[1]), "lon": round(ascmc[1],4)}}
    return {"jd": jd, "planets": pos, "angles": angles,
            "house_cusps": [round(c,4) for c in cusps], "houses_used": used}


def angles_only(jd, lat, lon_geo):
    """(Ascendant, Midheaven) longitudes. Neither depends on the house system,
    so this asks for Porphyry, which exists at every latitude: asking for
    Placidus here is how a Tromsø birth 500'd /api/rectify."""
    _, ascmc = swe.houses(jd, lat, lon_geo, b"O")
    return ascmc[0], ascmc[1]


def with_time(chart, time_known):
    """
    Mark a chart with whether its birth time is real.

    Charts for an unknown time are drawn at noon, and their angles and houses
    are fiction: Today used to headline "Moon square your Midheaven · 0.1°" for
    someone with no Midheaven. Carrying the fact on the chart itself means every
    consumer — aspects(), the sky, the tools, the salience walk — gets it
    without each caller having to remember to pass it.
    """
    if chart is not None:
        chart["time_known"] = bool(time_known)
    return chart


def natal_targets(natal_chart, time_known=None):
    """
    The natal points a transit can honestly be measured against.

    Without a birth time the angles could be anywhere and the Moon is only
    known to about six degrees either way (it moves ~13° a day), so a 0.1° orb
    to either is invented precision. They are left out — the same rule as
    engine/skyevents.natal_points.
    """
    if time_known is None:
        time_known = natal_chart.get("time_known", True)
    pts = {k: v["lon"] for k, v in natal_chart["planets"].items()}
    if time_known:
        pts.update({k: v["lon"] for k, v in natal_chart["angles"].items()})
    else:
        pts.pop("Moon", None)
    return pts


# The words a person reads for an engine body. "TrueNode" is a Swiss Ephemeris
# identifier; it reached Today as "TrueNode conjunction your Sun".
PLAIN = {"TrueNode": "North Node"}


def plain(name):
    return PLAIN.get(name, name)

def _orb(lon, target, angle):
    diff = abs(lon - target) % 360
    if diff > 180: diff = 360 - diff
    return abs(diff - angle)

def aspects(transit_pos, natal_chart, time_known=None):
    """
    Transits to natal points, each marked applying or separating.

    Whether a contact is building or fading is the first thing an astrologer
    reads off an aspect, and it was hard-coded None. The natal point is fixed, so
    the transiting body's own speed decides it: step it forward an hour and see
    whether the orb closes. That uses the real speed, so a retrograde planet
    backing onto a point is correctly applying.

    Only the points the birth time supports are targets (natal_targets):
    `time_known` defaults to the chart's own flag (with_time).
    """
    natal_pts = natal_targets(natal_chart, time_known)
    found = []
    for tname, tp in transit_pos.items():
        for nname, nlon in natal_pts.items():
            for aname, angle, orb in ASPECTS:
                delta = _orb(tp["lon"], nlon, angle)
                if delta <= orb:
                    later = _orb(tp["lon"] + tp.get("speed", 0) / 24, nlon, angle)
                    found.append({"transit": tname, "aspect": aname, "natal": nname,
                                  "orb": round(delta,2),
                                  "applying": later < delta,
                                  "retrograde": tp["retrograde"]})
    return sorted(found, key=lambda a: a["orb"])

# The named moments of the lunar month are moments, not quarters of it. With
# eight equal 45° bands "full moon" was claimed for about 3.4 days running; the
# Moon gains ~12.2° a day on the Sun, so ±6° is about half a day either side.
PHASE_EXACT = 6.0


def phase_name(ang):
    """The phase label for a Moon-minus-Sun elongation in degrees."""
    ang %= 360
    for centre, label in ((0, "new moon"), (90, "first quarter"),
                          (180, "full moon"), (270, "last quarter"), (360, "new moon")):
        if abs(ang - centre) <= PHASE_EXACT:
            return label
    if ang < 90:
        return "waxing crescent"
    if ang < 180:
        return "waxing gibbous"
    if ang < 270:
        return "waning gibbous"
    return "waning crescent"


def moon_phase(jd):
    sun = swe.calc_ut(jd, swe.SUN, FLAGS)[0][0]
    moon = swe.calc_ut(jd, swe.MOON, FLAGS)[0][0]
    ang = (moon - sun) % 360
    return {"elongation_deg": round(ang,2), "phase": phase_name(ang),
            "illumination_pct": round((1 - __import__("math").cos(__import__("math").radians(ang)))/2*100, 1)}

def _elong(jd):
    return (swe.calc_ut(jd, swe.MOON, FLAGS)[0][0] - swe.calc_ut(jd, swe.SUN, FLAGS)[0][0]) % 360


def day_phase(day, tz=None):
    """
    The Moon's phase for her local calendar day, the way a calendar names it.

    A named phase is a moment. Labelled at local noon with a ±6° band, a full
    Moon at 00:02 in Honolulu was "waning gibbous" on its own date, and one at
    23:02 in Tonga "waxing gibbous". Here the day is named for the exact
    phase when that moment falls between her midnights; any other day gets the
    in-between name. Illumination and elongation are still her local noon's.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo
    if isinstance(tz, str) or tz is None:
        try:
            tz = ZoneInfo(tz) if tz else _dt.timezone.utc
        except Exception:
            tz = _dt.timezone.utc

    def jd_at(d):
        u = _dt.datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(_dt.timezone.utc)
        return jd_ut(u.year, u.month, u.day, u.hour + u.minute / 60 + u.second / 3600)

    start, end = jd_at(day), jd_at(day + _dt.timedelta(days=1))
    noon = start + (end - start) / 2
    out = moon_phase(noon)
    a, b = _elong(start), _elong(end)
    span = (b - a) % 360                      # ~12°; the Moon never laps a quarter in a day
    named = None
    for centre, label in ((0, "new moon"), (90, "first quarter"),
                          (180, "full moon"), (270, "last quarter")):
        if (centre - a) % 360 < span:
            named = label
    if named is None:
        ang = out["elongation_deg"]
        named = ("waxing crescent" if ang < 90 else "waxing gibbous" if ang < 180
                 else "waning gibbous" if ang < 270 else "waning crescent")
    out["phase"] = named
    return out


if __name__ == "__main__":
    with open(sys.argv[1]) as fh:
        cfg = json.load(fh)
    n = natal(*cfg["natal"])
    tjd = jd_ut(*cfg["transit"])
    tpos = positions(tjd)
    for k,v in tpos.items():
        v["natal_house"] = house_of(v["lon"], n["house_cusps"])
    print(json.dumps({
        "natal": n, "transits": tpos,
        "aspects_to_natal": aspects(tpos, n),
        "moon": moon_phase(tjd),
    }, indent=1))
