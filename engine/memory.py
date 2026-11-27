"""
Nightly extraction and the weekly pattern job.

Design rule that everything here exists to enforce: the model PROPOSES a
pattern, code VERIFIES it. The model never gets to write "4 of the last 5
cycles" — it emits a machine-checkable predicate, and Python does the counting
against real episodes. Getting caught overclaiming costs more than being boring,
and an LLM asked to summarize its own observations will overclaim every time.
"""
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import re
from collections import Counter
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from engine import models
from engine.store import load, save

MODEL = models.EXTRACT
PROPOSE_WINDOW = 120          # ~four months of days
_client = None


def client():
    global _client
    if _client is None:
        # Here, not at the top: ~0.3s of import that most cold starts never use.
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def has_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


QUESTIONS_BYTES = 150_000
EPISODES_KEEP = 300          # live window; older ones move to a per-year archive


def _cap_bytes(items, limit):
    """Newest items that fit in `limit` bytes. Counted, not guessed: a list
    capped by length alone still crossed the item ceiling in long messages."""
    out, total = [], 0
    for it in reversed(items):
        n = len(json.dumps(it, default=str, ensure_ascii=False).encode("utf-8")) + 2  # ", "
        if total + n > limit:
            break
        out.append(it)
        total += n
    return list(reversed(out))


def upsert_episode(user_id, episode):
    """One episode per date. Re-running extraction replaces, never duplicates —
    the day is the key, and a day can be extracted more than once as it fills in.

    The live list keeps the most recent EPISODES_KEEP days; older ones move to
    episodes-<year>. One item per day for ever would cross the item ceiling in
    about ten months of daily use, and memory would silently stop."""
    eps = [e for e in load(user_id, "episodes", []) if e["date"] != episode["date"]]
    eps.append(episode)
    eps.sort(key=lambda e: e["date"])
    if len(eps) > EPISODES_KEEP:
        old, eps = eps[:-EPISODES_KEEP], eps[-EPISODES_KEEP:]
        by_year = {}
        for e in old:
            by_year.setdefault(e["date"][:4], []).append(e)
        for year, batch in by_year.items():
            arch = load(user_id, f"episodes-{year}", []) or []
            have = {e["date"] for e in batch}
            arch = sorted([e for e in arch if e["date"] not in have] + batch,
                          key=lambda e: e["date"])
            save(user_id, f"episodes-{year}", arch)
    save(user_id, "episodes", eps)
    return episode


def person_ids(user_id, names):
    """team2/memory: the saved connections these names refer to, so an episode
    about Jake can be found by Jake's id — not by a fuzzy text match that also
    finds every other Jake-shaped word. Names she has not saved map to nothing."""
    rows = load(user_id, "connections", []) or []
    want = {(n or "").strip().lower() for n in names or []}
    return sorted({r["id"] for r in rows
                   if (r.get("name") or "").strip().lower() in want
                   or (r.get("name") or "").split(" ")[0].lower() in want})


def all_episodes(user_id):
    """The live window plus every archived year, oldest first — for search,
    where a callback from eighteen months ago is the whole point."""
    from engine.store import keys
    years = sorted(k for k in keys(user_id) if re.fullmatch(r"episodes-\d{4}", k))
    out = []
    for k in years:
        out += load(user_id, k, []) or []
    return out + (load(user_id, "episodes", []) or [])


def learn_people(user_id, names):
    """Anyone she mentions by name becomes someone the app knows about. Birth
    data stays empty until she offers it — never guess at a third party's chart."""
    prof = load(user_id, "profile", None)
    if not prof:
        return []
    known = {p["name"].lower() for p in prof.get("people", [])}
    added = []
    for n in names:
        if n and n.lower() not in known and 1 < len(n) < 30:
            prof.setdefault("people", []).append({"name": n, "relation": None, "birth": None})
            known.add(n.lower())
            added.append(n)
    if added:
        save(user_id, "profile", prof)
    return added


# ---------------------------------------------------------------- extraction

