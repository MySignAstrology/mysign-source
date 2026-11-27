"""
A placement deep-dive: everything about her Sun, Moon or Rising, in layers.

Tap a card in the reading and this is what opens. It is meant to be long — a
place to get lost reading about yourself — without ever saying something the
chart or her data doesn't support. The layers, cheapest first:

  structure   the placement's anatomy: degree, decan and term, sign, house,
              element and mode, dignity, the planet ruling it and where that
              planet sits (the dispositor chain), every aspect to it.
              Pure geometry plus the tables in placement_kb.
  her data    what her own check-ins and conversations show that relates to
              this placement — counts, averages, and an honest "not enough yet"
              when there isn't enough. The product rule: traits come from her
              data, never from a sign.
  sky         the astronomy of the moment (engine/birthsky), the facts only.
  classical   what Ptolemy and Lilly said, with page citations (engine/classical).
  narrative   one model call that writes the long read from all of the above,
              grounding-checked sentence by sentence (engine/grounding). Cached:
              the chart never changes; the read is refreshed as her data grows.

Every section can be marked "this is me" / "not me"; the next narrative is
told which ones didn't fit.
"""
import contextlib
import json
import math
import os
import re
import statistics

from engine import classical, corpus, glossary, grounding, models
from engine import interpret as I
from engine import placement_kb as KB

MODEL = models.READING
VERSION = 1
POINTS = ("sun", "moon", "rising")
MIN_CHECKINS = 14          # below this, her-data stats are shown as "building"
MIN_GROUP = 4


def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _dms(lon):
    total = round((lon % 30) * 60)          # whole arcminutes; 23°59.7' is 24°00'
    return f"{total // 60}°{total % 60:02d}'"


# ================================================================ structure

