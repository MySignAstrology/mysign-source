"""
Copy the activity already in DynamoDB into the analytics lake, once.

    AWS_PROFILE=mysign AWS_REGION=us-east-2 MYSIGN_TABLE=<table> python tools/backfill_events.py
    AWS_PROFILE=mysign AWS_REGION=us-east-2 MYSIGN_TABLE=<table> python tools/backfill_events.py \\
        --write --bucket <LakeBucket output of mysign-analytics>

A dry run (the default) reads and counts and writes nothing. `--write` puts:

  raw/events-json/dt=<day>/source=backfill/backfill-<run>.jsonl
      every `act-*` event of every user who hasn't switched analytics off,
      as the envelope (app/events.py), plus the funnel's day buckets as
      uid-less `funnel.<step>` server events. Table `events_json`; query it
      with `events` through the `events_all` view (docs/analytics.md).
  agg/activity_daily/dt=<day>/backfill-<run>.jsonl   {dt, field, count}
  agg/funnel_daily/dt=<day>/backfill-<run>.jsonl     {dt, step, count}
      the global counters as they are. No ids, so no expiry.

Raw objects carry an expiry tag (deploy/analytics_stack.py BACKFILL_TAG) so
copied history still leaves within 90 days of when it happened, not 90 days
after the copy. Days already past that aren't written at all.

Idempotent per run id only: running it twice writes the events twice. Delete
the previous run's objects (they share the run id) before a second attempt.

Then the rollups (deploy/rollups) for those days, so /ops shows them:

    AWS_PROFILE=mysign AWS_REGION=us-east-2 MYSIGN_TABLE=<table> python tools/backfill_events.py \
        --bucket <LakeBucket> --rollup-from 2026-08-01 --rollup-to 2026-10-27

(with --write as well, it copies first and rolls up after). The summaries go
to MYSIGN_TABLE, or --table; --workgroup/--database default to prod's.
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import activity, auth, events
from engine import funnel, store
from engine.store import load

RAW_DAYS = 90
TAG = "mysign-expire-days"
TAG_DAYS = (60, 30, 7, 1)          # deploy/analytics_stack.py BACKFILL_DAYS
JSON_PREFIX = "raw/events-json/"


def expiry_days(day, today):
    """The tag that keeps `day`'s events inside 90 days: None for a day young
    enough that the bucket's own 90-day rule does it, 0 for a day already out."""
    left = RAW_DAYS - (today - day).days
    if left >= RAW_DAYS:
        return None
    for d in TAG_DAYS:
        if d <= left:
            return d
    return 0


def _date(s):
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def user_events(uid, received_at):
    """Her stored events as envelopes. Nothing for someone who switched off —
    the switch deleted these already; this is the belt to that brace."""
    acct = load(uid, "account", {}) or {}
    if not activity.enabled(acct):
        return []
    out = []
    for k in store.keys(uid):
        if not k.startswith("act-") or "#" in k:
            continue
        for e in load(uid, k, []) or []:
            if not isinstance(e, dict) or e.get("ty") not in events.TYPES \
                    or not events.name_ok(e.get("n")) \
                    or not isinstance(e.get("at"), (int, float)):
                continue
            s = e.get("s")
            out.append(events.envelope(
                e, source="backfill", received_at=received_at, uid=uid,
                session_id=None if s in (None, "server", "unknown") else s))
    return out


def _days_from_keys(pk, prefix):
    days = set()
    for k in store.keys(pk):
        if k.startswith(prefix):
            d = _date(k[len(prefix):len(prefix) + 10])
            if d:
                days.add(d)
    return sorted(days)


def funnel_rows():
    return [{"dt": d.isoformat(), "step": s, "count": n}
            for d in _days_from_keys("_study", "funnel-")
            for s, n in sorted(funnel._counts(d).items()) if n]


def activity_rows():
    return [{"dt": d.isoformat(), "field": f, "count": n}
            for d in _days_from_keys("_activity", "_act#")
            for f, n in sorted(store.counters("_activity", f"_act#{d.isoformat()}").items()) if n]


