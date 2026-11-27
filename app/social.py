"""
Circles and "Before you date" — the HTTP side. The arithmetic is engine/social.py.

A router of its own rather than another block in server.py: four teams are
adding endpoints this week and every one of them appends to the bottom of that
file. server.py includes this router in one line at its end, after every
helper below exists, so the import here is not circular in practice.

Privacy, in one place:
- A circle is hers. Members are her saved connections; a linked member's
  1-10 appears only through server._their_day, which already honours their
  share_mood. Check-in text is never read here.
- A quick check writes nothing. Not the store, not the log (the log gets
  whether a time was given, never a name or a date). Saving is the ordinary
  POST /api/connections, so a saved check is an ordinary person.
- Pairs between two members use only birth data already in her People —
  what she typed, or what a linked friend agreed to share with her — and come
  back as numbers, never as anyone's chart.
- Cards carry first names or initials and nobody's birth data.
"""
import datetime as dt
import hashlib
import itertools
import json
import time
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app import cities, log
from app.server import (
    _birth_instant,
    _brief,
    _connections,
    _day_ctx,
    _profile,
    _their_day,
    _tz,
    consented,
)
from engine import compat, daily, plan
from engine import social as SOC
from engine.store import delete, load, save

router = APIRouter()


def _first(name, initials=False):
    parts = (name or "").split()
    first = parts[0] if parts else "Them"
    return first[0].upper() + "." if initials else first


def _date_line(day):
    return f"{day.strftime('%A')} · {day.strftime('%B')} {day.day}"


# ===================================================================== circles

class Circle(BaseModel):
    name: str = Field(max_length=40)
    members: List[str] = Field(max_length=SOC.MAX_MEMBERS)


def _circles(uid):
    return load(uid, "circles", []) or []


def may_add_circle(uid, count):
    """One circle free, any number on Plus — and, like the people cap, not
    enforced until Plus can actually be bought."""
    if not plan.for_sale():
        return True
    return plan.get(uid)["tier"] == "plus" or count < SOC.FREE_CIRCLES


def _live(uid, circle, rows=None):
    """Members that still exist. Someone she removed, or a linked friend who
    deleted their account, drops out of every circle without a write."""
    by_id = {r["id"]: r for r in (rows if rows is not None else _connections(uid))}
    return [by_id[m] for m in circle["members"] if m in by_id]


def _public(uid, c, rows):
    members = _live(uid, c, rows)
    return {"id": c["id"], "name": c["name"], "created_at": c.get("created_at"),
            "members": [{"id": r["id"], "name": r.get("name") or "them",
                         "linked": bool(r.get("linked_uid"))} for r in members],
            "count": len(members)}


@router.get("/api/circles")
def list_circles(uid: str = Depends(consented)):
    rows = _connections(uid)
    return {"circles": [_public(uid, c, rows) for c in _circles(uid)],
            "people": len(rows),           # lets Home decide on "Before you date" cheaply
            "free_circles": SOC.FREE_CIRCLES, "min": SOC.MIN_MEMBERS, "max": SOC.MAX_MEMBERS,
            "plan_note": "Your first circle is free. With Plus you can have as many as you like — "
                         "it's included in the same price, never a separate purchase."}


