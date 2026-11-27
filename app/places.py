"""
The wider birthplace search, for places too small for the bundled list.

app/cities.py answers from ~230k places offline and covers almost everyone.
What it can't hold are the villages, hamlets and rural addresses under 500
people — and "I couldn't put my town in" ends a signup. This asks Amazon
Location Service (HERE data), which returns the IANA timezone with each
result: the one field a birth chart cannot do without.

Deliberate limits:

- Only on request. The app offers it when the offline list comes up short and
  she taps "Search everywhere"; it never fires per keystroke.
- It runs in our own AWS account, so the place name goes to AWS under our
  agreement rather than to a third-party geocoder.
- The index is created for Storage use, because the coordinates are kept as
  part of her birth data.
- A per-person daily cap, so a stuck client or a curious script can't run up
  the bill.

Off (returns nothing, says so) when MYSIGN_PLACE_INDEX is unset — local dev and
tests never call AWS.
"""
import datetime as dt
import os

from app import cities

DAILY_CAP = 30


def available():
    return bool(os.environ.get("MYSIGN_PLACE_INDEX"))


def _client():
    import boto3
    return boto3.client("location")


def under_cap(uid, load=None, save=None):
    """Count this search against her day; False once she's past the cap.
    One atomic counter with an expiry: a load, +1, save let parallel searches
    past the cap (each a billed call), and the item never went away."""
    import time

    from engine import store
    key = f"places-{dt.date.today().isoformat()}"
    return store.incr(uid, key, "n", limit=DAILY_CAP, ttl=int(time.time()) + 2 * 86400) is not None


def search(q, limit=6, client=None):
    """Results shaped like cities.search(), with ids cities.resolve() accepts."""
    index = os.environ.get("MYSIGN_PLACE_INDEX")
    q = (q or "").strip()
    if not index or len(q) < 2:
        return []
    client = client or _client()
    r = client.search_place_index_for_text(IndexName=index, Text=q[:200], MaxResults=limit,
                                           Language="en")
    out, seen = [], set()
    for hit in r.get("Results", []):
        p = hit.get("Place", {})
        tz = (p.get("TimeZone") or {}).get("Name")
        point = (p.get("Geometry") or {}).get("Point")
        label = p.get("Label")
        if not (tz and point and label):
            continue            # no timezone, no chart — not worth offering
        lon, lat = point
        cid = cities.geo_id(lat, lon, tz, label)
        if cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "label": label, "name": label.split(",")[0].strip(),
                    "region": p.get("Region", ""), "country": p.get("Country", ""),
                    "tz": tz, "wider": True})
    return out
