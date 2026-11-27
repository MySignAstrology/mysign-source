"""
Pattern unlock moments.

memory.verify() is the only thing in the product allowed to say "you, in your
own data". This module turns what it returns into a moment she sees once — the
claim with its numbers and the dates behind them — and an honest progress meter
for the ninety-odd days before there is anything to show.

Three rules this file exists to keep:

  1. Nothing unlocks that verify() did not return. A stored pattern is
     re-verified against her current episodes before it is shown, so a dict
     that found its way into the store some other way (an old job, a bad
     migration, a test) cannot become a ceremony.
  2. Every number on screen comes from that verify() result — support, lift,
     p-value — and the dates are recounted here from her episodes, not kept
     from whenever the pattern was first found.
  3. Before the first pattern holds, progress is a count, never a claim.
     "3 possibilities are being counted" is true; naming them would be a
     pattern that has not earned it, which is a horoscope with extra steps.

Co-Star has no equivalent: it does not learn from what you log, so it has
nothing of yours to tell you. This is the part of My Sign it cannot copy
without a year of her check-ins.
"""
import datetime as dt
import hashlib
import json

from engine import memory as M
from engine.store import load, save

TARGET_DAYS = 90        # about how long a real pattern takes to clear the gates
SEEN = "pattern-seen"   # {pattern_id: {"at": iso, "support": "..."}}

# Outcomes the code-side enumerator tests. Thresholds sit either side of the
# middle of the 1-10 scale, so "below 5" and "above 6" never overlap.
LOW, HIGH = 5, 6
MIN_TOPIC_DAYS = 3


# ----------------------------------------------------------------- identity

