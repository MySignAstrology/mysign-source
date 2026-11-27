"""
Circles and "Before you date" — the two group-chat features.

Co-Star sells relationships one pair at a time (Add Manually $2.99, Crush
Report $4.99, Advanced Chart-Relationships $8.99) and has no group at all. The
two things friends actually share are "how are the girls doing" and "should I
go out with him", so both live here, computed the same way as everything else:

- A circle is a named handful of her saved people. Its day is each member's
  daily.py score against her, ranked by daily.rank_people; its week is the
  last seven of those scores, summarised by the formulas below — every
  superlative states its number, and a tie is broken by name, never by chance.
- A quick check is compat.deep() on a name and a birth date she typed, kept in
  memory only. Nothing is written until she taps "Save to People", because the
  person in it never agreed to be stored.

No model anywhere in this file. No check-in text anywhere in this file either:
superlatives are about the sky between two charts, never about what anyone
wrote.
"""
import datetime as dt
import statistics

from engine import compat, daily
from engine.chart import SIGNS, positions

MIN_MEMBERS = 2          # her plus two is the smallest group that is a group
MAX_MEMBERS = 8          # past eight it is a list, and the card stops fitting
FREE_CIRCLES = 1         # Plus keeps any number; see may_add_circle in app/social.py
MAX_CIRCLES = 20         # an abuse bound, not a price
WEEK = 7

# Points the Moon is checked against for the group note. The Moon itself only
# when the birth time pins it — otherwise it moved up to 13° across the day.
MOON_TARGETS = ("Sun", "Moon", "Venus", "Mars")
MOON_NOTE_ORB = daily.ORB["Moon"]


# ------------------------------------------------------------------ circles
def superlatives(weeks):
    """
    The week in a circle, from real scores.

    `weeks` is {cid: {"name", "series": [{"date", "score", "delta"}...]}} with
    the same seven dates for everyone. Formulas, so the copy can never claim
    more than the arithmetic:

    - warmest:  highest mean score over the seven days ("avg 71")
    - steadiest: lowest population standard deviation of the score ("moves ±1.2")
    - most_charged: highest standard deviation, if it is a different person and
      actually moved (sd >= 2 — a point of jitter is not "charged")
    - best_day: the date with the highest mean score across every member

    Ties go to the alphabetically first name, so the same week always reads
    the same way.
    """
    rows = []
    for cid, w in weeks.items():
        scores = [s["score"] for s in w["series"]]
        if not scores:
            continue
        rows.append({"id": cid, "name": w["name"], "mean": statistics.fmean(scores),
                     "sd": statistics.pstdev(scores) if len(scores) > 1 else 0.0})
    if not rows:
        return []
    out = []
    by_name = lambda r: r["name"].lower()  # noqa: E731
    warm = min(rows, key=lambda r: (-round(r["mean"], 6), by_name(r)))
    out.append({"key": "warmest", "label": "Warmest this week", "id": warm["id"],
                "name": warm["name"], "value": round(warm["mean"], 1),
                "says": f"averaged {warm['mean']:.0f} over {WEEK} days"})
    steady = min(rows, key=lambda r: (round(r["sd"], 6), by_name(r)))
    out.append({"key": "steadiest", "label": "Steadiest this week", "id": steady["id"],
                "name": steady["name"], "value": round(steady["sd"], 1),
                "says": f"moved only ±{steady['sd']:.1f} all week"})
    charged = min(rows, key=lambda r: (-round(r["sd"], 6), by_name(r)))
    if charged["id"] != steady["id"] and charged["sd"] >= 2:
        out.append({"key": "most_charged", "label": "Most charged this week",
                    "id": charged["id"], "name": charged["name"],
                    "value": round(charged["sd"], 1),
                    "says": f"swung ±{charged['sd']:.1f} this week"})
    dates = {}
    for w in weeks.values():
        for s in w["series"]:
            dates.setdefault(s["date"], []).append(s["score"])
    if dates:
        best = min(dates.items(), key=lambda kv: (-round(statistics.fmean(kv[1]), 6), kv[0]))
        day = dt.date.fromisoformat(best[0])
        out.append({"key": "best_day", "label": "Best day together", "date": best[0],
                    "name": day.strftime("%A"), "value": round(statistics.fmean(best[1]), 1),
                    "says": f"{day.strftime('%a')}: averaged {statistics.fmean(best[1]):.0f} "
                            f"across all of you"})
    return out


