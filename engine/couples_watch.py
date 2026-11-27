"""
Keep /couples current: re-read the public record weekly, and write the reads.

Runs from the scheduler (app/cron.py, job "couples_watch") and on demand from
ops. Three jobs, each conservative on purpose:

1. Status. Every tracked person's spouse/partner statements are re-read from
   Wikidata. A pair whose status would change — a wedding, a divorce, a split —
   is held as *pending* until a second run at least six days later sees the
   same thing. Wikidata is edited by the public; a one-week debounce means a
   vandal's "divorced" never reaches the page unless nobody reverts it.

2. New partners. A statement linking a tracked person to someone we don't list
   (Kim Kardashian's next relationship, say) is a *candidate*, shown to ops.
   It is never published automatically: adding a couple is a curation choice
   (adults, public relationship, someone people know) and a code change to
   app/data/couples.json.

3. Story. Living couples' public timelines are researched on the web and
   checked mechanically (engine/couples_research.py); single-source finds wait
   for a person in ops.

4. Reads. Each couple's written read is generated once by the model from the
   measured report, grounded line by line, and cached; it is regenerated when
   the couple's status or story changes. Page views never call the model.
"""
import datetime as dt
import json
import os
import time

from engine import couples as C
from engine import couples_research as R
from engine import grounding, wikidata
from engine.store import load, save

DEBOUNCE_DAYS = 6
CANDIDATE_YEARS = 3          # older new-partner statements are history, not news
READS_PER_RUN = 12


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _published():
    return load(C.STORE, "relations", None) or {"relations": C._snapshot()["relations"],
                                                 "checked": C._snapshot()["retrieved"]}


def check_status(fetched=None, now=None):
    """Compare the live record with what the page shows; debounce changes."""
    now = now or _now()
    tracked = {frozenset((c["people"][0]["qid"], c["people"][1]["qid"])): c for c in C.couples()}
    qids = sorted({q for pair in tracked for q in pair})
    live = fetched if fetched is not None else wikidata.relations(qids)

    pub = _published()
    current = pub["relations"]
    pending = load(C.STORE, "pending", {}) or {}
    changes, published = [], []
    for pair, c in tracked.items():
        mine_live = [r for r in live if frozenset((r["a"], r["b"])) == pair]
        mine_pub = [r for r in current if frozenset((r["a"], r["b"])) == pair]
        before = C.status(c, mine_pub)["label"]
        after = C.status(c, mine_live)["label"]
        if after == before or not mine_live:
            # Back to agreeing (or Wikidata lost the statements entirely, which
            # is far likelier a bad edit than a fact): drop anything pending.
            pending.pop(c["slug"], None)
            continue
        p = pending.get(c["slug"])
        if p and p["label"] == after and \
                now - dt.datetime.fromisoformat(p["first_seen"]) >= dt.timedelta(days=DEBOUNCE_DAYS):
            current = [r for r in current if frozenset((r["a"], r["b"])) != pair] + mine_live
            pending.pop(c["slug"], None)
            published.append({"slug": c["slug"], "from": before, "to": after})
        elif not p or p["label"] != after:
            pending[c["slug"]] = {"label": after, "was": before,
                                  "first_seen": now.isoformat(timespec="seconds")}
            changes.append({"slug": c["slug"], "from": before, "to": after})

    save(C.STORE, "relations", {"relations": current, "checked": now.date().isoformat()})
    save(C.STORE, "pending", pending or None)
    _note_candidates(live, {q for pair in tracked for q in pair}, tracked, now)
    if published:
        hist = (load(C.STORE, "history", []) or [])[-199:]
        save(C.STORE, "history", hist + [{**p, "at": now.isoformat(timespec="seconds")}
                                         for p in published])
    return {"pending": changes, "published": published}


def _note_candidates(live, tracked_people, tracked_pairs, now):
    """New partners of people we follow, for a human to look at."""
    cands = load(C.STORE, "candidates", {}) or {}
    dismissed = set(load(C.STORE, "dismissed", []) or [])
    horizon = (now - dt.timedelta(days=365 * CANDIDATE_YEARS)).date().isoformat()
    for r in live:
        if r["b"] in tracked_people or frozenset((r["a"], r["b"])) in tracked_pairs:
            continue
        if r.get("end") or (r.get("start") or "") < horizon:
            continue
        key = f"{r['a']}-{r['b']}"
        if key in dismissed:
            continue
        cands.setdefault(key, {"with": C.person(r["a"])["name"], "who": r.get("b_name"),
                               "qid": r["b"], "kind": r["kind"], "since": r.get("start"),
                               "source": f"https://www.wikidata.org/wiki/{r['b']}",
                               "first_seen": now.isoformat(timespec="seconds")})
    save(C.STORE, "candidates", cands or None)


