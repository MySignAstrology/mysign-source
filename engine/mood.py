"""
Mood: the check-in, made into something she grows with.

The 1-10 number stays the spine (memory.py's pattern verifier counts on it).
Around it, three optional taps, each grounded in the research rather than in
what looks good on a screen:

- **Energy**, high or low. With pleasantness (the 1-10) that places the day in
  one of the four quadrants of the Mood Meter (Brackett, Yale RULER).
- **One to three feeling words** from that quadrant. Naming a feeling dampens
  it (affect labelling, Lieberman et al. 2007), and people who tell "anxious"
  from "disappointed" from "lonely" handle distress better (emotional
  granularity, Kashdan, Barrett & McKnight 2015). Each word carries a
  one-line meaning, so the vocabulary is learned, not just picked.
- **What it's about**: a few tags. These are what turn a mood log into
  behavioural activation (Ekers et al. 2014): "your better days tend to have
  outdoors in them" is a thing she can do more of.

Two rules the summary keeps, matching the rest of the engine:

- **Descriptive, never causal, and never a pattern.** A tag gap is shown with
  its counts and called an early read. Only memory.py's verifier, behind its
  three gates, gets to call something a pattern. The sky is context on the
  timeline, never the cause of a mood.
- **No streak to lose.** "11 of the last 14 days", never "streak: 0". Missing a
  day does not dent habit formation (Lally et al. 2010); a reset-to-zero
  counter is what makes people quit after one slip.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter

# quadrant -> [(word, meaning)]. Eight per quadrant: enough to be precise,
# few enough to read in a glance on a phone.
FEELINGS: dict[str, list[tuple[str, str]]] = {
    "high_unpleasant": [
        ("anxious", "worried about something that hasn't happened yet"),
        ("stressed", "more being asked of you than you have today"),
        ("frustrated", "blocked from something you want"),
        ("angry", "something crossed a line that matters to you"),
        ("overwhelmed", "too much at once to sort out"),
        ("restless", "can't settle, body or mind"),
        ("jealous", "wanting what someone else has, or fearing losing what you have"),
        ("on edge", "braced for something to go wrong"),
    ],
    "low_unpleasant": [
        ("sad", "a loss, big or small, that you're feeling"),
        ("drained", "running on empty"),
        ("lonely", "wanting connection you're not getting"),
        ("disappointed", "it didn't go the way you hoped"),
        ("hurt", "someone's words or actions landed hard"),
        ("numb", "feeling less than you'd expect to"),
        ("bored", "nothing is holding your attention"),
        ("heavy", "a weight you can't quite name"),
    ],
    "low_pleasant": [
        ("calm", "steady, nothing pulling at you"),
        ("content", "enough, as it is"),
        ("grateful", "noticing something good you didn't have to earn"),
        ("relieved", "a worry has lifted"),
        ("safe", "nothing to guard against right now"),
        ("cozy", "warm, held, comfortable"),
        ("reflective", "quietly turning something over"),
        ("at peace", "settled with how things are"),
    ],
    "high_pleasant": [
        ("excited", "looking forward to something"),
        ("hopeful", "believing it could go well"),
        ("proud", "you did something that took something"),
        ("inspired", "an idea or person lit you up"),
        ("playful", "light, up for fun"),
        ("confident", "trusting yourself today"),
        ("loved", "cared for, and you can feel it"),
        ("energized", "plenty of fuel and somewhere to put it"),
    ],
}
WORDS = {w: (q, m) for q, ws in FEELINGS.items() for w, m in ws}

TAGS = ["sleep", "work", "school", "love", "friends", "family", "body", "money",
        "alone time", "outdoors", "creating", "phone", "period"]

MAX_FEELINGS = 3
MAX_TAGS = 6
# A tag gap is only shown when both sides have at least this many days.
MIN_SIDE = 3
# Below this gap (in mood points) two averages are called even.
MIN_GAP = 1.0
PATTERN_DAYS = 90
# "What helped" notes come from the day after a low one (mood <= LOW).
LOW = 4
# Words used in the first FIRST_DAYS days of check-ins, for "up from N".
FIRST_DAYS = 30

# The line of the day, reacted to in one tap. Said as her words, so the model
# answers a person and not a button.
LINE_REACTIONS = {"yes": "yes, that's exactly it", "sort": "sort of",
                  "no": "not really, no"}


def after_low_title(yesterday: dict | None) -> str | None:
    """
    The morning push title, the day after a low check-in. Said plainly and
    briefly, without a silver lining; the line of the day is still the body.
    None on any other morning.
    """
    m = (yesterday or {}).get("mood")
    if not isinstance(m, (int, float)) or m > LOW:
        return None
    # No number: a lock screen is read by whoever is near the phone.
    return "Yesterday was hard. Gently, then."


def line_text(line: str, reaction: str) -> str:
    return f'About today\'s line, "{line.strip()}": {LINE_REACTIONS[reaction]}.'


def quadrant(mood: int | None, energy: str | None) -> str | None:
    """Pleasantness from the 1-10, energy from the tap. None if either is missing."""
    if mood is None or energy not in ("high", "low"):
        return None
    return f"{energy}_{'pleasant' if mood >= 6 else 'unpleasant'}"


def clean(energy, feelings, tags):
    """Keep only known values, in order, de-duplicated and capped."""
    e = energy if energy in ("high", "low") else None

    def keep(xs, allowed, cap):
        out = []
        for x in xs or []:
            x = str(x).strip().lower()
            if x in allowed and x not in out:
                out.append(x)
        return out[:cap]

    return e, keep(feelings, WORDS, MAX_FEELINGS), keep(tags, TAGS, MAX_TAGS)


def vocabulary():
    """What the check-in UI draws: the quadrants, their words and meanings, and the tags."""
    return {"feelings": {q: [{"word": w, "means": m} for w, m in ws] for q, ws in FEELINGS.items()},
            "tags": TAGS}


def reflect_text(rec: dict) -> str:
    """
    The check-in, said as her first message of the day. Built server-side from
    what was saved, so the free reflection can only ever be about the check-in.
    """
    parts = [f"Checking in: {rec['mood']}/10"]
    feel = rec.get("feelings") or []
    if feel:
        parts.append(", ".join(feel))
    elif rec.get("energy"):
        parts.append(f"{rec['energy']} energy")
    line = " — ".join(parts) + "."
    tags = rec.get("tags") or []
    if tags:
        line += f" It's about {', '.join(tags)}."
    if rec.get("text"):
        line += f" {rec['text']}"
    if rec.get("helped"):
        line += f" What helped since yesterday: {rec['helped']}"
    return line


# Check-in counts worth marking, and what each one unlocks. Counts, never a
# streak: a gap never takes one away.
MILESTONES = [(1, "first check-in"), (7, "a week in colour"), (14, "first early reads"),
              (30, "a month in colour"), (60, "two months"), (90, "patterns get checked")]


def milestones(n: int) -> dict:
    reached = [{"n": k, "label": t} for k, t in MILESTONES if n >= k]
    nxt = next(({"n": k, "label": t, "to_go": k - n} for k, t in MILESTONES if n < k), None)
    return {"reached": reached, "next": nxt}


def _avg(xs):
    return round(sum(xs) / len(xs), 1) if xs else None


def summary(rows: list[tuple[str, dict]], today: dt.date) -> dict:
    """
    rows: [(iso date, check-in record)], any order. Everything shown on the
    Mood tab, computed here so it can be tested without a browser.
    """
    days = sorted(((d, r) for d, r in rows if isinstance(r, dict) and r.get("mood")),
                  key=lambda x: x[0])
    by_date = dict(days)

    def window(start, n):
        return [by_date[(start - dt.timedelta(days=i)).isoformat()]["mood"]
                for i in range(n) if (start - dt.timedelta(days=i)).isoformat() in by_date]

    this_week = window(today, 7)
    last_week = window(today - dt.timedelta(days=7), 7)
    recent14 = len(window(today, 14))

    words = Counter(w for _, r in days for w in (r.get("feelings") or []))
    quads = Counter(q for _, r in days
                    if (q := quadrant(r.get("mood"), r.get("energy"))))

    # Tag gaps: average mood on days with the tag vs days without, among the
    # days where she tagged anything at all (an untagged day says nothing).
    tagged = [(r["mood"], set(r.get("tags") or [])) for _, r in days if r.get("tags")]
    gaps = []
    for t in TAGS:
        on = [m for m, ts in tagged if t in ts]
        off = [m for m, ts in tagged if t not in ts]
        if len(on) < MIN_SIDE or len(off) < MIN_SIDE:
            continue
        gap = round(_avg(on) - _avg(off), 1)
        if abs(gap) < MIN_GAP:
            continue
        gaps.append({"tag": t, "with": _avg(on), "without": _avg(off), "gap": gap,
                     "n_with": len(on), "n_without": len(off)})
    gaps.sort(key=lambda g: -abs(g["gap"]))
    lifts = [g for g in gaps if g["gap"] > 0][:3]
    drags = [g for g in gaps if g["gap"] < 0][:3]

    yesterday = by_date.get((today - dt.timedelta(days=1)).isoformat())

    # What helped: her own words, newest first, from the day after a low day.
    helped = []
    for d, r in reversed(days):
        if not r.get("helped"):
            continue
        before = by_date.get((dt.date.fromisoformat(d) - dt.timedelta(days=1)).isoformat())
        if not before or before["mood"] > LOW:
            continue                     # only what got her out of a low day
        helped.append({"date": d, "text": r["helped"], "mood": r["mood"],
                       "before": before["mood"] if before else None})
    helped = helped[:6]

    # The week, Monday to today: what a recap card needs.
    monday = today - dt.timedelta(days=today.weekday())
    wk = [(d, r) for d, r in days if d >= monday.isoformat()]
    if today.weekday() == 0:          # on Monday, look back at the week just ended
        last_mon = monday - dt.timedelta(days=7)
        wk = [(d, r) for d, r in days if last_mon.isoformat() <= d < monday.isoformat()]
    week = None
    if wk:
        best = max(wk, key=lambda x: x[1]["mood"])
        week = {"from": wk[0][0], "to": wk[-1][0], "days": len(wk),
                "avg": _avg([r["mood"] for _, r in wk]),
                "words": [w for w, _ in Counter(w for _, r in wk for w in (r.get("feelings") or [])).most_common(3)],
                "tags": [t for t, _ in Counter(t for _, r in wk for t in (r.get("tags") or [])).most_common(3)],
                "best": {"date": best[0], "mood": best[1]["mood"], "tags": best[1].get("tags") or []},
                "moods": [{"date": d, "mood": r["mood"]} for d, r in wk]}

    # Words used early on, against all of them: granularity growing.
    first = dt.date.fromisoformat(days[0][0]) if days else today
    early = {w for d, r in days if d < (first + dt.timedelta(days=FIRST_DAYS)).isoformat()
             for w in (r.get("feelings") or [])}
    span = (today - first).days

    return {
        "count": len(days),
        "recent14": recent14,
        "this_week": _avg(this_week), "this_week_n": len(this_week),
        "last_week": _avg(last_week), "last_week_n": len(last_week),
        "words": [{"word": w, "n": n, "means": WORDS[w][1], "quadrant": WORDS[w][0]}
                  for w, n in words.most_common(8) if w in WORDS],
        "vocab_size": len(words),
        # Only meaningful once she is past her first month.
        "vocab_first_month": len(early) if span >= FIRST_DAYS + 7 else None,
        "helped": helped,
        "week": week,
        "quadrants": dict(quads),
        "lifts": lifts,
        "drags": drags,
        "yesterday": ({"mood": yesterday["mood"], "feelings": yesterday.get("feelings") or []}
                      if yesterday else None),
        "days_to_patterns": max(0, PATTERN_DAYS - len(days)),
        "milestones": milestones(len(days)),
        # This calendar year, for the year-in-colour grid.
        "year": {d: r["mood"] for d, r in days if d.startswith(str(today.year))},
        "calendar": [{"date": d, "mood": r["mood"], "energy": r.get("energy"),
                      "feelings": r.get("feelings") or [], "tags": r.get("tags") or []}
                     for d, r in days if d >= (today - dt.timedelta(days=34)).isoformat()],
    }


# ------------------------------------------------- a note for a harder day
#
# Written on a good day (7+), handed back on a hard one (4 or under): her own
# voice, which lands better than anything the app could say. Each note is
# given once, oldest first; the day it is given, it stays for that day.

GOOD, NOTE_MAX, NOTES_KEPT = 7, 500, 30


def add_note(notes: list, day: str, mood: int, text: str) -> list:
    text = " ".join((text or "").split())[:NOTE_MAX]
    if not text:
        raise ValueError("Write something first.")
    if mood < GOOD:
        raise ValueError("These are written on good days.")
    notes = [n for n in notes if n.get("date") != day]           # one a day; the latest wins
    notes.append({"date": day, "mood": mood, "text": text, "given": None})
    return notes[-NOTES_KEPT:]


def note_for(notes: list, day: str, mood) -> tuple[dict | None, list]:
    """(the note to show today, notes updated). Only on a low day."""
    if not isinstance(mood, (int, float)) or mood > LOW:
        return None, notes
    same = next((n for n in notes if n.get("given") == day), None)
    if same:
        return same, notes
    fresh = next((n for n in notes if not n.get("given") and n.get("date") != day
                  and n.get("text")), None)
    if not fresh:
        return None, notes
    fresh = {**fresh, "given": day}
    return fresh, [fresh if n is not None and n.get("date") == fresh.get("date") else n for n in notes]