class ComingUp(BaseModel):
    what: str = Field(description="What is coming, in her terms, under 12 words. "
                      "e.g. 'interview at the design agency', 'talking to mum about Christmas'.")
    when: Optional[str] = Field(description="YYYY-MM-DD if she said or clearly implied a day "
                                "(resolve 'tomorrow', 'Friday' against the conversation date). If it "
                                "can only happen after something else she mentioned (an offer after "
                                "an interview), the day after that. Else null.")
    ask: str = Field(description="The question to ask her once it has happened: warm, specific, "
                     "under 15 words, in her own terms. e.g. 'So how did the agency interview go?'")
    kind: Literal["event", "decision", "prediction", "worry"] = Field(
        description="event: something scheduled. decision: something she is weighing. "
                    "prediction: a call she made about what will happen. worry: something she is dreading.")


class Extraction(BaseModel):
    summary: str = Field(description="One factual line. What happened, not how it felt.")
    her_words: List[str] = Field(description="Verbatim quotes worth keeping. Empty if none stand out.")
    people: List[str] = Field(description="Names of people she mentioned.")
    topics: List[str] = Field(description="Short lowercase tags, e.g. 'work', 'theo', 'sleep'.")
    decision: Optional[str] = Field(description="What she decided to do, if she decided anything.")
    mood_inferred: Optional[int] = Field(description="1-10 if she did not give one and it is clear from context, else null.")
    tier: Literal["green", "amber", "red"] = Field(description="Highest safety tier reached in this conversation.")
    coming_up: List[ComingUp] = Field(description="Things still ahead of her that a friend would ask about "
                                      "later. Empty if none. Never anything from a red-tier conversation. "
                                      "Never one already listed under OPEN FOLLOW-UPS.")
    resolved: List[str] = Field(default_factory=list,
                                description="ids from OPEN FOLLOW-UPS whose outcome she told you in this "
                                "conversation: it happened and she said how it went, or she decided. "
                                "Empty if none.")


EXTRACT_PROMPT = """Extract durable facts from one day of conversation.

You are writing to a memory store that will be read back to her weeks from now.
Keep what will still matter then. Drop small talk, drop your own replies, drop
anything you inferred rather than observed.

Quote her exactly or not at all. Her own words read back to her later are the
most valuable thing in the store; a paraphrase you invented is the least.

Also note what is still AHEAD of her — the interview on Friday, the talk she is
putting off, the call she made about whether he texts first. These are what a
friend asks about next time, and they are what lets the app ask "so how did it
go?" instead of starting cold. Only things she actually said; at most four.

The conversation took place on {date}.

--- OPEN FOLLOW-UPS (things she said were coming; id: what) ---
{open_loops}

--- CHECK-IN ---
{checkin}

--- CONVERSATION ---
{transcript}"""


def extract_day(user_id, date, checkin, transcript, sky_state):
    """One episode per day. Falls back to a heuristic pass without an API key so
    memory still accumulates in demo mode — cruder, and labelled as such."""
    if not has_key():
        return extract_day_heuristic(user_id, date, checkin, transcript, sky_state)
    # Any failure falls back to the heuristic pass. This runs on the request
    # path, and a raise here used to 500 /api/state — a blank app — on every
    # load, for good, if the model refused one day's transcript. Crisis days
    # are the likeliest to be refused, so the people locked out would have
    # been the ones who most needed in.
    try:
        r = client().messages.parse(
            model=MODEL,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": EXTRACT_PROMPT.format(
                checkin=json.dumps(checkin), transcript=transcript, date=date,
                open_loops=_open_lines(user_id))}],
            output_format=Extraction,
        )
        e = r.parsed_output
        if e is None:
            raise ValueError(f"no parsed output (stop_reason={r.stop_reason})")
    except Exception as err:
        from app import log
        log.error("memory.extract.failed", exc=err, uid=user_id)
        return extract_day_heuristic(user_id, date, checkin, transcript, sky_state)
    episode = {
        "date": date,
        # Only the number she gave counts as data; a mood the model inferred
        # is kept apart, so verify() never counts a guess as a fact.
        "mood": checkin.get("mood"),
        "mood_inferred": None if checkin.get("mood") else e.mood_inferred,
        # What she tagged the day as being about, for the pattern verifier.
        "tags": checkin.get("tags") or [],
        "summary": e.summary,
        "her_words": e.her_words,
        "people": e.people,
        "topics": e.topics,
        "decision": e.decision,
        "tier": e.tier,
        "sky_state": sky_state,          # what the sky looked like, for pattern matching later
        "person_ids": person_ids(user_id, e.people),
    }
    learn_people(user_id, e.people)
    upsert_episode(user_id, episode)
    resolve_loops(user_id, e.resolved or [], date)
    if e.tier != "red":
        add_loops(user_id, date, [c.model_dump() for c in e.coming_up[:4]])
    return episode


