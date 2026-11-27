"""
Daily compatibility — how the pair is doing today, not just in general.

compat.deep() is the pair's standing: two birth charts never change, so neither
does that score. What changes is the sky. This module takes the fast movers —
the Moon above all, which crosses a sign every two and a half days, then the
Sun, Mercury, Venus and Mars — and asks what each is touching today:

1. The relationship's own (composite) chart — the pair as a pair.
2. Each person's Moon, Venus, Mars and Sun — how each of them is feeling.
3. The pair's strongest synastry contacts. This is the part that explains a
   fight: Mars crossing the point where her Moon squares his Mars sets off the
   exact friction the two charts already carry. A transit to a contact
   "lights it up", in the contact's own tone.

The slow planets (Jupiter through Pluto) on the composite add a small,
weeks-long "season" — mostly context, deliberately a light weight so a
six-week Saturn square doesn't pin the score low every day.

Same rules as the rest of the engine: positions from the ephemeris, valence
from the tables below, nothing from a model. The score is the pair's usual
score moved by at most MAX_SWING; the reasons are the transits that moved it.
"""
import datetime as dt
import math
from functools import lru_cache

from engine import compat
from engine import synastry as S
from engine.chart import jd_ut, positions

MAX_SWING = 15          # the day can move the usual score this far, no further
# Contributions saturate rather than clip, so a pile-up of hits reads as a
# strong day, not a pinned one. Calibrated over 20 random pairs x 365 days:
# ~40% of days within ±3 of usual, ~13% at ±10 or more, ~1% near the cap,
# median day-to-day change 4. See tests/test_daily.py.
SOFTNESS = 3.0
STEADY = 4              # |delta| under this reads as "about usual"

FAST = ["Moon", "Sun", "Mercury", "Venus", "Mars"]
# Orbs for a planet crossing a point on a given day. The Moon's is wider
# because it moves 13° a day — at 5° the contact holds most of the day.
ORB = {"Moon": 5.0, "Sun": 2.0, "Mercury": 2.0, "Venus": 2.0, "Mars": 2.0}
SLOW_ORB = 1.5

# How strongly each transiting planet colours a contact, and which way it leans
# by nature. Venus warms even a square a little; Mars sharpens even a trine.
NATURE = {
    "Moon": (1.0, 0.0), "Sun": (0.6, 0.0), "Mercury": (0.6, 0.0),
    "Venus": (0.8, 0.5), "Mars": (0.8, -0.5),
    "Jupiter": (0.5, 0.4), "Saturn": (0.5, -0.4), "Uranus": (0.4, -0.2),
    "Neptune": (0.3, 0.0), "Pluto": (0.4, -0.2),
}
# A transit's aspect, as harmony. Conjunction is mild: it takes the
# transiting planet's own nature.
TONE = {"trine": 1.0, "sextile": 0.7, "conjunction": 0.3,
        "square": -1.0, "opposition": -0.8}

WEIGHT_COMPOSITE = 1.0
WEIGHT_NATAL = 0.5
WEIGHT_CONTACT = 0.9
WEIGHT_SEASON = 0.35
TOP_CONTACTS = 8

COMPOSITE_TARGETS = ("Sun", "Moon", "Venus", "Mars", "Ascendant")
NATAL_TARGETS = ("Moon", "Venus", "Mars", "Sun")

# ------------------------------------------------------------- words
# What each transiting planet does to whatever it touches, easy and hard.
DOES = {
    "Moon": ("feelings land softly", "moods clash and small things sting"),
    "Sun": ("you notice each other", "pride gets in the way"),
    "Mercury": ("talking is easy", "words get misread"),
    "Venus": ("affection comes easily", "you want different things from each other"),
    "Mars": ("there's energy to do something together", "tempers run short"),
    "Jupiter": ("there's room to be generous", "it's easy to promise more than you mean"),
    "Saturn": ("things feel steady and serious", "duty can create distance"),
    "Uranus": ("something new opens up between you", "restlessness sets in and someone needs space"),
    "Neptune": ("tenderness comes easily", "it's easy to hear what you hope rather than what's said"),
    "Pluto": ("closeness goes deep", "power struggles can surface"),
}
COMPOSITE_MEANS = {
    "Sun": "how you work as a pair",
    "Moon": "how safe you feel together",
    "Venus": "your affection",
    "Mars": "how you two argue and what you want",
    "Ascendant": "how you come across together",
}
NATAL_MEANS = {"Moon": "feelings", "Venus": "heart", "Mars": "drive and temper", "Sun": "sense of self"}
_POINT_FROM_LABEL = {"North Node": "TrueNode"}

