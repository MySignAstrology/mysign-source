"""
Celebrity couple compatibility: famous pairs, scored by the same engine users get.

Nothing here is special-cased for celebrities. Each couple is two birth records
handed to engine/compat.deep — the exact function behind a user's People tab —
with the same weighting ("romantic") and the same honesty about precision: a
birth time we cannot source is unknown, so the score is a range across the day
and Moon and house claims are only made where they hold for all of it.

Where the facts come from:
  app/data/couples.json   the curated list (who is paired, which group)
  app/data/celebs.json    birth date, birthplace and relationship statements,
                          snapshotted from Wikidata (CC0) by tools/couples_build.py
  store "_couples"        live relationship status from the weekly watcher
                          (engine/couples_watch.py) and cached written reads

The write-up comes in two layers. `breakdown()` is the measured report put
into words — always available, no model involved. The "read" is a fun,
grounded narrative generated in the background by the watcher and cached; a
page view never triggers a model call, so traffic cannot run up a bill.
"""
import datetime as dt
import functools
import json
import pathlib
import re
import unicodedata

from engine import compat
from engine.chart import natal

DATA = pathlib.Path(__file__).resolve().parent.parent / "app" / "data"
STORE = "_couples"

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra",
         "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]

DIMENSION_NOTE = {
    "spark": "attraction and pull — the part that starts things",
    "ease": "how easy it is to be together day to day",
    "staying": "what holds it together over years",
}


@functools.lru_cache(maxsize=1)
def _curated():
    return json.loads((DATA / "couples.json").read_text())


@functools.lru_cache(maxsize=1)
def _snapshot():
    return json.loads((DATA / "celebs.json").read_text())


def slugify(text):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def person(qid):
    return _snapshot()["people"][qid]


def couples():
    """The curated list, each with its slug and both people resolved."""
    out = []
    for c in _curated()["couples"]:
        a, b = person(c["a"]), person(c["b"])
        out.append({**c, "slug": f"{slugify(a['name'])}-and-{slugify(b['name'])}",
                    "people": (a, b)})
    return out


def by_slug(slug):
    return next((c for c in couples() if c["slug"] == slug), None)


# ----------------------------------------------------------- birth records

def birth_ut(p):
    """[y, m, d, hour_ut, lat, lon] and whether the time is known.

    With a sourced time and offset we convert exactly. Without one we use local
    noon, approximated from longitude; compat.deep then samples the whole day
    around it, which is the point — the time is unknown, so every time counts.
    """
    y, m, d = (int(x) for x in p["birth_date"].split("-"))
    if p.get("birth_time") and p.get("utc_offset") is not None:
        hh, mm = (int(x) for x in p["birth_time"].split(":"))
        local = dt.datetime(y, m, d, hh, mm)
        utc = local - dt.timedelta(hours=p["utc_offset"])
        known = True
    else:
        utc = dt.datetime(y, m, d, 12) - dt.timedelta(hours=p["lon"] / 15)
        known = False
    return [utc.year, utc.month, utc.day, round(utc.hour + utc.minute / 60, 4),
            p["lat"], p["lon"]], known