def anchor(chart, point):
    """(longitude, sign, house, planet-name) of the placement."""
    if point == "rising":
        lon = chart["angles"]["Ascendant"]["lon"]
        return lon, KB.SIGNS[int(lon // 30)], 1, "Ascendant"
    name = {"sun": "Sun", "moon": "Moon"}[point]
    p = chart["planets"][name]
    return p["lon"], p["sign"], p.get("house"), name


def _dispositors(chart, sign, rulerships=None):
    """Follow 'ruled by' until it loops or lands on a planet in its own sign.

    In her chosen scheme (engine/tradition): the chain was hard-coded
    traditional, so on "modern" the reading said her chart answers to Pluto
    while this page said Scorpio is ruled by Mars."""
    from engine import tradition
    table = tradition.rulerships(rulerships)
    chain, seen = [], set()
    while True:
        ruler = table[sign]
        p = chart["planets"].get(ruler)
        if not p or ruler in seen:
            break
        seen.add(ruler)
        chain.append({"planet": ruler, "sign": p["sign"], "house": p.get("house")})
        if table[p["sign"]] == ruler:             # at home: the chain ends here
            break
        sign = p["sign"]
        if len(chain) >= 4:
            break
    return chain


def _moon_range(out, chart):
    """
    A Moon charted at noon for an unknown time, said as the range it really is.

    It moves about thirteen degrees across her birth day, so "1°20' Capricorn"
    was a precision the chart does not have — and the decan and term hang off
    that degree. The range spans her whole local birth day (the noon chart
    ±12 hours); a sign it may not be in is not claimed either.
    """
    from engine.chart import fmt, positions
    lo = positions(chart["jd"] - 0.5)["Moon"]["lon"]
    hi = positions(chart["jd"] + 0.5)["Moon"]["lon"]
    certain, signs = I.sign_steady(chart["jd"], "Moon")
    out["degree_range"] = (f"Your Moon was somewhere between {fmt(lo)['label']} and {fmt(hi)['label']} "
                           "— without a birth time, its degree is a range")
    out["signs"] = signs
    out["sign_certain"] = certain
    out["decan"] = out["term"] = None
    out["late_or_early"] = None
    if not certain:
        out["dignity"] = out["dignity_note"] = None


def structure(chart, point, time_known=True, rulerships=None):
    lon, sign, house, name = anchor(chart, point)
    elem, mode = I.ELEMENT[sign], I.MODALITY[sign]
    decan, face_ruler = KB.face(lon)
    term_ruler = KB.term(lon)
    houses_ok = time_known
    dig = corpus.dignity(name, sign) if name in corpus.PLANETS else None

    aspects = []
    for a in I.natal_aspects(chart):
        if name not in (a["a"], a["b"]):
            continue
        other = a["b"] if a["a"] == name else a["a"]
        if other in ("TrueNode",) or (not time_known and other in ("Ascendant", "Midheaven")):
            continue
        if a["orb"] > (6 if point != "rising" else 5):
            continue
        meaning = (corpus.aspect_between(name, other, a["aspect"])
                   if name in corpus.PLANETS else None)
        aspects.append({"other": other, "aspect": a["aspect"], "orb": a["orb"],
                        "other_sign": (chart["planets"].get(other) or chart["angles"].get(other)
                                       or {}).get("sign"),
                        "tension": a["aspect"] in ("square", "opposition"),
                        "meaning": (meaning or {}).get("reading")})
    aspects = aspects[:8]

    out = {
        "point": point, "label": KB.ROLE[point]["label"], "planet": name,
        "sign": sign, "degree": _dms(lon), "degree_num": round(lon % 30, 2),
        "house": house if houses_ok else None,
        "element": elem, "modality": mode,
        "element_function": KB.ELEMENT_FUNCTION[elem],
        "modality_function": KB.MODALITY_FUNCTION[mode],
        "sign_kb": KB.SIGN[sign],
        "role": KB.ROLE[point],
        "decan": {"n": decan, "ruler": face_ruler, "key": KB.PLANET_KEY.get(face_ruler)},
        "term": {"ruler": term_ruler, "key": KB.PLANET_KEY.get(term_ruler)},
        "dignity": dig,
        "dignity_note": corpus.DIGNITY_NOTE.get(dig) if dig else None,
        "ruler_chain": (_dispositors(chart, sign, rulerships)
                        if houses_ok or point != "rising" else []),
        "aspects": aspects,
        "house_kb": KB.HOUSE.get(house) if houses_ok and house else None,
        "late_or_early": ("This is an early degree, so the sign's qualities feel fresh and still arriving"
                          if lon % 30 < 3 else
                          "This is a late degree, so the sign's qualities are fully ripe and already leaning toward the next sign"
                          if lon % 30 >= 27 else None),
    }
    if point == "moon" and not time_known and chart.get("jd"):
        _moon_range(out, chart)
    if point == "rising":
        out["house"] = None                     # the Ascendant *is* the 1st-house cusp
        out["house_kb"] = None
    return out


# ================================================================ her data

def _bucket_phase(name):
    n = (name or "").lower()
    if "new" in n:
        return "new moon"
    if "full" in n:
        return "full moon"
    if "waxing" in n or "first" in n:
        return "waxing"
    if "waning" in n or "last" in n:
        return "waning"
    return None


def _welch_p(a, b):
    """Two-sided p for a difference in means, normal approximation. Small
    samples overstate certainty here, which is why MIN_GROUP gates use."""
    if len(a) < 2 or len(b) < 2:
        return 1.0
    va, vb = statistics.variance(a), statistics.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b)) or 1e-9
    z = abs(statistics.mean(a) - statistics.mean(b)) / se
    return math.erfc(z / math.sqrt(2))


def _compare(label_in, label_out, inside, outside):
    if len(inside) < MIN_GROUP or len(outside) < MIN_GROUP:
        return None
    mi, mo = statistics.mean(inside), statistics.mean(outside)
    p = _welch_p(inside, outside)
    diff = mi - mo
    verdict = ("a clear difference" if p < 0.05 and abs(diff) >= 0.8 else
               "a hint, not yet reliable" if abs(diff) >= 0.8 else
               "no real difference")
    return {"in": label_in, "out": label_out, "mean_in": round(mi, 1), "mean_out": round(mo, 1),
            "n_in": len(inside), "n_out": len(outside), "diff": round(diff, 1),
            "p": round(p, 3), "verdict": verdict}