# How long each transit lasts, roughly, at these orbs — so "this passes" can be
# said honestly. The Moon's hits are over by tomorrow. These are the typical
# values; lasts() below uses the planet's actual speed today.
LASTS = {"Moon": "passes by tomorrow", "Sun": "lasts a few days",
         "Mercury": "lasts a few days", "Venus": "lasts a few days",
         "Mars": "lasts most of the week"}
STATIONING = 0.2      # °/day: below this Mercury, Venus or Mars is about to turn


def lasts(planet, speed, max_orb):
    """
    How long a contact stays in orb, from today's real speed.

    A fixed "Mercury lasts a few days" is false exactly when it matters: near a
    station Mercury, Venus or Mars can sit on one point for two to six weeks. The
    whole window is 2·orb / speed days; say that, in words.
    """
    if planet not in FAST:
        return LASTS.get(planet, "lasts weeks")
    days = 2 * max_orb / max(abs(speed or 0), 0.05)
    if days < 1.5:
        return "passes by tomorrow"
    if days < 5:
        return "lasts a few days"
    if days < 9:
        return "lasts most of the week"
    if abs(speed or 0) < STATIONING:
        return "lingers for weeks — it's slowing down to turn"
    return "lasts a week or two"


# ------------------------------------------------------------- the sky
def local_noon_jd(day, tz):
    """Noon where she is, as a Julian day. 'Today' is her today."""
    noon = dt.datetime(day.year, day.month, day.day, 12, tzinfo=tz)
    u = noon.astimezone(dt.timezone.utc)
    return jd_ut(u.year, u.month, u.day, u.hour + u.minute / 60)


@lru_cache(maxsize=4096)
def _sky(jd):
    return positions(jd)


@lru_cache(maxsize=1024)
def _natal(birth_ut, precision):
    return compat.samples(list(birth_ut), precision)[0]


def _aspect(a, b, orb):
    sep = compat._sep(a, b)
    for name, angle, _o in S.ASPECTS:
        d = abs(sep - angle)
        if d <= orb:
            return name, d
    return None


VERB = {"conjunction": "meets", "sextile": "sextiles", "square": "squares",
        "trine": "trines", "opposition": "opposes"}


def _the(planet):
    return f"the {planet}" if planet in ("Sun", "Moon") else planet


def _cap(text):
    return text[0].upper() + text[1:]


def _valence(planet, aspect):
    mag, bias = NATURE[planet]
    return TONE[aspect] * mag + bias


def _closeness(orb, max_orb):
    return 0.4 + 0.6 * (1 - orb / max_orb)


def _says(planet, aspect):
    easy, hard = DOES[planet]
    return easy if _valence(planet, aspect) >= 0 else hard