# ------------------------------------------------------------- open loops
#
# What is still ahead of her: the interview Friday, the talk she keeps putting
# off, the call she made about whether he texts first. A friend asks about
# these next time; an app that never does is a horoscope with a chat box.
#
# A loop is raised at most on two separate days, then retired, whether or not
# the conversation got to it — asking three times is nagging, and a loop she
# ignored is an answer too. It is RESOLVED as soon as she tells us how it went:
# the nightly extraction reads the open loops and names the ones her
# conversation answered (audit HIGH-4 — none ever closed, so the interview she
# had already described was briefed again the next morning).

LOOP_LIMIT = 30          # kept per user, newest last
LOOP_STALE_DAYS = 6      # past its day by more than this, it's no longer news
LOOP_RAISES = 2
# An undated decision or prediction is not "tomorrow": "possible offer and
# negotiating" came due the day after she mentioned it, before the interview it
# depended on had happened. Undated, these wait at least this long, and never
# come due before a dated loop from the same conversation.
UNDATED_WAIT = {"event": 1, "worry": 2, "decision": 3, "prediction": 3}
# Words that say nothing about WHICH thing is coming.
_LOOP_STOP = {"go", "went", "how", "did", "so", "about", "with", "the", "back", "hear",
              "hearing", "possible", "thing", "talk", "talking", "ask", "she", "her",
              "you", "your", "it", "at", "on", "up", "out", "get", "got", "one"}


def _loop_tokens(text):
    from engine import retrieval
    return {t for t in retrieval.tokens(text) if t not in _LOOP_STOP and len(t) > 2}


def _due(loop):
    """The day after it happens. Asking 'how did the interview go?' on the
    morning of the interview is worse than not asking at all."""
    base = dt.date.fromisoformat(loop["said_on"])
    if loop.get("when"):
        with contextlib.suppress(ValueError):
            return max(base, dt.date.fromisoformat(loop["when"])) + dt.timedelta(days=1)
    wait = UNDATED_WAIT.get(loop.get("kind") or "event", 1)
    due = base + dt.timedelta(days=wait)
    if loop.get("after"):
        with contextlib.suppress(ValueError):
            due = max(due, dt.date.fromisoformat(loop["after"]) + dt.timedelta(days=1))
    return due


def same_loop(a, b):
    """Near-duplicates: the interview stored as 'final round interview at
    Fieldwork design studio' and again as 'interview at Fieldwork with Priya
    Raman'. Same day (or no day on one side) and two telling words in common,
    or one when that is all either has."""
    if a.get("when") and b.get("when") and a["when"] != b["when"]:
        return False
    ta, tb = _loop_tokens(a.get("what", "")), _loop_tokens(b.get("what", ""))
    if not ta or not tb:
        return False
    shared = ta & tb
    return len(shared) >= min(2, len(ta), len(tb)) and (
        bool(a.get("when")) == bool(b.get("when")) or len(shared) >= 2)


def add_loops(user_id, said_on, items):
    """Store what she said is coming. Same thing twice is one loop."""
    if not items:
        return []
    loops = load(user_id, "loops", []) or []
    added = []
    # The latest dated thing in this batch or still ahead: an undated decision
    # mentioned alongside it can only be asked about after it.
    dated = [it.get("when") for it in items if it.get("when")]
    dated += [loop["when"] for loop in loops
              if loop.get("status") == "open" and loop.get("when") and loop["when"] >= str(said_on)]
    anchor = max(dated) if dated else None
    for it in items:
        what = (it.get("what") or "").strip()
        if not what:
            continue
        cand = {"what": what, "when": it.get("when")}
        twin = next((loop for loop in loops if loop.get("status") == "open"
                     and (loop["what"].strip().lower() == what.lower() or same_loop(loop, cand))),
                    None)
        if twin:
            # Same thing again: a date she gives the second time fills the gap.
            if not twin.get("when") and it.get("when"):
                twin["when"] = it["when"]
            continue
        loop = {"id": hashlib.sha1(f"{said_on}|{what}".encode()).hexdigest()[:8],
                "what": what[:120], "when": it.get("when"),
                "ask": (it.get("ask") or "").strip()[:160],
                "kind": it.get("kind") or "event",
                "said_on": str(said_on), "raised": [], "status": "open"}
        if not loop["when"] and loop["kind"] != "event" and anchor:
            loop["after"] = anchor
        loops.append(loop)
        added.append(loop)
    save(user_id, "loops", loops[-LOOP_LIMIT:])
    return added


