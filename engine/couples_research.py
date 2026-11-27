"""
Each couple's public story: how they met, the timeline, what they're known as.

Two layers, like the rest of /couples:

  app/data/couples_story.json   the researched snapshot — every item carries the
                                URLs it came from. Reviewed by a person before it
                                was committed. Documented birth times found in
                                the same research go into app/data/couples.json,
                                never here: a birth time changes the score, so it
                                is a code change with a test, not a cache write.
  store "_couples" story-{key}  milestones the weekly job found since then.

The weekly job (couples_watch.run) asks the model to search the web for what
has happened to each living couple since the last milestone we have. What comes
back is checked mechanically before anything is kept:

  - every source URL must be one the search actually returned in this call, on
    a domain in REPUTABLE — a model can't cite a page it never saw;
  - the event must be one of EVENTS, the date must parse, and nothing in the
    text may touch the subjects we never cover (children, health, scandal,
    rumour — see OFF_LIMITS);
  - an item cited by two independent outlets is published; one cited by one
    outlet waits in ops ("story_pending") for a person to approve or drop.

Wikidata (couples_watch.check_status) stays the authority for the status line;
the story is colour around it, and a milestone never changes a status by itself.
"""
import datetime as dt
import functools
import json
import os
import re
import urllib.parse

from engine import couples as C
from engine.store import load, save

EVENTS = ("met", "public", "engaged", "married", "separated", "divorced",
          "reunited", "widowed", "split")
REPUTABLE = (
    "apnews.com", "reuters.com", "bbc.com", "bbc.co.uk", "nytimes.com",
    "theguardian.com", "washingtonpost.com", "people.com", "vogue.com",
    "variety.com", "billboard.com", "hollywoodreporter.com", "rollingstone.com",
    "vanityfair.com", "time.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com",
    "cnn.com", "npr.org", "latimes.com", "usatoday.com", "elle.com",
    "harpersbazaar.com", "royal.uk", "britannica.com", "biography.com",
    "en.wikipedia.org", "whitehouse.gov", "obamalibrary.gov", "jfklibrary.org",
    "fdrlibrary.org", "nobelprize.org", "espn.com", "nfl.com", "today.com",
    "etonline.com", "hellomagazine.com", "independent.co.uk", "telegraph.co.uk",
    "news.sky.com", "cnbc.com", "forbes.com", "nbcwashington.com", "abc7.com",
    "townandcountrymag.com",
)
# A hit drops the item. The story is relationship milestones, and these are
# the subjects the page promises never to touch. Word starts, so "season" is
# not "son" and "courtship" is not "court".
OFF_LIMITS = re.compile(
    r"\b(pregnan|bab(y|ies)|child|sons?\b|daughter|kids?\b|rehab|addict|overdose|"
    r"suicide|arrest|lawsuit|sued\b|custody|court\b|cheat|affair|scandal|rumou?r|"
    r"reportedly|allegedly|insider|sources? (told|say|said)|feud|abuse|illness|"
    r"cancer|diagnos)", re.I)
RESEARCH_PER_RUN = 6
RECHECK_DAYS = 21
MAX_SEARCHES = 5


@functools.lru_cache(maxsize=1)
def _snapshot():
    p = C.DATA / "couples_story.json"
    return json.loads(p.read_text()) if p.exists() else {"couples": {}}


def key(c):
    return f"{c['a']}-{c['b']}"


def _domain_ok(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in REPUTABLE)


def _clean(text):
    return not OFF_LIMITS.search(text or "")


def _date_ok(d):
    """YYYY, YYYY-MM or YYYY-MM-DD, and not in the future: a milestone that
    hasn't happened is a prediction."""
    if not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", d or ""):
        return False
    return d <= dt.date.today().isoformat()[:len(d)]


def _outlets(urls):
    """Independent outlets behind a list of URLs (bbc.com and bbc.co.uk are one)."""
    out = set()
    for u in urls:
        host = (urllib.parse.urlparse(u).hostname or "").lower()
        d = next((d for d in REPUTABLE if host == d or host.endswith("." + d)), host)
        out.add(d.split(".")[0])
    return out


