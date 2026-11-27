"""
Birthplace lookup.

Was 128 hand-written cities, then 34,106 (GeoNames cities15000), which still
left out most of the towns people are born in — Thibodaux, Ellijay, Winterset
and Tok all failed. Now ~230,000 places from GeoNames cities500: every
settlement it records with 500+ people. Smaller places go through the
place-search fallback (app/places.py). Rebuild with tools/build_cities.py.

Bundled rather than called. A geocoding API would mean a key, a rate limit, a
network round trip inside signup, and a third party learning where everyone was
born. The source is ~5.5MB gzipped; search runs on a SQLite index built from it.

The dataset carries an IANA timezone per city, which is the field that actually
matters: without it a birth time cannot be converted to UT, and every house
placement depends on that conversion.

Data: GeoNames (https://www.geonames.org), CC BY 4.0.
"""
import gzip
import math
import os
import pathlib
import sqlite3
import tempfile
import threading
import unicodedata
from functools import lru_cache

DATA = pathlib.Path(__file__).parent / "data" / "cities.tsv.gz"
# Ids saved before the data was rebuilt, where GeoNames had since renamed the
# region (France's merged regions, for one). A saved person must not stop
# resolving because a département was reorganised.
LEGACY = pathlib.Path(__file__).parent / "data" / "legacy_ids.json"
_legacy = None

# GeoNames stores US regions as full names, but people type "chicago, il" — and
# that abbreviation is also what they expect to read back.
US_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY", "Puerto Rico": "PR",
}

# GeoNames already uses English names for nearly every major city — Vienna,
# Prague, Munich, Copenhagen and Moscow all resolve as typed. These are the ones
# that do not. Indexing the full alternate-names field was tried and rejected:
# it cost 361KB, and Koeln's first three Latin alternates are a Roman name, an
# airport code and the Italian spelling. A short list is smaller and correct.
ALIAS = {
    # Old names that are still on birth certificates, and what people type.
    "allahabad": "prayagraj", "gurgaon": "gurugram", "trivandrum": "thiruvananthapuram",
    "cochin": "kochi", "mysore": "mysuru", "benares": "varanasi", "banaras": "varanasi",
    "poona": "pune", "baroda": "vadodara", "simla": "shimla",
    "nyc": "new york city", "new york ny": "new york city", "cdmx": "mexico city",
    "ciudad de mexico": "mexico city", "mexico df": "mexico city",
    "geneve": "geneva", "geneva switzerland": "geneva",
    "philly": "philadelphia", "nola": "new orleans",
    # Not "la", "sf" or "dc": aliases also apply to a query's first word, and
    # "la paz" or "la plata" must not turn into Los Angeles.
    "cologne": "koln", "seville": "sevilla", "kiev": "kyiv",
    "bombay": "mumbai", "calcutta": "kolkata", "madras": "chennai",
    "saigon": "ho chi minh", "peking": "beijing", "rangoon": "yangon",
    "canton": "guangzhou", "bangalore": "bengaluru",
}