def resolve_loops(user_id, ids, day):
    """She told us how it went: never ask again."""
    ids = set(ids or [])
    if not ids:
        return []
    loops = load(user_id, "loops", []) or []
    done = []
    for loop in loops:
        if loop["id"] in ids and loop.get("status") == "open":
            loop["status"] = "resolved"
            loop["resolved_on"] = str(day)
            done.append(loop["id"])
    if done:
        save(user_id, "loops", loops)
    return done


def _open_lines(user_id):
    rows = [f"{loop['id']}: {loop['what']}" + (f" ({loop['when']})" if loop.get("when") else "")
            for loop in load(user_id, "loops", []) or [] if loop.get("status") == "open"]
    return "\n".join(rows) or "(none)"


def due_loops(user_id, today, limit=2):
    """Open loops whose day has come (never the day she mentioned them), newest
    first, not yet raised today, not raised too often, not stale, and one per
    near-duplicate group."""
    today = today if isinstance(today, dt.date) else dt.date.fromisoformat(str(today))
    out = []
    for loop in load(user_id, "loops", []) or []:
        if loop.get("status") != "open" or loop["said_on"] >= today.isoformat():
            continue
        due = _due(loop)
        if due > today or (today - due).days > LOOP_STALE_DAYS:
            continue
        if today.isoformat() in loop.get("raised", []) or len(loop.get("raised", [])) >= LOOP_RAISES:
            continue
        out.append(loop)
    out.sort(key=_due, reverse=True)
    kept = []
    for loop in out:
        if not any(same_loop(loop, k) for k in kept):
            kept.append(loop)
    return kept[:limit]


def raised_in(reply, loops):
    """The briefed loops this reply actually asked about. 'Ask about at most
    one' means the other is briefed and not raised, and marking both used up
    the offer loop's two raises without her ever being asked (audit HIGH-4)."""
    said = _loop_tokens(reply or "")
    out = []
    for loop in loops:
        want = _loop_tokens(f"{loop.get('what', '')} {loop.get('ask', '')}")
        if want and len(said & want) >= min(2, len(want)):
            out.append(loop["id"])
    return out


def mark_raised(user_id, ids, today, reply=None):
    """Called once a reply that carried these loops actually went out. With
    `reply`, only the loops the reply really asked about count as raised."""
    if not ids:
        return
    loops = load(user_id, "loops", []) or []
    if reply is not None:
        ids = raised_in(reply, [loop for loop in loops if loop["id"] in ids])
        if not ids:
            return
    day = today.isoformat() if isinstance(today, dt.date) else str(today)
    for loop in loops:
        if loop["id"] in ids and day not in loop.setdefault("raised", []):
            loop["raised"].append(day)
            if len(loop["raised"]) >= LOOP_RAISES:
                loop["status"] = "retired"
    save(user_id, "loops", loops)


# Without a model the extraction is a pattern match, so this is too: a
# sentence that points at a day ahead becomes a loop. Crude, labelled, and
# enough for demo mode to show the follow-up working end to end.
_AHEAD = re.compile(
    r"\b(tomorrow|tonight|this weekend|next week|on (?:mon|tues|wednes|thurs|fri|satur|sun)day|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b", re.I)
_WEEKDAY = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _resolve(word, said_on):
    w = word.lower().replace("on ", "")
    if w in ("tonight",):
        return said_on
    if w == "tomorrow":
        return said_on + dt.timedelta(days=1)
    if w == "this weekend":
        return said_on + dt.timedelta(days=(5 - said_on.weekday()) % 7 or 7)
    if w == "next week":
        return said_on + dt.timedelta(days=7 - said_on.weekday())
    if w in _WEEKDAY:
        return said_on + dt.timedelta(days=(_WEEKDAY.index(w) - said_on.weekday()) % 7 or 7)
    return said_on + dt.timedelta(days=1)


