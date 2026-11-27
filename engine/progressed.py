"""
Secondary progressions: where she is in her own life, not just where she started.

The rule is the oldest timing method still in daily use: one day after birth
stands for one year of life. The chart drawn for "age in years" days after the
birth moment is the progressed chart. Every position here is Swiss Ephemeris,
read at that progressed instant; nothing is estimated.

The progressed Moon is the part people feel. It moves about 13° a progressed
day, so it changes sign roughly every two and a half years and walks the whole
zodiac in about 27. Its angle to the progressed Sun gives a slow lunation cycle
of about 29.5 years, eight phases of ~3.7 years each.

Copy rules, the same as everywhere else: the tradition's reading of a placement
is said as the tradition's reading, never as a fact about her, and nothing here
predicts an event.

With no birth time the natal Moon is uncertain by up to ±6.6°, which is about
half a year of progressed movement. So the window is sampled (compat.samples's
±12h) and a sign is named only when every sample agrees; dates become ranges.
"""
import datetime as dt

import swisseph as swe

from engine.chart import SIGNS, house_of, jd_ut
from engine.ephemeris import FLAGS

YEAR = 365.2422       # one progressed day per tropical year of life

# Hours either side of the recorded time that the birth could be.
WINDOW = {"exact": (0.0,), "approximate": (-1.0, 0.0, 1.0),
          "unknown": (-12.0, -6.0, 0.0, 6.0, 12.0)}

# Rudhyar's progressed lunation phases, each 45° of Moon-minus-Sun.
PHASES = [
    ("new", "New phase",
     "the tradition's seed time: something starts before you can name it, and "
     "acting on instinct counts for more than having a plan"),
    ("crescent", "Crescent phase",
     "the tradition's push-off: what began a few years ago meets resistance, "
     "and you keep it going on purpose"),
    ("first_quarter", "First quarter phase",
     "the tradition's crisis in action: a time for building, choosing and "
     "clearing out what no longer fits"),
    ("gibbous", "Gibbous phase",
     "the tradition's refining stretch: less starting, more improving what "
     "you have and getting it right"),
    ("full", "Full phase",
     "the tradition's culmination: what you built becomes visible, and "
     "relationships show you yourself"),
    ("disseminating", "Disseminating phase",
     "the tradition's sharing years: what you learned wants an audience, "
     "and you hand it on"),
    ("last_quarter", "Last quarter phase",
     "the tradition's turning point: beliefs get revised and some old "
     "structures are let go"),
    ("balsamic", "Balsamic phase",
     "the tradition's closing years: quieter, more inward, clearing ground "
     "for the next new phase"),
]

# How the tradition reads the progressed Moon in each sign: where emotional
# attention tends to go for these two and a half years. Themes, not traits.
MOON_SEASON = {
    "Aries": "starting things, independence, and wanting to move first",
    "Taurus": "steadiness, comfort, money and the body",
    "Gemini": "learning, talking, siblings and a busier mind",
    "Cancer": "home, family and what makes you feel safe",
    "Leo": "being seen, creating, romance and play",
    "Virgo": "routines, health, work and getting things right",
    "Libra": "partnership, fairness and the people beside you",
    "Scorpio": "depth, intimacy, shared resources and what you keep private",
    "Sagittarius": "travel, study, belief and room to roam",
    "Capricorn": "ambition, responsibility and building something that holds",
    "Aquarius": "friends, groups, the future and doing it your own way",
    "Pisces": "rest, imagination, compassion and letting go",
}


def _lon(jd, body):
    return swe.calc_ut(jd, body, FLAGS)[0][0]