def signs(p):
    """Sun, Moon, Venus, Mars — with the Moon given as two signs when an
    unknown birth time leaves it crossing a sign that day."""
    ut, known = birth_ut(p)
    n = natal(*ut)
    pl = n["planets"]
    out = {k: pl[k]["sign"] for k in ("Sun", "Venus", "Mars", "Mercury")}
    moon = pl["Moon"]
    if known:
        out["Moon"] = moon["sign"]
        out["Rising"] = n["angles"]["Ascendant"]["sign"]
    else:
        reach = abs(moon.get("speed", 13.2)) / 2
        lo = SIGNS[int(((moon["lon"] - reach) % 360) // 30)]
        hi = SIGNS[int(((moon["lon"] + reach) % 360) // 30)]
        out["Moon"] = moon["sign"] if lo == hi else f"{lo} or {hi}"
    return out


# --------------------------------------------------------------- status

def _year(d):
    return d[:4] if d else None


def _best(statements):
    """Among duplicate statements (Wikidata often has a year-only and an exact
    one), prefer an exact start date, then the latest."""
    def key(r):
        exact = bool(r.get("start")) and not r["start"].endswith("-01-01")
        return (exact, r.get("start") or "", r.get("end") or "")
    return max(statements, key=key) if statements else None


def status(c, relations=None):
    """What the public record says about this pair, in plain words."""
    a, b = c["people"]
    rels = relations if relations is not None else live_relations()
    pair = [r for r in rels if {r["a"], r["b"]} == {a["qid"], b["qid"]}]
    spouse = _best([r for r in pair if r["kind"] == "spouse"])
    partner = _best([r for r in pair if r["kind"] == "partner"])
    deaths = {x["death_date"][:4]: x["short"] for x in (a, b) if x.get("death_date")}

    def ended(r):
        y = _year(r["end"])
        if y and y in deaths:
            return "death", f"until {deaths[y]}'s death in {y}"
        return "end", None

    if spouse:
        since = _year(spouse.get("start"))
        if spouse.get("end"):
            why, phrase = ended(spouse)
            if why == "death":
                return {"state": "married", "label": f"Married {since or ''}, {phrase}".replace("Married , ", "Married, "),
                        "current": False, "since": since, "until": _year(spouse["end"])}
            return {"state": "divorced", "label": f"Married {since}–{_year(spouse['end'])}" if since
                    else f"Married until {_year(spouse['end'])}",
                    "current": False, "since": since, "until": _year(spouse["end"])}
        if deaths:
            y = max(deaths)
            return {"state": "married", "label": f"Married {since}, until {deaths[y]}'s death in {y}" if since
                    else f"Married until {deaths[y]}'s death in {y}",
                    "current": False, "since": since, "until": y}
        return {"state": "married", "label": f"Married since {since}" if since else "Married",
                "current": True, "since": since}
    if partner:
        since = _year(partner.get("start"))
        if partner.get("end"):
            return {"state": "split", "label": f"Together {since}–{_year(partner['end'])}" if since
                    else f"Together until {_year(partner['end'])}",
                    "current": False, "since": since, "until": _year(partner["end"])}
        return {"state": "together", "label": f"Together since {since}" if since else "Together",
                "current": True, "since": since}
    return {"state": "unknown", "label": "Relationship on record", "current": False}


def live_relations():
    """The watcher's latest confirmed statements, else the build snapshot."""
    from engine.store import load
    live = load(STORE, "relations", None)
    return (live or {}).get("relations") or _snapshot()["relations"]


# --------------------------------------------------------------- scoring

@functools.lru_cache(maxsize=128)
def report(slug):
    c = by_slug(slug)
    if not c:
        return None
    a, b = c["people"]
    ua, ka = birth_ut(a)
    ub, kb = birth_ut(b)
    return compat.deep({"birth_ut": ua}, {"birth_ut": ub}, kind="romantic",
                       precision_a="exact" if ka else "unknown",
                       precision_b="exact" if kb else "unknown",
                       name_b=b["short"])


_OBJ = r"(of|with|to|for|at|between|around|without|from|about|like|toward|towards|behind|against|than|on|into|onto|over|under|near|through|by)"
_THIRD = [(r"\byou two\b", "the two of them"), (r"\bYou two\b", "The two of them"),
          (rf"\b{_OBJ} you\b", r"\1 them"),
          (rf"\b({_OBJ.title().replace('|', '|')}) you\b", r"\1 them"),
          (r"\b(makes|make|lets|let|keeps|keep|gives|give|tells|tell|asks|ask|leaves|leave|pulls|pull|pushes|push|drives|drive|sends|send|shows|show|brings|bring|takes|take|holds|hold|wants|want|needs|need|sees|see|knows|know|finds|find|catches|catch|teaches|teach|reminds|remind|calms|calm|grounds|ground|steadies|steady|frees|free|tests|test) you\b", r"\1 them"),
          (r"\bYou're\b", "They're"), (r"\byou're\b", "they're"),
          (r"\bYourselves\b", "Themselves"), (r"\byourselves\b", "themselves"),
          (r"\bYourself\b", "Themselves"), (r"\byourself\b", "themselves"),
          (r"\bYours\b", "Theirs"), (r"\byours\b", "theirs"),
          (r"\bYour\b", "Their"), (r"\byour\b", "their"),
          (r"\bYou\b", "They"), (r"\byou\b", "they"),
          (r"\bI won't\b", "we won't"), (r"\bI'll\b", "we'll")]


def third_person(text):
    """The report is written to the user ("you will argue about pace"). Here it
    describes two other people, and English happens to make that a word swap:
    'you' and 'they' take the same verb forms."""
    for pat, rep in _THIRD:
        text = re.sub(pat, rep, text)
    return text


def _whose(c, side, planet):
    a, b = c["people"]
    return f"{(a if side == 'yours' else b)['short']}'s {planet}"


def _contact_text(c, k):
    return f"{_whose(c, 'theirs', k['theirs'])} {k['aspect']} {_whose(c, 'yours', k['yours'])}"


def breakdown(c, rep):
    """The measured report, in words, for two named people."""
    a, b = c["people"]
    sc = rep["score"]
    themes = []
    for t in rep["themes"][:5]:
        themes.append({
            "title": third_person(t["title"]),
            "means": third_person(t["means"]) if t.get("means") else None,
            "presence": t["presence"],
            "tone": t.get("tone"), "note": third_person(t["note"]) if t.get("note") else None,
            "contacts": [{"text": _contact_text(c, k), "orb": k["orb"],
                          "certainty": k["certainty"], "meaning": third_person(k["meaning"])}
                         for k in t["contacts"][:3]]})
    comp = rep["composite"]
    bal = rep["balance"]
    vm = []
    for v in bal.get("venus_mars", []):
        text = (v["text"].replace("Your ", f"{a['short']}'s ").replace("your ", f"{a['short']}'s ")
                .replace("Their ", f"{b['short']}'s ").replace("their ", f"{b['short']}'s "))
        vm.append({"text": text, "note": third_person(v["note"])})
    return {
        "score": {k: sc[k] for k in ("overall", "overall_range", "band", "dimensions", "exact")},
        "dimensions": [{"key": k, "value": v, "means": DIMENSION_NOTE[k],
                        "range": sc.get("dimension_ranges", {}).get(k)}
                       for k, v in sc["dimensions"].items()],
        "themes": themes,
        "composite": {"sun_sign": comp["signs"]["Sun"], "sun": third_person(comp["sun"]),
                      "moon_sign": comp["signs"].get("Moon") if comp.get("moon") else None,
                      "moon": third_person(comp["moon"]) if comp.get("moon") else None},
        "balance": {"note": third_person(bal["note"]), "venus_mars": vm,
                    "elements": {a["short"]: bal["yours"], b["short"]: bal["theirs"]}},
        "caveats": [third_person(u).replace("there's no Ascendant", "there's no rising sign")
                    for u in rep.get("uncertainty", [])],
    }


def _person_card(p):
    _ut, known = birth_ut(p)
    out = {"name": p["name"], "short": p["short"], "born": p["birth_date"],
           "place": p.get("place"), "died": p.get("death_date"),
           "time_known": known, "signs": signs(p), "source": p["source"]}
    if known:
        out["time"] = p["birth_time"]
        out["time_source"] = p.get("time_source")
    return out


def summary(c):
    rep = report(c["slug"])
    sc = rep["score"]
    a, b = c["people"]
    s = status(c)
    return {"slug": c["slug"], "group": c["group"], "names": [a["name"], b["name"]],
            "shorts": [a["short"], b["short"]], "overall": sc["overall"],
            "range": sc["overall_range"], "band": sc["band"], "exact": sc["exact"],
            "status": s["label"], "current": s.get("current", False),
            "suns": [signs(a)["Sun"], signs(b)["Sun"]]}


def detail(slug):
    c = by_slug(slug)
    if not c:
        return None
    from engine.store import load
    rep = report(slug)
    a, b = c["people"]
    read = load(STORE, f"read-{slug}", None)
    return {**summary(c), "people": [_person_card(a), _person_card(b)],
            "status_detail": status(c), "breakdown": breakdown(c, rep),
            "read": read.get("read") if read else None,
            "story": _story(c),
            "sources": {"birth_data": "Wikidata (CC0)",
                        "retrieved": _snapshot()["retrieved"],
                        "status_checked": (load(STORE, "relations", None) or {}).get("checked")
                        or _snapshot()["retrieved"]}}


def _story(c):
    from engine import couples_research
    return couples_research.story(c)


def listing():
    cur = _curated()
    items = [summary(c) for c in couples()]
    return {"groups": cur["groups"], "couples": items}
