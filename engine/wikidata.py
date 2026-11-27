"""
Public facts about public people, from Wikidata.

Wikidata is the structured database behind Wikipedia. Its data is CC0 — free to
use for anything, commercial included — and every statement can carry a
reference. It gives us what a celebrity compatibility page needs and nothing
more: birth date, birthplace (with coordinates), death date, and marriages and
partnerships with their start and end dates. It does not hold birth times, and
we do not guess them.

Used two ways: tools/couples_build.py snapshots it into app/data/celebs.json
at build time, and engine/couples_watch.py re-reads the relationship statements
weekly so a divorce or a new marriage shows up without anyone editing a file.
"""
import json
import ssl
import urllib.parse
import urllib.request

ENDPOINT = "https://query.wikidata.org/sparql"
UA = "MySign/1.0 (https://mysignapp.com; hello@mysignapp.com) couples page"

PERSON_QUERY = """
SELECT ?p ?pLabel ?birth ?birthPrec ?death ?place ?placeLabel ?coord WHERE {
  VALUES ?p { %(ids)s }
  OPTIONAL { ?p p:P569/psv:P569 [ wikibase:timeValue ?birth ; wikibase:timePrecision ?birthPrec ] . }
  OPTIONAL { ?p wdt:P570 ?death . }
  OPTIONAL { ?p wdt:P19 ?place . OPTIONAL { ?place wdt:P625 ?coord . } }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""

# Spouse (P26) and unmarried partner (P451), with start (P580) and end (P582).
RELATION_QUERY = """
SELECT ?p ?prop ?other ?otherLabel ?start ?end WHERE {
  VALUES ?p { %(ids)s }
  VALUES ?prop { p:P26 p:P451 }
  ?p ?prop ?st .
  ?st ?ps ?other .
  FILTER(?ps IN (ps:P26, ps:P451))
  OPTIONAL { ?st pq:P580 ?start . }
  OPTIONAL { ?st pq:P582 ?end . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}"""


def _ssl_context():
    """The OS trust store for this request only, where truststore is installed
    (it fixes TLS behind corporate proxies on a laptop). Never
    truststore.inject_into_ssl(): that swaps ssl.SSLContext for the whole
    process, and in the cron Lambda botocore's next client then recursed until
    it crashed — the couples job, and every later job on that warm container."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return ssl.create_default_context()


def sparql(query, timeout=60):
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return json.loads(r.read())["results"]["bindings"]


def _qid(uri):
    return uri.rsplit("/", 1)[-1]


def _v(row, k):
    return row[k]["value"] if k in row else None


def _date(v):
    return v[:10].lstrip("+") if v else None


def people(qids):
    """{qid: {name, birth_date, birth_precision, death_date, place, lat, lon}}"""
    out = {}
    ids = " ".join(f"wd:{q}" for q in qids)
    for row in sparql(PERSON_QUERY % {"ids": ids}):
        q = _qid(_v(row, "p"))
        rec = out.setdefault(q, {"qid": q, "name": _v(row, "pLabel")})
        b = _v(row, "birth")
        prec = int(_v(row, "birthPrec") or 0)
        # A person can carry several birth-date statements (a disputed day, a
        # year-only value). Keep the most precise one.
        if b and prec >= rec.get("birth_precision", -1):
            rec["birth_date"], rec["birth_precision"] = _date(b), prec
        if _v(row, "death"):
            rec["death_date"] = _date(_v(row, "death"))
        coord = _v(row, "coord")
        if coord and "lat" not in rec:
            lon, lat = coord.removeprefix("Point(").rstrip(")").split()
            rec.update(place=_v(row, "placeLabel"), lat=float(lat), lon=float(lon))
    return out


def relations(qids):
    """[{a, b, kind: spouse|partner, start, end}] for the given people."""
    out = []
    ids = " ".join(f"wd:{q}" for q in qids)
    for row in sparql(RELATION_QUERY % {"ids": ids}):
        out.append({"a": _qid(_v(row, "p")), "b": _qid(_v(row, "other")),
                    "b_name": _v(row, "otherLabel"),
                    "kind": "spouse" if _v(row, "prop").endswith("P26") else "partner",
                    "start": _date(_v(row, "start")), "end": _date(_v(row, "end"))})
    return out
