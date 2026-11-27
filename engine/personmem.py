"""
Relationship-aware chat: what she has said about a person, next to what the
sky is doing between them.

Co-Star's "Ask the stars" answers one question at a time, sold in packs, with
no memory of the person you asked about and no visible reason. Here, when she
names someone she has saved, the chat gets three things and nothing else:

  - the pair's synastry, from engine/compat (the same report People shows);
  - their day today and the next seven, with reasons, from engine/daily;
  - her OWN words about them, with dates — her episodes, her check-in notes
    on that person, her own chat lines — never anything the other person wrote.

A linked person's check-ins live in THEIR store. Nothing here reads another
uid, so their notes cannot reach her context; only the 1-10 they chose to
share does, through server._their_day, which the caller adds.

Every claim the demo reply makes is one of those three, quoted or computed.
"""
import datetime as dt
import hashlib
import re

from engine import compat, daily
from engine.store import load, save

LOOKAHEAD = 7           # days of "when is it easier" in the context
ECHO_DAYS = 30          # how far ahead to look for a past day's transit coming back
MAX_NOTES = 8
THREAD_SCAN = 300       # recent chat lines searched for her mentions
REMOVED = "(removed at your request)"



def _episodes(uid):
    """Every day she has, archived years included: a callback to something
    she said about him eighteen months ago is the whole point."""
    from engine import memory
    return memory.all_episodes(uid)

def _first(name):
    return (name or "").strip().split(" ")[0]


def mentioned(uid, text):
    """Saved people named in this message, longest name first, so 'Sam Lee'
    wins over 'Sam'. First names count — she won't type the surname."""
    rows = load(uid, "connections", []) or []
    low = (text or "").lower()
    out = []
    for r in sorted(rows, key=lambda r: -len(r.get("name") or "")):
        for n in {(r.get("name") or "").strip(), _first(r.get("name"))}:
            if len(n) >= 2 and re.search(rf"\b{re.escape(n.lower())}\b", low):
                out.append(r)
                break
    return out


# ------------------------------------------------------------------ her notes

def _key(source, date, text):
    return f"{source}:{date}:{hashlib.sha256((text or '').encode()).hexdigest()[:8]}"


def _forgotten(uid, cid):
    return set(load(uid, f"forget-{cid}", []) or [])


def notes(uid, row):
    """
    What SHE said about this person, newest first, each with a date, a source
    and a key she can delete it by. Quotes are exact; a summary is labelled as
    one.
    """
    from engine import thread
    from engine.memory import QUESTION_RE
    cid, name = row["id"], row.get("name") or ""
    pat = re.compile(rf"\b{re.escape(_first(name).lower())}\b") if len(_first(name)) >= 2 else None
    gone = _forgotten(uid, cid)
    out = []

    def add(source, date, text, **extra):
        text = (text or "").strip()
        if not text:
            return
        k = _key(source, date, text)
        if k in gone or any(n["key"] == k for n in out):
            return
        out.append({"key": k, "source": source, "date": date, "text": text[:280], **extra})

    for e in _episodes(uid):
        about = cid in (e.get("person_ids") or []) or (
            pat and any(pat.search((p or "").lower()) for p in e.get("people") or []))
        # A question she asked the app gets copied into that day's episode too,
        # so the same rule as the thread below applies here: the message being
        # answered right now must not come back as "On <today> you said: ...".
        asked = lambda t: t.rstrip().endswith("?") and QUESTION_RE.match(t.strip())  # noqa: E731
        quotes = [q for q in e.get("her_words") or [] if pat and pat.search(q.lower()) and not asked(q)]
        for q in quotes:
            add("quote", e["date"], q)
        if about and not quotes and e.get("summary") not in (None, "", "no notes", REMOVED) \
                and not asked(e["summary"]):
            add("day", e["date"], e["summary"])

    for date, rec in (load(uid, f"pulse-{cid}", {}) or {}).items():
        if rec.get("note"):
            add("check-in", date, rec["note"], feel=rec.get("feel"))

    if pat:
        for m in thread.recent(uid, THREAD_SCAN):
            t = (m.get("text") or "").strip()
            # Questions to the app are not things she said about him — and the
            # message being answered right now is one of them.
            if QUESTION_RE.search(t) or t.endswith("?"):
                continue
            if m.get("role") == "user" and pat.search(t.lower()):
                add("chat", (m.get("at") or "")[:10], m["text"])

    out.sort(key=lambda n: n["date"], reverse=True)
    return out


