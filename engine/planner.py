"""
"Best day to..." — the next seven days with one person, read for a purpose.

daily.py already scores any day for a pair: the usual score moved by what the
fast planets are touching. A week of those numbers answers "when is it easiest
between us", but that is not what she is asking. She is asking when to have the
hard conversation, when to plan the date, when to ask for the thing, and which
day to leave it alone. Those are different questions with different planets
behind them, so each intent re-weighs the SAME computed reasons — it never adds
a reason, moves a date, or changes the day's score.

How a day is ranked for an intent (all of it is here, nothing in a model):

    fit = delta + SCALE · Σ_reasons  weight · multiplier(intent, transit, sign of weight)

`weight` is daily.py's own signed contribution of that transit today. The
multipliers below say which planets matter for which purpose: Mercury and the
Moon for talking, Venus for a date, and for asking a favour a hard Mars counts
extra against the day while an easy one does not count for. "space" is the
odd one out — it looks for the most tender day, the one to go gently on, so its
fit is the negative of the rest.

Ties break on the date, earliest first, so the order is fixed for a given day,
zone and pair: the sky reorders it, nothing else does.
"""
import datetime as dt
import re

from engine import compat, daily

DAYS = 7
SCALE = 6.0      # reason weights run ~0.1-1; this puts a strong reason on par with ~6 points

# intent -> transit -> (multiplier when the reason helps, multiplier when it hurts)
INTENTS = {
    "talk": {"Mercury": (1.5, 1.5), "Moon": (1.0, 1.0)},
    "date": {"Venus": (1.5, 1.5), "Moon": (0.6, 0.6), "Mars": (0.5, 0.0)},
    "ask": {"Sun": (0.8, 0.8), "Venus": (0.8, 0.8), "Mars": (0.0, 1.5)},
    "space": {"Mars": (0.0, 1.0), "Moon": (0.0, 1.0)},
}
VERB = {
    "talk": "Best day to talk it through",
    "date": "Best day for a date or quality time",
    "ask": "Best day to ask for something",
    "space": "A day to go gently",
}
# The planets each intent listens to, said plainly in the UI — "why this day?"
LISTENS = {
    "talk": "Mercury (words) and the Moon (feelings)",
    "date": "Venus (affection), the Moon and an easy Mars",
    "ask": "the Sun and Venus, and it steers away from a hard Mars",
    "space": "a hard Moon or Mars — the touchy days",
}

# Copy that would turn a sky reading into a forecast. A test runs every string
# this module writes through it.
PREDICTIVE = re.compile(r"\b(will|going to happen|guaranteed|definitely|destined)\b", re.I)


def _mult(intent, reason):
    up, down = INTENTS[intent].get(reason["transit"], (0.0, 0.0))
    return up if reason["weight"] >= 0 else down


def fit(intent, day):
    """How well one scored day suits the intent. Documented weights only."""
    extra = SCALE * sum(r["weight"] * _mult(intent, r) for r in day["reasons"])
    raw = day["delta"] + extra
    return round(-raw if intent == "space" else raw, 2)


def label(delta):
    """Honest words for the number: inside ±STEADY is 'about usual', never spun."""
    return "about usual" if abs(delta) < daily.STEADY else daily.mood(delta)


def _reason(r):
    return {k: r.get(k) for k in ("transit", "aspect", "orb", "target", "text",
                                  "short", "lasts", "weight", "kind")}


def _key_reason(intent, day):
    """The reason that most decided this day for this intent, else the day's top one."""
    rs = day["reasons"]
    if not rs:
        return None
    scored = sorted(rs, key=lambda r: -abs(r["weight"] * _mult(intent, r)))
    best = scored[0]
    return best if _mult(intent, best) else rs[0]


def _line(intent, day):
    """'Thu: Venus trines the relationship's Moon — lasts a few days.'"""
    wd = dt.date.fromisoformat(day["date"]).strftime("%a")
    r = _key_reason(intent, day)
    if not r:
        return f"{wd}: the sky is quiet between you — {day['label']}."
    return f"{wd}: {r['text']} — {r['lasts']}."


def week(prof, row, report, today, tz, days=DAYS):
    """Seven scored days for one pair, today first. Same natal setup as for_pair."""
    pm, pt = compat.my_precision(prof), compat.their_precision(row)
    mine = daily._natal(tuple(prof["birth_ut"]), pm)
    theirs = daily._natal(tuple(row["birth_ut"]), pt)
    kw = {"moon_mine": pm != "unknown", "moon_theirs": pt != "unknown"}
    name = row.get("name", "them")
    out = []
    for i in range(days):
        d = daily.day_score(report, mine, theirs, today + dt.timedelta(days=i), tz, name, **kw)
        out.append({"date": d["date"],
                    "weekday": dt.date.fromisoformat(d["date"]).strftime("%A"),
                    "score": d["score"], "usual": d["usual"], "delta": d["delta"],
                    "label": label(d["delta"]),
                    "reasons": [_reason(r) for r in d["reasons"]]})
    return out


def rank(intent, days):
    """Days best-first for the intent. Deterministic: (-fit, date)."""
    if intent not in INTENTS:
        raise ValueError(f"intent must be one of {', '.join(INTENTS)}")
    scored = [{**d, "fit": fit(intent, d)} for d in days]
    return sorted(scored, key=lambda d: (-d["fit"], d["date"]))


def plan(intent, days, name):
    """
    The answer for one intent: the days in date order, each with its fit and a
    one-line why, plus the best day. When nothing in the week moves off usual
    and no reason speaks to the intent, it says so rather than crowning a day.
    """
    ranked = rank(intent, days)
    order = {d["date"]: i for i, d in enumerate(ranked)}
    best = ranked[0]
    standout = best["fit"] >= daily.STEADY or abs(best["delta"]) >= daily.STEADY
    dated = sorted(ranked, key=lambda d: d["date"])
    for d in dated:
        d["rank"] = order[d["date"]] + 1
        d["line"] = _line(intent, d)
        d["best"] = d["date"] == best["date"]
    if standout:
        summary = f"{VERB[intent]} with {name}: {best['weekday']}. {_line(intent, best)}"
    else:
        summary = (f"No day this week stands out for that with {name} — the sky stays "
                   f"close to usual. {best['weekday']} edges it.")
    return {"intent": intent, "verb": VERB[intent], "listens_to": LISTENS[intent],
            "summary": summary, "standout": standout,
            "best_day": {k: best[k] for k in ("date", "weekday", "score", "delta", "label", "fit")},
            "days": dated}


def free_view(p):
    """
    The free plan's honest slice: which day, and its number — the day is real
    and the score is never faked. The why (the reasons and the lines that quote
    them) is what Plus adds.
    """
    return {"intent": p["intent"], "verb": p["verb"], "listens_to": p["listens_to"],
            "standout": p["standout"], "best_day": p["best_day"], "days": None,
            "summary": f"{p['verb']}: {p['best_day']['weekday']}.",
            "plus_required": True, "plus_note": "Plus shows why, and the whole week."}