# ------------------------------------------------------------- one day
def day_score(report, mine, theirs, day, tz=dt.timezone.utc, name="them",
              moon_mine=True, moon_theirs=True):
    """
    Today's score for one pair. `report` is compat.deep()'s output; `mine` and
    `theirs` are natal charts (compat.samples(...)[0]). A Moon whose owner has
    no birth time moves too far across the day to be a target, so it's left out.
    """
    sky = _sky(local_noon_jd(day, tz))
    comp = report["composite"]["positions"]
    hits = []

    def hit(kind, planet, aspect, orb, max_orb, weight, text, meaning, harmony=None,
            short=None, target=None):
        v = _valence(planet, aspect)
        if harmony is not None:          # a lit-up contact speaks in its own tone too
            v = 0.5 * v + 0.5 * harmony * 1.2
        c = weight * v * _closeness(orb, max_orb)
        hits.append({"kind": kind, "transit": planet, "aspect": aspect,
                     "orb": round(orb, 2), "weight": round(c, 3),
                     "text": text, "meaning": meaning,
                     "short": short or meaning.rstrip("."),
                     # Which point was crossed, so a reason can be re-checked
                     # against the ephemeris (planner tests do exactly that).
                     "target": target,
                     "lasts": lasts(planet, sky.get(planet, {}).get("speed"), max_orb)})

    # 1. the relationship's chart
    targets = {k: v for k, v in comp.items() if k in COMPOSITE_TARGETS}
    if not report["composite"].get("moon_certain", True):
        targets.pop("Moon", None)
    for p in FAST:
        for k, lon in targets.items():
            a = _aspect(sky[p]["lon"], lon, ORB[p])
            if a:
                hit("pair", p, a[0], a[1], ORB[p], WEIGHT_COMPOSITE,
                    _cap(f"{_the(p)} {VERB[a[0]]} the relationship's {k}"),
                    f"{_cap(_says(p, a[0]))}, which affects {COMPOSITE_MEANS[k]}.",
                    target={"chart": "composite", "point": k})

    # 2. each of them
    for who, chart, moon_ok, label in ((
            "you", mine, moon_mine, "your"), (name, theirs, moon_theirs, f"{name}'s")):
        for k in NATAL_TARGETS:
            if k == "Moon" and not moon_ok:
                continue
            lon = chart["planets"][k]["lon"]
            for p in FAST:
                if p == k == "Moon":
                    continue            # the Moon on its own natal place is monthly noise
                a = _aspect(sky[p]["lon"], lon, ORB[p])
                if a:
                    hit("person", p, a[0], a[1], ORB[p], WEIGHT_NATAL,
                        _cap(f"{_the(p)} {VERB[a[0]]} {label} {k}"),
                        f"{_cap(_says(p, a[0]))}, which affects {label} {NATAL_MEANS[k]}.",
                        target={"chart": "you" if who == "you" else "them", "point": k})

    # 3. the contacts the two charts already carry, lit up
    def lon_of(chart, label):
        key = _POINT_FROM_LABEL.get(label, label)
        src = chart["angles"] if key in ("Ascendant", "Midheaven") else chart["planets"]
        return src.get(key, {}).get("lon")

    lit = set()
    for c in report["contacts"][:TOP_CONTACTS]:
        if c["certainty"] == "depends on birth time":
            continue
        ends = [lon_of(mine, c["yours"]), lon_of(theirs, c["theirs"])]
        for p in FAST:
            if (c["text"], p) in lit:
                continue
            for end, lon in zip(("you", "them"), ends):
                if lon is None:
                    continue
                # Tighter than a point hit: only a planet right on the contact sets it off.
                a = _aspect(sky[p]["lon"], lon, min(ORB[p], 3.0))
                if a and a[0] in ("conjunction", "square", "opposition"):
                    lit.add((c["text"], p))
                    carry = ("brings out a strength already built into your two charts"
                             if c["harmony"] > 0 else
                             "presses on a sore spot already built into your two charts")
                    # Words follow the combined effect, so a lift never reads as a warning.
                    v = 0.5 * _valence(p, a[0]) + 0.5 * c["harmony"] * 1.2
                    says = DOES[p][0] if v >= 0 else DOES[p][1]
                    hit("contact", p, a[0], a[1], min(ORB[p], 3.0), WEIGHT_CONTACT,
                        f"Today's {p} sets off the {c['aspect']} between {name}'s "
                        f"{c['theirs']} and your {c['yours']}",
                        f"{_cap(says)}. It {carry}. {c['meaning']}",
                        harmony=c["harmony"],
                        short=f"{_cap(says)}, "
                              + ("bringing out a strength" if c["harmony"] > 0
                                 else "hitting a sore spot")
                              + " the two of you already share",
                        target={"chart": end, "point": _POINT_FROM_LABEL.get(
                            c["yours"] if end == "you" else c["theirs"],
                            c["yours"] if end == "you" else c["theirs"])})
                    break

    # 4. the season — slow transits to the composite, lightly weighted
    season = []
    for t in compat.TRANSIT_SLOW:
        if t not in sky:
            continue
        for k, lon in targets.items():
            a = _aspect(sky[t]["lon"], lon, SLOW_ORB)
            if a:
                v = WEIGHT_SEASON * _valence(t, a[0]) * _closeness(a[1], SLOW_ORB)
                season.append({"transit": t, "aspect": a[0], "point": k,
                               "weight": round(v, 3),
                               "text": f"{t} {VERB[a[0]]} the relationship's {k}",
                               "meaning": f"{_cap(_says(t, a[0]))}, which affects {COMPOSITE_MEANS[k]}."})

    raw = sum(h["weight"] for h in hits) + sum(s["weight"] for s in season)
    delta = round(MAX_SWING * math.tanh(raw / SOFTNESS))
    base = report["score"]["overall"]
    score = max(1, min(99, base + delta))
    hits.sort(key=lambda h: -abs(h["weight"]))
    return {
        "date": day.isoformat(),
        "score": score,
        "usual": base,
        "delta": score - base,
        "mood": mood(score - base),
        "band": S._band(score),
        "reasons": hits[:3],
        "all": len(hits),
        "season": sorted(season, key=lambda s: -abs(s["weight"]))[:2],
    }