def her_data(episodes, chart, point, patterns=None):
    """Stats from her own logged days that touch this placement. Honest about n."""
    moods = [e for e in episodes if isinstance(e.get("mood"), (int, float))]
    out = {"checkins": len(moods), "enough": len(moods) >= MIN_CHECKINS,
           "need_more": max(0, MIN_CHECKINS - len(moods)), "findings": [],
           "verified_patterns": len(patterns or [])}
    if moods:
        vals = [e["mood"] for e in moods]
        out["average_mood"] = round(statistics.mean(vals), 1)
        out["range"] = [min(vals), max(vals)]
        first = min(e["date"] for e in moods)
        out["since"] = first
    if not out["enough"]:
        return out

    _, sign, _, _ = anchor(chart, point)

    def mood_where(pred):
        a = [e["mood"] for e in moods if pred(e)]
        b = [e["mood"] for e in moods if not pred(e)]
        return a, b

    a, b = mood_where(lambda e: (e.get("sky_state") or {}).get("moon_sign") == sign)
    what = {"sun": "your Sun sign", "moon": "your Moon sign", "rising": "your rising sign"}[point]
    f = _compare(f"days the Moon was in {sign} ({what})", "other days", a, b)
    if f:
        f["why"] = ("The Moon passes through each sign for about two and a half days a month. "
                    f"Tradition says its trip through {what} is a monthly check-in with that part of you.")
        out["findings"].append(f)

    if point == "moon":
        groups = {}
        for e in moods:
            g = _bucket_phase((e.get("sky_state") or {}).get("moon_phase"))
            if g:
                groups.setdefault(g, []).append(e["mood"])
        shown = {g: v for g, v in groups.items() if len(v) >= MIN_GROUP}
        if len(shown) >= 2:
            out["phases"] = {g: {"mean": round(statistics.mean(v), 1), "n": len(v)}
                             for g, v in shown.items()}
            hi = max(shown, key=lambda g: statistics.mean(shown[g]))
            lo = min(shown, key=lambda g: statistics.mean(shown[g]))
            rest = [m for g, v in shown.items() if g != hi for m in v]
            c = _compare(f"{hi} days", "the rest of the cycle", shown[hi], rest)
            if c:
                c["why"] = (f"Your highest average is around the {hi}, your lowest around the "
                            f"{lo}. Research on the Moon and sleep is mixed — some studies find "
                            "lighter sleep before the full moon, others nothing — so your own "
                            "data is the only version that counts.")
                out["findings"].append(c)
    return out


# ================================================================ sky + classical

# Where the IAU (1930) constellation boundaries cross the ecliptic, J2000
# longitude. The Sun spends 7 days in Scorpius and 45 in Virgo, and crosses
# Ophiuchus, which no zodiac uses.
CONSTELLATIONS = [
    (28.687, "Pisces"), (53.417, "Aries"), (90.140, "Taurus"), (117.988, "Gemini"),
    (138.038, "Cancer"), (173.851, "Leo"), (217.810, "Virgo"), (241.047, "Libra"),
    (247.638, "Scorpius"), (266.238, "Ophiuchus"), (299.656, "Sagittarius"),
    (327.488, "Capricornus"), (351.650, "Aquarius"), (360.0, "Pisces"),
]
SIGN_TO_CONSTELLATION = dict(zip(KB.SIGNS, [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpius",
    "Sagittarius", "Capricornus", "Aquarius", "Pisces"]))


def constellation(j2000_lon):
    """The constellation at an ecliptic longitude ON the ecliptic. Right for
    the Sun, which never leaves it; for anything else use constellation_at."""
    lon = j2000_lon % 360
    return next(name for edge, name in CONSTELLATIONS if lon < edge)


