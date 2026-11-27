"""
What the agent can look up.

Until now the model got a briefing assembled before the call and answered from
it. Anything the briefing did not cover — a future date, a person, a memory
older than the retrieval window — it had to decline, because the system prompt
correctly forbids inventing. That is honest and it is also the ceiling on what
this product can be. A real astrologer looks things up.

Every tool returns COMPUTED FACTS, never prose. The engine does the astronomy,
the model does the synthesis and the voice — the same division as everywhere
else, extended so the model can ask for more facts mid-answer.
"""
import contextlib
import datetime as dt
import re

import swisseph as swe

from engine import synastry
from engine.chart import (
    SIGNS,
    aspects,
    day_phase,
    house_of,
    jd_ut,
    local_noon_jd,
    natal_targets,
    plain,
    positions,
)
from engine.ephemeris import BASE, FLAGS
from engine.store import load

MAX_FORWARD_DAYS = 400
BODIES = {"Sun": swe.SUN, "Moon": swe.MOON, "Mercury": swe.MERCURY,
          "Venus": swe.VENUS, "Mars": swe.MARS, "Jupiter": swe.JUPITER,
          "Saturn": swe.SATURN, "Uranus": swe.URANUS, "Neptune": swe.NEPTUNE,
          "Pluto": swe.PLUTO, "Chiron": swe.CHIRON}
ASPECT_ANGLES = {"conjunction": 0, "sextile": 60, "square": 90,
                 "trine": 120, "opposition": 180}

DEFINITIONS = [
    {
        "name": "get_sky",
        "description": (
            "Compute the sky for any date and how it aspects her natal chart. "
            "Use whenever she asks about a specific day other than today — "
            "'what about Thursday', 'is next week better', 'what was happening "
            "in March'. Past and future both work."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"date": {"type": "string",
                                    "description": "YYYY-MM-DD"}},
            "required": ["date"], "additionalProperties": False},
    },
    {
        "name": "get_natal",
        "description": (
            "Her birth chart: every placement with sign, degree and house, plus "
            "the angles. Use when she asks where something is in her chart, or "
            "when you need a placement the briefing did not include."),
        "strict": True,
        "input_schema": {"type": "object", "properties": {},
                         "required": [], "additionalProperties": False},
    },
    {
        "name": "get_synastry",
        "description": (
            "The full compatibility report for someone she has saved: the score "
            "(with its range when a birth time is uncertain), themes — communication, "
            "emotional bond, attraction, affection, commitment, intensity, fate and "
            "more — each with the specific contacts and what they mean, house "
            "overlays both ways, the composite chart, element balance, what is "
            "active for the pair now, and TODAY: the day's score against their usual "
            "one with the transits that moved it, plus her own recent check-ins about "
            "how it actually went (closer / steady / strained, with her notes). Use "
            "for any question about a specific person "
            "or the dynamic between them. Contacts marked 'depends on birth time' "
            "are not certain; say so."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string",
                                    "description": "The person's name as she saved it"}},
            "required": ["name"], "additionalProperties": False},
    },
    {
        "name": "get_person_day",
        "description": (
            "Everything you may say about one saved person, in one lookup: the "
            "pair's usual score and leading contacts, today's score with the "
            "transits moving it, the next seven days, and HER OWN dated words about "
            "them — each with that day's computed transit and, where it recurs, "
            "the date it comes back. Use whenever she names someone she has saved. "
            "Quote her words exactly with their date; cite a transit only as "
            "written in the result; never predict what the other person will do."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string",
                                    "description": "The person's name as she saved it, "
                                                   "or their first name"}},
            "required": ["name"], "additionalProperties": False},
    },
    {
        "name": "compare_connections",
        "description": (
            "Compare everyone she has saved, ranked. Use when she asks which of "
            "them is better for her, who she is most compatible with, or how two "
            "people stack up. Returns ties where the difference is too small to "
            "be real — report those as level rather than picking one."),
        "strict": True,
        "input_schema": {"type": "object", "properties": {},
                         "required": [], "additionalProperties": False},
    },
    {
        "name": "search_memory",
        "description": (
            "Search everything she has ever told you, not just the recent window "
            "already in context. Use when she asks whether she has felt this "
            "before, or refers to something you cannot see. Returns the best "
            "matches for THIS query plus her last seven days — not her whole "
            "history — so a search that misses something does not mean she never "
            "said it. Episodes in MEMORY are real whether or not a search finds them. "
            "Dates in the query ('this week', 'on the 21st', '2026-10-21') are honoured."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"},
                           "limit": {"type": "integer",
                                     "description": "How many episodes, 1-10"},
                           "person": {"type": "string",
                                      "description": "A saved person's name: return only "
                                                     "what she has said about them, dated"}},
            "required": ["query"], "additionalProperties": False},
    },
    {
        "name": "find_next",
        "description": (
            "Every pass of a transiting planet over one of her natal points in "
            "the next year, whether each is exact or only a closest approach, "
            "and the date it ends (`ends`). Use for 'when does this end', 'when "
            "does it get easier', or to give a feeling an end date."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "transiting": {"type": "string",
                               "description": "Sun, Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune or Pluto"},
                "natal_point": {"type": "string",
                                "description": "A natal placement, e.g. Moon, Mercury, Ascendant"},
                "aspect": {"type": "string",
                           "description": "conjunction, sextile, square, trine or opposition"},
                "from_date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"}},
            "required": ["transiting", "natal_point", "aspect"],
            "additionalProperties": False},
    },
    {
        "name": "get_lunations",
        "description": (
            "New and full moons in a date range, with the sign and which of her "
            "houses each falls in, plus the most recent new and full moon before "
            "it (`previous`). Use for 'when is the next full moon', 'when was the "
            "last full moon', or to anchor advice to a lunar cycle."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"from_date": {"type": "string", "description": "YYYY-MM-DD"},
                           "days": {"type": "integer", "description": "How far ahead, max 120"}},
            "required": ["from_date"], "additionalProperties": False},
    },
    {
        "name": "get_retrogrades",
        "description": (
            "A planet's retrograde periods from a date: when each starts and ends "
            "(the station dates), the sign it is in at each end, which of her houses "
            "it falls in, and whether it is retrograde now. Use for 'when is the next "
            "Mercury retrograde', 'when does this retrograde end', 'is Venus going "
            "retrograde this year'."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "planet": {"type": "string",
                           "description": "Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune, Pluto or Chiron"},
                "from_date": {"type": "string", "description": "YYYY-MM-DD, defaults to today"}},
            "required": ["planet"], "additionalProperties": False},
    },
]