def forget(uid, cid, key):
    """
    Delete one remembered note about a person, and from retrieval at once.

    Episodes, check-in notes and chat lines are all scrubbed in place — the
    words are gone, not hidden. The confirm sheet tells her it is "deleted from
    what I remember", and for a chat line that has to include the thread: the
    last ten messages go back to the model as history every turn, so a line
    left there would come straight back into chat. The key is recorded too, so
    nothing that re-derives the same words can resurface them.
    """
    rows = load(uid, "connections", []) or []
    row = next((r for r in rows if r["id"] == cid), None)
    if not row:
        return False
    hit = next((n for n in notes(uid, row) if n["key"] == key), None)
    if not hit:
        return False
    from engine import thread
    words = hit["text"]

    def scrub(m):
        t = m.get("text") or ""
        if (hit["source"] == "chat" and m.get("role") == "user"
                and (m.get("at") or "")[:10] == hit["date"] and t.strip()[:280] == words):
            return REMOVED
        # Whatever the source, a reply that quoted her ("On Oct 3 you said: ...")
        # would carry the words straight back as history; the quote goes, the
        # reply around it stays.
        if m.get("role") == "assistant" and words in t:
            return t.replace(words, REMOVED)
        return None

    thread.rewrite(uid, scrub)
    if hit["source"] in ("quote", "day", "chat"):
        eps = _episodes(uid)
        for e in eps:
            if e["date"] != hit["date"]:
                continue
            e["her_words"] = [q for q in e.get("her_words") or [] if q.strip() != hit["text"]]
            # The same words as the day's summary go too — forgetting a quote
            # and then finding it again as a summary is not forgetting.
            if (e.get("summary") or "").strip() == hit["text"]:
                e["summary"] = REMOVED
            if hit["source"] == "day":
                e["person_ids"] = [p for p in e.get("person_ids") or [] if p != cid]
        save(uid, "episodes", eps)
    elif hit["source"] == "check-in":
        pulse = load(uid, f"pulse-{cid}", {}) or {}
        if hit["date"] in pulse:
            pulse[hit["date"]] = {**pulse[hit["date"]], "note": None}
            save(uid, f"pulse-{cid}", pulse)
    gone = load(uid, f"forget-{cid}", []) or []
    save(uid, f"forget-{cid}", sorted(set(gone) | {key}))
    return True


def by_person(uid):
    """Settings > Memory: every saved person she has said something about."""
    out = []
    for r in load(uid, "connections", []) or []:
        ns = notes(uid, r)
        if ns:
            out.append({"cid": r["id"], "name": r.get("name"), "notes": ns})
    return out


# ------------------------------------------------------------------ the sky

def _setup(prof, row):
    pm, pt = compat.my_precision(prof), compat.their_precision(row)
    mine = daily._natal(tuple(prof["birth_ut"]), pm)
    theirs = daily._natal(tuple(row["birth_ut"]), pt)
    return mine, theirs, {"moon_mine": pm != "unknown", "moon_theirs": pt != "unknown"}


def _reason(r):
    return {"text": r["text"], "transit": r["transit"], "aspect": r["aspect"],
            "lasts": r["lasts"], "pulls": "easier" if r["weight"] > 0 else "harder"}