# Country names are searchable but not stored per row — repeating "United
# States" 34,000 times in the data file would be silly.
COUNTRY = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AF": "Afghanistan",
    "AG": "Antigua and Barbuda", "AI": "Anguilla", "AL": "Albania",
    "AM": "Armenia", "AN": "Netherlands Antilles", "AO": "Angola",
    "AQ": "Antarctica", "AR": "Argentina", "AS": "American Samoa",
    "AT": "Austria", "AU": "Australia", "AW": "Aruba", "AX": "Aland Islands",
    "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina", "BB": "Barbados",
    "BD": "Bangladesh", "BE": "Belgium", "BF": "Burkina Faso",
    "BG": "Bulgaria", "BH": "Bahrain", "BI": "Burundi", "BJ": "Benin",
    "BL": "Saint Barthelemy", "BM": "Bermuda", "BN": "Brunei",
    "BO": "Bolivia", "BQ": "Bonaire, Saint Eustatius and Saba ",
    "BR": "Brazil", "BS": "Bahamas", "BT": "Bhutan", "BV": "Bouvet Island",
    "BW": "Botswana", "BY": "Belarus", "BZ": "Belize", "CA": "Canada",
    "CC": "Cocos Islands", "CD": "Democratic Republic of the Congo",
    "CF": "Central African Republic", "CG": "Republic of the Congo",
    "CH": "Switzerland", "CI": "Ivory Coast", "CK": "Cook Islands",
    "CL": "Chile", "CM": "Cameroon", "CN": "China", "CO": "Colombia",
    "CR": "Costa Rica", "CS": "Serbia and Montenegro", "CU": "Cuba",
    "CV": "Cabo Verde", "CW": "Curacao", "CX": "Christmas Island",
    "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DJ": "Djibouti",
    "DK": "Denmark", "DM": "Dominica", "DO": "Dominican Republic",
    "DZ": "Algeria", "EC": "Ecuador", "EE": "Estonia", "EG": "Egypt",
    "EH": "Western Sahara", "ER": "Eritrea", "ES": "Spain", "ET": "Ethiopia",
    "FI": "Finland", "FJ": "Fiji", "FK": "Falkland Islands",
    "FM": "Micronesia", "FO": "Faroe Islands", "FR": "France", "GA": "Gabon",
    "GB": "United Kingdom", "GD": "Grenada", "GE": "Georgia",
    "GF": "French Guiana", "GG": "Guernsey", "GH": "Ghana", "GI": "Gibraltar",
    "GL": "Greenland", "GM": "Gambia", "GN": "Guinea", "GP": "Guadeloupe",
    "GQ": "Equatorial Guinea", "GR": "Greece",
    "GS": "South Georgia and the South Sandwich Islands", "GT": "Guatemala",
    "GU": "Guam", "GW": "Guinea-Bissau", "GY": "Guyana", "HK": "Hong Kong",
    "HM": "Heard Island and McDonald Islands", "HN": "Honduras",
    "HR": "Croatia", "HT": "Haiti", "HU": "Hungary", "ID": "Indonesia",
    "IE": "Ireland", "IL": "Israel", "IM": "Isle of Man", "IN": "India",
    "IO": "British Indian Ocean Territory", "IQ": "Iraq", "IR": "Iran",
    "IS": "Iceland", "IT": "Italy", "JE": "Jersey", "JM": "Jamaica",
    "JO": "Jordan", "JP": "Japan", "KE": "Kenya", "KG": "Kyrgyzstan",
    "KH": "Cambodia", "KI": "Kiribati", "KM": "Comoros",
    "KN": "Saint Kitts and Nevis", "KP": "North Korea", "KR": "South Korea",
    "KW": "Kuwait", "KY": "Cayman Islands", "KZ": "Kazakhstan", "LA": "Laos",
    "LB": "Lebanon", "LC": "Saint Lucia", "LI": "Liechtenstein",
    "LK": "Sri Lanka", "LR": "Liberia", "LS": "Lesotho", "LT": "Lithuania",
    "LU": "Luxembourg", "LV": "Latvia", "LY": "Libya", "MA": "Morocco",
    "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro", "MF": "Saint Martin",
    "MG": "Madagascar", "MH": "Marshall Islands", "MK": "North Macedonia",
    "ML": "Mali", "MM": "Myanmar", "MN": "Mongolia", "MO": "Macao",
    "MP": "Northern Mariana Islands", "MQ": "Martinique", "MR": "Mauritania",
    "MS": "Montserrat", "MT": "Malta", "MU": "Mauritius", "MV": "Maldives",
    "MW": "Malawi", "MX": "Mexico", "MY": "Malaysia", "MZ": "Mozambique",
    "NA": "Namibia", "NC": "New Caledonia", "NE": "Niger",
    "NF": "Norfolk Island", "NG": "Nigeria", "NI": "Nicaragua",
    "NL": "The Netherlands", "NO": "Norway", "NP": "Nepal", "NR": "Nauru",
    "NU": "Niue", "NZ": "New Zealand", "OM": "Oman", "PA": "Panama",
    "PE": "Peru", "PF": "French Polynesia", "PG": "Papua New Guinea",
    "PH": "Philippines", "PK": "Pakistan", "PL": "Poland",
    "PM": "Saint Pierre and Miquelon", "PN": "Pitcairn", "PR": "Puerto Rico",
    "PS": "Palestinian Territory", "PT": "Portugal", "PW": "Palau",
    "PY": "Paraguay", "QA": "Qatar", "RE": "Reunion", "RO": "Romania",
    "RS": "Serbia", "RU": "Russia", "RW": "Rwanda", "SA": "Saudi Arabia",
    "SB": "Solomon Islands", "SC": "Seychelles", "SD": "Sudan",
    "SE": "Sweden", "SG": "Singapore", "SH": "Saint Helena", "SI": "Slovenia",
    "SJ": "Svalbard and Jan Mayen", "SK": "Slovakia", "SL": "Sierra Leone",
    "SM": "San Marino", "SN": "Senegal", "SO": "Somalia", "SR": "Suriname",
    "SS": "South Sudan", "ST": "Sao Tome and Principe", "SV": "El Salvador",
    "SX": "Sint Maarten", "SY": "Syria", "SZ": "Eswatini",
    "TC": "Turks and Caicos Islands", "TD": "Chad",
    "TF": "French Southern Territories", "TG": "Togo", "TH": "Thailand",
    "TJ": "Tajikistan", "TK": "Tokelau", "TL": "Timor Leste",
    "TM": "Turkmenistan", "TN": "Tunisia", "TO": "Tonga", "TR": "Turkey",
    "TT": "Trinidad and Tobago", "TV": "Tuvalu", "TW": "Taiwan",
    "TZ": "Tanzania", "UA": "Ukraine", "UG": "Uganda",
    "UM": "United States Minor Outlying Islands", "US": "United States",
    "UY": "Uruguay", "UZ": "Uzbekistan", "VA": "Vatican",
    "VC": "Saint Vincent and the Grenadines", "VE": "Venezuela",
    "VG": "British Virgin Islands", "VI": "U.S. Virgin Islands",
    "VN": "Vietnam", "VU": "Vanuatu", "WF": "Wallis and Futuna",
    "WS": "Samoa", "XK": "Kosovo", "YE": "Yemen", "YT": "Mayotte",
    "ZA": "South Africa", "ZM": "Zambia", "ZW": "Zimbabwe",
}