def story(c):
    """The published story: snapshot plus milestones the job has confirmed."""
    snap = _snapshot()["couples"].get(key(c)) or {}
    extra = (load(C.STORE, f"story-{key(c)}", None) or {}).get("milestones", [])
    seen = {(m["date"], m["event"]) for m in snap.get("milestones", [])}
    milestones = snap.get("milestones", []) + [m for m in extra if (m["date"], m["event"]) not in seen]
    if not snap and not milestones:
        return None
    return {"met": snap.get("met"), "known_as": snap.get("known_as"),
            "milestones": sorted(milestones, key=lambda m: m["date"])}


# ----------------------------------------------------------- weekly research

PROMPT = """Research the public relationship timeline of {a} and {b}, as of {today}.

We already have these milestones:
{have}

Search reputable news (Variety, Billboard, Vogue, Vanity Fair, Rolling Stone,
NBC, CBS, ABC, CNN, NPR, USA Today, Today, Hello!, official sites) for relationship milestones we are MISSING, especially
anything since {since}: going public, engagement, marriage, separation, divorce,
reunion, a partner's death. Return only milestones confirmed by the couple or by
public record (a wedding, a court filing date, an official statement), cited by
the pages you found. If nothing new has happened, return an empty list — that is
the usual and correct answer.

Never include: children or pregnancy, health, legal disputes beyond the date of
a filing, cheating, feuds, rumours, or anything attributed to unnamed sources.
Write each `text` in your own words, under 20 words.

End your reply with the result as one JSON object and nothing after it:
{{"milestones": [{{"date": "YYYY-MM-DD, YYYY-MM or YYYY", "event": "one of {events}",
"text": "...", "sources": ["https://..."]}}]}}"""

def _seen_urls(content):
    """Every URL the web search handed the model in this response."""
    urls = set()
    for block in content:
        if getattr(block, "type", None) == "web_search_tool_result" and isinstance(block.content, list):
            urls |= {r.url for r in block.content if getattr(r, "url", None)}
    return urls


def validate(items, seen, have):
    """Split the model's milestones into publishable, needs-review, and rejected."""
    known = {(m["date"], m["event"]) for m in have}
    ok, review, rejected = [], [], []
    for m in items:
        srcs = [u for u in m.get("sources", []) if u in seen and _domain_ok(u)]
        why = (None if m.get("event") in EVENTS else "event") or \
              (None if _date_ok(m.get("date")) else "date") or \
              (None if _clean(m.get("text", "")) else "off-limits") or \
              (None if srcs else "no verified source") or \
              (None if len(m.get("text", "").split()) <= 25 else "too long") or \
              ("duplicate" if (m["date"], m["event"]) in known else None)
        if why:
            rejected.append({**m, "why": why})
            continue
        item = {"date": m["date"], "event": m["event"], "text": m["text"].strip(), "sources": srcs}
        (ok if len(_outlets(srcs)) >= 2 else review).append(item)
    return ok, review, rejected


def research(c, client):
    """One web-searching call for one couple: (publish, review, rejected)."""
    from engine import models
    a, b = c["people"]
    have = (story(c) or {}).get("milestones", [])
    since = max([m["date"] for m in have], default="the start of their relationship")
    messages = [{"role": "user", "content": PROMPT.format(
        a=a["name"], b=b["name"], today=dt.date.today().isoformat(), since=since,
        have="\n".join(f"- {m['date']} {m['event']}: {m['text']}" for m in have) or "- none",
        events=", ".join(EVENTS))}]
    seen, content = set(), []
    for _ in range(3):                       # pause_turn: a long search turn resumes
        r = client.messages.create(
            model=models.READING, max_tokens=16000, thinking={"type": "adaptive"},
            # No json_schema format here: web search results carry citations,
            # and structured outputs reject citations with a 400. The JSON is
            # parsed from the reply text and validated below like any other.
            output_config={"effort": "medium"},
            # No allowed_domains: the search can't reach several of these
            # outlets (AP, Reuters, BBC, People...) and the API rejects the
            # whole call if any listed domain is unreachable. validate() keeps
            # only REPUTABLE sources the search actually returned.
            tools=[{"type": "web_search_20260209", "name": "web_search",
                    "max_uses": MAX_SEARCHES}],
            messages=messages)
        seen |= _seen_urls(r.content)
        content = r.content
        if r.stop_reason != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": r.content}]
    if r.stop_reason != "end_turn":
        return [], [], []
    items = _milestones("".join(x.text for x in content if x.type == "text"))
    if items is None:
        return [], [], []
    return validate(items, seen, have)