_GRID = None
# B1875.0, the epoch the IAU boundaries are drawn on, as a Julian day.
B1875_JD = 2405889.258550475


def _grid():
    global _GRID
    if _GRID is None:
        path = os.path.join(os.path.dirname(__file__), "reference", "constellations.json")
        with open(path) as fh:
            doc = json.load(fh)
        rows = []
        for runs in doc["rows"]:
            row = []
            for k, n in runs:
                row += [k] * n
            rows.append(row)
        _GRID = (doc["ra_hours"], doc["dec_deg"], rows, doc["names"])
    return _GRID


def _precess_j2000(ra_deg, dec_deg, jd):
    """J2000 equatorial -> mean equator and equinox of `jd` (IAU 1976
    precession, Lieske 1977). Plenty for a boundary lookup: the error at
    B1875 is well under an arcsecond."""
    t = (jd - 2451545.0) / 36525
    asec = math.pi / 180 / 3600
    zeta = (2306.2181 * t + 0.30188 * t * t + 0.017998 * t ** 3) * asec
    z = (2306.2181 * t + 1.09468 * t * t + 0.018203 * t ** 3) * asec
    theta = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) * asec
    a0, d0 = math.radians(ra_deg), math.radians(dec_deg)
    A = math.cos(d0) * math.sin(a0 + zeta)
    B = math.cos(theta) * math.cos(d0) * math.cos(a0 + zeta) - math.sin(theta) * math.sin(d0)
    C = math.sin(theta) * math.cos(d0) * math.cos(a0 + zeta) + math.cos(theta) * math.sin(d0)
    ra = (math.degrees(math.atan2(A, B) + z)) % 360
    return ra, math.degrees(math.asin(max(-1.0, min(1.0, C))))


def constellation_at(ra_j2000_deg, dec_j2000_deg):
    """
    The IAU constellation at a real position on the sky.

    The Moon strays up to ~5.3° from the ecliptic, and reading its
    constellation off the ecliptic crossings was wrong about one time in eight
    (Aries for a Moon in front of Cetus). This precesses the position to B1875
    and reads the boundary grid (tools/build_constellations.py).
    """
    import bisect
    ra_h, dec_d, rows, names = _grid()
    ra, dec = _precess_j2000(ra_j2000_deg, dec_j2000_deg, B1875_JD)
    i = bisect.bisect_left(ra_h, ra / 15)
    j = bisect.bisect_right(dec_d, dec)
    return names[rows[i][j]]


def body_constellation(jd, body):
    """Constellation behind a Swiss Ephemeris body at `jd` (UT)."""
    import swisseph as swe

    from engine.ephemeris import FLAGS
    eq = swe.calc_ut(jd, body, FLAGS | swe.FLG_J2000 | swe.FLG_EQUATORIAL)[0]
    return constellation_at(eq[0], eq[1])