def mood(delta):
    if delta >= 10: return "a warm day"
    if delta >= STEADY: return "easier than usual"
    if delta > -STEADY: return "about usual"
    if delta > -10: return "a bit tender"
    return "a tense day"


def headline(d, name):
    """One line a person can read at a glance. It leads with a reason that
    points the same way as the number — a tense day explained by its one
    pleasant transit reads as a contradiction."""
    low = lambda r: r["short"][0].lower() + r["short"][1:]  # noqa: E731
    up = next((r for r in d["reasons"] if r["weight"] > 0), None)
    down = next((r for r in d["reasons"] if r["weight"] < 0), None)
    if d["delta"] >= STEADY:
        return (f"Things are easier with {name} today — {low(up)}." if up
                else f"Things are easier with {name} today.")
    if d["delta"] <= -STEADY:
        return (f"Things are touchier with {name} today — {low(down)}." if down
                else f"Things are touchier with {name} today.")
    lead = f"It's an ordinary day with {name}"
    if up and down:
        return f"{lead}, with a pull both ways: {low(up)}, but {low(down)}."
    if down:
        return f"{lead}. One thing to watch: {low(down)}."
    if up:
        return f"{lead}. The bright spot: {low(up)}."
    return f"{lead}, and the sky is quiet between you."


# ------------------------------------------------------------- a stretch of days
def series(report, mine, theirs, today, tz, name, back=13, ahead=3, **kw):
    """Scores from `back` days ago to `ahead` days out. Cheap: one sky per day."""
    out = []
    for i in range(-back, ahead + 1):
        d = today + dt.timedelta(days=i)
        s = day_score(report, mine, theirs, d, tz, name, **kw)
        out.append({"date": s["date"], "score": s["score"], "delta": s["delta"]})
    return out


def for_pair(prof, row, report, today, tz):
    """The whole daily view for one saved person."""
    pm, pt = compat.my_precision(prof), compat.their_precision(row)
    mine = _natal(tuple(prof["birth_ut"]), pm)
    theirs = _natal(tuple(row["birth_ut"]), pt)
    kw = {"moon_mine": pm != "unknown", "moon_theirs": pt != "unknown"}
    name = row.get("name", "them")
    d = day_score(report, mine, theirs, today, tz, name, **kw)
    d["headline"] = headline(d, name)
    d["series"] = series(report, mine, theirs, today, tz, name, **kw)
    upcoming = [s for s in d["series"] if s["date"] > d["date"]]
    better = next((s for s in upcoming if s["score"] > d["score"] + STEADY), None)
    if d["delta"] <= -STEADY and better:
        day_name = dt.date.fromisoformat(better["date"]).strftime("%A")
        d["outlook"] = f"Things look easier by {day_name}."
    elif d["delta"] <= -STEADY:
        d["outlook"] = "The next few days stay close to this — go gently."
    else:
        d["outlook"] = None
    return d


# ------------------------------------------------------------- check-ins
FEELS = ("closer", "steady", "strained")
KEEP_DAYS = 180


