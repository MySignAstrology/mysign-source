"""
Snapshot the public facts behind /couples from Wikidata.

    python tools/couples_build.py

Reads the curated list (app/data/couples.json), fetches each person's birth
date, birthplace and death date plus every spouse/partner statement between the
tracked people, and writes app/data/celebs.json. Birth data never changes, so
this runs when the list changes; relationship status is refreshed live every
week by engine/couples_watch.py, with this snapshot as the starting point.

A person is refused — and the build fails loudly — if Wikidata has no
day-precision birth date or no birthplace coordinates and the curated entry
does not supply them with a source. Every number on the page has to trace to
something.
"""
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import wikidata

CURATED = ROOT / "app" / "data" / "couples.json"
OUT = ROOT / "app" / "data" / "celebs.json"
SITEMAP = ROOT / "site" / "sitemap.xml"
BEGIN, END = "  <!-- couples:begin -->", "  <!-- couples:end -->"


def write_sitemap():
    """Every couple page in the sitemap, between markers, so search engines
    find them. Rerun after changing the list."""
    from engine import couples
    couples._curated.cache_clear()
    couples._snapshot.cache_clear()
    lines = ["  <url><loc>https://mysignapp.com/couples</loc><changefreq>weekly</changefreq>"
             "<priority>0.8</priority></url>"]
    lines += [f"  <url><loc>https://mysignapp.com/couples/{c['slug']}</loc>"
              "<changefreq>weekly</changefreq><priority>0.6</priority></url>"
              for c in couples.couples()]
    text = SITEMAP.read_text()
    block = "\n".join([BEGIN, *lines, END])
    if BEGIN in text:
        head, rest = text.split(BEGIN, 1)
        text = head + block + rest.split(END, 1)[1]
    else:
        text = text.replace("</urlset>", block + "\n</urlset>")
    SITEMAP.write_text(text)
    print(f"sitemap: {len(lines)} couples URLs")


def main():
    cur = json.loads(CURATED.read_text())
    qids = sorted(cur["people"])
    fetched = wikidata.people(qids)
    people, problems = {}, []
    for q in qids:
        w, c = fetched.get(q, {}), cur["people"][q]
        rec = {"qid": q, "name": c["name"], "short": c.get("short") or c["name"],
               "birth_date": w.get("birth_date"), "death_date": w.get("death_date"),
               "place": c.get("place") or w.get("place"),
               "lat": c.get("lat", w.get("lat")), "lon": c.get("lon", w.get("lon")),
               "source": f"https://www.wikidata.org/wiki/{q}"}
        for k in ("birth_time", "utc_offset", "time_source", "place_source"):
            if k in c:
                rec[k] = c[k]
        if w.get("birth_precision", 0) < 11:
            problems.append(f"{c['name']} ({q}): no day-precision birth date")
        if rec["lat"] is None:
            problems.append(f"{c['name']} ({q}): no birthplace coordinates")
        if rec["birth_date"] and rec["birth_date"] < "1800-01-01":
            problems.append(f"{c['name']} ({q}): born before 1800, outside the ephemeris")
        people[q] = rec
    if problems:
        raise SystemExit("refusing to build:\n  " + "\n  ".join(problems))

    tracked = {frozenset((c["a"], c["b"])) for c in cur["couples"]}
    rels = [r for r in wikidata.relations(qids) if frozenset((r["a"], r["b"])) in tracked]
    OUT.write_text(json.dumps({
        "source": "Wikidata (CC0), https://www.wikidata.org",
        "retrieved": dt.date.today().isoformat(),
        "people": people, "relations": rels}, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(people)} people, {len(rels)} relationship statements → {OUT}")
    write_sitemap()


if __name__ == "__main__":
    main()
