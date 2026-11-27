"""
Fetch independent reference positions from NASA JPL Horizons.

Our tests used to check the engine against published events — equinoxes, an
eclipse — which pins the Sun and the Moon on a handful of days. This pins every
body we chart, on dates spread across the years our users were born in and the
years they will check transits in, against the source NASA itself uses for
spacecraft navigation.

Horizons quantity 31 is the observer-centred ecliptic-of-date longitude of the
apparent position — light-time, deflection and aberration applied. That is the
same frame Swiss Ephemeris returns by default, so the two should agree to well
under an arcsecond. The output is committed to engine/reference/horizons.json —
it ships, so the live site can rerun the comparison — and the test reads it
offline. Rerun this only to widen the set.

    python tools/fetch_horizons.py
"""
import datetime as dt
import json
import pathlib
import random
import urllib.parse
import urllib.request

try:
    # Use the OS trust store when it is installed — a network that re-signs TLS
    # with its own root is trusted by the keychain and not by certifi.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

API = "https://ssd.jpl.nasa.gov/api/horizons.api"
OUT = pathlib.Path(__file__).resolve().parent.parent / "engine" / "reference" / "horizons.json"

# Horizons ids. Planet centres (x99), not barycentres; Chiron by its asteroid
# number, with the trailing ';' that tells Horizons it is a small body.
BODIES = {"Sun": "10", "Moon": "301", "Mercury": "199", "Venus": "299",
          "Mars": "499", "Jupiter": "599", "Saturn": "699", "Uranus": "799",
          "Neptune": "899", "Pluto": "999", "Chiron": "2060;"}


def instants():
    """Fixed, reproducible: the birth years of an 18–34 audience (and their
    parents and partners), plus the transit years the app reads today."""
    rng = random.Random(1993)
    out = [dt.datetime(2000, 1, 1, 12)]
    for year in [1955, 1962, 1970, 1978, 1985, 1988, 1990, 1992, 1994, 1996,
                 1998, 1999, 2001, 2003, 2005, 2007, 2008, 2015, 2024, 2026,
                 2027, 2030, 2035]:
        out.append(dt.datetime(year, rng.randint(1, 12), rng.randint(1, 28),
                               rng.randint(0, 23), rng.randint(0, 59)))
    # Horizons returns a TLIST in chronological order whatever order it was
    # sent in, so send it sorted and check each row's timestamp on the way back.
    return sorted(out)


def jd(t):
    return 2440587.5 + (t - dt.datetime(1970, 1, 1)).total_seconds() / 86400


def fetch(command, jds):
    q = {"format": "text", "COMMAND": f"'{command}'", "EPHEM_TYPE": "OBSERVER",
         "CENTER": "'500@399'", "TLIST_TYPE": "JD", "TIME_TYPE": "UT",
         "TLIST": " ".join(f"'{j:.6f}'" for j in jds),
         "QUANTITIES": "'31'", "ANG_FORMAT": "DEG", "CSV_FORMAT": "YES",
         "OBJ_DATA": "NO"}
    with urllib.request.urlopen(f"{API}?{urllib.parse.urlencode(q)}", timeout=60) as r:
        text = r.read().decode()
    rows = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0].strip().splitlines()
    if len(rows) != len(jds):
        raise RuntimeError(f"{command}: asked for {len(jds)} rows, got {len(rows)}")
    return [(dt.datetime.strptime(r.split(",")[0].strip()[:17], "%Y-%b-%d %H:%M"),
             float(r.split(",")[3])) for r in rows]


def main():
    ts = instants()
    jds = [jd(t) for t in ts]
    rows = [{"utc": t.isoformat(), "jd_ut": round(j, 6), "lon": {}} for t, j in zip(ts, jds)]
    for name, cmd in BODIES.items():
        for row, t, (stamp, lon) in zip(rows, ts, fetch(cmd, jds)):
            if abs((stamp - t).total_seconds()) > 60:
                raise RuntimeError(f"{name}: row for {stamp} does not match {t}")
            row["lon"][name] = lon
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "NASA JPL Horizons (ssd.jpl.nasa.gov), quantity 31: apparent "
                  "geocentric ecliptic-of-date longitude",
        "retrieved": dt.date.today().isoformat(),
        "instants": rows}, indent=1) + "\n")
    print(f"wrote {len(rows)} instants × {len(BODIES)} bodies → {OUT}")


if __name__ == "__main__":
    main()