def record_checkin(log, day, feel, note=None, at=None):
    """`log` is {date: {...}}; one check-in per day, the latest wins."""
    if feel not in FEELS:
        raise ValueError(f"feel must be one of {FEELS}")
    log = dict(log or {})
    log[day.isoformat()] = {"feel": feel, "note": (note or "").strip()[:280] or None,
                            "at": at or dt.datetime.now(dt.timezone.utc).isoformat()}
    cutoff = (day - dt.timedelta(days=KEEP_DAYS)).isoformat()
    return {k: v for k, v in log.items() if k >= cutoff}


# ------------------------------------------------------------- the list, cheaply
def brief_for_pair(prof, row, report, today, tz):
    """
    One day for one person, without the fortnight around it.

    for_pair() scores seventeen days to draw a trend line; the people list and
    the ranking only ever read today. Same natal setup, same day_score, so the
    number here is the number on the person's own page — just one sky, not 17.
    """
    pm, pt = compat.my_precision(prof), compat.their_precision(row)
    mine = _natal(tuple(prof["birth_ut"]), pm)
    theirs = _natal(tuple(row["birth_ut"]), pt)
    name = row.get("name", "them")
    d = day_score(report, mine, theirs, today, tz, name,
                  moon_mine=pm != "unknown", moon_theirs=pt != "unknown")
    d["headline"] = headline(d, name)
    return d


def rank_people(briefs):
    """
    Her people, most moved by today's sky first.

    `briefs` is [{"id", "name", "score", "delta", ...}]. The order is
    (-|delta|, -score, name): fixed for a given day, so only the sky reshuffles
    it — never a coin flip, never a nudge. The spotlight names someone only
    when the sky actually moved them (|delta| >= STEADY); a quiet day says so.
    """
    ranked = sorted(briefs, key=lambda b: (-abs(b["delta"]), -b["score"], b["name"].lower()))
    up = max(briefs, key=lambda b: (b["delta"], b["score"]), default=None)
    down = min(briefs, key=lambda b: (b["delta"], -b["score"]), default=None)
    top = ranked[0] if ranked else None
    spotlight = {
        "warmest": up["id"] if up and up["delta"] >= STEADY else None,
        "most_charged": top["id"] if top and abs(top["delta"]) >= STEADY else None,
        "tender": down["id"] if down and down["delta"] <= -STEADY else None,
    }
    return {"ranked": ranked, "spotlight": spotlight,
            "quiet": all(abs(b["delta"]) < STEADY for b in briefs)}


# ------------------------------------------------------------- showing up
def streak(dates, today, rest_per_week=1, window=KEEP_DAYS):
    """
    Days in a row she has checked in on someone, counted back from today.

    One missed day a week is forgiven automatically — a rest day, never sold,
    never bought, nothing to "save". Today not done yet doesn't break anything:
    the day isn't over. `best` is the longest run in the check-in window, by
    the same rules. Computed from what's stored; there is no streak record to
    lose.
    """
    have = set(dates)
    today_done = today.isoformat() in have
    stop = today - dt.timedelta(days=window)

    def run_from(start):
        """(check-in days, the rest days spent inside that run)."""
        days, rested, gap = 0, [], None
        d = start
        while d >= stop:
            if d.isoformat() in have:
                days += 1
                if gap:
                    rested.append(gap)    # forgiven only once she's on its far side
                gap = None
            elif (rest_per_week and gap is None
                  and d.isocalendar()[:2] not in {r.isocalendar()[:2] for r in rested}):
                gap = d
            else:
                break
            d -= dt.timedelta(days=1)
        return days, rested

    start = today if today_done else today - dt.timedelta(days=1)
    days, rested = run_from(start)
    wk = today.isocalendar()[:2]
    best = days
    for iso in have:
        try:
            s = dt.date.fromisoformat(iso)
        except ValueError:
            continue
        # Only the newest day of each run needs walking back from.
        if stop <= s < start and (s + dt.timedelta(days=1)).isoformat() not in have:
            best = max(best, run_from(s)[0])
    return {"days": days, "today_done": today_done,
            "rest_used_this_week": any(r.isocalendar()[:2] == wk for r in rested),
            "best": best}