def loops_heuristic(lines, said_on):
    said_on = said_on if isinstance(said_on, dt.date) else dt.date.fromisoformat(str(said_on))
    items = []
    for line in lines:
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            m = _AHEAD.search(sentence)
            if not m or len(sentence) > 160:
                continue
            what = sentence.strip().rstrip(".!?")
            items.append({"what": what[:80], "when": _resolve(m.group(1), said_on).isoformat(),
                          "ask": f"Last time you said \u201c{what[:80]}\u201d. How did it go?",
                          "kind": "event"})
    return items[:4]


# --------------------------------------------------- heuristic (no-key) path

TOPIC_WORDS = {
    "work": ("work", "job", "boss", "shift", "meeting", "interview", "quit"),
    "sleep": ("sleep", "slept", "tired", "insomnia", "awake", "nap"),
    "money": ("money", "rent", "broke", "paid", "bills", "afford"),
    "family": ("mom", "dad", "sister", "brother", "family", "parents"),
    "friends": ("friend", "friends", "party", "hang", "plans"),
    "dating": ("text", "texted", "date", "dating", "situationship", "crush", "ex"),
    "health": ("sick", "doctor", "gym", "workout", "period", "anxious"),
}
NAME_RE = re.compile(r"\b([A-Z][a-z]{2,14})\b")
STOP = {"I", "Im", "Ive", "The", "And", "But", "So", "Not", "You", "It", "My",
        "We", "He", "She", "They", "This", "That", "What", "Why", "How", "Today",
        "Yesterday", "Tomorrow", "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday", "Quiet", "Answer", "Neptune", "Mercury",
        "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Pluto", "Moon", "Sun"}


def extract_day_heuristic(user_id, date, checkin, transcript, sky_state):
    """No model. Good enough to make memory visible in demo mode, and honest
    about being cruder — it keeps her exact words, which is the part that matters,
    and guesses at everything else."""
    lines = [line[6:].strip() for line in transcript.splitlines() if line.startswith("user:")]
    blob = " ".join(lines)
    low = blob.lower()

    topics = sorted(t for t, words in TOPIC_WORDS.items() if any(w in low for w in words))
    people = {n for n in NAME_RE.findall(blob) if n not in STOP}
    # People she has already named stay recognizable when she stops capitalizing —
    # which she will, because this is a chat and not an essay.
    for known in load(user_id, "profile", {}).get("people", []):
        if re.search(rf"\b{re.escape(known['name'].lower())}\b", low):
            people.add(known["name"])
    people = sorted(people)
    summary = (checkin.get("text") or (lines[0] if lines else "")).strip()[:140] or "no notes"
    # Don't keep a quote that just restates the summary — on a day where the only
    # thing she wrote was the check-in, those are the same sentence.
    quotes = [line for line in sorted((line for line in lines if 12 <= len(line) <= 160),
                                key=len, reverse=True)
              if line.strip().lower() != summary.strip().lower()][:2]

    episode = {
        "date": date,
        "mood": checkin.get("mood"),
        "tags": checkin.get("tags") or [],
        "summary": summary,
        "her_words": quotes,
        "people": people,
        "topics": topics,
        "decision": None,
        "tier": "green",
        "sky_state": sky_state,
        "extraction": "heuristic",
        "person_ids": person_ids(user_id, people),
    }
    learn_people(user_id, people)
    upsert_episode(user_id, episode)
    # No model to judge an answer: a loop asked about that day, and a line of
    # hers that day naming the same thing, is taken as her telling us.
    asked = [loop for loop in load(user_id, "loops", []) or []
             if loop.get("status") == "open" and date in loop.get("raised", [])]
    resolve_loops(user_id, [loop["id"] for loop in asked
                            if any(raised_in(line, [loop]) for line in lines)], date)
    add_loops(user_id, date, loops_heuristic(lines, date))
    return episode


# ------------------------------------------------------------ pattern mining

class Condition(BaseModel):
    kind: Literal["moon_phase", "moon_sign", "transit_aspect", "weekday", "house_transit", "tag", "tag_day_before"]
    value: str = Field(description="e.g. 'full moon', 'Aquarius', 'Neptune-conjunction-Mercury', 'Saturday', '12', "
                                   "or for 'tag' / 'tag_day_before' one of her check-in tags: 'sleep', 'work', 'outdoors'")
    window_days: int = Field(description="0 = the day itself. 2 = also counts the two days before.")


