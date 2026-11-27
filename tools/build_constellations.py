"""
Build engine/reference/constellations.json — the IAU constellation boundaries
as a lookup grid on B1875.0 right ascension and declination.

"The stars behind your Moon" was read off where the boundaries cross the
ecliptic. The Sun lives on the ecliptic so that was right for it, but the Moon
wanders up to about 5.3° either side, and on roughly one birth in eight it sat
in front of a different constellation — Cetus rather than Aries, Scorpius
rather than Ophiuchus. The boundaries are drawn along B1875 lines of RA and
Dec (Delporte 1930), so the honest lookup precesses the real position back to
1875 and reads the grid (Roman 1987, PASP 99, 695; CDS catalogue VI/42).

The grid itself is the one Skyfield bundles (MIT licence), compiled from
Roman's table. This script re-encodes it without numpy: run it from any
environment that has Skyfield installed, e.g.

    python tools/build_constellations.py engine/reference/constellations.json

It is a build tool only; nothing at runtime imports Skyfield.
"""
import json
import os
import sys


def main(out):
    import numpy as np
    import skyfield
    from skyfield.api import load_constellation_names

    d = np.load(os.path.join(os.path.dirname(skyfield.__file__), "data", "constellations.npz"))
    names = dict(load_constellation_names())
    abbr = [str(a) for a in d["indexed_abbreviations"]]
    grid = d["radec_to_index"]
    # Run-length rows: the grid is mostly long constant stretches.
    rows = []
    for row in grid:
        runs, prev, n = [], int(row[0]), 0
        for v in row:
            v = int(v)
            if v == prev:
                n += 1
            else:
                runs.append([prev, n])
                prev, n = v, 1
        runs.append([prev, n])
        rows.append(runs)
    doc = {"source": "IAU boundaries, Roman (1987) CDS VI/42, via Skyfield's grid",
           "epoch": "B1875.0",
           "ra_hours": [round(float(x), 6) for x in d["sorted_ra"]],
           "dec_deg": [round(float(x), 6) for x in d["sorted_dec"]],
           "names": [names.get(a, a) for a in abbr], "abbr": abbr, "rows": rows}
    with open(out, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))


if __name__ == "__main__":
    main(sys.argv[1])