DB = DATA.with_name("cities.sqlite")
_local = threading.local()


# Abbreviations people type, normalised the same way on both sides: "St Paul",
# "St. Paul" and "Saint Paul" all meet, as do "Ft Worth" and "Fort Worth".
_WORDS = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount", "pt": "port"}


def _fold(s):
    """Case-, accent- and punctuation-insensitive. Someone born in Bogota
    should not have to find the accent key to say so, and "D.C." is "DC"."""
    plain = "".join(c for c in unicodedata.normalize("NFD", s.lower())
                    if unicodedata.category(c) != "Mn").replace(".", "")
    return " ".join(_WORDS.get(w, w) for w in plain.split(" "))


def _fingerprint():
    """How names are folded, as a number. The index stores folded names, so a
    change to _fold or _WORDS makes an old index silently wrong ("St. Louis"
    stopped matching once "st" began folding to "saint") even though the data
    file never changed. The index records this and is rebuilt when it differs."""
    import zlib
    probe = ["St. Louis", "Ft. Worth", "Zürich", "Mt. Pleasant", "D.C.", "São Paulo", "Ste-Foy"]
    # SCHEMA rides along: an index from before namesakes had their own ids and
    # a detail column must be rebuilt, not queried for a column it lacks.
    return zlib.crc32("|".join([SCHEMA] + [_fold(p) for p in probe]).encode()) & 0x7FFFFFFF


SCHEMA = "v2-namesakes"


