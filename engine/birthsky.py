"""
The sky the moment she was born, as an astronomer would describe it.

The natal chart is a diagram. This is the sky itself: whether the Sun had set,
which planets were up in the dark over her birthplace, whether Venus was the
morning star or the evening star, the eclipse that came before her, the Moon's
distance that night. Every fact here is computed by Swiss Ephemeris from the
same data the accuracy page checks against NASA JPL, and the eclipse dates are
tested against NASA's eclipse catalogue (tests/test_birthsky.py).

That makes it the most defensible material in the reading — no interpretation
is needed for "Jupiter was rising in the east when you were born" to be true —
and the most personal, because nobody else born that day saw that exact sky
from that exact place.

Interpretation of these facts lives in engine/tradition_notes.py, each with its
public-domain source. This module only measures.
"""
import datetime as dt
import math
from zoneinfo import ZoneInfo

import swisseph as swe

from engine.ephemeris import BASE, FLAGS

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra",
         "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]

VISIBLE = [("Moon", swe.MOON), ("Mercury", swe.MERCURY), ("Venus", swe.VENUS),
           ("Mars", swe.MARS), ("Jupiter", swe.JUPITER), ("Saturn", swe.SATURN)]
STATIONING = [("Mercury", swe.MERCURY), ("Venus", swe.VENUS), ("Mars", swe.MARS),
              ("Jupiter", swe.JUPITER), ("Saturn", swe.SATURN), ("Uranus", swe.URANUS),
              ("Neptune", swe.NEPTUNE), ("Pluto", swe.PLUTO)]

# Chaldean order, slowest to fastest — the sequence the planetary hours cycle in.
CHALDEAN = ["Saturn", "Jupiter", "Mars", "Sun", "Venus", "Mercury", "Moon"]
# Python's weekday(): Monday is 0.
DAY_RULER = ["Moon", "Mars", "Mercury", "Jupiter", "Venus", "Saturn", "Sun"]

# Bright stars with a long astrological record. Positions come from the Swiss
# Ephemeris star catalogue (engine/ephe/sefstars.txt, Hipparcos-based), precessed
# to the birth date — a star drifts about a degree every 72 years, which is why
# a 1990 table is already wrong for a 2005 birth.
STARS = ["Regulus", "Spica", "Antares", "Aldebaran", "Fomalhaut", "Algol", "Sirius",
         "Arcturus", "Vega", "Pollux", "Castor", "Betelgeuse", "Rigel", "Deneb Algedi",
         "Alcyone", "Capella", "Procyon"]
# Achernar is left out on purpose: which Eridanus star Ptolemy meant is disputed.
STAR_ORB = 1.0

MOON_MEAN_KM = 384_400
AU_KM = 149_597_870.7
LIGHT_KM_S = 299_792.458