def astronomy(birth_ut, point, time_known=True):
    """Facts birthsky doesn't already give: the real constellation behind the
    Sun and Moon, precession, and how fast the rising degree was moving."""
    import swisseph as swe

    from engine.chart import jd_ut
    from engine.ephemeris import FLAGS
    y, m, d, h, lat, lon = birth_ut
    jd = jd_ut(int(y), int(m), int(d), float(h))
    out = []
    if point in ("sun", "moon"):
        body = swe.SUN if point == "sun" else swe.MOON
        trop = swe.calc_ut(jd, body, FLAGS)[0][0]
        sign, con = KB.SIGNS[int(trop // 30)], body_constellation(jd, body)
        name = "Sun" if point == "sun" else "Moon"
        # Without a time the Moon could be ~6° either way, which is enough to
        # put a different constellation behind it. Say nothing rather than
        # state one as "real sky fact".
        if point == "moon" and not time_known:
            pass
        elif SIGN_TO_CONSTELLATION[sign] == con:
            out.append({"kind": "stars", "title": f"Your {name} really was in {con}",
                        "body": f"Against the actual stars, the {name} sat in the constellation "
                                f"{con} — sign and constellation agree, which is no longer true "
                                "for most people."})
        else:
            prec = swe.get_ayanamsa_ut(jd)
            out.append({"kind": "stars", "title": f"Your {name} sign is {sign}; the stars behind it were {con}",
                        "body": f"Both are true. Signs follow the seasons — {sign} is a fixed "
                                f"stretch measured from the spring equinox — while constellations "
                                f"are star patterns. The equinox has drifted about {prec:.0f}° "
                                "against the stars since the zodiac was named, so the two maps no "
                                f"longer line up. On your birthday the {name} was in front of {con}."})
    if point == "rising" and time_known:
        # The Ascendant does not depend on the house system; asking Placidus
        # for it raised inside the polar circles — where this fact matters
        # most — and the suppress() in sky_items hid it.
        from engine.chart import angles_only
        a0 = angles_only(jd, lat, lon)[0]
        a1 = angles_only(jd + 4 / 1440, lat, lon)[0]
        moved = (a1 - a0) % 360
        if moved:
            out.append({"kind": "horizon", "title": "How fast your rising sign was moving",
                        "body": f"The degree on the eastern horizon was moving {moved:.2f}° every "
                                f"four minutes — about {round(30 / moved * 4)} minutes per sign at "
                                "your birthplace. That's why a birth time even half an hour off "
                                "can change the rising sign."})
    return out


SKY_KINDS = {"sun": ("light", "eclipse"), "moon": ("moon",),
             "rising": ("overhead", "venus", "station")}


def sky_items(sky, point, birth_ut=None, time_known=True):
    """Birth-sky facts for this placement, as {kind, title, body} items."""
    items = []
    if sky:
        try:
            from engine import birthsky
            items = [i for i in birthsky.describe(sky) if i.get("kind") in SKY_KINDS[point]]
        except Exception:
            items = []
        syz = sky.get("syzygy") or {}
        if point == "moon" and syz.get("kind"):
            items.append({"kind": "syzygy", "title": f"The {syz['kind']} before you",
                          "body": f"{round(syz.get('days_before', 0))} days before you were born, there "
                                  f"was a {syz['kind']} at {syz.get('label')}. Tradition, going back to "
                                  f"Ptolemy, gives the last {syz['kind']} before a birth a say in the chart."})
        sun_d = (sky.get("distances") or {}).get("Sun")
        if point == "sun" and sun_d:
            items.append({"kind": "distance", "title": "Light from the Sun",
                          "body": f"The Sun was {sun_d['km'] / 1e6:.1f} million km away; the "
                                  f"sunlight of your birthday left it {sun_d['light_minutes']} "
                                  "minutes earlier."})
    if birth_ut:
        with contextlib.suppress(Exception):
            items += astronomy(birth_ut, point, time_known)
    return items


def classical_items(chart, sky, time_known, point):
    want = {"sun": ("Sun", "sect", "temperament"), "moon": ("Moon", "temperament", "sect"),
            "rising": ("Ascendant", "rising", "temperament")}[point]
    try:
        items = classical.items(chart, sky or {}, time_known)
    except Exception:
        return []
    out = []
    for it in items:
        blob = f"{it.get('kind', '')} {it.get('title', '')} {it.get('body', '')}"
        if any(w.lower() in blob.lower() for w in want):
            out.append(it)
    return out[:4]


# ================================================================ narrative

SECTIONS = [
    ("heart", "The heart of it"),
    ("daily", "How it shows up day to day"),
    ("tension", "The tension you carry"),
    ("close", "Up close — love and the people you let in"),
    ("best_worst", "At your best, under pressure"),
    ("data", "What your own days say"),
    ("edge", "Your growing edge"),
]
SCHEMA = {
    "type": "object",
    "properties": {
        "tagline": {"type": "string"},
        **{k: {"type": "string"} for k, _ in SECTIONS},
    },
    "required": ["tagline"] + [k for k, _ in SECTIONS],
    "additionalProperties": False,
}

PROMPT = """You are writing the deep read of ONE placement in My Sign, an astrology app.
She tapped her {label} card because she wants to get lost reading about herself.

Write it like a perceptive friend who knows astrology deeply and also respects
her intelligence: specific, warm, a little bold, never generic, never flattery
for its own sake. Second person. No headings inside sections. No bullet lists.

Hard rules:
- Use ONLY the facts below. Never mention a planet, sign, house or aspect that is
  not listed. Never invent a degree.
- Astrology here is a symbolic lens, not a cause. Say "tradition reads this as",
  "this placement is associated with", "you may recognise" — never "because you
  are a {sign}, you are X".
- Name trade-offs, not just strengths. Make statements specific enough that she
  could disagree with them.
- The "data" section may only use HER DATA below. If it says there isn't enough
  yet, say so plainly and tell her what checking in will reveal. Never claim a
  pattern the numbers don't show; repeat numbers exactly. Her words may be
  echoed back, never twisted.
- If she marked a section as "not me" before, approach that theme differently.

Length: tagline one line (under 90 characters). Every other section 170–260
words. Weave in the decan, term, ruler chain, aspects and house where they
genuinely add something — that is what makes this hers and not every {sign}'s.

FACTS
{facts}

HER DATA
{data}

NOT ME (sections she rejected before)
{rejected}
"""
# Beginners first: every technical word explained where it appears (engine/glossary).
PROMPT = PROMPT + "\n\n" + glossary.PLAIN_RULE



def facts_text(st, chart, analysis, sky_l, classical_l):
    s, kb = st, st["sign_kb"]
    where = (f"{' or '.join(s['signs'])} (birth time unknown; {s['degree_range']})"
             if s.get("degree_range") and not s.get("sign_certain", True)
             else f"{s['sign']}, degree uncertain ({s['degree_range']})" if s.get("degree_range")
             else f"{s['sign']} at {s['degree']}")
    lines = [
        f"Placement: {s['label']} in {where}"
        + (f", {_ordinal(s['house'])} house" if s.get("house") else ""),
        f"What the {s['label']} is: {s['role']['about']}",
        f"{s['sign']}: {kb['image']} Element {s['element']} ({s['element_function'][0]}); "
        f"{s['modality']}. {s['modality_function']}",
        f"{s['sign']} gift: {kb['gift']} Shadow: {kb['shadow']} Need: {kb['need']} "
        f"Growth: {kb['growth']} In love: {kb['love']} Under stress: {kb['stress']} "
        f"At best: {kb['best']}",
    ]
    if s.get("decan"):
        lines += [
            f"Decan: {_ordinal(s['decan']['n'])} decan of {s['sign']}, ruled by {s['decan']['ruler']} "
            f"({s['decan']['key']}).",
            f"Term: the term of {s['term']['ruler']} ({s['term']['key']}).",
        ]
    if s.get("late_or_early"):
        lines.append(f"Degree: {s['late_or_early']}.")
    if s.get("dignity"):
        lines.append(f"Dignity: {s['label']} in {s['sign']} is {s['dignity']} — {s['dignity_note']}.")
    if s.get("house_kb"):
        lines.append(f"House: {s['label']} in the {_ordinal(s['house'])} house — "
                     f"{s['house_kb'][0]}. {s['house_kb'][1]}")
    for i, c in enumerate(s["ruler_chain"]):
        h = f", {_ordinal(c['house'])} house" if c.get("house") and analysis.get("time_known") else ""
        lines.append(("Ruler: " if i == 0 else "Its ruler: ")
                     + f"{c['planet']} in {c['sign']}{h}.")
    for a in s["aspects"]:
        other = f"{a['other']} in {a['other_sign']}" if a.get("other_sign") else a["other"]
        lines.append(f"Aspect: {s['planet']} {a['aspect']} {other} (orb {a['orb']}°)."
                     + (f" {a['meaning']}" if a.get("meaning") else ""))
    b = analysis["big3"]
    lines.append(f"The rest of her big three: Sun in {b['sun']['sign']}, Moon in {b['moon']['sign']}"
                 + (f", {b['rising']['sign']} rising" if b.get("rising") else "") + ".")
    lines.append(f"Chart balance: mostly {analysis['element']}, mostly {analysis['modality']}.")
    lines += [f"Sky at birth: {x['title']}. {x['body']}" for x in sky_l]
    lines += [f"Classical ({c.get('source', '')}): {c.get('title', '')}. {c.get('body', '')}"
              for c in classical_l]
    return "\n".join(lines)


def data_text(d, episodes):
    if not d["checkins"]:
        return "She has not checked in yet."
    lines = [f"Check-ins logged: {d['checkins']} (since {d.get('since')}); "
             f"average mood {d.get('average_mood')} on a 1–10 scale."]
    if not d["enough"]:
        lines.append(f"Not enough yet for patterns: {d['need_more']} more check-ins needed.")
    for f in d["findings"]:
        lines.append(f"{f['in']}: average {f['mean_in']} (n={f['n_in']}) vs {f['out']}: "
                     f"{f['mean_out']} (n={f['n_out']}) — {f['verdict']}.")
    quotes = [e.get("her_words") for e in episodes[-30:] if e.get("her_words")]
    for q in quotes[-3:]:
        lines.append(f'Her words (a recent day): "{str(q)[:200]}"')
    return "\n".join(lines)


_SENT = re.compile(r"(?<=[.!?])\s+")


def _ground(text, context):
    """Keep every sentence that passes the grounding check; drop the rest."""
    kept, dropped = [], []
    for s in _SENT.split(text or ""):
        ok, bad = grounding.check(s, context)
        (kept if ok else dropped).append(s if ok else bad)
    return " ".join(kept).strip(), [b for x in dropped for b in x]


def narrative(st, chart, analysis, sky_l, classical_l, her, episodes, rejected=(),
              client=None):
    """The long read, or None without a model / on failure."""
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        import anthropic
        client = anthropic.Anthropic()
    facts = facts_text(st, chart, analysis, sky_l, classical_l)
    data = data_text(her, episodes)
    context = facts + "\n" + data
    try:
        r = client.messages.create(
            model=MODEL, max_tokens=16000,
            thinking={"type": "adaptive"},
            # medium, not high: the read has to land inside the Lambda's 120s.
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(
                label=st["label"], sign=st["sign"], facts=facts, data=data,
                rejected=", ".join(rejected) or "none")}])
    except Exception as e:
        from app import log
        log.error("placement.narrative.failed", exc=e)
        return None
    if r.stop_reason != "end_turn":
        return None
    text = next((b.text for b in r.content if b.type == "text"), "")
    try:
        out = json.loads(text)
    except ValueError:
        return None
    dropped_all = []
    for k in ["tagline"] + [k for k, _ in SECTIONS]:
        out[k], dropped = _ground(out.get(k, ""), context)
        dropped_all += dropped
    if dropped_all:
        from app import log
        log.emit("placement.ungrounded", level="error", claims=dropped_all[:5])
    return out