def build_db(src=DATA, dst=DB):
    """
    The searchable index, built from the TSV. Rows go in population order, so
    rowid order *is* ranking order.

    In memory, 230k places cost five seconds to parse and 267MB on every cold
    start, and a keystroke scanned all of them in Python. SQLite opens in
    milliseconds, pages in only what a query touches, and answers a prefix
    search from an index. deploy/build.sh runs this so Lambda ships it ready;
    anywhere else the first lookup builds it.
    """
    tmp = pathlib.Path(f"{dst}.tmp-{os.getpid()}")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute("""CREATE TABLE places (id TEXT, name TEXT, region TEXT, country TEXT,
                   lat REAL, lon REAL, tz TEXT, pop INTEGER, label TEXT,
                   f TEXT, fa TEXT, fl TEXT, detail TEXT)""")
    seen = set()

    def rows():
        with gzip.open(src, "rt", encoding="utf-8") as f:
            for line in f:
                cols = line.rstrip("\n").split("\t")
                name, alt, region, cc, lat, lon, tz, pop = cols[:8]
                # A namesake carries its GeoNames id as a suffix; the place that
                # always owned name|region|cc keeps the bare id (tools/build_cities.py).
                suffix, detail = (cols[8], cols[9]) if len(cols) >= 10 else ("", "")
                cid = f"{name}|{region}|{cc}" + (f"|{suffix}" if suffix else "")
                if cid in seen:
                    continue
                seen.add(cid)
                shown = US_ABBR.get(region, region) if cc == "US" else region
                label = ", ".join(x for x in (name, shown, cc) if x)
                # The local name and GeoNames' transliteration differ more than
                # you would expect — Zurich is stored as "Zuerich", which nobody types.
                yield (cid, name, region, cc, float(lat), float(lon), tz, int(pop), label,
                       _fold(name), _fold(alt) if alt else "",
                       _fold(f"{label} {alt} {region} {COUNTRY.get(cc, '')} {detail}"),
                       detail)

    con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows())
    con.execute("CREATE INDEX by_f ON places(f)")
    con.execute("CREATE INDEX by_fa ON places(fa)")
    con.execute("CREATE INDEX by_id ON places(id)")
    con.execute(f"PRAGMA user_version = {_fingerprint()}")
    con.commit()
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, dst)
    return dst


def _db():
    """One read-only connection per thread; the server answers on a pool."""
    con = getattr(_local, "con", None)
    if con is not None:
        return con
    path = DB
    # The Lambda zip ships the index without its 5MB source; there the index is
    # the dataset, and staleness only means the folding changed.
    stale = DATA.exists() and path.exists() and path.stat().st_mtime < DATA.stat().st_mtime
    if not path.exists() or stale or not _current(path):
        try:
            build_db()
        except (OSError, sqlite3.Error):    # read-only package: build in /tmp instead
            path = build_db(dst=pathlib.Path(tempfile.gettempdir()) / "mysign-cities.sqlite")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    _local.con = con
    return con


def _current(path):
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return con.execute("PRAGMA user_version").fetchone()[0] == _fingerprint()
        finally:
            con.close()
    except sqlite3.Error:
        return False


def count():
    return _db().execute("SELECT count(*) FROM places").fetchone()[0]


def countries():
    return {r[0] for r in _db().execute("SELECT DISTINCT country FROM places")}


def _public(r):
    # detail is set only where two places share a label ("Rowan County · New
    # York time"); the picker shows it so the label is never the only clue.
    return {k: r[k] for k in ("id", "label", "name", "region", "country", "tz", "detail")}


def _upper(prefix):
    """The smallest string greater than every string starting with `prefix`."""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def _words(q):
    """The folded words of a query, aliases applied. Alias the whole query
    first, then the leading word, so both "cologne" and "cologne, germany" land."""
    parts = [p for p in _fold(q or "").replace(",", " ").split() if p]
    if not parts:
        return []
    whole = " ".join(parts)
    if whole in ALIAS:
        return _fold(ALIAS[whole]).split()
    if parts[0] in ALIAS:
        return _fold(ALIAS[parts[0]]).split() + parts[1:]
    return parts


