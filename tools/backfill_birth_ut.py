"""
Recompute stored birth instants after a change to the time zone rules.

    python tools/backfill_birth_ut.py                    # dry run: report, write nothing
    python tools/backfill_birth_ut.py --uid u123         # one account
    python tools/backfill_birth_ut.py --apply            # write the corrections

`birth_ut` is converted from her local birth time once, at onboarding, and
stored; every chart is read from it afterwards. So when the rules change — here,
shipping tzdb's backzone history (engine/localtime.py) — accounts already
created keep the old instant until it is recomputed. Who moves: births before
1970 in the zones the default tzdb folded together (Iceland, Norway, Sweden,
Denmark, the Netherlands, Luxembourg, Curaçao/Aruba, the Bahamas, much of West
and East Africa, the Gulf...), by 15 minutes to two hours. Everyone else
recomputes to exactly the value already stored and is reported as unchanged.

The recomputation is onboarding's own: server._birth_instant from the stored
local date, time (noon when unknown), time zone and coordinates. Which
occurrence of a repeated hour she chose (fold) is not stored; it is read back
from the birth-time note ("Charted as the second occurrence"), else the first,
which is what onboarding assumed. Saved people (connections) are recomputed the
same way from their stored place, and skipped — reported, never moved — if the
place no longer resolves to the coordinates the row was charted at.

--apply writes the new birth_ut, then drops everything derived from it the way
a birth-time edit does (reading, placement deep-dives, cached skies), refits the
salience thresholds and rescores every saved person. Idempotent: a second run
finds nothing to change. Works on either store backend; accounts are taken from
auth.all_users() — the users set plus the legacy _auth/users roster, which is
empty on DynamoDB, so reading only the roster there checked nobody.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import auth, cities
from app import server as S
from engine import salience
from engine.chart import natal
from engine.store import load, save


def _fold(note):
    return 1 if note and "second occurrence" in note else 0


def _ut(utc, lat, lon):
    return [utc.year, utc.month, utc.day, round(utc.hour + utc.minute / 60, 4), lat, lon]


def _local(date, time_):
    y, m, d = (int(x) for x in date.split("-"))
    known = bool(time_)
    hh, mm = (int(x) for x in time_.split(":")) if known else (12, 0)
    return y, m, d, hh, mm, known


def _minutes(a, b):
    import datetime as dt
    def t(u):
        return dt.datetime(u[0], u[1], u[2]) + dt.timedelta(hours=u[3])
    return round((t(b) - t(a)).total_seconds() / 60, 1)


def _rising(ut):
    return natal(*ut)["angles"]["Ascendant"]["sign"]


def plan_profile(prof):
    """(new_birth_ut, new_note) for her own chart, or None if nothing moves."""
    date = prof.get("birth_date") or (prof.get("birth_local") or "").split(" ")[0]
    if not date or not prof.get("birth_ut") or not prof.get("tz"):
        return None
    old = prof["birth_ut"]
    y, m, d, hh, mm, _ = _local(date, prof.get("birth_time"))
    known = prof.get("birth_time_known", True)
    utc, note = S._birth_instant(y, m, d, hh, mm, prof["tz"], old[4], old[5], known,
                                 _fold(prof.get("birth_time_note")))
    new = _ut(utc, old[4], old[5])
    return None if new == old else (new, note)


def plan_connection(row):
    """(new_birth_ut, None) | ("skip", reason) | None."""
    if not row.get("birth_date") or not row.get("birth_ut"):
        return None
    old = row["birth_ut"]
    c = cities.resolve(row.get("birth_city") or "")
    if not c:
        return "skip", "place no longer resolves"
    if (round(c["lat"], 3), round(c["lon"], 3)) != (round(old[4], 3), round(old[5], 3)):
        return "skip", f"place resolves to {c['label']} at other coordinates"
    y, m, d, hh, mm, known = _local(row["birth_date"], row.get("birth_time"))
    utc, _ = S._birth_instant(y, m, d, hh, mm, c["tz"], old[4], old[5], known)
    new = _ut(utc, old[4], old[5])
    return None if new == old else (new, None)


def run(uid, apply=False, out=print):
    """Report (and with apply, fix) one account. Returns the number of changes."""
    prof = load(uid, "profile", None)
    if not prof:
        return 0
    changes = 0
    p = plan_profile(prof)
    if p:
        new, note = p
        old = prof["birth_ut"]
        line = f"  {uid}  self  {old[:4]} -> {new[:4]}  ({_minutes(old, new):+} min)"
        if prof.get("birth_time_known", True):
            line += f"  rising {_rising(old)} -> {_rising(new)}"
        out(line)
        changes += 1
        if apply:
            prof["birth_ut"] = new
            prof["birth_time_note"] = note if prof.get("birth_time_known", True) else None
            approx = prof.get("birth_time_approx") or {}
            if approx.get("time"):
                hh, mm = (int(x) for x in approx["time"].split(":"))
                approx["rising"] = S._stable_rising(prof, hh, mm)
            save(uid, "profile", prof)

    rows = load(uid, "connections", []) or []
    moved = False
    for r in rows:
        c = plan_connection(r)
        if not c:
            continue
        if c[0] == "skip":
            out(f"  {uid}  person {r.get('id')}  SKIPPED: {c[1]}")
            continue
        old, new = r["birth_ut"], c[0]
        out(f"  {uid}  person {r.get('id')}  {old[:4]} -> {new[:4]}  ({_minutes(old, new):+} min)")
        changes += 1
        if apply:
            r["birth_ut"] = new
            moved = True
    if apply and moved:
        save(uid, "connections", rows)

    if apply and changes:
        houses, rulers = S._tradition(uid)
        save(uid, f"reading-{houses}-{rulers}", None)
        for pt in S.placement.POINTS:
            save(uid, S._placement_key(uid, pt), None)
        for iso in S._known_dates(uid) + [S._today(uid).isoformat()]:
            save(uid, S._sky_key(iso, houses), None)
        bands = salience.calibrate(S._natal(uid), tz=prof.get("tz"))
        if bands:
            save(uid, "salience_bands", bands)
        S._rescore_connections(uid)      # overlays and compat reports, both sides moved
        save(uid, "widget", None)
    return changes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write corrections (default: dry run)")
    ap.add_argument("--uid", action="append", help="only this account (repeatable)")
    a = ap.parse_args(argv)
    uids = a.uid or auth.all_users()
    print(f"  tz rules: {S.localtime.TZ_RULES}   accounts: {len(uids)}   "
          f"{'APPLYING' if a.apply else 'dry run'}")
    total = 0
    for uid in uids:
        total += run(uid, apply=a.apply)
    print(f"  {total} birth instant(s) {'corrected' if a.apply else 'would change'}")
    return total


if __name__ == "__main__":
    main()