class Proposal(BaseModel):
    claim: str = Field(description="One sentence in her second person. No numbers — code supplies those.")
    condition: Condition
    outcome_kind: Literal["mood_below", "mood_above", "topic_present"]
    outcome_value: str = Field(description="A number as a string for mood, or a topic tag.")


class Proposals(BaseModel):
    proposals: List[Proposal]


PROPOSE_PROMPT = """Here are dated episodes from one person, each with the sky state on that day.

Propose up to 5 candidate patterns worth TESTING. You are not asserting anything — a
separate process will count the evidence and throw out whatever does not hold. So
propose things that are specific and checkable, including ones you are unsure about.

Do not put any number in the claim text. Do not write "usually" or "often". Write the
claim as if the count will be appended to it.

EPISODES:
{episodes}"""


def propose_patterns(user_id):
    eps = load(user_id, "episodes", [])
    if len(eps) < 10:
        return []                                   # not enough to mine yet
    if not has_key():
        # team2/memory: no model, so code proposes — every observed sky
        # condition against her own outcomes. verify() still decides, and the
        # long list makes its Bonferroni gate stricter, not looser.
        from engine import unlocks
        return unlocks.candidates(user_id)
    # The proposal reads a bounded window, not her whole history: it used to
    # send every episode every week, a cost that grew without limit. Proposals
    # only suggest; verify() still tests each one against every episode.
    r = client().messages.parse(
        model=models.PATTERNS,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        messages=[{"role": "user", "content": PROPOSE_PROMPT.format(
            episodes=json.dumps(eps[-PROPOSE_WINDOW:], indent=1))}],
        output_format=Proposals,
    )
    return r.parsed_output.proposals if r.parsed_output else []


# ------------------------------------------------------------ verification