def search(q, limit=8):
    """
    Prefix first, then contains, ties broken by population.

    Accepts "chicago", "chicago, il", "chicago illinois" and "chicago, united
    states" — people reach for the region when the city name is ambiguous, which
    is exactly when they need to.
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []
    words = _words(q)
    if not words:
        return []
    head, tail = words[0], words[1:]

    # Every other word must start a word of the place's label, region or
    # country — "springfield il", "mount pleasant sc". A bare substring sent
    # "Hope, AR" to Hope Mills, NC: the "ar" inside "Carolina".
    narrow = "".join(" AND instr(' ' || replace(fl, ',', ' '), ' ' || ?) > 0" for _ in tail)
    db = _db()
    hi = _upper(head)
    starts = db.execute(
        f"""SELECT rowid, * FROM places WHERE f >= ? AND f < ?{narrow}
            UNION
            SELECT rowid, * FROM places WHERE fa >= ? AND fa < ?{narrow}
            ORDER BY rowid LIMIT ?""",
        (head, hi, *tail, head, hi, *tail, limit)).fetchall()
    out = list(starts)
    # The contains pass is a full scan (~20ms locally, 40+ on a slow runner),
    # so it runs only when nothing starts with what she typed — mid-word is a
    # rescue for "york" -> New York, not a way to pad a list that already has
    # the town she is spelling out.
    if not out:
        seen = [r["rowid"] for r in out] or [-1]
        out += db.execute(
            f"""SELECT rowid, * FROM places
                WHERE (instr(f, ?) > 0 OR instr(fa, ?) > 0){narrow}
                  AND rowid NOT IN ({",".join("?" * len(seen))})
                ORDER BY rowid LIMIT ?""",
            (head, head, *tail, *seen, limit)).fetchall()
    if not out and not tail and len(head) >= 4:
        return _near_misses(head, limit)
    return [_public(r) for r in out[:limit]]


def _near_misses(head, limit, pool=4000, cutoff=0.8):
    """
    "Chicgo" used to come back empty. Not silent — she could see it — but the
    fix was a retype she had to work out for herself. When nothing starts with
    or contains what she typed, offer the big places spelled nearly like it.
    They are marked fuzzy, and resolve() never accepts one unasked.
    """
    import difflib
    rows = _db().execute(
        "SELECT rowid, * FROM places WHERE f >= ? AND f < ? ORDER BY rowid LIMIT ?",
        (head[:2], _upper(head[:2]), pool)).fetchall()
    scored = [(difflib.SequenceMatcher(None, head, r["f"]).ratio(), -r["rowid"], r)
              for r in rows]
    best = sorted((s for s in scored if s[0] >= cutoff), key=lambda s: s[:2], reverse=True)
    return [_public(r) | {"fuzzy": True} for _, _, r in best[:limit]]


GEO = "geo|"


def geo_id(lat, lon, tz, label):
    """
    A self-contained id for a place found by the wider search (app/places.py).
    Everything a chart needs travels in it, so nothing has to be stored between
    the search and the signup. It names only the user's own birthplace, so a
    hand-edited one can only mislead its author.
    """
    label = " ".join(label.replace("|", "/").split())[:160]
    return f"{GEO}{lat:.4f}|{lon:.4f}|{tz}|{label}"


def nearest(lat, lon, box=1.5):
    """The closest listed place, for a country and region to go with a point."""
    rows = _db().execute(
        """SELECT * FROM places WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?""",
        (lat - box, lat + box, lon - box, lon + box)).fetchall()
    if not rows:
        return None
    k = max(0.2, abs(math.cos(math.radians(lat))))
    return min(rows, key=lambda r: (r["lat"] - lat) ** 2 + ((r["lon"] - lon) * k) ** 2)


def _from_geo(city_id):
    try:
        _, lat, lon, tz, label = city_id.split("|", 4)
        lat, lon = float(lat), float(lon)
        from zoneinfo import ZoneInfo
        ZoneInfo(tz)
    except Exception:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not label.strip():
        return None
    near = nearest(lat, lon)
    return {"id": city_id, "label": label, "name": label.split(",")[0].strip(),
            "region": near["region"] if near else "",
            "country": near["country"] if near else "",
            "tz": tz, "lat": lat, "lon": lon}


def _names_it(words, r):
    """
    How well the query names this place: 0 not at all ("chicago il" does not
    name Chicago Heights), 1 by name with the other words starting words of its
    label, 2 by name with every other word a whole word of it. "los angeles ca"
    names Los Angeles, CA at 2 and Los Angeles in Caraga (PH) only at 1.
    """
    best = 0
    for name in (r["name"], r["alt"] or ""):
        nw = _fold(name).replace(",", " ").split()
        if not nw or words[:len(nw)] != nw:
            continue
        label = set(r["fl"].replace(",", " ").split())
        best = max(best, 2 if all(w in label for w in words[len(nw):]) else 1)
    return best


def resolve(city_id):
    """A place, or None. See lookup() for why a plain name can be refused."""
    return lookup(city_id)[0]


def refusal(value, candidates, missing):
    """The 400 detail for a city string that was not one place: the candidates
    to choose from when it was several, or the plain message when it was none."""
    if not candidates:
        return missing
    return {"code": "ambiguous_city",
            "message": f"More than one place is called {value}. Pick one from the list.",
            "candidates": [{k: c.get(k) for k in ("id", "label", "detail", "tz")}
                           for c in candidates]}


@lru_cache(maxsize=2048)
def lookup(city_id):
    """
    (place, candidates). Look up an exact id from search().

    A well-formed id that does not exist returns None rather than falling back to
    a name search. Silently resolving to a different city would hand someone a
    chart for the wrong place, which is worse than an error they can see.

    The same goes for a plain name, the fallback for API clients and legacy
    values with no "|" in them. It used to take the most populous match, so
    "Springfield" was Missouri and "Paris" was France, 200 OK, with nothing
    saying which. Now it is accepted only when it names one place: the top
    result is named by it and no other result is ("chicago il", "Paris, TX",
    "New York City"), or the search found exactly one place. Otherwise the
    answer is no place plus the candidates, so the client can ask her.
    """
    global _legacy
    if city_id.startswith(GEO):
        return _from_geo(city_id), ()
    if _legacy is None:
        import json
        _legacy = json.loads(LEGACY.read_text(encoding="utf-8")) if LEGACY.exists() else {}
    # Ids saved before a rebuild, where GeoNames had since renamed the region.
    city_id = _legacy.get(city_id, city_id)
    hit = _db().execute("SELECT * FROM places WHERE id = ?", (city_id,)).fetchone()
    if hit is None and "|" not in city_id:
        return _by_name(city_id)
    if hit is None:
        return None, ()
    return _public(hit) | {"lat": hit["lat"], "lon": hit["lon"]}, ()


def _by_name(value):
    found = [r for r in search(value, limit=50) if not r.get("fuzzy")]
    if not found:
        return None, ()
    words = _words(value)
    db = _db()
    rank = {r["id"]: _names_it(words, db.execute(
        "SELECT name, fa AS alt, fl FROM places WHERE id = ?", (r["id"],)).fetchone())
        for r in found}
    for level in (2, 1):
        named = [r for r in found if rank[r["id"]] >= level]
        if len(named) == 1 and (level == 2 or named[0] is found[0]):
            return lookup(named[0]["id"])[0], ()
        if named:
            break
    if len(found) == 1:
        return lookup(found[0]["id"])[0], ()
    return None, tuple(named or found)[:8]


@lru_cache(maxsize=512)
def country_for_tz(tz):
    """
    Where someone probably lives now, from the browser's IANA zone: the country
    holding most of the listed population in that zone. America/New_York is
    US, Europe/London GB, Africa/Lagos NG. Not where she was born — the crisis
    line has to be one she can actually ring. None for zones no listed place
    uses (Etc/UTC, a typo), so the caller can ask rather than guess.
    """
    if not tz or len(tz) > 64:
        return None
    row = _db().execute(
        """SELECT country, sum(pop) AS n FROM places WHERE tz = ?
           GROUP BY country ORDER BY n DESC LIMIT 1""", (tz,)).fetchone()
    return row["country"] if row else None


def is_country(code):
    """A real ISO 3166 code we can name — "ZZ" is two letters, not a place."""
    return isinstance(code, str) and code.upper() in COUNTRY