def _sep(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _natal_points(nat):
    # Only what her birth time supports: no angles, and no natal Moon to the
    # day, when the time is unknown (engine/chart.natal_targets).
    return natal_targets(nat)


# ------------------------------------------------------------------ handlers

def get_sky(uid, nat, date, **_):
    d = dt.date.fromisoformat(date)
    tz = ((load(uid, "account", {}) or {}).get("tz")
          or (load(uid, "profile", {}) or {}).get("tz"))
    j = local_noon_jd(d, tz)
    tp = positions(j)
    asp = aspects(tp, nat)
    known = nat.get("time_known", True)
    return {
        "date": date,
        "weekday": d.strftime("%A"),
        "moon": day_phase(d, tz),
        "placements": {k: {"at": v["label"],
                           "her_house": house_of(v["lon"], nat["house_cusps"]) if known else None,
                           "retrograde": v["retrograde"]}
                       for k, v in tp.items()},
        "aspects_to_her_chart": [
            {"transiting": plain(a["transit"]), "aspect": a["aspect"],
             "her": plain(a["natal"]), "orb": a["orb"]}
            for a in asp[:12]],
    }


def get_natal(uid, nat, **_):
    known = nat.get("time_known", True)
    out = {
        "placements": {plain(k): {"at": v["label"], "house": v["house"] if known else None,
                                  "retrograde": v["retrograde"]}
                       for k, v in nat["planets"].items()},
        # A noon chart's angles are not hers; the tool used to hand them over.
        "angles": {k: v["label"] for k, v in nat["angles"].items()} if known else {},
    }
    if not known:
        out["birth_time"] = ("unknown — no rising sign, Midheaven or houses; the Moon's "
                             "degree is a range across the birth day")
    return out


def get_synastry(uid, nat, name, **_):
    people = load(uid, "connections", []) or []
    match = next((p for p in people
                  if p.get("name", "").lower() == name.lower().strip()), None)
    if not match:
        known = [p.get("name") for p in people]
        return {"error": f"No saved chart for {name!r}.",
                "saved_people": known,
                "hint": "She has to add their birth details before this works."}
    # This called synastry.compare(nat, match) — compare() ranks a list of
    # people and takes one argument, so every lookup of a single person errored
    # and the chat could never talk about anyone specific.
    from engine import compat
    rep = compat.report_for(uid, match)
    out = compat.summary(rep, match)
    out["today"] = _today_for(uid, match, rep)
    return out


def _today_for(uid, row, rep):
    """Today's score and her check-ins. Optional garnish — never sinks the report."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    from engine import daily
    try:
        acct = load(uid, "account", {}) or {}
        prof = load(uid, "profile", {}) or {}
        try:
            tz = ZoneInfo(acct.get("tz") or prof.get("tz") or "UTC")
        except Exception:
            tz = dt.timezone.utc
        today = dt.datetime.now(tz).date()
        d = daily.for_pair(prof, row, rep, today, tz)
        pulse = load(uid, f"pulse-{row['id']}", {}) or {}
        recent = sorted(pulse.items())[-14:]
        return {"date": d["date"], "score": d["score"], "usual": d["usual"],
                "delta": d["delta"], "mood": d["mood"], "headline": d["headline"],
                "outlook": d["outlook"],
                "reasons": [{k: r[k] for k in ("text", "meaning", "lasts")}
                            for r in d["reasons"]],
                "her_checkins": [{"date": k, "feel": v["feel"], "note": v.get("note"),
                                  "that_days_score": next(
                                      (s["score"] for s in d["series"] if s["date"] == k), None)}
                                 for k, v in recent]}
    except Exception:
        return None


def _tz(uid):
    from zoneinfo import ZoneInfo
    acct = load(uid, "account", {}) or {}
    prof = load(uid, "profile", {}) or {}
    try:
        return ZoneInfo(acct.get("tz") or prof.get("tz") or "UTC")
    except Exception:
        return dt.timezone.utc


def _person_row(uid, name):
    """Full name first, then first name — she types 'Sam', not 'Sam Lee'."""
    want = (name or "").lower().strip()
    rows = load(uid, "connections", []) or []
    return (next((r for r in rows if (r.get("name") or "").lower().strip() == want), None)
            or next((r for r in rows
                     if (r.get("name") or "").split(" ")[0].lower() == want), None))


# Rides inside the result rather than the system context, so the rule and the
# facts it governs arrive together and are graded together.
PERSON_RULE = ("Facts computed by the engine, or HER OWN words with the date she said "
               "them. Quote her words exactly with their date; cite a transit only as "
               "written here; never predict what the other person will do.")


def person_day(uid, row, today=None):
    """
    team2/memory: the person facts as a tool result. The chat route pre-runs
    this for a person named in the message and the model can call it itself;
    both paths go through here, so what the reply cites is always checkable
    against a tool result in its grounding — never only the system context.
    """
    from engine import personmem
    tz = _tz(uid)
    f = personmem.facts(uid, row, today or dt.datetime.now(tz).date(), tz)
    f["rule"] = PERSON_RULE
    return f


def get_person_day(uid, nat, name, today=None, **_):
    row = _person_row(uid, name)
    if not row:
        return {"error": f"No saved person called {name!r}.",
                "saved_people": [r.get("name") for r in load(uid, "connections", []) or []]}
    return person_day(uid, row, today)


def compare_connections(uid, nat, **_):
    rows = load(uid, "connections", []) or []
    result = synastry.compare(rows)
    if not result.get("ok"):
        return {**result,
                "hint": "She needs at least two saved people before this means "
                        "anything. Do not compare from memory."}
    return result


def search_memory(uid, nat, query, limit=5, person=None, today=None, **_):
    from engine import memory, retrieval
    if person:
        # team2/memory: one person's notes only — her words about them, with
        # dates, from her own store. Never another person's, never theirs.
        from engine import personmem
        row = _person_row(uid, person)
        if not row:
            return {"error": f"No saved person called {person!r}."}
        ns = personmem.notes(uid, row)[:max(1, min(int(limit or 5), 10))]
        return {"query": query, "person": row["name"], "found": len(ns),
                "notes": [{k: n.get(k) for k in ("date", "source", "text", "feel")}
                          for n in ns]}
    episodes = memory.all_episodes(uid)
    lim = max(1, min(int(limit or 5), 10))
    today = today or dt.date.today()
    window = date_window(query, today)
    # A lower floor than the automatic per-turn retrieval uses, and synonyms:
    # she asked for this search explicitly, so a weak match is better than
    # nothing found.
    hits = retrieval.relevant(query, episodes, limit=lim, floor=0.2, synonyms=True)
    if window:
        lo, hi = window
        dated = [e for e in episodes if lo <= e["date"] <= hi]
        hits = dated + [h for h in hits if h not in dated]
    hits = hits[:max(lim, len(hits) if window else 0)]
    week = (today - dt.timedelta(days=7)).isoformat()
    recent = [e for e in episodes if week <= e["date"] <= today.isoformat() and e not in hits]

    def row(h):
        return {"date": h["date"], "mood": h.get("mood"), "summary": h.get("summary"),
                "her_words": h.get("her_words", [])[:2], "topics": h.get("topics", [])}

    out = {"query": query, "searched": len(episodes), "found": len(hits),
           "episodes": [row(h) for h in hits],
           # The last week rides along whatever the query: it is what "lately"
           # questions are about, and what the model most needs to stand by.
           "last_7_days": [row(e) for e in recent],
           "note": SEARCH_NOTE}
    if window:
        out["dates"] = {"from": window[0], "to": window[1]}
    if not hits:
        out["no_match_for_this_query"] = True
    return out


# Rides in every search result, so the rule arrives with the facts it governs.
SEARCH_NOTE = ("Best matches for this query, not everything she has said. Nothing found "
               "here is not evidence it never happened: what is in MEMORY, what she "
               "said earlier in this conversation and what you already told her from "
               "a lookup all still stand. Never take any of it back because a search "
               "missed it.")

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]
_ORD_DAY = re.compile(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b", re.I)
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({'|'.join(m[:3] for m in _MONTHS)})[a-z]*\b"
                        rf"|\b({'|'.join(m[:3] for m in _MONTHS)})[a-z]*\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)


def date_window(query, today):
    """(from, to) ISO dates a search query names, or None. 'This week' and
    'lately' are the last seven days; 'last week' the seven before; a day of
    the month is the most recent one that is not in the future."""
    q = (query or "").lower()
    iso = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", q)
    if iso:
        return min(iso), max(iso)

    def span(a, b):
        return (today - dt.timedelta(days=a)).isoformat(), (today - dt.timedelta(days=b)).isoformat()
    if "yesterday" in q:
        return span(1, 1)
    if re.search(r"\btoday\b", q):
        return span(0, 0)
    if re.search(r"\blast week\b", q):
        return span(14, 7)
    if re.search(r"\b(this week|past week|lately|recently|these days|the last few days)\b", q):
        return span(7, 0)
    if re.search(r"\b(this|past|last) month\b", q):
        return span(31, 0)
    m = _DAY_MONTH.search(q)
    if m:
        day = int(m.group(1) or m.group(4))
        mon = [x[:3] for x in _MONTHS].index((m.group(2) or m.group(3))[:3]) + 1
        year = today.year if (mon, day) <= (today.month, today.day) else today.year - 1
        with contextlib.suppress(ValueError):
            d = dt.date(year, mon, day).isoformat()
            return d, d
    m = _ORD_DAY.search(q)
    if m:
        day = int(m.group(1))
        cand = today.replace(day=1) - dt.timedelta(days=1) if day > today.day else today
        with contextlib.suppress(ValueError):
            d = cand.replace(day=day).isoformat()
            return d, d
    return None


EXACT_ORB = 0.25     # closer than this, a pass is called exact
END_ORB = 1.5        # wider than this, it has stopped being "on" her chart


def find_next(uid, nat, transiting, natal_point, aspect, from_date=None, today=None, **_):
    """
    Every pass of a transit over one of her points in the coming year, and the
    day it lets go.

    This used to return the first day the orb stopped shrinking and call it
    exact. A slow planet that turns retrograde 0.7° short of a point never gets
    there, so a near miss was reported as a hit — and the day it ended was not
    reported at all, though "when does this end" is what the tool is for. The
    model was handed dates that disagreed with the sky it had been shown, and
    said so to her.
    """
    body = BODIES.get(transiting.capitalize())
    if body is None:
        return {"error": f"{transiting!r} is not a body I track.",
                "available": sorted(BODIES)}
    target_angle = ASPECT_ANGLES.get(aspect.lower())
    if target_angle is None:
        return {"error": f"{aspect!r} is not an aspect I check.",
                "available": sorted(ASPECT_ANGLES)}
    pts = _natal_points(nat)
    key = next((k for k in pts if k.lower() == natal_point.lower()), None)
    if key is None:
        return {"error": f"{natal_point!r} is not in her chart.",
                "available": sorted(pts)}

    # The conversation's day, not the server's: they differ for anything
    # replayed, and a date counted from the wrong one is a wrong date.
    start = (dt.date.fromisoformat(from_date) if from_date
             else today or dt.date.today())
    target = pts[key]
    flags = FLAGS

    def orb_at(j):
        return abs(_sep(swe.calc_ut(j, body, flags)[0][0], target) - target_angle)

    # Daily at noon to find the shape, then hourly around each minimum: the
    # Moon moves 13° a day and Mercury up to 2°, so a daily sample can sit a
    # degree either side of a hit that is exact.
    # The Moon gets a two-hour grid; at a day it could skip a whole aspect.
    step = 1 / 12 if body == swe.MOON else 1.0
    n = int(MAX_FORWARD_DAYS / step)
    j0 = jd_ut(start.year, start.month, start.day, 12.0)
    js = [j0 + k * step for k in range(-1, n + 1)]
    orbs = [orb_at(j) for j in js]

    def date_of(j):
        y, m, d_, _ = swe.revjul(j)
        return dt.date(y, m, d_)

    passes = []
    for i in range(1, len(orbs) - 1):
        if not (orbs[i] <= orbs[i - 1] and orbs[i] < orbs[i + 1] and orbs[i] < 1.5):
            continue
        # ±one grid step at 1/24 of it: an hour for planets, five minutes
        # for the Moon.
        best_j, best = min(((js[i] + k * step / 24, orb_at(js[i] + k * step / 24))
                            for k in range(-24, 25)), key=lambda x: x[1])
        if best >= 1.0:
            continue
        hit = date_of(best_j)
        if hit < start:
            continue
        passes.append({"date": hit.isoformat(), "weekday": hit.strftime("%A"),
                       "days_away": (hit - start).days, "orb": round(best, 2),
                       "exact": best <= EXACT_ORB})

    desc = f"transiting {transiting} {aspect} her natal {key}"
    in_effect = orbs[1] <= END_ORB
    now = {"orb": round(orbs[1], 2), "in_effect": in_effect,
           "closing": orbs[2] < orbs[1]}

    # The current contact ends the first day it is wider than END_ORB. A slow
    # planet can loop back later; that later pass is listed, and is a return,
    # not a continuation.
    ends = None
    if in_effect:
        ends = next((date_of(js[i]) for i in range(1, len(js)) if orbs[i] > END_ORB), None)

    out = {"description": desc, "now": now, "passes": passes,
           "found": bool(passes),
           "ends": ends.isoformat() if ends else None}
    if passes:
        # Kept flat for callers that only want the next one.
        out.update(date=passes[0]["date"], weekday=passes[0]["weekday"],
                   days_away=passes[0]["days_away"], orb_at_exact=passes[0]["orb"])
    notes = []
    if in_effect and ends is None:
        notes.append(f"Still within {END_ORB}° at the end of the {MAX_FORWARD_DAYS}-day "
                     "search. Slow planets can stay on a point for years.")
    if not passes:
        notes.append(f"No pass closer than 1° in {MAX_FORWARD_DAYS} days.")
    elif not passes[0]["exact"]:
        notes.append("The next pass is a closest approach, not exact. Say 'closest', "
                     "not 'exact'.")
    if notes:
        out["note"] = " ".join(notes)
    return out


def _lunations(start, hours, nat, step=1):
    flags = BASE
    out, prev = [], None
    for hour in range(0, hours, step):
        j = jd_ut(start.year, start.month, start.day, 0) + hour / 24
        ang = (swe.calc_ut(j, swe.MOON, flags)[0][0]
               - swe.calc_ut(j, swe.SUN, flags)[0][0]) % 360
        if prev is not None:
            for target, label in ((0, "new moon"), (180, "full moon")):
                a, b = (prev - target) % 360, (ang - target) % 360
                if a > 350 and b < 10:
                    mlon = swe.calc_ut(j, swe.MOON, flags)[0][0]
                    y, m, d, _h = swe.revjul(j, swe.GREG_CAL)
                    out.append({"kind": label, "date": f"{y:04d}-{m:02d}-{d:02d}",
                                "sign": SIGNS[int(mlon // 30)],
                                "her_house": house_of(mlon, nat["house_cusps"])})
        prev = ang
    return out


def get_lunations(uid, nat, from_date, days=60, **_):
    days = max(1, min(int(days or 60), 120))
    start = dt.date.fromisoformat(from_date)
    # The last of each before the range too: "was that the full moon last
    # night?" had no answer, because the lookup only looked forward (audit LOW).
    back = start - dt.timedelta(days=31)
    before = _lunations(back, 31 * 24, nat)
    previous = {}
    for x in before:
        previous[x["kind"]] = x
    return {"from": from_date, "days": days, "lunations": _lunations(start, days * 24, nat),
            "previous": sorted(previous.values(), key=lambda x: x["date"])}


_RX_BODIES = {"Mercury": swe.MERCURY, "Venus": swe.VENUS, "Mars": swe.MARS,
              "Jupiter": swe.JUPITER, "Saturn": swe.SATURN, "Uranus": swe.URANUS,
              "Neptune": swe.NEPTUNE, "Pluto": swe.PLUTO, "Chiron": swe.CHIRON}


def _speed(code, jd):
    from engine.ephemeris import FLAGS
    xx, _ = swe.calc_ut(jd, code, FLAGS)
    return xx[0], xx[3]


def _station(code, a, b):
    """The moment between jd a and b (a day apart) where the speed changes sign."""
    sa = _speed(code, a)[1]
    for _ in range(20):                       # to well under a minute
        m = (a + b) / 2
        if (_speed(code, m)[1] < 0) == (sa < 0):
            a = m
        else:
            b = m
    return (a + b) / 2


def get_retrogrades(uid, nat, planet, from_date=None, today=None, **_):
    """
    Retrograde periods, from the ephemeris: a station is where the speed
    changes sign, found by stepping a day at a time and then bisecting. The
    model never works out a retrograde date for itself; it reads it here.
    """
    name = (planet or "").strip().title()
    code = _RX_BODIES.get(name)
    if code is None:
        return {"error": f"{planet!r} has no retrograde to look up. Try Mercury, Venus, Mars, "
                         "Jupiter, Saturn, Uranus, Neptune, Pluto or Chiron."}
    start = dt.date.fromisoformat(from_date) if from_date else (today or dt.date.today())
    j0 = jd_ut(start.year, start.month, start.day, 12.0)
    cusps = (nat or {}).get("house_cusps")

    def at(jd):
        lon = _speed(code, jd)[0]
        y, m, d, _h = swe.revjul(jd)
        out = {"date": dt.date(y, m, d).isoformat(), "sign": SIGNS[int(lon // 30)],
               "degree": int(lon % 30)}
        if cusps:
            out["her_house"] = house_of(lon, cusps)
        return out

    now_rx = _speed(code, j0)[1] < 0
    periods, began = [], None
    if now_rx:                                # find when the current one began
        j = j0
        while _speed(code, j - 1)[1] < 0 and j0 - j < 400:
            j -= 1
        began = at(_station(code, j - 1, j))
    horizon = 400 if name in ("Mercury", "Venus", "Mars") else 420
    prev = _speed(code, j0)[1]
    for k in range(1, horizon + 1):
        cur = _speed(code, j0 + k)[1]
        if (prev < 0) != (cur < 0):
            st = at(_station(code, j0 + k - 1, j0 + k))
            if cur < 0:
                began = st
            else:
                periods.append({"starts": began or {"date": "before " + start.isoformat()}, "ends": st})
                began = None
            if len(periods) >= 3:
                break
        prev = cur
    if began:
        periods.append({"starts": began, "ends": {"date": "after this lookup's year"}})
    out = {"planet": name, "from": start.isoformat(), "retrograde_now": now_rx, "periods": periods}
    if not periods:
        out["note"] = f"No {name} retrograde starts in the year after {start.isoformat()}."
    return out


HANDLERS = {"get_sky": get_sky, "get_natal": get_natal,
            "compare_connections": compare_connections,
            "get_synastry": get_synastry, "search_memory": search_memory,
            "get_person_day": get_person_day,
            "find_next": find_next, "get_lunations": get_lunations,
            "get_retrogrades": get_retrogrades}


def run(name, uid, nat, args, today=None):
    """Dispatch. A tool that raises returns an error the model can read and
    recover from, rather than collapsing the whole turn. `today` is the
    conversation's date, for tools that count forward from it."""
    fn = HANDLERS.get(name)
    if fn is None:
        return {"error": f"No tool called {name!r}."}
    try:
        extra = ({"today": today}
                 if name in ("find_next", "get_person_day", "search_memory", "get_retrogrades")
                 and today else {})
        return fn(uid, nat, **(args or {}), **extra)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