def pattern_id(p):
    """Stable across re-runs: what the pattern IS (condition + outcome), not
    its numbers, which move every week as days are added."""
    key = json.dumps({"c": p["condition"], "o": p["outcome"]}, sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _proposal(p):
    c = p["condition"]
    return M.Proposal(claim=p.get("claim", ""),
                      condition=M.Condition(kind=c["kind"], value=str(c["value"]),
                                            window_days=int(c.get("window_days") or 0)),
                      outcome_kind=p["outcome"]["kind"],
                      outcome_value=str(p["outcome"]["value"]))


def reverify(uid, p):
    """The pattern as verify() sees it today, or None. Uses the same number of
    tests the weekly job corrected for, so the bar is not lowered on re-read.

    A pattern stored before the job recorded n_tests has no bar to hold it to.
    Re-verifying it at n_tests=1 would be the uncorrected alpha — one test where
    the job may have run hundreds — so it stays shut until the next weekly run
    re-derives it and stamps the count. A week's wait beats a false unlock."""
    try:
        n = int(p.get("n_tests") or 0)
    except (TypeError, ValueError):
        n = 0
    if n < 1:
        return None
    try:
        v = M.verify(uid, _proposal(p), n_tests=n)
    except Exception:
        return None
    if v:
        v["id"] = pattern_id(v)
        v["n_tests"] = n
    return v


# ----------------------------------------------------------------- words

def condition_words(c):
    k, v, w = c["kind"], str(c["value"]), int(c.get("window_days") or 0)
    if k == "moon_phase":
        base = f"on {v} days"
    elif k == "moon_sign":
        base = f"on days the Moon is in {v}"
    elif k == "weekday":
        base = f"on {v}s"
    elif k == "transit_aspect":
        parts = v.split("-")
        verb = {"conjunction": "meets", "opposition": "opposes", "square": "squares",
                "trine": "trines", "sextile": "sextiles"}
        base = (f"on days transiting {parts[0]} {verb.get(parts[1], parts[1])} your {parts[2]}"
                if len(parts) == 3 else f"on days with {v}")
    elif k == "house_transit":
        base = f"on days something transits your house {v}"
    elif k == "tag":
        base = f"on days you tagged {v}"
    elif k == "tag_day_before":
        base = f"on the day after you tag {v}"
    else:
        base = f"when {v}"
    if w:
        base += " (or the day before)" if w == 1 else f" (or the {w} days before)"
    return base


def outcome_words(o):
    k, v = o["kind"], o["value"]
    if k == "mood_below":
        return f"a mood below {v}"
    if k == "mood_above":
        return f"a mood above {v}"
    return f"a day about {v}"


def _cap(s):
    return s[:1].upper() + s[1:]


def _today_nudge(o):
    """The heads-up fires on a day the condition holds again; say so in her terms."""
    return {"mood_below": " Today's one of those days, so go easy on yourself.",
            "mood_above": " Today's one of those days, so make the most of it."}.get(o["kind"], "")


def lift_words(lift):
    """4.29 -> 'over 4 times as often as usual'. The exact number stays in the data."""
    if lift < 2:
        return "more often than usual"
    whole = int(lift)
    return (f"over {whole} times as often as usual" if lift - whole >= 0.2
            else f"about {round(lift)} times as often as usual")


def p_words(p):
    """verify() rounds to 4 places; a 0.0 there means smaller, not zero."""
    return "p<0.0001" if p < 0.0001 else f"p={p}"


def claim_for(c, o):
    """The no-number claim the code-side proposer uses. 'you logged' is in
    every one: the pattern is about what she wrote down, not about her sign."""
    return f"{_cap(condition_words(c))}, you logged {outcome_words(o)}."


# ----------------------------------------------------------------- evidence

def evidence(uid, p):
    """The dates behind the numbers — days where the condition held, split by
    whether the outcome did. Recounted, using memory's own predicates."""
    cond = _proposal(p).condition
    hits, misses = [], []
    eps = load(uid, "episodes", []) or []
    by_date = {e["date"]: e for e in eps}
    for ep in eps:
        o = M._outcome_holds(p["outcome"]["kind"], str(p["outcome"]["value"]), ep)
        if o is None or not M._condition_holds(cond, ep, by_date):
            continue
        (hits if o else misses).append(ep["date"])
    return {"hits": hits, "misses": misses}


def card(uid, v):
    """What the reveal shows. Built only from a verify() result."""
    hit, tot = (int(x) for x in v["support"].replace("of the last", "of").split(" of "))
    ev = evidence(uid, v)
    cond, outc = condition_words(v["condition"]), outcome_words(v["outcome"])
    return {
        "id": v["id"],
        "title": "Okay, you have a pattern",
        "text": (f"{_cap(cond)}, you logged {outc} — {hit} of the last {tot} times. "
                 f"That's {lift_words(v['lift'])}, which is not a coincidence."),
        "p_text": p_words(v["p_value"]), "lift_text": lift_words(v["lift"]),
        "condition": cond, "outcome": outc,
        "hit": hit, "of": tot, "support": v["support"],
        "lift": v["lift"], "p_value": v["p_value"], "rate": v["rate"],
        "base_rate": v["base_rate"], "confidence": v["confidence"],
        "dates": ev["hits"][-12:], "misses": ev["misses"][-12:],
        "tests_corrected_for": v.get("n_tests", 1),
    }


# ----------------------------------------------------------------- unlocks

def current(uid):
    """Every stored pattern that still verifies, with its id."""
    out = []
    for p in load(uid, "patterns", []) or []:
        v = reverify(uid, p)
        if v:
            out.append(v)
    return out


def pending(uid):
    """Patterns she has not seen yet, as reveal cards. Re-verified first."""
    seen = load(uid, SEEN, {}) or {}
    return [card(uid, v) for v in current(uid) if v["id"] not in seen]


def mark_seen(uid, pid):
    """Only a pattern that verifies now can be marked; an unknown id is a no."""
    v = next((x for x in current(uid) if x["id"] == pid), None)
    if not v:
        return False
    seen = load(uid, SEEN, {}) or {}
    seen[pid] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 "support": v["support"]}
    save(uid, SEEN, seen)
    return True


def prune_seen(uid, survivors):
    """
    Called after the weekly job. A pattern that dropped out is forgotten as
    seen, so if her data brings it back later it unlocks again — with the new
    numbers, which are the ones that are true then.
    """
    seen = load(uid, SEEN, {}) or {}
    live = {pattern_id(p) for p in survivors}
    keep = {k: v for k, v in seen.items() if k in live}
    if keep != seen:
        save(uid, SEEN, keep or None)


def find(uid, pid):
    """One verified pattern as a card, for the share image. None if it no
    longer holds — a card for a pattern that stopped being true is not sent."""
    v = next((x for x in current(uid) if x["id"] == pid), None)
    return card(uid, v) if v else None


# ----------------------------------------------------------------- proposals

