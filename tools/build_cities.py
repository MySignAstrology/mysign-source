#!/usr/bin/env python3
"""
Rebuild app/data/cities.tsv.gz from a GeoNames dump.

    curl -O https://download.geonames.org/export/dump/cities500.zip
    curl -O https://download.geonames.org/export/dump/admin1CodesASCII.txt
    curl -O https://download.geonames.org/export/dump/admin2Codes.txt
    unzip cities500.zip
    python tools/build_cities.py cities500.txt admin1CodesASCII.txt app/data/cities.tsv.gz \\
        --admin2 admin2Codes.txt --previous app/data/cities.tsv.gz

cities500 is every settlement GeoNames records with 500+ people (~237k
places). The previous file was cities15000 (34k), which left out most of the
towns people are actually born in — a small-town birthplace could not finish
onboarding. Anything smaller still is reached through the place-search fallback
in app/places.py.

Output columns, one place per line, sorted by population (search ranks by
document order): name, ascii name if it differs, region (admin1), country code,
lat, lon (4 dp — 11 m, far finer than a chart needs), IANA timezone, population,
id suffix, and a disambiguator.

Namesakes. Ids were name|region|cc and only the first place per id was kept,
which dropped ~6,200 real towns — and offered the one that was kept under
exactly the label of the one she was born in. Born in Morehead, Kentucky (the
Eastern-time one) and you got the Morehead 346 km away on Chicago time: an hour
of UT and a longitude, with nothing on screen to say so. Every place is kept
now. The place that already owned name|region|cc keeps that id, so every stored
profile and person still resolves to the same town; its namesakes get
name|region|cc|<geonameid>, which is stable across rebuilds by construction.

--previous pins that: the bare id goes to the namesake nearest the place the
previous file gave it to (GeoNames reorders its file between dumps), and a place
GeoNames has since dropped is carried forward rather than leaving a saved chart
pointing at nothing.

The disambiguator is filled only for places that share a label: the county
(GeoNames admin2), or "near <larger town>" where there is no county or two
namesakes share one, plus the time zone when the namesakes are in different
zones — the case where picking the wrong one moves the chart by an hour.

Data: GeoNames (https://www.geonames.org), CC BY 4.0.
"""
import argparse
import gzip
import math
from collections import defaultdict


def _names(path):
    out = {}
    if not path:
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            code, name, *_ = line.rstrip("\n").split("\t")
            out[code] = name
    return out


def _previous(path):
    """{id: (lat, lon)} from an earlier build, and its rows by id (8 or 10 columns)."""
    pins, rows = {}, {}
    if not path:
        return pins, rows
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            name, _alt, region, cc, lat, lon = c[:6]
            suffix = c[8] if len(c) > 8 else ""
            cid = f"{name}|{region}|{cc}" + (f"|{suffix}" if suffix else "")
            if cid not in pins:
                pins[cid] = (float(lat), float(lon))
                rows[cid] = c[:8]
    return pins, rows


def _dist(a, b):
    """Kilometres, near enough: equirectangular is fine at town scale."""
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return 111.2 * math.hypot(a[0] - b[0], (a[1] - b[1]) * k)


def _zone(tz):
    """'America/Indiana/Indianapolis' -> 'Indianapolis time'."""
    return tz.rsplit("/", 1)[-1].replace("_", " ") + " time"


class _Grid:
    """Places in 1° cells, to find the nearest bigger town without numpy."""

    def __init__(self, places):
        self.cells = defaultdict(list)
        for p in places:
            self.cells[(math.floor(p["lat"]), math.floor(p["lon"]))].append(p)

    def nearest_bigger(self, p, rings=2):
        best, best_d = None, None
        cy, cx = math.floor(p["lat"]), math.floor(p["lon"])
        for dy in range(-rings, rings + 1):
            for dx in range(-rings, rings + 1):
                for q in self.cells.get((cy + dy, cx + dx), ()):
                    if q["pop"] <= p["pop"] or q["name"] == p["name"]:
                        continue
                    d = _dist((p["lat"], p["lon"]), (q["lat"], q["lon"]))
                    if best_d is None or d < best_d:
                        best, best_d = q, d
        return best