def _sign(lon):
    lon %= 360
    s = int(lon // 30)
    deg = lon - 30 * s
    # Round once, in minutes: rounding the fraction alone turned 4°59.7' into 4°00'.
    d, mins = divmod(min(round(deg * 60), 30 * 60 - 1), 60)
    return {"sign": SIGNS[s], "deg": round(deg, 2), "label": f"{d}°{mins:02d}' {SIGNS[s]}"}


def _sep(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _lon(jd, body, flags=FLAGS):
    return swe.calc_ut(jd, body, flags)[0]


def _local(jd, tz):
    y, m, d, h = swe.revjul(jd)
    t = dt.datetime(y, m, d, tzinfo=dt.timezone.utc) + dt.timedelta(hours=h)
    return t.astimezone(ZoneInfo(tz)) if tz else t


def _hhmm(t):
    return t.strftime("%H:%M")


def _rise_set(jd_start, geo, which):
    flag = (swe.CALC_RISE if which == "rise" else swe.CALC_SET) | swe.BIT_DISC_CENTER
    res, tret = swe.rise_trans(jd_start, swe.SUN, flag, geo, 0.0, 0.0, BASE)
    return tret[0] if res == 0 else None


def _alt(jd, geo, lon_lat_dist):
    _az, true_alt, _app = swe.azalt(jd, swe.ECL2HOR, geo, 0.0, 0.0, lon_lat_dist)
    return true_alt


# --------------------------------------------------------------- sun & sect

def sun_and_hours(jd, lat, lon, tz):
    """Sunrise, sunset, day or night, and the planetary day and hour.

    The planetary day runs sunrise to sunrise, not midnight to midnight, so a
    04:00 birth on a Tuesday belongs to Monday's planetary day. Each half of
    the day is split into twelve equal hours — long summer hours, short winter
    ones — ruled in Chaldean order starting from the day's ruler at sunrise.
    """
    geo = (lon, lat, 0.0)
    rise = _rise_set(jd - 1.0, geo, "rise")
    # The last sunrise at or before birth.
    while rise is not None:
        nxt = _rise_set(rise + 0.01, geo, "rise")
        if nxt is None or nxt > jd:
            break
        rise = nxt
    if rise is None:
        return {"polar": True}      # midnight sun or polar night: no hours exist
    sset = _rise_set(rise + 0.01, geo, "set")
    next_rise = _rise_set(rise + 0.01, geo, "rise")
    if sset is None or next_rise is None:
        return {"polar": True}

    sun_pos = _lon(jd, swe.SUN)
    sun_alt = _alt(jd, geo, sun_pos[:3])
    day_birth = jd < sset
    if day_birth:
        length = (sset - rise) / 12
        n = int((jd - rise) / length)
    else:
        length = (next_rise - sset) / 12
        n = 12 + int((jd - sset) / length)
    n = max(0, min(23, n))

    rise_local = _local(rise, tz)
    day_ruler = DAY_RULER[rise_local.weekday()]
    start = CHALDEAN.index(day_ruler)
    hour_ruler = CHALDEAN[(start + n) % 7]

    if sun_alt > 0:
        light = "daylight"
    elif sun_alt > -6:
        light = "civil twilight"
    elif sun_alt > -12:
        light = "nautical twilight"
    elif sun_alt > -18:
        light = "astronomical twilight"
    else:
        light = "full dark"

    return {
        "sunrise": _hhmm(rise_local), "sunset": _hhmm(_local(sset, tz)),
        "day_length_h": round((sset - rise) * 24, 2),
        "sun_altitude": round(sun_alt, 1), "light": light,
        "sect": "day" if day_birth else "night",
        "planetary_day": day_ruler, "planetary_hour": hour_ruler,
        "hour_number": n % 12 + 1, "hour_of": "day" if day_birth else "night",
        "hour_minutes": round(length * 24 * 60, 1),
    }


# --------------------------------------------------------------- the moon

PHASES = [(0, "new moon"), (45, "waxing crescent"), (90, "first quarter"),
          (135, "waxing gibbous"), (180, "full moon"), (225, "waning gibbous"),
          (270, "last quarter"), (315, "waning crescent")]


def moon(jd):
    sun = _lon(jd, swe.SUN)[0]
    m = _lon(jd, swe.MOON)
    elong = (m[0] - sun) % 360
    phase = PHASES[int(((elong + 22.5) % 360) // 45)][1]
    illum = (1 - math.cos(math.radians(elong))) / 2 * 100
    # Days since the last new moon, found by stepping back on the elongation.
    age = elong / 12.19
    dist_km = m[2] * AU_KM
    return {"phase": phase, "elongation": round(elong, 1),
            "illumination_pct": round(illum, 1), "waxing": elong < 180,
            "age_days": round(age, 1), "distance_km": round(dist_km),
            "vs_average_pct": round((dist_km - MOON_MEAN_KM) / MOON_MEAN_KM * 100, 1),
            "light_seconds": round(dist_km / LIGHT_KM_S, 2)}


# ------------------------------------------------------ venus & mercury

def morning_evening(jd):
    """Venus and Mercury are either ahead of the Sun (rising before it: the
    morning star) or behind it (setting after it: the evening star). Close to
    the Sun they are lost in its glare — combust, in the old language."""
    sun = _lon(jd, swe.SUN)[0]
    out = {}
    for name, code in (("Venus", swe.VENUS), ("Mercury", swe.MERCURY)):
        lon = _lon(jd, code)[0]
        signed = (lon - sun + 180) % 360 - 180      # + east of the Sun = evening
        mag = swe.pheno_ut(jd, code, BASE)[4]
        hidden = abs(signed) < (10 if name == "Venus" else 12)
        out[name] = {"elongation": round(signed, 1),
                     "star": "evening star" if signed > 0 else "morning star",
                     "position": "occidental" if signed > 0 else "oriental",
                     "in_suns_glare": hidden, "magnitude": round(mag, 1)}
    return out


# --------------------------------------------------------- the sky that night

def overhead(jd, lat, lon):
    """Which bodies were above the horizon, and which of those she could have
    seen: the Sun well down and the planet up."""
    geo = (lon, lat, 0.0)
    sun_alt = _alt(jd, geo, _lon(jd, swe.SUN)[:3])
    dark = sun_alt < -6
    up, seen = [], []
    for name, code in VISIBLE:
        p = _lon(jd, code)
        a = _alt(jd, geo, p[:3])
        if a > 0:
            up.append({"body": name, "altitude": round(a, 1)})
            if dark or (name == "Moon" and sun_alt < 0) or (name == "Venus" and sun_alt < -3):
                seen.append(name)
    return {"above_horizon": up, "visible": seen, "sky_dark": dark}


# ------------------------------------------------------------- eclipses

_SOLAR = [(swe.ECL_TOTAL, "total"), (swe.ECL_ANNULAR, "annular"),
          (swe.ECL_ANNULAR_TOTAL, "hybrid"), (swe.ECL_PARTIAL, "partial")]
_LUNAR = [(swe.ECL_TOTAL, "total"), (swe.ECL_PARTIAL, "partial"),
          (swe.ECL_PENUMBRAL, "penumbral")]


def _kind(flag, table):
    return next((name for bit, name in table if flag & bit), "eclipse")


def eclipses(jd):
    """The solar and lunar eclipse before birth, and the next of each after."""
    out = {}
    for label, back in (("before", True), ("after", False)):
        f, t = swe.sol_eclipse_when_glob(jd, BASE, 0, back)
        sun = _lon(t[0], swe.SUN)[0]
        out[f"solar_{label}"] = {"jd": t[0], "date": _local(t[0], None).date().isoformat(),
                                 "type": _kind(f, _SOLAR), **_sign(sun),
                                 "days": round(abs(jd - t[0]))}
        f, t = swe.lun_eclipse_when(jd, BASE, 0, back)
        moon_lon = _lon(t[0], swe.MOON)[0]
        out[f"lunar_{label}"] = {"jd": t[0], "date": _local(t[0], None).date().isoformat(),
                                 "type": _kind(f, _LUNAR), **_sign(moon_lon),
                                 "days": round(abs(jd - t[0]))}
    return out


def prenatal_syzygy(jd):
    """The last new or full moon before birth — the lunation Ptolemy looks to."""
    j, prev = jd, None
    while j > jd - 31:
        e = (_lon(j, swe.MOON)[0] - _lon(j, swe.SUN)[0]) % 360
        if prev is not None:
            for target, kind in ((0, "new moon"), (180, "full moon")):
                a, b = (e - target) % 360, (prev - target) % 360
                # stepping backwards: elongation shrinks toward the target
                if b < 20 and a > 340:
                    lo, hi = j, j + 0.25
                    for _ in range(30):
                        mid = (lo + hi) / 2
                        em = (_lon(mid, swe.MOON)[0] - _lon(mid, swe.SUN)[0] - target) % 360
                        if em > 180:
                            lo = mid
                        else:
                            hi = mid
                    lon = _lon(hi, swe.MOON)[0]
                    return {"kind": kind, "date": _local(hi, None).date().isoformat(),
                            "days_before": round(jd - hi, 1), **_sign(lon)}
        prev = e
        j -= 0.25
    return None


# ------------------------------------------------------------- stations

def stations(jd, window=None):
    """Planets within a few days of standing still — turning retrograde or
    direct. A stationary planet barely moves for days, which is why the old
    texts single it out."""
    out = []
    for name, code in STATIONING:
        w = window or (3 if name in ("Mercury", "Venus", "Mars") else 7)
        speeds = [(k, _lon(jd + k, code)[3]) for k in range(-w, w + 1)]
        for (k1, s1), (k2, s2) in zip(speeds, speeds[1:]):
            if (s1 > 0) != (s2 > 0):
                out.append({"planet": name,
                            "turning": "retrograde" if s1 > 0 else "direct",
                            "days_from_birth": k2 if abs(k2) < abs(k1) else k1})
                break
    return out


# ---------------------------------------------------------- fixed stars

def stars(jd, natal_chart, time_known):
    pts = {k: v["lon"] for k, v in natal_chart["planets"].items()
           if k in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn")}
    if time_known:
        pts.update({k: v["lon"] for k, v in natal_chart["angles"].items()})
    hits = []
    for star in STARS:
        try:
            xx, _name, _f = swe.fixstar2_ut(star, jd, BASE)
            mag = swe.fixstar2_mag(star)[0]
        except Exception:
            continue
        for point, plon in pts.items():
            orb = _sep(xx[0], plon)
            if orb <= STAR_ORB:
                hits.append({"star": star, "point": point, "orb": round(orb, 2),
                             "magnitude": round(mag, 2), **_sign(xx[0])})
    return sorted(hits, key=lambda h: h["orb"])


# -------------------------------------------------------------- lots

def lots(natal_chart, sect=None):
    """The Lot of Fortune as Ptolemy gives it (Tetrabiblos III.13) and Lilly
    follows (Christian Astrology pp. 143–144): the Sun-to-Moon arc laid off
    from the ascendant, the same by day and by night. Later Hellenistic
    authors reverse it at night; Ptolemy explicitly does not, and he is the
    source we cite. The Lot of Spirit is left out — we found no public-domain
    text that states it cleanly."""
    asc = natal_chart["angles"]["Ascendant"]["lon"]
    sun = natal_chart["planets"]["Sun"]["lon"]
    mon = natal_chart["planets"]["Moon"]["lon"]
    fortune = (asc + mon - sun) % 360
    return {"Fortune": {**_sign(fortune), "lon": round(fortune, 4)}}


# ------------------------------------------------------------ distances

def distances(jd):
    out = {}
    for name, code in (("Sun", swe.SUN), ("Jupiter", swe.JUPITER), ("Saturn", swe.SATURN)):
        au = _lon(jd, code)[2]
        out[name] = {"au": round(au, 4), "km": round(au * AU_KM),
                     "light_minutes": round(au * AU_KM / LIGHT_KM_S / 60, 1)}
    return out


# ---------------------------------------------------------------- all

def compute(birth_ut, natal_chart, time_known=True, tz=None):
    """Everything above for one birth. birth_ut is the profile's
    [y, m, d, hour_ut, lat, lon]."""
    y, m, d, h, lat, lon = birth_ut
    jd = swe.julday(y, m, d, h, swe.GREG_CAL)
    out = {"moon": moon(jd), "venus_mercury": morning_evening(jd),
           "eclipses": eclipses(jd), "syzygy": prenatal_syzygy(jd),
           "stations": stations(jd), "stars": stars(jd, natal_chart, time_known),
           "distances": distances(jd), "time_known": time_known}
    if time_known:
        sun = sun_and_hours(jd, lat, lon, tz)
        out["sun"] = sun
        out["overhead"] = overhead(jd, lat, lon)
        if not sun.get("polar"):
            out["lots"] = lots(natal_chart, sun["sect"])
    return out


# ------------------------------------------------------------ in words

def _fmt_gap(minutes):
    h, m = divmod(round(abs(minutes)), 60)
    return (f"{h} hour{'s' if h != 1 else ''} and {m} minutes" if h
            else f"{m} minutes")


def _and(items):
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _a(word):
    return f"an {word}" if word[0] in "aeiou" else f"a {word}"


def _days(n):
    return "the same day" if n == 0 else f"{n} day{'s' if n != 1 else ''}"


def describe(f):
    """The facts as short items for the reading. Astronomy only — nothing
    here needs a tradition to be true. Each item says which measurement it
    rests on, so the page can show it."""
    out = []
    sun = f.get("sun") or {}
    if sun and not sun.get("polar"):
        alt = sun["sun_altitude"]
        if sun["light"] == "daylight":
            body = (f"The Sun was {alt:.0f}° above the horizon. Sunrise had been at "
                    f"{sun['sunrise']}, sunset would come at {sun['sunset']} — a day of "
                    f"{sun['day_length_h']:.1f} hours.")
            title = "You were born in daylight"
        else:
            title = {"full dark": "You were born in full dark",
                     "astronomical twilight": "You were born in the last of the dark",
                     "nautical twilight": "You were born in deep twilight",
                     "civil twilight": "You were born at twilight"}[sun["light"]]
            body = (f"The Sun was {abs(alt):.0f}° below the horizon. "
                    f"That day it rose at {sun['sunrise']} and set at {sun['sunset']}.")
        out.append({"kind": "light", "title": title, "body": body})

    over = f.get("overhead") or {}
    if over.get("visible"):
        names = [n for n in over["visible"] if n != "Moon"]
        moonup = "Moon" in over["visible"]
        alts = {u["body"]: u["altitude"] for u in over["above_horizon"]}
        parts = [f"{n} {alts[n]:.0f}° up" for n in names]
        if moonup:
            parts.append(f"the Moon ({f['moon']['illumination_pct']:.0f}% lit) {alts['Moon']:.0f}° up")
        out.append({"kind": "overhead", "title": "What was in your sky",
                    "body": f"Looking up from where you were born, you could have seen {_and(parts)}."})
    elif over.get("above_horizon"):
        names = ["the Moon" if u["body"] == "Moon" else u["body"] for u in over["above_horizon"]]
        out.append({"kind": "overhead", "title": "Above the horizon, behind the daylight",
                    "body": f"{_and(names)[0].upper() + _and(names)[1:]} {'was' if len(names) == 1 else 'were'} "
                            "above the horizon when you were born, washed out by the Sun."})

    m = f["moon"]
    closer = "closer" if m["vs_average_pct"] < 0 else "farther"
    out.append({"kind": "moon", "title": f"A {m['phase']} Moon, {m['illumination_pct']:.0f}% lit",
                "body": f"The Moon was {m['distance_km']:,} km away — {abs(m['vs_average_pct']):.1f}% "
                        f"{closer} than average. Its light took {m['light_seconds']} seconds to reach you."})

    v = f["venus_mercury"]["Venus"]
    if v["in_suns_glare"]:
        out.append({"kind": "venus", "title": "Venus was hidden in the Sun's glare",
                    "body": f"It was only {abs(v['elongation']):.0f}° from the Sun, too close to see, "
                            "between its morning and evening appearances."})
    else:
        when = "after sunset in the west" if v["star"] == "evening star" else "before dawn in the east"
        out.append({"kind": "venus", "title": f"Venus was the {v['star']}",
                    "body": f"It was {abs(v['elongation']):.0f}° from the Sun, shining at magnitude "
                            f"{str(v['magnitude']).replace('-', '−')} {when} — the brightest thing in the sky after the Moon."})

    e = f["eclipses"]
    sb, lb = e["solar_before"], e["lunar_before"]
    first = sb if sb["days"] <= lb["days"] else lb
    which = "solar" if first is sb else "lunar"
    out.append({"kind": "eclipse", "title": "The eclipse before you",
                "body": (f"{_days(first['days']).capitalize()} before you were born" if first["days"]
                         else "On the day you were born") + f", there was {_a(first['type'])} "
                        f"{which} eclipse at {first['label']}. The next came {_days(min(e['solar_after']['days'], e['lunar_after']['days']))} after."})

    for s in f["stations"][:2]:
        d = s["days_from_birth"]
        when = "the day you were born" if d == 0 else (
            f"{abs(d)} day{'s' if abs(d) != 1 else ''} {'after' if d > 0 else 'before'} you were born")
        out.append({"kind": "station", "title": f"{s['planet']} was standing still",
                    "body": f"{s['planet']} stopped and turned {s['turning']} {when}. "
                            "For a few days around a station a planet barely moves against the stars."})

    sat = f["distances"]["Saturn"]
    out.append({"kind": "distance", "title": "Light from Saturn",
                "body": f"Saturn was {sat['km']:,} km away. Light leaving it as you were born "
                        f"arrived {_fmt_gap(sat['light_minutes'])} later."})
    return out