@router.post("/api/circles")
def create_circle(b: Circle, uid: str = Depends(consented)):
    name = " ".join(b.name.split())
    if not name:
        raise HTTPException(400, "Give the circle a name.")
    ids = list(dict.fromkeys(b.members))           # keep her order, drop repeats
    if not SOC.MIN_MEMBERS <= len(ids) <= SOC.MAX_MEMBERS:
        raise HTTPException(400, f"A circle needs {SOC.MIN_MEMBERS} to {SOC.MAX_MEMBERS} "
                                 f"of your people, plus you.")
    known = {r["id"] for r in _connections(uid)}
    if not set(ids) <= known:
        raise HTTPException(400, "Everyone in a circle has to be in your People first.")
    circles = _circles(uid)
    if len(circles) >= SOC.MAX_CIRCLES:
        raise HTTPException(400, f"You've reached the limit of {SOC.MAX_CIRCLES} circles. Remove one first.")
    if not may_add_circle(uid, len(circles)):
        log.emit("plan.gated_circles", uid=uid)
        raise HTTPException(402, {"code": "plus_circles",
                                  "message": "Your first circle is free. With Plus you can "
                                             "have as many as you like.",
                                  "prices": plan.PRICES})
    c = {"id": uuid.uuid4().hex[:10], "name": name, "members": ids,
         "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    circles.append(c)
    save(uid, "circles", circles)
    log.emit("circle.created", uid=uid, members=len(ids))
    return _public(uid, c, None)


def _circle(uid, circle_id):
    c = next((c for c in _circles(uid) if c["id"] == circle_id), None)
    if not c:
        raise HTTPException(404, "No such circle.")
    return c


@router.delete("/api/circles/{circle_id}")
def delete_circle(circle_id: str, uid: str = Depends(consented)):
    circles = _circles(uid)
    kept = [c for c in circles if c["id"] != circle_id]
    if len(kept) == len(circles):
        raise HTTPException(404, "No such circle.")
    save(uid, "circles", kept)
    delete(uid, _pairs_key(circle_id))
    return {"ok": True}


def _week(uid, row, ctx):
    """The last seven days (today included) for one member, by daily.series."""
    prof = ctx["prof"] or {}
    pm, pt = compat.my_precision(prof), compat.their_precision(row)
    rep = compat.report_for(uid, row)
    mine = daily._natal(tuple(prof["birth_ut"]), pm)
    theirs = daily._natal(tuple(row["birth_ut"]), pt)
    return daily.series(rep, mine, theirs, ctx["day"], ctx["tz"], row.get("name", "them"),
                        back=SOC.WEEK - 1, ahead=0,
                        moon_mine=pm != "unknown", moon_theirs=pt != "unknown")


# ------------------------------------------------------------ pairs
# Every pair in the circle, not only her against each of them: "how are Sam and
# Leila today" is the question a group chat actually asks. A member pair is
# scored only from birth data she already holds — a row she typed herself, or a
# linked friend's chart, which exists in her People only because they agreed
# to share it. Nothing about either member is fetched from anywhere else.

def _pairs_key(circle_id):
    return f"circlepairs-{circle_id}"


def _pair_sig(members, tz, day):
    """What the cached pair numbers were computed from. Change a birth time,
    swap a member, cross midnight or move zones, and the cache no longer
    matches — it is recomputed, never served stale."""
    basis = [[r["id"], r["birth_ut"], compat.their_precision(r)] for r in members]
    raw = json.dumps([day.isoformat(), str(tz), basis], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _member_pairs(uid, c, members, ctx):
    """
    n*(n-1)/2 member pairs, each today plus six days back (SOC.pair_week).

    Cached per circle per day: at eight members that is 28 reports and 196
    skies, which is fine once and wasteful on every open of the tab. Names are
    not in the cache — they are applied at read time, so "initials only" and
    a renamed person never need a recompute. Expires on its own after two days.
    """
    usable = [r for r in members if r.get("birth_ut")]
    sig = _pair_sig(usable, ctx["tz"], ctx["day"])
    hit = load(uid, _pairs_key(c["id"]), None)
    if hit and hit.get("sig") == sig:
        return hit["pairs"]
    out = []
    for a, b in itertools.combinations(usable, 2):
        try:
            w = SOC.pair_week(a["birth_ut"], compat.their_precision(a),
                              b["birth_ut"], compat.their_precision(b), ctx["day"], ctx["tz"])
        except Exception as e:      # one odd row costs one cell, not the circle
            log.error("circle.pair_failed", exc=e, uid=uid)
            continue
        out.append({"a": a["id"], "b": b["id"], **w})
    save(uid, _pairs_key(c["id"]), {"sig": sig, "pairs": out},
         ttl=int(time.time()) + 2 * 86400)
    return out


def _pairs(uid, c, members, ctx, briefs, weeks, names):
    """Her pairs (the numbers already on each member's row) plus every member
    pair, in one list, warmest today first; and the week's pair superlatives
    over all of them from the same seven days of real scores."""
    pairs, pweeks = [], {}
    for b in briefs:
        pid = f"you-{b['id']}"
        pairs.append({"id": pid, "a": {"id": "you", "name": "You"},
                      "b": {"id": b["id"], "name": b["name"]}, "yours": True,
                      **{k: b[k] for k in ("score", "usual", "delta", "mood")}})
        if b["id"] in weeks:
            pweeks[pid] = {"name": f"You & {b['name']}", "series": weeks[b["id"]]["series"]}
    for p in _member_pairs(uid, c, members, ctx):
        if p["a"] not in names or p["b"] not in names:
            continue
        pid = f"{p['a']}-{p['b']}"
        an, bn = names[p["a"]], names[p["b"]]
        pairs.append({"id": pid, "a": {"id": p["a"], "name": an},
                      "b": {"id": p["b"], "name": bn}, "yours": False,
                      **{k: p[k] for k in ("score", "usual", "delta", "mood")}})
        pweeks[pid] = {"name": f"{an} & {bn}", "series": p["series"]}
    pairs.sort(key=lambda p: (-p["score"], -p["delta"], p["a"]["name"].lower(),
                              p["b"]["name"].lower()))
    return pairs, SOC.pair_superlatives(pweeks)


def _circle_day(uid, c, initials=False):
    ctx = _day_ctx(uid)
    if not ctx["prof"]:
        raise HTTPException(400, "Finish your own chart first.")
    members = _live(uid, c)
    briefs, weeks, charts = [], {}, []
    prof = ctx["prof"]
    pm = compat.my_precision(prof)
    charts.append(("your", daily._natal(tuple(prof["birth_ut"]), pm), pm != "unknown"))
    for r in members:
        shown = _first(r.get("name"), initials)
        # Read under the shown name: the engine writes names into its reasons.
        row = {**r, "name": shown}
        d = _brief(uid, row, ctx)
        if d is None:
            continue
        # The reason shown points the same way as the number, as in
        # daily.headline — a warm day explained by its one sharp transit reads
        # as a contradiction.
        same = [x for x in d["reasons"] if (x["weight"] >= 0) == (d["delta"] >= 0)]
        top = (same or d["reasons"] or [None])[0]
        briefs.append({"id": r["id"], "name": shown, "score": d["score"], "usual": d["usual"],
                       "delta": d["delta"], "mood": d["mood"], "headline": d["headline"],
                       "why": top and top["short"], "lasts": top and top["lasts"],
                       "linked": bool(r.get("linked_uid")), "their_day": _their_day(r)})
        try:
            weeks[r["id"]] = {"name": shown, "series": _week(uid, row, ctx)}
        except Exception as e:      # a member without a week still has a today
            log.error("circle.week_failed", exc=e, uid=uid)
        pt = compat.their_precision(r)
        charts.append((f"{shown}'s", daily._natal(tuple(r["birth_ut"]), pt), pt != "unknown"))
    ranked = daily.rank_people(briefs)
    names = {r["id"]: _first(r.get("name"), initials) for r in members}
    pairs, pair_week = _pairs(uid, c, members, ctx, briefs, weeks, names)
    in_sync = max(briefs, key=lambda b: (b["score"], -abs(b["delta"]), b["name"].lower()),
                  default=None)
    return {"id": c["id"], "name": c["name"], "date": ctx["day"].isoformat(),
            "count": len(briefs), **ranked,
            "in_sync": in_sync and {"id": in_sync["id"], "name": in_sync["name"],
                                    "score": in_sync["score"]},
            "moon": SOC.moon_note(daily.local_noon_jd(ctx["day"], ctx["tz"]), charts),
            "week": SOC.superlatives(weeks),
            "pairs": pairs, "pair_count": len(pairs), "pair_week": pair_week,
            "formulas": {"pairs": "every two of you, today's score by daily.py; two members "
                                  "are read as friends","warmest": "highest average score, last 7 days",
                         "steadiest": "smallest spread (standard deviation), last 7 days",
                         "most_charged": "largest spread, if at least ±2",
                         "best_day": "the day with the highest average across everyone"}}


@router.get("/api/circles/{circle_id}/today")
def circle_today(circle_id: str, uid: str = Depends(consented)):
    """
    The group, today: each member's score against her, ranked by how much the
    sky moved them; every pair in the group, members with each other included;
    the one Moon crossing they share; and the week's superlatives, per person
    and per pair. Every number is daily.py's.
    """
    out = _circle_day(uid, _circle(uid, circle_id))
    log.emit("circle.today", uid=uid, members=out["count"])
    return out


@router.get("/api/card/circle/{circle_id}.svg")
def card_circle(circle_id: str, initials: int = 0, uid: str = Depends(consented)):
    c = _circle(uid, circle_id)
    d = _circle_day(uid, c, initials=bool(initials))
    # The pair the group chat will argue about first; her warmest person when
    # there are no member pairs to compare.
    sup = next((s for s in d["pair_week"] if s["key"] == "in_sync"), None) or next(
        (s for s in d["week"] if s["key"] == "warmest"), None)
    # On a card she posts, she is her first name (or initial), as on the check
    # card — "You" means nothing to whoever reads it in the group chat.
    me = _first((_profile(uid) or {}).get("name") or "Me", bool(initials))
    people = [("you", me)] + [(p["id"], p["name"]) for p in d["ranked"]]
    if sup and sup["name"].startswith("You & "):
        sup = {**sup, "name": me + sup["name"][3:]}
    svg = SOC.circle_card(c["name"], _date_line(dt.date.fromisoformat(d["date"])),
                          d["ranked"], sup, people=people, pairs=d["pairs"])
    log.emit("card.circle", uid=uid, initials=bool(initials))
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-circle.svg"',
                             "Cache-Control": "private, no-store"})