# --------------------------------------------------------------- reads

PROMPT = """You write the celebrity compatibility read on My Sign, an astrology
app. This page is public and fun: two famous people, scored by our
compatibility engine from their birth dates. Make it the kind of thing people
send to the group chat.

Voice: witty, warm, a little cheeky. Write about {a} and {b} in the third
person, by their first names. Concrete over abstract. No emoji, no "the
universe", no therapy-speak.

Fields:
- headline: the two of them in one line, under 12 words.
- vibe: two or three sentences on what this pairing is about, astrologically.
- why_it_works, where_it_rubs, the_chemistry, the_long_game: one to three
  sentences each.
- verdict: one line that sums it up with a wink. If the score is low, find the
  angle that makes it interesting — effortful pairs that go the distance are a
  story — rather than calling them doomed.
- every "because" names the placements behind the line, with whose they are:
  "{a}'s Venus in Leo", "{b}'s Moon square {a}'s Sun", "composite Sun in Taurus".

Rules, no exceptions:
- Use ONLY the facts below. Every planet, sign and aspect you mention must be in
  them, and belong to the person you say.
- These are real people. Say nothing about their private lives, behaviour,
  scandals, health, children, or anything that happened between them beyond the
  STATUS, KNOWN AS, MET and MILESTONE lines. Those are public record: use them
  for colour ("the Gemini who proposed on a beach in 2016"), never as evidence
  of anything private. Never predict a breakup, a divorce, or anything else.
- If a STATUS says the marriage ended with a death, be kind about it. No jokes
  near a death.
- Birth times are mostly unknown. Where a fact says it depends on a birth time,
  or there is a CAVEAT, don't present it as certain.

FACTS
{facts}"""

_ITEM = {"type": "object", "properties": {"text": {"type": "string"},
                                           "because": {"type": "string"}},
         "required": ["text", "because"], "additionalProperties": False}
SECTIONS = [("why_it_works", "Why it works"), ("where_it_rubs", "Where it rubs"),
            ("the_chemistry", "The chemistry"), ("the_long_game", "The long game")]
SCHEMA = {"type": "object",
          "properties": {"headline": {"type": "string"}, "vibe": {"type": "string"},
                         **{k: _ITEM for k, _t in SECTIONS},
                         "verdict": {"type": "string"}},
          "required": ["headline", "vibe", *[k for k, _t in SECTIONS], "verdict"],
          "additionalProperties": False}


def facts(c):
    """One fact per line, so the grounding check pairs a planet with a sign
    only where the chart does."""
    a, b = c["people"]
    d = C.detail(c["slug"])
    bd = d["breakdown"]
    L = [f"COUPLE: {a['name']} and {b['name']}",
         f"STATUS: {d['status']}",
         *_story_facts(d.get("story")),
         f"SCORE: {d['overall']}/100 ({d['band']})"
         + ("" if d["exact"] else f", range {d['range'][0]}-{d['range'][1]} across unknown birth times")]
    for person_card in d["people"]:
        short = person_card["short"]
        for planet, sign in person_card["signs"].items():
            if planet == "Rising":
                L.append(f"{short}'s rising sign {sign}")
            elif " or " in sign:
                L.append(f"{short}'s Moon in {sign} (depends on birth time)")
            else:
                L.append(f"{short}'s {planet} in {sign}")
    for dim in bd["dimensions"]:
        L.append(f"DIMENSION {dim['key']}: {dim['value']}/100 — {dim['means']}")
    for t in bd["themes"]:
        L.append(f"THEME {t['title']} — {t['presence']}" + (f", {t['tone']}" if t["tone"] else ""))
        for k in t["contacts"]:
            L.append(f"  {k['text']} ({k['orb']}°, {k['certainty']}): {k['meaning']}")
    comp = bd["composite"]
    L.append(f"composite Sun in {comp['sun_sign']}: {comp['sun']}")
    if comp.get("moon_sign"):
        L.append(f"composite Moon in {comp['moon_sign']}: {comp['moon']}")
    L.append(f"BALANCE: {bd['balance']['note']}")
    for v in bd["balance"]["venus_mars"]:
        L.append(f"  {v['text']}: {v['note']}")
    for cav in bd["caveats"]:
        L.append(f"CAVEAT: {cav}")
    return "\n".join(L)


