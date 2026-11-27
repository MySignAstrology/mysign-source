"""
The same chart in every tradition, side by side.

Built to be handed to an astrologer. The tradition question is the single
decision that most changes what this app says, and "whole sign moves things
around" is much less persuasive than seeing which things.

    python tools/compare_traditions.py 1999-03-14 04:42 America/Chicago 41.8781 -87.6298
"""
import argparse
import datetime as dt
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", "/tmp/mysign-compare")

from engine import interpret as I
from engine import tradition
from engine.chart import natal
from engine.interpret import HOUSE
from engine.reading import _ordinal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date"); ap.add_argument("time")
    ap.add_argument("tz"); ap.add_argument("lat", type=float)
    ap.add_argument("lon", type=float)
    a = ap.parse_args()

    y, m, d = (int(x) for x in a.date.split("-"))
    hh, mm = (int(x) for x in a.time.split(":"))
    utc = dt.datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(a.tz)).astimezone(dt.timezone.utc)
    args = (utc.year, utc.month, utc.day, utc.hour + utc.minute / 60, a.lat, a.lon)

    systems = list(tradition.HOUSE_SYSTEMS)
    charts = {s: natal(*args, houses=s) for s in systems}
    planets = [p for p in charts[systems[0]]["planets"]
               if p not in ("TrueNode", "Chiron")]

    print(f"\n  {a.date} {a.time} {a.tz}  ({a.lat}, {a.lon})")
    asc = charts["placidus"]["angles"]["Ascendant"]["label"]
    print(f"  Ascendant {asc} — identical in every system; it is what they are built from.\n")

    width = max(len(tradition.HOUSE_SYSTEMS[s]["label"]) for s in systems)
    print(f"    {'planet':10} {'at':22} " + " ".join(
        f"{tradition.HOUSE_SYSTEMS[s]['label']:>{width}}" for s in systems))
    for p in planets:
        row = [f"{charts[s]['planets'][p]['house']:>{width}}" for s in systems]
        moves = len({charts[s]["planets"][p]["house"] for s in systems}) > 1
        print(f"    {p:10} {charts[systems[0]]['planets'][p]['label']:22} "
              + " ".join(row) + ("   <-- differs" if moves else ""))

    print("\n  What each system would have the app say about the Moon:")
    for s in systems:
        h = charts[s]["planets"]["Moon"]["house"]
        print(f"    {tradition.HOUSE_SYSTEMS[s]['label']:>{width}}  {_ordinal(h):>4} — {HOUSE[h]}")

    print("\n  Chart ruler, by rulership scheme:")
    rwidth = max(len(x) for x in tradition.RULERSHIPS)
    for scheme in sorted(tradition.RULERSHIPS):
        r = I.analyze(charts["placidus"], rulerships=scheme)["chart_ruler"]
        print(f"    {scheme:>{rwidth}}  {r['planet']} in {r['sign']}, "
              f"{_ordinal(r['house'])} house")
    print()


if __name__ == "__main__":
    main()