def candidates(uid):
    """
    Code-side proposals, for when there is no model to propose.

    Every observed Moon phase, Moon sign, weekday and recurring aspect against
    low mood, high mood and recurring topics. Conditions and outcomes are
    pre-filtered only on how often each occurs ON ITS OWN — never on how they
    co-occur — so the filter cannot sneak a look at the answer. The weekly job
    then Bonferroni-corrects for however many this returns, which makes a long
    list strict, not lenient.
    """
    eps = load(uid, "episodes", []) or []
    if len(eps) < 10:
        return []
    conds = {}

    def add(kind, value):
        conds.setdefault((kind, value), 0)
        conds[(kind, value)] += 1

    for e in eps:
        sky = e.get("sky_state") or {}
        if sky.get("moon_phase"):
            add("moon_phase", sky["moon_phase"])
        if sky.get("moon_sign"):
            add("moon_sign", sky["moon_sign"])
        add("weekday", dt.date.fromisoformat(e["date"]).strftime("%A"))
        for k in sky.get("aspect_keys") or []:
            add("transit_aspect", k)
        for t in e.get("tags") or []:
            add("tag", t)
    conds = [k for k, n in conds.items() if n >= 3]

    outs = []
    moods = [e.get("mood") for e in eps if isinstance(e.get("mood"), (int, float))]
    if sum(m < LOW for m in moods) >= 3:
        outs.append(("mood_below", str(LOW)))
    if sum(m > HIGH for m in moods) >= 3:
        outs.append(("mood_above", str(HIGH)))
    topics = {}
    for e in eps:
        for t in e.get("topics") or []:
            topics[t] = topics.get(t, 0) + 1
    outs += [("topic_present", t) for t, n in sorted(topics.items()) if n >= MIN_TOPIC_DAYS]

    props = []
    for kind, value in sorted(conds):
        for ok_, ov in outs:
            if kind == "tag" and ok_ == "topic_present":
                continue                 # her tag echoes into her topics (verify refuses it)
            # A tag is also tried on the day after: a bad night's sleep is
            # logged on one day and felt on the next.
            for k2 in ((kind, "tag_day_before") if kind == "tag" else (kind,)):
                c = {"kind": k2, "value": value, "window_days": 0}
                o = {"kind": ok_, "value": ov}
                props.append(M.Proposal(claim=claim_for(c, o), condition=M.Condition(**c),
                                        outcome_kind=ok_, outcome_value=ov))
    return props


# ----------------------------------------------------------------- progress

def progress(uid, verified_count=None):
    """
    The wait, made honest. Days logged against the ~90 a pattern needs, and how
    many candidates have partial support (seen at least twice, beating her base
    rate) without yet clearing the significance gate. A count only: naming a
    candidate would be claiming it.
    """
    eps = load(uid, "episodes", []) or []
    # Counting every candidate twice is a few hundred passes over her days;
    # the answer only changes when a day is added, so it is kept per count.
    memo = load(uid, "pattern-progress", None) or {}
    if memo.get("days") == len(eps) and memo.get("last") == (eps[-1]["date"] if eps else None):
        tracking = memo["tracking"]
    else:
        tracking = _tracking(uid, eps)
        save(uid, "pattern-progress", {"days": len(eps), "tracking": tracking,
                                       "last": eps[-1]["date"] if eps else None})
    return {"days_logged": len(eps), "target": TARGET_DAYS,
            "days_to_go": max(0, TARGET_DAYS - len(eps)),
            "candidates_tracking": tracking,
            "verified": verified_count if verified_count is not None
            else len(current(uid))}


def _tracking(uid, eps):
    tracking = 0
    if len(eps) >= 10:
        props = candidates(uid)
        for p in props:
            loose = M.verify(uid, p, min_support=2, min_lift=1.5, alpha=1.0)
            if loose and not M.verify(uid, p, n_tests=len(props)):
                tracking += 1
    return tracking


# ----------------------------------------------------------------- today

def heads_up(uid, sky_state, date):
    """
    Today's sky matches the condition of one of her verified patterns.
    One line, the support string included, re-verified first. The morning
    push may use it only when confidence is medium or high (`pushable`).
    """
    out = []
    for p in M.fires_today(uid, sky_state, {"date": date}):
        v = reverify(uid, p)
        if not v:
            continue
        out.append({
            "id": v["id"],
            "text": (f"Heads-up from your own check-ins: {condition_words(v['condition'])}, "
                     f"you logged {outcome_words(v['outcome'])} — {v['support']} times."
                     + _today_nudge(v["outcome"])),
            "support": v["support"], "lift": v["lift"], "p_value": v["p_value"],
            "p_text": p_words(v["p_value"]), "lift_text": lift_words(v["lift"]),
            "confidence": v["confidence"],
            "pushable": v["confidence"] in ("medium", "high"),
        })
    return out