def _milestones(text):
    """The last {"milestones": [...]} object in the reply, or None."""
    starts = [m.start() for m in re.finditer(r'\{\s*"milestones"', text)]
    if not starts:
        return None
    start = starts[-1]
    try:
        obj, _end = json.JSONDecoder().raw_decode(text[start:])
        items = obj["milestones"]
    except (ValueError, KeyError, TypeError):
        return None
    return [m for m in items if isinstance(m, dict)] if isinstance(items, list) else None


def _due(now):
    """Living couples, least recently researched first. Couples where both
    people have died have a finished story; the snapshot holds it."""
    out = []
    for c in C.couples():
        a, b = c["people"]
        if a.get("death_date") and b.get("death_date"):
            continue
        last = (load(C.STORE, f"story-{key(c)}", None) or {}).get("checked")
        if last and now.date() - dt.date.fromisoformat(last) < dt.timedelta(days=RECHECK_DAYS):
            continue
        out.append((last or "", c["group"] != "now", c))
    return [c for *_k, c in sorted(out, key=lambda t: (t[0], t[1]))]


def publish(c, items):
    rec = load(C.STORE, f"story-{key(c)}", None) or {}
    rec["milestones"] = rec.get("milestones", []) + items
    save(C.STORE, f"story-{key(c)}", rec)


def run(limit=RESEARCH_PER_RUN, remaining=None, client=None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return {"researched": [], "skipped": "no model key"}
        import anthropic
        client = anthropic.Anthropic()
    pending = load(C.STORE, "story_pending", {}) or {}
    done, published, held = [], [], 0
    for c in _due(now)[:limit]:
        if remaining and remaining() < 90:
            break
        try:
            ok, review, _rej = research(c, client)
        except Exception as e:                      # one bad call never stops the run
            from app import log
            # The API's message is safe to log here — public figures, no user's
            # words — and without it a 400 is undiagnosable from the logs.
            log.error("couples.research.failed", exc=e, slug=c["slug"], detail=str(e)[:300])
            continue
        if ok:
            publish(c, ok)
            published += [{"slug": c["slug"], **m} for m in ok]
        for m in review:
            pending[f"{key(c)}:{m['date']}:{m['event']}"] = {"slug": c["slug"], **m,
                                                             "found": now.date().isoformat()}
            held += 1
        rec = load(C.STORE, f"story-{key(c)}", None) or {}
        save(C.STORE, f"story-{key(c)}", {**rec, "checked": now.date().isoformat()})
        done.append(c["slug"])
    save(C.STORE, "story_pending", pending or None)
    return {"researched": done, "published": published, "held_for_review": held}


def approve(pending_key):
    pending = load(C.STORE, "story_pending", {}) or {}
    m = pending.pop(pending_key, None)
    if not m:
        return False
    c = C.by_slug(m["slug"])
    if c:
        publish(c, [{k: m[k] for k in ("date", "event", "text", "sources")}])
    save(C.STORE, "story_pending", pending or None)
    return True


def drop(pending_key):
    pending = load(C.STORE, "story_pending", {}) or {}
    if pending.pop(pending_key, None) is None:
        return False
    save(C.STORE, "story_pending", pending or None)
    return True