# The pairs inside a circle that don't include her. A friendship is what a
# circle is for, so two members are read as friends: compat.KIND_WEIGHTS
# leans on ease, not attraction. Her own pairs keep the kind she chose.
PAIR_KIND = "friend"


def pair_week(a_ut, a_prec, b_ut, b_prec, today, tz):
    """
    Two members, today and the six days before, by exactly the machinery her
    own pairs use: compat.deep() on the two birth instants she holds, then
    daily.day_score() one sky per day. A Moon is a target only when its
    owner's birth time pins it, the same rule as everywhere else.

    Only numbers come back. day_score's reasons are written from her side
    ("your Sun"), and here neither chart is hers, so no sentence of it is
    reused — a reason in the wrong voice is a wrong fact.
    """
    rep = compat.deep({"birth_ut": list(a_ut)}, {"birth_ut": list(b_ut)}, kind=PAIR_KIND,
                      precision_a=a_prec, precision_b=b_prec, today=today)
    a = daily._natal(tuple(a_ut), a_prec)
    b = daily._natal(tuple(b_ut), b_prec)
    ser = daily.series(rep, a, b, today, tz, "them", back=WEEK - 1, ahead=0,
                       moon_mine=a_prec != "unknown", moon_theirs=b_prec != "unknown")
    now = ser[-1]
    return {"score": now["score"], "usual": rep["score"]["overall"], "delta": now["delta"],
            "mood": daily.mood(now["delta"]), "series": ser}


PAIR_LABELS = {"warmest": ("in_sync", "Most in sync pair"),
               "steadiest": ("steadiest", "Steadiest pair this week"),
               "most_charged": ("most_charged", "Most charged pair")}


def pair_superlatives(weeks):
    """
    superlatives() over pairs instead of people — same formulas, same
    tie-break, relabelled. The best-day line is left to the per-person week:
    a "best day" over pairs counts every member several times over, and a
    number that double-counts is not one worth printing.
    """
    out = []
    for s in superlatives(weeks):
        if s["key"] in PAIR_LABELS:
            key, label = PAIR_LABELS[s["key"]]
            out.append({**s, "key": key, "label": label})
    return out