# "It makes a square to your Moon" means nothing to a beginner.
ASPECT_PLAIN = {
    "conjunction": "a conjunction (sitting right alongside)",
    "opposition": "an opposition (a pull from the opposite side)",
    "square": "a square (a tense angle)",
    "trine": "a trine (an easy, flowing angle)",
    "sextile": "a sextile (a helpful angle)",
}


def fallback_narrative(st, her):
    """The same shape from the knowledge base, for demo mode and failed calls."""
    s, kb, role = st, st["sign_kb"], st["role"]
    house = (f" It sits in your {_ordinal(s['house'])} house, the area of {s['house_kb'][0]}. "
             f"{s['house_kb'][1]}") if s.get("house_kb") else ""
    chain = s["ruler_chain"]
    key = KB.PLANET_KEY.get(chain[0]["planet"]) if chain else None
    rp = (("the " if chain[0]["planet"] in ("Sun", "Moon") else "") + chain[0]["planet"]) if chain else ""
    ruler = (f" The ruling planet of {s['sign']} is {rp}, which sits in {chain[0]['sign']} in "
             f"your chart — so this part of you takes on {rp}'s themes"
             + (f" ({key})" if key else "")
             + f", expressed in {'an' if chain[0]['sign'][0] in 'AEIOU' else 'a'} {chain[0]['sign']} way.") if chain else ""
    asp = s["aspects"][:2]
    asp_t = " ".join(f"It makes {ASPECT_PLAIN.get(a['aspect'], 'an aspect')} to your {a['other']} "
                     f"({a['orb']}° from exact)."
                     + (" " + a["meaning"] if a.get("meaning") else "") for a in asp)
    data = (f"You've logged {her['checkins']} check-in{'s' if her['checkins'] != 1 else ''}. "
            + (f"After {her['need_more']} more, this section starts showing what your own days say "
               "about this part of you — real averages, with the numbers shown."
               if not her["enough"] else
               " ".join(f"On {f['in']} your mood averaged {f['mean_in']} against {f['mean_out']} "
                        f"on {f['out']} ({f['verdict']})." for f in her["findings"])
               or "So far your days don't show a difference tied to this placement — which is a real answer too.")
            ) if her["checkins"] else ("Check in for a couple of weeks and this section fills with "
                                        "what your own days say — averages and counts, never guesses.")
    return {
        "tagline": (f"{s['sign']} rising" if s["point"] == "rising" else f"{s['label']} in {s['sign']}")
                   + f" — {kb['gift'].split(',')[0].lower()}.",
        "heart": (f"Your rising sign is {s['sign']}." if s["point"] == "rising"
                  else f"Your {s['label']} is in {s['sign']}.")
                 + f" {kb['image']} Tradition links this placement with "
                 f"{kb['gift'][0].lower()}{kb['gift'][1:]}{house}",
        "daily": f"{s['element_function'][1]} {s['modality_function']} In practice: {kb['best']}"
                 f"{ruler}",
        "tension": f"The shadow side is {kb['shadow'][0].lower()}{kb['shadow'][1:]} {asp_t}".strip(),
        "close": kb["love"] + f" What you need: {kb['need'][0].lower()}{kb['need'][1:]}",
        "best_worst": f"At your best: {kb['best']} Under pressure: {kb['stress']}",
        "data": data,
        "edge": f"Your growing edge is {kb['growth'][0].lower()}{kb['growth'][1:]} "
                f"The question this placement keeps asking is: {role['question']}",
    }


