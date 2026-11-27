"""
"How's it going between us?" — a two-sided check-in for linked pairs.

Each of them answers privately: Closer, Steady or Strained, plus an optional
word that is only ever theirs. Neither sees the other's answer until both have
answered. Then both see both, next to what the sky was doing between them
that week, so a strained stretch gets a neutral, shared explanation instead
of blame.

One record per pair, under the global "_reveal" namespace, keyed by the two
user ids sorted — so it is the same record whoever opens it. Inside, one round
per ISO week, keyed by the week of the first answerer's local date
("2026-W45"). Weekly, not daily: "how's it going between us" is a question
about a relationship, not a mood log, and asking it every day turns a
moment into a chore and the partner's push into noise.

Two people in London and Chicago do not share a Monday midnight, so a round
left half-answered at the very end of a week stays joinable for BRIDGE_HOURS
after it opened — that bridge is the only reason an answer ever lands in a
week other than the answerer's own.

The privacy rules, all enforced in view():
- before both answered, the other person's choice is absent, not hidden;
- the note is returned to its author only, never to the partner, ever;
- what the partner learns before answering is that an answer exists (that is
  the invitation), never which one.
"""
import datetime as dt
import time

FEELS = ("closer", "steady", "strained")
NS = "_reveal"
BRIDGE_HOURS = 36
KEEP_ROUNDS = 26          # half a year of weeks


def key(a, b):
    lo, hi = sorted((a, b))
    return f"rv-{lo}-{hi}"


def week_of(day):
    """ISO week label for a local date. Zero-padded so labels sort in time order."""
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def current(rec, today, now=None):
    """The round this person is in: this week's, or last week's while it is
    half-answered and inside the bridge. None means a fresh week, nothing yet."""
    rounds = (rec or {}).get("rounds", {})
    if not rounds:
        return None
    latest = max(rounds)
    wk = week_of(today)
    if latest >= wk:
        # Theirs is already the newer week (the partner's clock is ahead) or
        # it is this week's: either way, that is the round.
        return latest
    if latest != week_of(today - dt.timedelta(days=7)):
        return None
    # Last week's round: joinable only half-answered and only just opened. A
    # finished one is history; a stale one would make a Monday answer count
    # for a week that is over.
    rnd = rounds[latest]
    now = now or time.time()
    if len(rnd.get("answers", {})) < 2 and now - rnd.get("started", 0) < BRIDGE_HOURS * 3600:
        return latest
    return None


def answer(rec, uid, today, feel, note=None, now=None):
    """Record uid's answer. Returns (record, week, first) — `first` is True
    when this answer opened the round, so the partner can be told."""
    if feel not in FEELS:
        raise ValueError(f"feel must be one of {FEELS}")
    now = now or time.time()
    old = rec or {}
    rec = {**old, "rounds": dict(old.get("rounds", {}))}     # keeps nudged_week
    wk = current(rec, today, now) or week_of(today)
    rnd = dict(rec["rounds"].get(wk) or {"started": now, "day": today.isoformat(), "answers": {}})
    answers = dict(rnd.get("answers", {}))
    if len(answers) >= 2 and uid in answers:
        # Revealed already. Changing it now would rewrite what the other person
        # has seen — the reveal stands; a new round opens next week.
        raise PermissionError("This week's is already revealed — a new one opens next week.")
    first = not answers
    answers[uid] = {"feel": feel, "note": (note or "").strip()[:140] or None, "at": now}
    rnd["answers"] = answers
    rec["rounds"][wk] = rnd
    for gone in sorted(rec["rounds"])[:-KEEP_ROUNDS]:
        rec["rounds"].pop(gone)
    return rec, wk, first


def should_nudge(rec, wk):
    """The "X answered — your turn" push goes at most once a week per pair,
    whatever happens in between (a bridged round, a changed answer)."""
    return (rec or {}).get("nudged_week") != wk


def view(rec, uid, other, today, now=None):
    """What uid may see of the current round. Nothing of `other`'s answer
    leaves here until both have answered, and other's note never does."""
    wk = current(rec, today, now)
    if not wk:
        return {"week": None, "day": None, "revealed_at": None, "state": "open",
                "mine": None, "theirs": None, "they_answered": False}
    rnd = rec["rounds"][wk]
    answers = rnd.get("answers", {})
    mine = answers.get(uid)
    theirs = answers.get(other)
    both = bool(mine and theirs)
    return {
        "week": wk,
        "day": rnd.get("day"),
        # When the second answer landed — the reveal's sky looks back from here.
        "revealed_at": max(mine["at"], theirs["at"]) if both else None,
        "state": "revealed" if both else "waiting" if mine else "your_turn" if theirs else "open",
        "mine": mine and {"feel": mine["feel"], "note": mine.get("note")},
        "theirs": {"feel": theirs["feel"]} if both else None,
        "they_answered": bool(theirs) and not mine,
    }


def history(rec, uid, other, limit=6):
    """Past revealed rounds: both choices, never a note. Unrevealed ones are left out."""
    out = []
    for wk in sorted((rec or {}).get("rounds", {}), reverse=True):
        rnd = rec["rounds"][wk]
        a = rnd.get("answers", {})
        if uid in a and other in a:
            out.append({"week": wk, "day": rnd.get("day"), "mine": a[uid]["feel"], "theirs": a[other]["feel"]})
        if len(out) >= limit:
            break
    return out


def sky_ranges(days, top=3):
    """Fold a week of daily.day_score results into what the sky was doing:
    the same reason on consecutive days becomes one range ("Tue–Thu"), and the
    three strongest ranges by |weight| are kept. Every text, lasts and weight
    is daily.py's; this only groups and orders them."""
    runs = []
    open_ = {}                                     # text -> run still extending
    for d in sorted(days, key=lambda d: d["date"]):
        seen = set()
        for r in d["reasons"]:
            if r["text"] in seen:
                continue
            seen.add(r["text"])
            run = open_.get(r["text"])
            if run and run["_last"] == dt.date.fromisoformat(d["date"]) - dt.timedelta(days=1):
                run["to"] = d["date"]
                run["lasts"] = r["lasts"]          # the latest day's is the current one
                if abs(r["weight"]) > abs(run["weight"]):
                    run["weight"], run["short"] = r["weight"], r["short"]
            else:
                run = {"text": r["text"], "short": r["short"], "lasts": r["lasts"],
                       "transit": r["transit"], "aspect": r["aspect"], "weight": r["weight"],
                       "from": d["date"], "to": d["date"]}
                runs.append(run)
                open_[r["text"]] = run
            run["_last"] = dt.date.fromisoformat(d["date"])
    runs.sort(key=lambda r: (-abs(r["weight"]), r["from"]))
    return [{k: v for k, v in r.items() if k != "_last"} for r in runs[:top]]


def agree_line(mine, theirs, name):
    """Plain words for the pair of answers — describing, never judging."""
    if mine == theirs:
        return f"You and {name} felt the same: {mine}."
    return f"You felt {mine}; {name} felt {theirs}. Different reads of the same week — worth comparing notes."


def date_label(day):
    return dt.date.fromisoformat(day).strftime("%a %b %-d")
