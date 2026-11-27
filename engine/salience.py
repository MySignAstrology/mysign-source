"""
Is anything actually happening today?

Deterministic — no model involved. The model is heavily biased toward "yes,
something is happening," because every piece of astrology writing it ever saw
said so. This scores the day in code and hands the model a verdict it cannot
argue with, including permission to say nothing is going on.

The non-obvious part is novelty. Pluto sits within 1 degree of a natal point for
weeks. Score on orb alone and every day for two months reads HIGH, which is
exactly how a daily push becomes noise. Slow transits decay each time we
actually surface them, and re-sharpen on their exact date.
"""
import datetime as dt

PERSONAL   = {"Sun", "Moon", "Mercury", "Venus", "Mars", "Ascendant", "Midheaven"}
FAST       = {"Moon", "Sun", "Mercury", "Venus", "Mars"}
SLOW       = {"Jupiter", "Saturn", "Uranus", "Neptune", "Pluto", "TrueNode", "Chiron"}

ASPECT_W   = {"conjunction": 1.00, "opposition": 0.90, "square": 0.85,
              "trine": 0.60, "sextile": 0.40}

# score -> (band, max_sentences, model instruction)
#
# Thresholds are CALIBRATED, not guessed: p85 and p40 of a full year of this
# chart's daily scores, which lands the mix near 15/45/40. Recalibrate per user
# from a rolling 90-day window once they have one — a chart with a lot of tight
# natal aspects is busy every day, and a global constant would either shout at
# her constantly or never speak.
BANDS = [
    (62, "high",   4, "Lead with the tightest item below. It is genuinely notable."),
    (40, "medium", 3, "Something is worth saying, but keep it light."),
    (0,  "low",    2, "Nothing tight today. Say so plainly. Do not manufacture "
                      "significance — 'quiet day, those exist' is a complete answer."),
]

MAX_ORB = 3.0


def _tightness(orb):
    """Steep falloff: 0° -> 1.0, 1° -> 0.54, 2° -> 0.19, 3° -> 0."""
    return max(0.0, 1.0 - orb / MAX_ORB) ** 1.5


def aspect_key(a):
    return f"{a['transit']}-{a['aspect']}-{a['natal']}"


def _recent_mentions(mention_log, key, today, days):
    """
    Count PRIOR days only. Today is excluded on purpose: a transit surfaced an
    hour ago is still today's news, and counting it makes the morning reading go
    quiet the moment she sends her first message.
    """
    cutoff = today - dt.timedelta(days=days)
    return sum(1 for d, keys in mention_log.items()
               if key in keys and cutoff <= dt.date.fromisoformat(d) < today)


def score_aspect(a, mention_log, today, exact_dates=None):
    w        = ASPECT_W.get(a["aspect"], 0.4)
    tight    = _tightness(a["orb"])
    personal = 1.0 if a["natal"] in PERSONAL else 0.55
    base     = 100 * w * tight * personal

    if a["transit"] in SLOW:
        # A months-long transit earns one loud day, then gets quieter.
        n = _recent_mentions(mention_log, aspect_key(a), today, days=21)
        base *= 0.5 ** n
        # ...unless it goes exact today or tomorrow. Then it is news again.
        exact = (exact_dates or {}).get(aspect_key(a))
        if exact and 0 <= (dt.date.fromisoformat(exact) - today).days <= 1:
            base = max(base, 100 * w * personal * 0.9)
    else:
        # Fast transits are inherently day-specific, so they never go stale —
        # but they are also smaller. Nobody needs a push about the Moon.
        base *= 0.75

    return round(base, 1)


def calibrate(natal_chart, today=None, days=365, sample_every=1, tz=None):
    """
    This chart's own score distribution over a year.

    The global thresholds were fitted to one chart. A chart dense with tight
    natal aspects scores above them most days — so it would be told something
    notable is happening almost every morning, which is how the push becomes
    noise. A sparse chart would be told the opposite: quiet forever, and it
    stops being worth opening.

    Thresholds are the 85th and 40th percentile of *her* year, so the mix of
    loud and quiet days is roughly constant across people even though the raw
    scores are not.
    """
    import datetime as _dt

    from engine.chart import aspects as _aspects
    from engine.chart import local_noon_jd as _noon
    from engine.chart import positions as _pos

    today = today or _dt.date.today()

    # The walk has to carry a mention log, because production does. Novelty
    # decay is what stops a six-week transit scoring high for six weeks, and
    # calibrating without it fits thresholds to scores no real user ever sees —
    # every day would look quieter than the model expected.
    log, scores = {}, []
    for offset in range(0, days, sample_every):
        day = today + _dt.timedelta(days=offset)
        # Her local noon — the instant Today scores (server._sky). Fitting
        # the thresholds at 17:00 UT put the Moon up to ~8° from where Today
        # measures it, for someone in Sydney or Manila.
        j = _noon(day, tz)
        verdict = score_day({"aspects_to_natal": _aspects(_pos(j), natal_chart),
                             "upcoming": []},
                            mention_log=log, today=day, bands=BANDS)
        scores.append(verdict["score"])
        if verdict["band"] in ("high", "medium"):
            log.setdefault(day.isoformat(), []).extend(verdict["mention_keys"])
    scores.sort()
    if not scores:
        return None

    def pct(q):
        return round(scores[min(len(scores) - 1, int(len(scores) * q))], 1)

    high, medium = pct(0.85), pct(0.40)
    if high <= medium:                       # degenerate; fall back to global
        return None
    return [(high, "high", 4, BANDS[0][3]),
            (medium, "medium", 3, BANDS[1][3]),
            (0, "low", 2, BANDS[2][3])]


def score_day(sky, mention_log=None, today=None, exact_dates=None, depth=3,
              bands=None):
    """
    sky: {"aspects_to_natal": [...], "upcoming": [{"kind","label","date"}, ...]}
    Returns a verdict dict the context assembler drops straight into the prompt.
    """
    today       = today or dt.date.today()
    mention_log = mention_log if mention_log is not None else {}

    ranked = sorted(
        ({**a, "score": score_aspect(a, mention_log, today, exact_dates)}
         for a in sky.get("aspects_to_natal", [])),
        key=lambda a: -a["score"],
    )
    total = ranked[0]["score"] if ranked else 0.0

    # Calendar events are their own kind of news, independent of aspects.
    bumps = []
    for ev in sky.get("upcoming", []):
        days = (dt.date.fromisoformat(ev["date"]) - today).days
        kind = ev["kind"]
        if kind == "lunation" and 0 <= days <= 2:
            b = 26 if not ev.get("hits_natal") else 40
        elif kind == "ingress" and 0 <= days <= 2 and ev.get("changes_house"):
            b = 20
        elif kind == "station" and 0 <= days <= 7:
            b = 22
        else:
            continue
        bumps.append({**ev, "bump": b})
        total += b

    total = round(min(total, 100.0), 1)
    table = bands or BANDS
    band, ceiling, instruction = next(b for b in table if total >= b[0])[1:]

    headlines = [a for a in ranked if a["score"] >= 18][:depth]
    return {
        "score": total,
        "band": band,
        "max_sentences": ceiling,
        "instruction": instruction,
        "headlines": headlines,
        "events": bumps,
        "mention_keys": [aspect_key(a) for a in headlines],
    }