# ============================================================ before you date

class Check(BaseModel):
    name: str = Field(max_length=60)
    date: str = Field(max_length=10)
    time: Optional[str] = Field(default=None, max_length=5)
    city: Optional[str] = Field(default=None, max_length=120)
    kind: Optional[str] = Field(default=None, max_length=20)
    initials: bool = False


def _their_chart(b: Check, uid):
    """
    (birth_ut, precision, notes, city_id). A time without a city means nothing
    — the offset could be anything from -12 to +14 hours — so it is set aside
    and said so, rather than silently read as UTC.
    """
    try:
        day = dt.date.fromisoformat(b.date)
    except ValueError as err:
        raise HTTPException(400, "That birth date didn't parse.") from err
    if day.year < 1900 or day > dt.datetime.now(_tz(uid)).date():
        raise HTTPException(400, "That birth date doesn't look right.")
    notes, time_ = [], (b.time or "").strip() or None
    if time_ and not _valid_time(time_):
        raise HTTPException(400, "Birth time should look like 21:15.")
    city = (b.city or "").strip()
    if city:
        c, maybe = cities.lookup(city)
        if not c:
            raise HTTPException(400, cities.refusal(
                city, maybe, f"I couldn't find {city}. Try the nearest big city."))
        hh, mm = (int(x) for x in time_.split(":")) if time_ else (12, 0)
        utc, _note = _birth_instant(day.year, day.month, day.day, hh, mm,
                                    c["tz"], c["lat"], c["lon"], bool(time_))
        ut = [utc.year, utc.month, utc.day, round(utc.hour + utc.minute / 60, 4),
              c["lat"], c["lon"]]
        return ut, ("exact" if time_ else "unknown"), notes, c.get("id")
    if time_:
        notes.append("A birth time only works with a birth city, so the time "
                     "was set aside.")
    # No place: noon UTC on the date, sampled across the whole day by compat's
    # "unknown" precision. Planet positions barely depend on where on Earth.
    return [day.year, day.month, day.day, 12.0, 0.0, 0.0], "unknown", notes, None