# ================================================================ assembly

def build(chart, analysis, point, *, episodes=(), patterns=(), sky=None, time_known=True,
          rejected=(), want_narrative=False, client=None, birth_ut=None,
          rulerships=None):
    if point not in POINTS:
        raise ValueError(point)
    if point == "rising" and not time_known:
        return {"point": point, "unavailable": True,
                "why": "Without a birth time there is no rising sign — it changes every two hours."}
    st = structure(chart, point, time_known, rulerships)
    her = her_data(list(episodes), chart, point, patterns)
    sky_l = sky_items(sky, point, birth_ut, time_known)
    cls = classical_items(chart, sky, time_known, point)
    out = {
        "version": VERSION, "point": point, "structure": st, "her_data": her,
        "sky": sky_l, "classical": cls, "prompts": KB.PROMPTS[point], "honest": KB.HONEST,
        "sections": [{"key": k, "title": t} for k, t in SECTIONS],
    }
    if want_narrative:
        n = narrative(st, chart, analysis, sky_l, cls, her, list(episodes), rejected, client)
        out["generated"] = n is not None
        out["narrative"] = n or fallback_narrative(st, her)
    return out


def data_bucket(n_checkins):
    """The narrative is regenerated when her data grows past these marks."""
    for mark in (0, 14, 30, 60, 120, 240):
        if n_checkins < mark:
            return mark
    return 10_000


def cache_key(point):
    return f"placement-{point}-v{VERSION}"