def _sky_on(date):
    """
    Moon phase and sign for any date, computed rather than stored.

    Needed because a window has to look at days that may have no episode at all
    — she does not check in every day, and a pattern about "the two days before
    the full moon" cannot depend on her having written something on both.

    Only the chart-independent properties. Anything involving her natal chart
    needs the chart, which is why the window is refused for those kinds below.
    """
    from engine.chart import SIGNS, jd_ut, moon_phase, positions
    d = dt.date.fromisoformat(date)
    j = jd_ut(d.year, d.month, d.day, 12.0)
    return {"moon_phase": moon_phase(j)["phase"],
            "moon_sign": SIGNS[int(positions(j)["Moon"]["lon"] // 30)]}


def _window_dates(date, window_days):
    d = dt.date.fromisoformat(date)
    return [(d - dt.timedelta(days=n)).isoformat()
            for n in range(max(0, int(window_days or 0)) + 1)]


def _condition_holds(cond, ep, by_date=None):
    """
    Does the condition hold for this episode?

    window_days used to be read and then thrown away — a pattern proposed as
    "the two days before the full moon" was silently checked against the exact
    day only, so a real pattern would be under-counted and rejected. The verifier
    was stricter than intended in a way nothing surfaced.
    """
    sky = ep.get("sky_state", {})
    window = max(0, int(cond.window_days or 0))

    if cond.kind == "moon_phase":
        if window == 0:
            return cond.value.lower() in (sky.get("moon_phase") or "").lower()
        return any(cond.value.lower() in _sky_on(d)["moon_phase"].lower()
                   for d in _window_dates(ep["date"], window))
    if cond.kind == "moon_sign":
        if window == 0:
            return cond.value.lower() == (sky.get("moon_sign") or "").lower()
        return any(cond.value.lower() == _sky_on(d)["moon_sign"].lower()
                   for d in _window_dates(ep["date"], window))
    if cond.kind == "weekday":
        return any(cond.value.lower() == dt.date.fromisoformat(d).strftime("%A").lower()
                   for d in _window_dates(ep["date"], window))
    if cond.kind == "tag":
        # Her own tag, that day or in the window before it ("the day after a
        # poor-sleep night"). Other days come from by_date; a day with no
        # check-in simply has no tags.
        v = cond.value.lower()
        for d in _window_dates(ep["date"], window):
            day = ep if d == ep["date"] else (by_date or {}).get(d) or {}
            if v in [t.lower() for t in day.get("tags") or []]:
                return True
        return False
    if cond.kind == "tag_day_before":
        # "The day after you tag sleep": the tag on the previous calendar day
        # only. Counting the tagged day as well halves a real next-day effect.
        prev = (dt.date.fromisoformat(ep["date"]) - dt.timedelta(days=1)).isoformat()
        day = (by_date or {}).get(prev) or {}
        return cond.value.lower() in [t.lower() for t in day.get("tags") or []]
    # These two read her stored sky_state, which only exists for days she
    # checked in. Honouring a window would mean recomputing her chart against
    # every day in it; until that is worth doing, a window here is refused
    # rather than silently ignored.
    if cond.kind == "transit_aspect":
        if window:
            return False
        return cond.value in (sky.get("aspect_keys") or [])
    if cond.kind == "house_transit":
        return cond.value in [str(h) for h in (sky.get("active_houses") or [])]
    return False


def _outcome_holds(kind, value, ep):
    if kind in ("mood_below", "mood_above"):
        if ep.get("mood") is None:
            return None                              # abstain, don't count as a miss
        m, v = ep["mood"], float(value)
        return m < v if kind == "mood_below" else m > v
    if kind == "topic_present":
        return value.lower() in [t.lower() for t in ep.get("topics", [])]
    return None


def _binom_sf(k, n, p):
    """P(X >= k) for X ~ Binomial(n, p). Exact — no scipy dependency."""
    if k <= 0:
        return 1.0
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))


def verify(user_id, proposal, min_support=3, min_lift=1.5, alpha=0.05, n_tests=1):
    """
    Count it. Three gates, and a pattern has to clear all of them:

      support   enough days where the condition actually occurred
      lift      beats her base rate — otherwise "you feel bad on full moons"
                is just a restatement of "you often feel bad"
      binomial  the run is unlikely to be noise, Bonferroni-corrected for how
                many proposals we tested this round

    That last gate is the one that matters and the one that is easy to skip.
    Test five candidate patterns against thirty noisy days and one of them will
    look convincing by chance. Telling her about it is how the product becomes
    a horoscope with extra steps.
    """
    eps = load(user_id, "episodes", [])
    by_date = {e["date"]: e for e in eps}
    tagged = proposal.condition.kind in ("tag", "tag_day_before")
    if tagged and proposal.outcome_kind == "topic_present":
        # Her tags are written into the check-in she sends, and topics are
        # extracted from it: "tagged work -> a day about work" is an echo, not
        # evidence. It held 10 of 10 on noise.
        return None
    if tagged:
        # Against the days she was tagging, not every day she ever logged: a
        # base full of untagged or pre-tag days says nothing about a tag.
        def _tagged(e):
            prev = (dt.date.fromisoformat(e["date"]) - dt.timedelta(days=1)).isoformat()
            return bool(e.get("tags") or (by_date.get(prev) or {}).get("tags"))
        eps = [e for e in eps if _tagged(e)]
    hit = tot = base_hit = base_tot = 0
    for ep in eps:
        o = _outcome_holds(proposal.outcome_kind, proposal.outcome_value, ep)
        if o is None:
            continue
        base_tot += 1
        base_hit += bool(o)
        if _condition_holds(proposal.condition, ep, by_date):
            tot += 1
            hit += bool(o)

    if tot < min_support or base_tot == 0 or hit == 0:
        return None
    rate, base_rate = hit / tot, base_hit / base_tot
    if base_rate == 0 or rate / base_rate < min_lift:
        return None

    p_value = _binom_sf(hit, tot, base_rate)
    if p_value > alpha / max(n_tests, 1):
        return None

    confidence = "high" if (tot >= 6 and rate >= 0.8) else "medium" if tot >= 4 else "low"
    return {
        "p_value": round(p_value, 4),
        "claim": proposal.claim,
        "support": f"{hit} of the last {tot}",
        "rate": round(rate, 2),
        "base_rate": round(base_rate, 2),
        "lift": round(rate / base_rate, 2),
        "confidence": confidence,
        "condition": proposal.condition.model_dump(),
        "outcome": {"kind": proposal.outcome_kind, "value": proposal.outcome_value},
    }


def weekly_pattern_job(user_id, props=None):
    """Propose, verify, keep only what survives. Overwrites — patterns are derived, not history."""
    props = propose_patterns(user_id) if props is None else props
    # The model picks its handful after reading her whole history, so the
    # real number of hypotheses is everything it could have picked from.
    # Correct for at least the code-side candidate list, never just the five.
    from engine import unlocks as _u
    n_tests = len(props)
    if has_key():
        try:
            n_tests = max(n_tests, len(_u.candidates(user_id)))
        except Exception:
            n_tests = max(n_tests, 50)
    survivors = [v for p in props
                 if (v := verify(user_id, p, n_tests=n_tests))]
    survivors.sort(key=lambda p: (-{"high": 2, "medium": 1, "low": 0}[p["confidence"]], -p["lift"]))
    # team2/memory: keep how many were tested, so a re-read re-verifies at the
    # same Bonferroni bar, and an id so each pattern unlocks exactly once.
    from engine import unlocks
    for v in survivors:
        v["n_tests"] = n_tests
        v["id"] = unlocks.pattern_id(v)
    save(user_id, "patterns", survivors)
    unlocks.prune_seen(user_id, survivors)
    return survivors


def fires_today(user_id, sky_state, ep_stub):
    """Which stored patterns are relevant to today's sky? Retrieval hook for the chat turn."""
    out = []
    # Tag patterns read her own check-ins: today's, and yesterday's for "the
    # day after you tag sleep". Episodes for those days may not exist yet.
    day = dt.date.fromisoformat(ep_stub["date"])
    # Every day a stored pattern's window can reach, as verify() reads them.
    reach = max([int((p.get("condition") or {}).get("window_days") or 0)
                 for p in load(user_id, "patterns", []) or []] + [1])
    by_date = {}
    for n in range(1, reach + 1):
        k = (day - dt.timedelta(days=n)).isoformat()
        by_date[k] = {"tags": (load(user_id, f"checkin-{k}", None) or {}).get("tags") or []}
    today_tags = (load(user_id, f"checkin-{ep_stub['date']}", None) or {}).get("tags") or []
    for p in load(user_id, "patterns", []):
        cond = Condition(**p["condition"])
        if _condition_holds(cond, {"date": ep_stub["date"], "sky_state": sky_state,
                                   "tags": today_tags}, by_date):
            out.append(p)
    return out


# ------------------------------------------------------- what she keeps asking

QUESTION_RE = re.compile(r"\b(should|why|what|how|when|will|is it|do you think|am i|can i)\b", re.I)


def remember_question(user_id, text, sky_state=None):
    """
    Every question, tagged and dated.

    Episodes record what happened; this records what she went looking for, which
    is a different thing and often the more revealing one. Someone can log a
    perfectly fine week and still have asked about the same person nine times.
    """
    topics = sorted(t for t, words in TOPIC_WORDS.items()
                    if any(w in text.lower() for w in words))
    known = [p["name"] for p in load(user_id, "profile", {}).get("people", [])
             if re.search(rf"\b{re.escape(p['name'].lower())}\b", text.lower())]
    rec = {
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "text": text[:300],
        "topics": topics,
        "people": known,
        "is_question": bool(QUESTION_RE.search(text) or text.rstrip().endswith("?")),
        "moon_sign": (sky_state or {}).get("moon_sign"),
    }
    qs = load(user_id, "questions", [])
    qs.append(rec)
    save(user_id, "questions", _cap_bytes(qs[-500:], QUESTIONS_BYTES))
    return rec


def recurring_themes(user_id, min_count=3, days=120):
    """
    Themes she has raised enough times to be worth naming back to her.

    Deliberately conservative. Saying "you keep asking about this" when she asked
    twice is the same overclaiming the pattern verifier exists to prevent — it
    just costs less to get wrong.
    """
    cutoff = dt.date.today() - dt.timedelta(days=days)
    topics, people = Counter(), Counter()
    for q in load(user_id, "questions", []):
        if dt.date.fromisoformat(q["at"][:10]) < cutoff:
            continue
        topics.update(q.get("topics", []))
        people.update(q.get("people", []))
    return {
        "topics": [{"name": t, "count": c} for t, c in topics.most_common()
                   if c >= min_count],
        "people": [{"name": p, "count": c} for p, c in people.most_common()
                   if c >= min_count],
        "total": len(load(user_id, "questions", [])),
    }