def moon_note(jd, charts):
    """
    "Moon in Leo crosses Sam's Venus and your Sun" — the one sky fact the whole
    group shares today. `charts` is [(label, chart, moon_ok)] where label is
    "your" or "Sam's". A conjunction within the Moon's daily orb only, so it
    names a real crossing rather than a loose aspect.
    """
    moon = positions(jd)["Moon"]["lon"]
    sign = SIGNS[int(moon // 30) % 12]
    hits = []
    for label, chart, moon_ok in charts:
        for k in MOON_TARGETS:
            if k == "Moon" and not moon_ok:
                continue
            lon = chart["planets"][k]["lon"]
            orb = compat._sep(moon, lon)
            if orb <= MOON_NOTE_ORB:
                hits.append((orb, label, k))
    hits.sort()
    # "Sam's Venus and Mars", not "Sam's Venus and Sam's Mars".
    grouped = {}
    for _orb, label, k in hits[:3]:
        grouped.setdefault(label, []).append(k)
    names = [f"{label} {_and(ks)}" for label, ks in grouped.items()]
    if not names:
        return {"sign": sign, "text": f"Moon in {sign}: today it isn't crossing anyone's "
                                      f"Sun, Moon, Venus or Mars."}
    return {"sign": sign, "text": f"Moon in {sign} crosses {_and(names)}."}


def _and(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ------------------------------------------------------------ before you date
CONFIDENCE = {
    "exact": "Birthday, time and city: this is the full read, nothing held back.",
    "date": ("This is the birthday-only read, so it covers the big stuff. Add a birth time "
             "and city to unlock the Moon and the deeper layers."),
}
TIER_WORDS = {"rare": "Rare", "strong": "Strong", "promising": "Promising",
              "mixed": "Mixed", "effortful": "Takes effort", "hard going": "Hard going"}


def _plain(contact, name):
    """'Their Venus trine your Moon' -> "Sam's Venus trines your Moon"."""
    verb = daily.VERB.get(contact["aspect"], contact["aspect"])
    return {"text": f"{name}'s {contact['theirs']} {verb} your {contact['yours']}",
            "meaning": contact["meaning"], "tone": ("easy" if contact["harmony"] > 0 else
                                                   "friction" if contact["harmony"] < 0
                                                   else "intense"),
            "aspect": contact["aspect"], "theirs": contact["theirs"],
            "yours": contact["yours"], "orb": contact["orb"]}


def quick(prof, their_birth_ut, precision, name, today, tz, kind="romantic"):
    """
    The pair, from a name and a birth date. `precision` is "exact" when she
    gave a time and a city, else "unknown" (the whole day is sampled, which is
    what compat already does for a missing time).

    Returns the score, its honest range, the three strongest contacts that
    hold without a birth time, and the best day in the next seven by
    daily.day_score. Nothing is stored.
    """
    pm = compat.my_precision(prof)
    rep = compat.deep({"birth_ut": prof["birth_ut"]}, {"birth_ut": their_birth_ut},
                      kind=kind, precision_a=pm, precision_b=precision, name_b=name,
                      today=today)
    known = precision != "unknown"

    def keep(c):
        if c["certainty"] == "depends on birth time":
            return False
        # Without their time the Moon could be anywhere in a 13° stretch, and
        # there is no Ascendant or Midheaven at all: say nothing rather than guess.
        return known or c["theirs"] not in ("Moon", "Ascendant", "Midheaven")

    contacts = [_plain(c, name) for c in rep["contacts"] if keep(c)][:3]
    mine = daily._natal(tuple(prof["birth_ut"]), pm)
    theirs = daily._natal(tuple(their_birth_ut), precision)
    days = []
    for i in range(WEEK):
        d = today + dt.timedelta(days=i)
        s = daily.day_score(rep, mine, theirs, d, tz, name,
                            moon_mine=pm != "unknown", moon_theirs=known)
        days.append(s)
    best = min(days, key=lambda s: (-s["score"], s["date"]))
    top = next((r for r in best["reasons"] if r["weight"] > 0), None)
    day = dt.date.fromisoformat(best["date"])
    sc = rep["score"]
    return {
        "name": name,
        "kind": sc["kind"],
        "score": sc["overall"],
        "score_range": sc["overall_range"],
        "tier": sc["band"],
        "tier_label": TIER_WORDS.get(sc["band"], sc["band"]),
        "precision": "exact" if known else "date",
        "confidence": CONFIDENCE["exact" if known else "date"],
        "caveats": rep["uncertainty"],
        "top_contacts": contacts,
        "best_day_this_week": {
            "date": best["date"], "weekday": day.strftime("%A"),
            "score": best["score"], "delta": best["delta"], "mood": best["mood"],
            # A best day that is still within STEADY of usual is said plainly:
            # the best of an ordinary week is an ordinary day.
            "standout": best["delta"] >= daily.STEADY,
            "reason": top and {"text": top["text"], "short": top["short"],
                               "lasts": top["lasts"]},
        },
    }


# ------------------------------------------------------------------- cards
# Same Velvet Sky frame as engine/cards.py; kept here so the two features ship
# as one file. Names arrive already cut to a first name or an initial.
def _arrow(delta):
    from engine.cards import BLUSH, INK2, OX_LIGHT
    if delta > 0:
        return f"▲ {delta}", BLUSH
    if delta < 0:
        return f"▼ {-delta}", OX_LIGHT
    return "level", INK2


def circle_card(title, date_line, members, superlative, people=None, pairs=None):
    """
    The group, today: one row per member (name, score, arrow, the day's mood
    word from daily.py), the pair grid — every two of them, today's score in
    the cell where their row and column cross — and one superlative. Never a
    check-in mood — a friend who shares her 1-10 with her has not agreed to
    have it posted to a group chat — and never anyone's birth data.

    `people` is [(id, shown name)] with her first, `pairs` the circle's pair
    list keyed by those ids. Names arrive already cut to a first name or an
    initial; the grid's column heads are cut shorter still to fit.
    """
    from xml.sax.saxutils import escape

    from engine.cards import CHAMP, INK, INK2, MONO, SANS, SERIF, _frame, _name
    w, h = 1080, 1350
    grid = bool(people and pairs and len(people) > 2)
    rows = ""
    top = 300 if grid else 330
    room = 300 if grid else 720            # the grid takes the lower half when there is one
    step = min(96, room // max(1, len(members)))
    big = max(26, min(52, step - 16)) if grid else 52
    for i, m in enumerate(members[:MAX_MEMBERS]):
        y = top + i * step
        arrow, tone = _arrow(m["delta"])
        mood = (f'<text x="80" y="{y + 32}" font-family="{SANS}" font-size="24" fill="{INK2}">'
                f'{escape(m["mood"])}</text>') if not grid else (
                f'<text x="460" y="{y}" font-family="{SANS}" font-size="{min(24, big - 4)}" '
                f'fill="{INK2}">{escape(m["mood"])}</text>')
        rows += (f'<text x="80" y="{y}" font-family="{SERIF}" font-size="{big}" fill="{INK}">'
                 f'{escape(_name(m["name"], 14))}</text>{mood}'
                 f'<text x="{w - 250}" y="{y}" text-anchor="end" font-family="{MONO}" '
                 f'font-size="{big}" fill="{CHAMP}">{int(m["score"])}</text>'
                 f'<text x="{w - 80}" y="{y}" text-anchor="end" font-family="{MONO}" '
                 f'font-size="{min(30, big)}" fill="{tone}">{arrow}</text>')
    cells = _pair_grid(people, pairs, 650, 1100) if grid else ""
    sup = ""
    if superlative:
        sup = (f'<text x="{w // 2}" y="1170" text-anchor="middle" font-family="{MONO}" '
               f'font-size="22" letter-spacing="4" fill="{CHAMP}">'
               f'{escape(superlative["label"]).upper()}</text>'
               f'<text x="{w // 2}" y="1226" text-anchor="middle" font-family="{SERIF}" '
               f'font-style="italic" font-size="44" fill="{INK}">'
               f'{escape(_name(superlative["name"], 22))} · {escape(superlative["says"])}</text>')
    inner = (f'<text x="80" y="{150 if grid else 170}" font-family="{SERIF}" font-size="84" '
             f'fill="{INK}">{escape(_name(title, 22))}</text>'
             f'<text x="80" y="{206 if grid else 226}" font-family="{MONO}" font-size="22" '
             f'letter-spacing="4" fill="{INK2}">{escape(date_line).upper()}</text>'
             f'{rows}{cells}{sup}')
    return _frame(w, h, inner, foot="MY SIGN · mysignapp.com")


def _pair_grid(people, pairs, y0, y1):
    """
    The lower triangle of a who-with-whom table: row i, column j (j < i) is
    the pair's score today, its fill warmed by the score the same way the pair
    card's lens is. Only the triangle — the other half would repeat it.
    """
    from xml.sax.saxutils import escape

    from engine.cards import BLUSH, CHAMP, INK, INK2, MONO, SANS, _name
    by = {}
    for p in pairs:
        by[(p["a"]["id"], p["b"]["id"])] = by[(p["b"]["id"], p["a"]["id"])] = p
    k = len(people)
    x0, label_w, right = 80, 190, 1000
    cw = (right - x0 - label_w) // (k - 1)
    ch = min(80, (y1 - y0 - 70) // (k - 1))
    out = (f'<text x="{x0}" y="{y0}" font-family="{MONO}" font-size="20" letter-spacing="4" '
           f'fill="{CHAMP}">EVERY PAIR, TODAY</text>')
    gx, gy = x0 + label_w, y0 + 60
    for j, (_pid, name) in enumerate(people[:-1]):
        out += (f'<text x="{gx + j * cw + cw // 2}" y="{gy - 12}" text-anchor="middle" '
                f'font-family="{SANS}" font-size="{min(22, cw // 4)}" fill="{INK2}">'
                f'{escape(_name(name, 6))}</text>')
    fs = max(16, min(30, ch - 22, cw // 3))
    for i in range(1, k):
        y = gy + (i - 1) * ch
        out += (f'<text x="{x0}" y="{y + ch * 0.62:.0f}" font-family="{SANS}" '
                f'font-size="{min(26, ch - 10)}" fill="{INK}">'
                f'{escape(_name(people[i][1], 10))}</text>')
        for j in range(i):
            p = by.get((people[i][0], people[j][0]))
            x = gx + j * cw
            if not p:
                continue
            op = round(.08 + .5 * max(0, min(99, p["score"])) / 100, 3)
            out += (f'<rect x="{x + 3}" y="{y + 3}" width="{cw - 6}" height="{ch - 6}" rx="10" '
                    f'fill="{BLUSH}" opacity="{op}"/>'
                    f'<text x="{x + cw // 2}" y="{y + ch // 2 + fs // 3}" text-anchor="middle" '
                    f'font-family="{MONO}" font-size="{fs}" fill="{INK}">{int(p["score"])}</text>')
    return out


def check_card(me, them, score, tier_label, contact, precision):
    """
    "Before you date", postable: two first names (or an initial), the score,
    the tier and one contact in plain words. No birth date, time or place —
    she typed those about someone who isn't on the app.
    """
    from xml.sax.saxutils import escape

    from engine.cards import BLUSH, CHAMP, INK, INK2, MONO, SANS, SERIF, _frame, _name, _wrap
    w, h, cy = 1080, 1350, 470
    pair = f"{escape(_name(me))} &amp; {escape(_name(them))}"
    body = _wrap(escape((contact or {}).get("meaning", "")), 36)[:4]
    lines = "".join(f'<text x="{w // 2}" y="{1010 + i * 46}" text-anchor="middle" '
                    f'font-family="{SANS}" font-size="34" fill="{INK}">{ln}</text>'
                    for i, ln in enumerate(body))
    head = escape((contact or {}).get("text", ""))
    note = "from birth dates" if precision == "date" else "full charts"
    inner = (
        f'<text x="{w // 2}" y="200" text-anchor="middle" font-family="{MONO}" font-size="24" '
        f'letter-spacing="5" fill="{CHAMP}">BEFORE YOU DATE</text>'
        f'<circle cx="{w // 2}" cy="{cy}" r="200" fill="none" stroke="{BLUSH}" stroke-width="2"/>'
        f'<text x="{w // 2}" y="{cy + 50}" text-anchor="middle" font-family="{MONO}" '
        f'font-size="150" fill="{CHAMP}">{int(score)}</text>'
        f'<text x="{w // 2}" y="760" text-anchor="middle" font-family="{SERIF}" '
        f'font-size="64" fill="{INK}">{pair}</text>'
        f'<text x="{w // 2}" y="830" text-anchor="middle" font-family="{SERIF}" font-style="italic" '
        f'font-size="48" fill="{BLUSH}">{escape(tier_label)}</text>'
        f'<text x="{w // 2}" y="920" text-anchor="middle" font-family="{MONO}" font-size="26" '
        f'fill="{CHAMP}">{head}</text>'
        f'{lines}'
        f'<text x="{w // 2}" y="1230" text-anchor="middle" font-family="{SANS}" font-size="22" '
        f'fill="{INK2}">{note} · contacts between two charts, not a forecast</text>')
    return _frame(w, h, inner, foot="MY SIGN · mysignapp.com")