def facts(uid, row, today, tz):
    """
    Everything the reply may say about this person, computed or quoted.
    The model gets this as JSON; the demo reply is built from it directly.
    """
    prof = load(uid, "profile", {}) or {}
    rep = compat.report_for(uid, row)
    mine, theirs, kw = _setup(prof, row)
    name = row.get("name") or "them"

    def day(d):
        return daily.day_score(rep, mine, theirs, d, tz, name, **kw)

    now = day(today)
    ahead = []
    for i in range(1, LOOKAHEAD + 1):
        d = day(today + dt.timedelta(days=i))
        ahead.append({"date": d["date"],
                      "weekday": dt.date.fromisoformat(d["date"]).strftime("%A"),
                      "score": d["score"], "delta": d["delta"], "mood": d["mood"],
                      "top": _reason(d["reasons"][0]) if d["reasons"] else None})

    # Each note's day, scored for the pair, and whether that day's leading
    # transit comes back soon. This is the "last time Mars squared his Venus
    # you said he went quiet — it's back Thursday" line, every part of it
    # either her words or the ephemeris.
    said = []
    for n in notes(uid, row)[:MAX_NOTES]:
        entry = dict(n)
        try:
            then = day(dt.date.fromisoformat(n["date"]))
        except ValueError:
            then = None
        if then and then["reasons"]:
            top = then["reasons"][0]
            entry["that_day"] = {"score": then["score"], "delta": then["delta"],
                                 "top": _reason(top)}
            # On again right now is its own answer; otherwise the first day it
            # returns. A day it is merely continuing is not a return.
            if n["date"] != now["date"] and any(r["text"] == top["text"] for r in now["reasons"]):
                entry["active_now"] = True
                said_on = dt.date.fromisoformat(n["date"])
                gap = [said_on + dt.timedelta(days=i)
                       for i in range(1, min((today - said_on).days, ECHO_DAYS))]
                # Present on every day since: it never left, so it isn't "back".
                entry["still_on"] = all(any(r["text"] == top["text"] for r in day(g)["reasons"])
                                        for g in gap)
            for i in range(1, ECHO_DAYS + 1) if not entry.get("active_now") else ():
                d = today + dt.timedelta(days=i)
                if d <= dt.date.fromisoformat(n["date"]):
                    continue
                again = day(d)
                if any(r["text"] == top["text"] for r in again["reasons"]):
                    entry["comes_back"] = {"date": again["date"],
                                           "weekday": d.strftime("%A"),
                                           "days_away": i}
                    break
        said.append(entry)

    best = max(ahead, key=lambda a: (a["score"], -ahead.index(a))) if ahead else None
    contacts = []
    for t in rep.get("themes", [])[:6]:
        for c in t.get("contacts", [])[:1]:
            contacts.append({"theme": t["title"], "text": c["text"],
                             "certainty": c.get("certainty"), "meaning": c.get("meaning")})
    return {
        "person": {"id": row["id"], "name": name, "relation": row.get("relation"),
                   "linked": bool(row.get("linked_uid"))},
        "usual": rep["score"]["overall"], "band": rep["score"].get("band"),
        "contacts": contacts[:3],
        # Their own placements, so "what's his rising?" is answered from his
        # chart rather than worked backwards from contacts (audit MEDIUM-1).
        "their_chart": compat.their_chart(row),
        "today": {"date": now["date"], "score": now["score"], "delta": now["delta"],
                  "mood": now["mood"], "headline": daily.headline(now, name),
                  "reasons": [_reason(r) for r in now["reasons"]]},
        "next_days": ahead,
        "best_next": best and {k: best[k] for k in ("date", "weekday", "score", "delta")},
        "she_said": said,
    }


def as_block(f):
    """For the model: the facts, and the rule for using them."""
    import json
    return ("<person>\n"
            f"She mentioned {f['person']['name']}. Facts below are computed by the "
            "engine or are HER OWN words with the date she said them. Quote her words "
            "exactly with their date. Never invent a transit or a number; when you use "
            "one from here, say what it means for the two of them in plain, playful "
            "words, not the astro mechanics. Never predict what "
            "the other person will do.\n"
            + json.dumps(f, default=str) + "\n</person>")


def _nice(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}"


def demo_reply(f, their_day=None):
    """
    Deterministic, grounded, no model. Every clause is one of: today's computed
    score and reason, her quoted note with its date, that day's computed
    transit, the computed date it recurs, the best computed day ahead.
    """
    name, t = f["person"]["name"], f["today"]
    gap = t["score"] - f["usual"]
    # The same line daily.mood draws, so the two halves of this sentence agree.
    up, down = gap >= daily.STEADY, gap <= -daily.STEADY
    how = "a little off" if down else "better than usual" if up else "pretty normal"
    parts = [f"You and {name} are {how} today, so it's {t['mood']}."]
    # daily.headline's rule: the reason points the way the number does. "Better
    # than usual. Blame the Moon" reads as a contradiction; say nothing instead.
    pull = "easier" if up else "harder" if down else None
    r = next((r for r in t["reasons"] if pull in (None, r["pulls"])), None)
    if r:
        who = daily._the(r["transit"])
        parts.append(f"Thank {who}: it's bringing out the best in you two, and it "
                     f"{r['lasts']}." if r["pulls"] == "easier" else
                     f"Blame {who}: it's poking at a sore spot between you two, and it "
                     f"{r['lasts']}.")
    if their_day and isinstance(their_day.get("mood"), int):
        parts.append(f"{name} says they're at {their_day['mood']}/10 today.")
    note = next((n for n in f["she_said"] if n.get("comes_back") or n.get("active_now")), None) or \
        next(iter(f["she_said"]), None)
    if note:
        parts.append(f"Remember {_nice(note['date'])}, when you said \"{note['text']}\"?")
        if note.get("that_day") and note.get("active_now"):
            parts.append("The sky is doing the exact same thing to you two today, so that "
                         "feeling might sound familiar.")
        elif note.get("that_day") and note.get("comes_back"):
            cb = note["comes_back"]
            parts.append(f"The sky was doing the same thing that day, and it's back "
                         f"{cb['weekday']} ({_nice(cb['date'])}), so maybe plan around it.")
    b = f.get("best_next")
    if b and b["score"] > t["score"]:
        parts.append(f"Your best day with {name} this week is {b['weekday']}.")
    return " ".join(parts)


def _cap(s):
    return s[:1].upper() + s[1:]