def funnel_events(rows, received_at):
    """Each funnel count as that many uid-less server events at noon UTC — the
    shape the live pipeline writes, so one query covers before and after."""
    out = []
    for r in rows:
        if r["step"] not in funnel.STEPS:
            continue
        noon = dt.datetime.fromisoformat(r["dt"]).replace(
            hour=12, tzinfo=dt.timezone.utc).timestamp()
        ev = {"at": noon, "ty": "server", "n": f"funnel.{r['step']}"}
        out += [events.envelope(ev, source="backfill", received_at=received_at)
                for _ in range(r["count"])]
    return out


def plan(today=None, received_at=None):
    """Everything a run would write, as {key: (lines, expiry_days)}, plus counts."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    received_at = received_at or dt.datetime.now(dt.timezone.utc).timestamp()
    run = uuid.uuid4().hex[:8]
    users = auth.all_users()
    raw = []
    for uid in users:
        raw += user_events(uid, received_at)
    frows, arows = funnel_rows(), activity_rows()
    raw += funnel_events(frows, received_at)

    objects, skipped = {}, 0
    by_day = collections.defaultdict(list)
    for e in raw:
        by_day[dt.datetime.fromtimestamp(e["ts"] / 1000, dt.timezone.utc).date()].append(e)
    for day, evs in sorted(by_day.items()):
        days = expiry_days(day, today)
        if days == 0:
            skipped += len(evs)
            continue
        key = f"{JSON_PREFIX}dt={day.isoformat()}/source=backfill/backfill-{run}.jsonl"
        objects[key] = ([json.dumps(e, separators=(",", ":"), sort_keys=True) for e in evs], days)
    for name, rows in (("funnel_daily", frows), ("activity_daily", arows)):
        per = collections.defaultdict(list)
        for r in rows:
            per[r["dt"]].append(json.dumps(r, separators=(",", ":"), sort_keys=True))
        for d, lines in per.items():
            objects[f"agg/{name}/dt={d}/backfill-{run}.jsonl"] = (lines, None)
    counts = {"users": len(users), "events": len(raw) - skipped,
              "skipped_past_retention": skipped,
              "funnel_rows": len(frows), "activity_rows": len(arows), "objects": len(objects)}
    return objects, counts


def write(objects, bucket, s3):
    for key, (lines, days) in objects.items():
        extra = {"Tagging": f"{TAG}={days}"} if days else {}
        s3.put_object(Bucket=bucket, Key=key, Body=("\n".join(lines) + "\n").encode(),
                      ContentType="application/x-ndjson", **extra)


ROLLUPS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "deploy", "rollups", "handler.py")


def rollups_module():
    """deploy/rollups/handler.py, under its own name (deploy/collect has a handler.py too)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("mysign_rollups", ROLLUPS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_rollups(a):
    table = a.table or os.environ.get("MYSIGN_TABLE")
    argv = ["--from", a.rollup_from or a.rollup_to, "--to", a.rollup_to or a.rollup_from,
            "--workgroup", a.workgroup, "--database", a.database, "--bucket", a.bucket]
    return rollups_module().main(argv + (["--table", table] if table else []))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--bucket", help="the lake bucket (mysign-analytics LakeBucket output)")
    ap.add_argument("--rollup-from", help="then run the rollups from this day (YYYY-MM-DD)")
    ap.add_argument("--rollup-to", help="... through this day")
    ap.add_argument("--workgroup", default="mysign", help="Athena workgroup (Workgroup output)")
    ap.add_argument("--database", default="mysign_analytics", help="GlueDatabase output")
    ap.add_argument("--table", help="app table for the /ops summaries (default MYSIGN_TABLE)")
    a = ap.parse_args(argv)
    rollup = bool(a.rollup_from or a.rollup_to)
    if rollup and not a.bucket:
        sys.exit("--rollup-from/--rollup-to need --bucket")
    if rollup and not a.write:
        return run_rollups(a)
    objects, counts = plan()
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    if not a.write:
        for key, (lines, days) in sorted(objects.items()):
            print(f"  would write s3://{a.bucket or '<bucket>'}/{key}  "
                  f"({len(lines)} lines{f', expires {days}d' if days else ''})")
        print("  dry run: nothing written. Add --write --bucket <lake> to copy.")
        return counts
    if not a.bucket:
        sys.exit("--write needs --bucket")
    import boto3
    write(objects, a.bucket, boto3.client("s3"))
    print(f"  wrote {len(objects)} objects to s3://{a.bucket}/")
    if rollup:
        run_rollups(a)
    return counts


if __name__ == "__main__":
    main()