def _story_facts(st):
    if not st:
        return []
    out = [f"KNOWN AS: {st['known_as']}"] if st.get("known_as") else []
    if st.get("met"):
        out.append(f"MET: {st['met'].get('year') or ''} {st['met']['text']}".replace("MET:  ", "MET: "))
    out += [f"MILESTONE {m['date']} {m['event']}: {m['text']}" for m in st.get("milestones", [])]
    return out


def _version(c):
    """What a read was written from: regenerate when either moves."""
    st = R.story(c) or {}
    return f"{C.status(c)['label']}|{len(st.get('milestones', []))}"


def write_read(c, client=None):
    """One structured, grounded read; None if there is no model or it misfires."""
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        import anthropic
        client = anthropic.Anthropic()
    from engine import models
    a, b = c["people"]
    fact_text = facts(c)
    r = client.messages.create(
        model=models.READING, max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": PROMPT.format(
            a=a["short"], b=b["short"], facts=fact_text)}])
    if r.stop_reason != "end_turn":
        return None
    out = json.loads(next(x.text for x in r.content if x.type == "text"))
    dropped = []
    for k, _t in SECTIONS:
        ok, bad = grounding.check(f"{out[k]['text']} {out[k]['because']}", fact_text)
        if not ok:
            out[k] = None
            dropped += bad
    for k in ("headline", "vibe", "verdict"):
        ok, bad = grounding.check(out[k], fact_text)
        if not ok:
            out[k] = None
            dropped += bad
    if not out.get("headline") or not out.get("vibe"):
        return None
    return {"headline": out["headline"], "vibe": out["vibe"], "verdict": out.get("verdict"),
            "sections": [{"key": k, "title": t, **out[k]} for k, t in SECTIONS if out.get(k)],
            "dropped": dropped}


def write_reads(limit=READS_PER_RUN, remaining=None, client=None):
    """Fill in missing reads, and redo any whose couple's status has changed."""
    done, failed = [], []
    if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return {"written": [], "failed": [], "skipped": "no model key"}
    attempts = 0
    for c in C.couples():
        # Every attempt counts toward the cap, failures included: a bad day at
        # the API must not turn into forty calls a run.
        if attempts >= limit or (remaining and remaining() < 45):
            break
        label = _version(c)
        have = load(C.STORE, f"read-{c['slug']}", None)
        if have and have.get("status") == label:
            continue
        attempts += 1
        try:
            read = write_read(c, client=client)
        except Exception as e:                         # one bad call never stops the run
            from app import log
            # Safe to log the API message: public figures, no user's words.
            log.error("couples.read.failed", exc=e, slug=c["slug"], detail=str(e)[:300])
            read = None
        if read is None:
            failed.append(c["slug"])
            continue
        save(C.STORE, f"read-{c['slug']}", {"read": read, "status": label,
                                            "generated_at": _now().isoformat(timespec="seconds")})
        done.append(c["slug"])
    return {"written": done, "failed": failed}


def run(remaining=None):
    started = time.time()
    out = {}
    try:
        out["status"] = check_status()
    except Exception as e:
        from app import log
        log.error("couples.watch.status_failed", exc=e)
        out["status"] = {"error": str(e)[:200]}
    # Reads before research: a missing read is a gap on the page; research is
    # colour, and whatever it finds is written into next week's reads. The job
    # has five minutes, and a web-searching call can take one of them.
    out["reads"] = write_reads(remaining=remaining)
    try:
        out["story"] = R.run(remaining=remaining)
    except Exception as e:
        from app import log
        log.error("couples.watch.story_failed", exc=e)
        out["story"] = {"error": str(e)[:200]}
    out["seconds"] = round(time.time() - started, 1)
    return out


def ops_view():
    return {"checked": _published().get("checked"),
            "pending": load(C.STORE, "pending", {}) or {},
            "candidates": load(C.STORE, "candidates", {}) or {},
            "history": (load(C.STORE, "history", []) or [])[-20:],
            "story_pending": load(C.STORE, "story_pending", {}) or {},
            "reads_missing": [c["slug"] for c in C.couples()
                              if not load(C.STORE, f"read-{c['slug']}", None)]}


def dismiss(key):
    cands = load(C.STORE, "candidates", {}) or {}
    if key not in cands:
        return False
    cands.pop(key)
    save(C.STORE, "candidates", cands or None)
    save(C.STORE, "dismissed", sorted(set(load(C.STORE, "dismissed", []) or []) | {key}))
    return True