def _details(group, grid):
    """The disambiguator for each place in a set that share one label."""
    areas = [p["area"] for p in group]
    out = []
    for p in group:
        if p["area"] and areas.count(p["area"]) == 1:
            out.append(p["area"])
            continue
        near = grid.nearest_bigger(p)
        out.append(f"near {near['name']}" if near else "")
    # The biggest namesake with nothing bigger nearby is simply the city —
    # Hanoi needs no "near" — and is told apart by its namesakes' details.
    biggest = max(range(len(group)), key=lambda i: group[i]["pop"])
    if not out[biggest] and out.count("") == 1:
        out[biggest] = None
    # Still identical (two hamlets by the same bigger town): coordinates are
    # dull but always distinct.
    for i, p in enumerate(group):
        if out[i] is None:
            out[i] = ""
        elif not out[i] or out.count(out[i]) > 1:
            out[i] = (f"{abs(p['lat']):.2f}°{'N' if p['lat'] >= 0 else 'S'} "
                      f"{abs(p['lon']):.2f}°{'E' if p['lon'] >= 0 else 'W'}")
    if len({p["tz"] for p in group}) > 1:
        out = [" · ".join(x for x in (d, _zone(p["tz"])) if x)
               for d, p in zip(out, group, strict=True)]
    return out


def build(cities_txt, admin1_txt, admin2_txt=None, previous=None):
    regions = _names(admin1_txt)
    counties = _names(admin2_txt)
    pins, prev_rows = _previous(previous)

    places, by_key = [], defaultdict(list)
    with open(cities_txt, encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            gid, name, ascii_, lat, lon, cc, admin1, admin2, pop, tz = (
                c[0], c[1], c[2], c[4], c[5], c[8], c[10], c[11], c[14], c[17])
            if not tz or not name:
                continue            # no timezone means no birth time can be converted
            region = regions.get(f"{cc}.{admin1}", "")
            p = {"gid": gid, "name": name, "alt": ascii_ if ascii_ != name else "",
                 "region": region, "cc": cc, "lat": round(float(lat), 4),
                 "lon": round(float(lon), 4), "tz": tz, "pop": int(pop or 0),
                 "area": counties.get(f"{cc}.{admin1}.{admin2}", "") if admin2 else "",
                 "suffix": "", "detail": ""}
            places.append(p)
            by_key[f"{name}|{region}|{cc}"].append(p)

    # Places the previous build had and GeoNames no longer does: carried, so a
    # saved chart never stops resolving.
    carried = 0
    for cid, row in prev_rows.items():
        name, alt, region, cc, lat, lon, tz, pop = row
        key = f"{name}|{region}|{cc}"
        if cid != key or key in by_key:
            continue
        p = {"gid": "", "name": name, "alt": alt, "region": region, "cc": cc,
             "lat": float(lat), "lon": float(lon), "tz": tz, "pop": int(pop or 0),
             "area": "", "suffix": "", "detail": ""}
        places.append(p)
        by_key[key].append(p)
        carried += 1

    grid = _Grid([p for p in places if p["pop"] >= 1000])
    namesakes = zone_split = 0
    for key, group in by_key.items():
        # Who owns the bare id: the place the previous build gave it to, else
        # the first in GeoNames' order (what the old build kept).
        owner = group[0]
        if key in pins:
            owner = min(group, key=lambda p: _dist((p["lat"], p["lon"]), pins[key]))
        for p in group:
            if p is not owner:
                p["suffix"] = p["gid"]
        if len(group) > 1:
            namesakes += len(group) - 1
            zone_split += len({p["tz"] for p in group}) > 1
            for p, d in zip(group, _details(group, grid), strict=True):
                p["detail"] = d

    places.sort(key=lambda p: -p["pop"])        # stable: ties keep GeoNames order
    stats = {"places": len(places), "namesakes": namesakes,
             "zone_split_labels": zone_split, "carried": carried}
    return places, stats


def write(places, out_path):
    with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as out:
        for p in places:
            out.write("\t".join((p["name"], p["alt"], p["region"], p["cc"],
                                 f"{p['lat']:.4f}", f"{p['lon']:.4f}", p["tz"],
                                 str(p["pop"]), p["suffix"], p["detail"])) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rebuild the bundled place list from GeoNames.")
    ap.add_argument("cities_txt")
    ap.add_argument("admin1_txt")
    ap.add_argument("out_path")
    ap.add_argument("--admin2", help="admin2Codes.txt, for the county disambiguator")
    ap.add_argument("--previous", help="the cities.tsv.gz being replaced, to keep its ids")
    a = ap.parse_args(argv)
    # build() reads --previous completely before write() opens the output,
    # which is usually the same path.
    places, stats = build(a.cities_txt, a.admin1_txt, a.admin2, a.previous)
    write(places, a.out_path)
    print(f"{stats['places']} places -> {a.out_path} ({stats['namesakes']} namesakes kept, "
          f"{stats['zone_split_labels']} labels split across zones, "
          f"{stats['carried']} carried from --previous)")


if __name__ == "__main__":
    main()