def _sign(lon):
    return SIGNS[int(lon % 360 // 30)]


def _real(birth_jd, pjd):
    """A progressed instant back to the calendar date it stands for."""
    return birth_jd + (pjd - birth_jd) * YEAR


def _date(jd):
    y, m, d, _ = swe.revjul(jd, swe.GREG_CAL)
    return dt.date(y, m, d)


def _cross(f, lo, hi, steps=40):
    """Bisect the first instant in [lo, hi] where f flips from False to True."""
    for _ in range(steps):
        mid = (lo + hi) / 2
        if f(mid):
            hi = mid
        else:
            lo = mid
    return hi


def _one(birth_jd, today_jd, cusps=None):
    pjd = birth_jd + (today_jd - birth_jd) / YEAR
    moon, sun = _lon(pjd, swe.MOON), _lon(pjd, swe.SUN)
    msign = _sign(moon)

    # Sign entry and exit. The progressed Moon is never retrograde, so the last
    # boundary behind it and the next ahead are found by stepping its longitude.
    idx = SIGNS.index(msign)
    back, ahead = pjd, pjd
    while _sign(_lon(back, swe.MOON)) == msign and pjd - back < 5:
        back -= 0.1
    entered = _cross(lambda j: _sign(_lon(j, swe.MOON)) == msign, back, back + 0.1) \
        if back > birth_jd else None
    while _sign(_lon(ahead, swe.MOON)) == msign and ahead - pjd < 5:
        ahead += 0.1
    leaves = _cross(lambda j: _sign(_lon(j, swe.MOON)) != msign, ahead - 0.1, ahead)

    elong = (moon - sun) % 360
    phase = int(elong // 45)
    # The phase began when the elongation last crossed phase*45°. ~12°/day.
    start_angle = phase * 45

    def past(j):
        e = (_lon(j, swe.MOON) - _lon(j, swe.SUN)) % 360
        return (e - start_angle) % 360 < 180
    lo = pjd - 4.6       # Moon gains 10.8–15°/day on the Sun: always > 45° back
    phase_start = _cross(past, lo, pjd) if lo > birth_jd else None

    nat_sun = _lon(birth_jd, swe.SUN)
    return {
        "pjd": pjd,
        "moon_lon": moon, "moon_sign": msign, "moon_idx": idx,
        "moon_house": house_of(moon, cusps) if cusps else None,
        "entered": _date(_real(birth_jd, entered)) if entered else None,
        "leaves": _date(_real(birth_jd, leaves)),
        "next_sign": SIGNS[(idx + 1) % 12],
        "sun_sign": _sign(sun),
        "natal_sun_sign": _sign(nat_sun),
        "phase": phase,
        "phase_start": _date(_real(birth_jd, phase_start)) if phase_start else None,
        "elongation": elong,
    }


def _when(dates, fmt="%B %Y"):
    """One date, or an honest range when the birth-time window disagrees."""
    ds = [d for d in dates if d]
    if not ds:
        return None
    lo, hi = min(ds), max(ds)
    if (hi - lo).days <= 45:
        return lo.strftime(fmt)
    return f"between {lo.strftime(fmt)} and {hi.strftime(fmt)}"


def _sun_moved_age(birth_jd, natal_sign, prog_sign):
    """Age when the progressed Sun left its natal sign (it moves ~1°/year)."""
    if natal_sign == prog_sign:
        return None
    lo, hi = birth_jd, birth_jd + 31

    def moved(j):
        return _sign(_lon(j, swe.SUN)) != natal_sign
    return int(_cross(moved, lo, hi) - birth_jd)


def compute(birth_ut, today, precision="exact", cusps=None):
    """
    The progressed picture for `today` (a date). `birth_ut` is the profile's
    [y, m, d, h_ut, lat, lon]; `precision` is exact / approximate / unknown.
    `cusps` are her natal house cusps, only passed when the time is known.
    """
    y, m, d, h = birth_ut[:4]
    base = jd_ut(int(y), int(m), int(d), float(h))
    today_jd = jd_ut(today.year, today.month, today.day, 12.0)
    runs = [_one(base + o / 24, today_jd, cusps if precision == "exact" else None)
            for o in WINDOW.get(precision, WINDOW["exact"])]
    c = runs[len(runs) // 2]                  # the recorded time is the centre
    certain_moon = len({r["moon_sign"] for r in runs}) == 1
    certain_phase = len({r["phase"] for r in runs}) == 1
    certain_sun = len({r["sun_sign"] for r in runs}) == 1

    out = {"precision": precision, "age": int((today_jd - base) // YEAR)}

    if certain_moon:
        sign = c["moon_sign"]
        out["moon"] = {
            "sign": sign,
            "house": c["moon_house"],
            "entered": _when([r["entered"] for r in runs]),
            "leaves": _when([r["leaves"] for r in runs]),
            "next_sign": c["next_sign"],
            "season": MOON_SEASON[sign],
        }
    else:
        signs = sorted({r["moon_sign"] for r in runs}, key=SIGNS.index)
        out["moon"] = {"sign": None, "between": signs,
                       "season": None}

    if certain_phase:
        key, title, body = PHASES[c["phase"]]
        out["phase"] = {"key": key, "title": title, "body": body,
                        "began": _when([r["phase_start"] for r in runs], "%Y"),
                        "elongation": round(c["elongation"], 1)}
    else:
        out["phase"] = None

    if certain_sun:
        age = _sun_moved_age(base, c["natal_sun_sign"], c["sun_sign"])
        out["sun"] = {"sign": c["sun_sign"], "natal_sign": c["natal_sun_sign"],
                      "moved_at_age": age}
    else:
        out["sun"] = None
    return out


def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# A short, human name for each stretch of the progressed lunar cycle. The
# tradition's reading follows in the body, credited to the tradition.
PHASE_TITLE = {
    "new": "A fresh start in your life", "crescent": "Keeping something new going",
    "first_quarter": "A time to build and choose", "gibbous": "A time to refine",
    "full": "A time when things come to light", "disseminating": "A time to share what you've learned",
    "last_quarter": "A time to let old things go", "balsamic": "A quieter, more inward stretch",
}


def describe(p):
    """Reading cards for the 'where you are now' chapter. Each leads with the
    chapter of her life it describes; the method sits in `because`. Sentences
    are built only from computed fields, and the tradition is named as the
    tradition: these are readings, not facts about her or predictions."""
    if not p:
        return []
    items = []
    mo = p.get("moon") or {}
    if mo.get("sign"):
        entered = mo.get("entered") if mo.get("entered") and not mo["entered"].startswith("between") else None
        leaves = mo.get("leaves") if mo.get("leaves") and not mo["leaves"].startswith("between") else None
        since = (f"From {entered} to {leaves}" if entered and leaves else
                 f"Since {entered}" if entered else
                 f"Until {leaves}" if leaves else "For these few years")
        until = ""
        house = (f", crossing your {_ordinal(mo['house'])} house" if mo.get("house") else "")
        items.append({
            "kind": "progressed_moon",
            "title": "Your current chapter",
            "body": (f"{since}{until}, the tradition reads your life as a season of "
                     f"{mo['season']}. Does that match where your attention has been?"),
            "because": (f"Your progressed Moon is in {mo['sign']}{house}. A progressed "
                        f"chart moves forward one day for every year you've lived."),
        })
    elif mo.get("between"):
        items.append({
            "kind": "progressed_moon",
            "title": "Your current chapter",
            "body": (f"Your progressed Moon is in {' or '.join(mo['between'])}, depending "
                     f"on your birth time. It moves about a degree a month, so your birth "
                     f"time decides which. Add it and I can tell you what this chapter is about."),
            "missing": True,
        })
    ph = p.get("phase")
    if ph:
        since = ""
        if ph.get("began"):
            since = (f"Since somewhere {ph['began']}, " if ph["began"].startswith("between")
                     else f"Since {ph['began']}, ")
        reading = ph["body"].split(":", 1)[1].strip() if ":" in ph["body"] else ph["body"]
        lead = since or "Right now, "
        items.append({
            "kind": "progressed_phase",
            "title": PHASE_TITLE.get(ph.get("key"), f"{ph['title']} of your life"),
            "body": (f"{lead}you're in what the tradition calls the {ph['title'].lower()}: "
                     f"{reading}."),
            "because": (f"Your progressed Moon and Sun are {round(ph['elongation'])}° apart, "
                        f"in a cycle that takes about 29 years."),
        })
    su = p.get("sun")
    if su and su.get("moved_at_age") is not None:
        items.append({
            "kind": "progressed_sun",
            "title": f"A {su['sign']} side of you has grown since you were {su['moved_at_age']}",
            "body": (f"At heart you're still a {su['natal_sign']}, and that doesn't change. "
                     f"The tradition says that around age {su['moved_at_age']}, a "
                     f"{su['sign']} side of you started to come forward too."),
            "because": (f"Your progressed Sun moves about a degree a year, and it crossed "
                        f"from {su['natal_sign']} into {su['sign']} then."),
        })
    return items


def _in(when):
    return when if when.startswith("between") else f"in {when}"