def _valid_time(t):
    try:
        hh, mm = (int(x) for x in t.split(":"))
    except ValueError:
        return False
    return 0 <= hh < 24 and 0 <= mm < 60


def _check(b: Check, uid):
    prof = _profile(uid)
    if not prof:
        raise HTTPException(400, "Finish your own chart first.")
    if not b.name.strip():
        raise HTTPException(400, "Give them a first name.")
    name = _first(" ".join(b.name.split()))
    if b.kind and b.kind not in compat.KINDS:
        raise HTTPException(400, f"Pick one of: {', '.join(compat.KINDS)}.")
    ut, precision, notes, city_id = _their_chart(b, uid)
    tz = _tz(uid)
    out = SOC.quick(prof, ut, precision, name, dt.datetime.now(tz).date(), tz,
                    kind=b.kind or "romantic")
    out["notes"] = notes
    # What "Save to People" sends back to POST /api/connections — her own
    # typing, returned to her, so the save needs no second form.
    out["save"] = {"name": name, "date": b.date,
                   "time": (b.time or None) if city_id else None,
                   "city": city_id, "kind": b.kind or "romantic"}
    return out


@router.post("/api/check")
@router.post("/api/quickcheck")
def quick_check(b: Check, uid: str = Depends(consented)):
    """
    "Before you date": a name and a birthday, a score in about a second.
    Nothing is stored. Free shows the score and the strongest contact; Plus the
    three and the best day — included in Plus, never a per-person fee.
    """
    out = _check(b, uid)
    full = plan.full_view(uid)
    if not full:
        out["top_contacts"] = out["top_contacts"][:1]
        out["best_day_this_week"] = None
        out["plus_required"] = True
    out["full"] = full
    log.emit("check.run", uid=uid, precision=out["precision"], full=full)
    return out


@router.post("/api/card/check.svg")
def card_check(b: Check, uid: str = Depends(consented)):
    """
    The quick check as a card. POST, not GET with the birthday in the query:
    a date someone typed about a person who isn't on the app has no business
    sitting in access logs and browser history.
    """
    out = _check(b, uid)
    me = _first((_profile(uid) or {}).get("name") or "Me")
    them = _first(out["name"], b.initials)
    contact = (out["top_contacts"] or [None])[0]
    if contact and b.initials:
        contact = {**contact, "text": contact["text"].replace(f"{out['name']}'s", f"{them}'s", 1)}
    svg = SOC.check_card(me, them, out["score"], out["tier_label"], contact, out["precision"])
    log.emit("card.check", uid=uid, initials=b.initials)
    return Response(svg, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'inline; filename="my-sign-before-you-date.svg"',
                             "Cache-Control": "private, no-store"})

