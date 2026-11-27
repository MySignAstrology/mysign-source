"""
The rollup Lambda: Athena in, aggregates out.

    EventBridge {"mode": "hourly"}   every hour at :05 — today so far, and
                                     yesterday too until 03:00 UTC (late events)
    EventBridge {"mode": "daily"}    02:30 UTC — yesterday, final, plus the
                                     retention snapshot (cohorts that matured)
    {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "reports": [...]}
                                     a rerun or a backfill (also the CLI below)

For each (report, day) it runs that report's queries (rollup_sql.py) in the
stack's Athena workgroup — all of them at once, up to MAX_INFLIGHT — and then:

  s3://<lake>/agg/<report>/dt=<day>/<report>.jsonl   rows with no ids, kept
  DynamoDB pk="_report" sk="<report>#<day>"          the summary /ops reads
      (only when APP_TABLE is set; the contract is in app/reports.py)

A partial (hourly) result never overwrites a final (daily) one. A query that
fails or runs out of time fails its report, not the run; the others are still
written, and the invocation then raises so the error alarm sees it.

boto3 only, from the Lambda runtime. From a laptop, once a backfill is loaded:

    AWS_PROFILE=mysign AWS_REGION=us-east-2 python deploy/rollups/handler.py \\
        --from 2026-10-01 --to 2026-10-27 --workgroup mysign \\
        --database mysign_analytics --bucket <LakeBucket> [--table <TableName>]
"""
import argparse
import contextlib
import datetime as dt
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:          # loaded by path (tools/backfill_events.py, tests)
    sys.path.append(_HERE)

import rollup_shape as S  # noqa: E402
import rollup_sql as Q  # noqa: E402

PK = "_report"
VERSION = 1
MAX_INFLIGHT = 8
POLL_S = 1.0
MARGIN_MS = 20_000           # stop starting / polling this long before the Lambda's own timeout
LATE_HOURS = 3               # the hourly run also redoes yesterday until 03:00 UTC
DONE = ("SUCCEEDED", "FAILED", "CANCELLED")


def _log(event, **fields):
    sys.stdout.write(json.dumps({"event": event, "service": "mysign-rollups", **fields},
                                default=str) + "\n")


def sk(report, day):
    return f"{report}#{day}"


# ---------------------------------------------------------------- what to run

def plan(event, now=None):
    """[(report, day, final)] for an invocation."""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date()
    yesterday = today - dt.timedelta(days=1)
    event = event or {}
    wanted = event.get("reports") or list(Q.REPORTS)
    unknown = set(wanted) - set(Q.REPORTS)
    if unknown:
        raise ValueError(f"unknown reports {sorted(unknown)}")
    mode = event.get("mode")
    if event.get("from") or event.get("to"):
        lo = dt.date.fromisoformat(event.get("from") or event["to"])
        hi = dt.date.fromisoformat(event.get("to") or event["from"])
        if hi < lo or (hi - lo).days > 400:
            raise ValueError("from..to must be ascending and at most 400 days")
        days = [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)]
        return [(r, d.isoformat(), d < today) for d in days for r in wanted]
    if mode == "daily":
        return [(r, yesterday.isoformat(), True) for r in wanted]
    if mode == "hourly":
        days = [today] + ([yesterday] if now.hour < LATE_HOURS else [])
        return [(r, d.isoformat(), False) for d in days for r in wanted
                if r not in Q.SNAPSHOT]
    raise ValueError(f"unknown mode {mode!r}")


# ---------------------------------------------------------------- Athena

_CASTS = {"bigint": int, "integer": int, "int": int, "smallint": int, "tinyint": int,
          "double": float, "real": float, "float": float, "decimal": float,
          "boolean": lambda v: v == "true"}


def _rows(pages):
    """get_query_results pages → [dict], typed by the result metadata."""
    out, cols, types = [], None, None
    for page in pages:
        rs = page["ResultSet"]
        if cols is None:
            info = rs["ResultSetMetadata"]["ColumnInfo"]
            cols = [c["Name"] for c in info]
            types = [c["Type"].lower() for c in info]
            rows = rs["Rows"][1:]           # the first row of the first page is the header
        else:
            rows = rs["Rows"]
        for r in rows:
            vals = [d.get("VarCharValue") for d in r["Data"]]
            out.append({c: (None if v is None else _CASTS.get(t, str)(v))
                        for c, t, v in zip(cols, types, vals)})
    return out


class Athena:
    def __init__(self, client, workgroup, database, *, max_inflight=MAX_INFLIGHT, poll_s=POLL_S,
                 clock=time.monotonic, sleep=time.sleep):
        self.c, self.wg, self.db = client, workgroup, database
        self.max_inflight, self.poll_s = max_inflight, poll_s
        self.clock, self.sleep = clock, sleep
        self.scanned = 0

    def _start(self, sql):
        r = self.c.start_query_execution(
            QueryString=sql, WorkGroup=self.wg,
            QueryExecutionContext={"Catalog": "AwsDataCatalog", "Database": self.db})
        return r["QueryExecutionId"]

    def _results(self, qid):
        pages, kw = [], {"QueryExecutionId": qid, "MaxResults": 1000}
        while True:
            p = self.c.get_query_results(**kw)
            pages.append(p)
            if not p.get("NextToken"):
                return _rows(pages)
            kw["NextToken"] = p["NextToken"]

    def run(self, jobs, budget_s):
        """
        {key: sql} → ({key: rows}, {key: error}). Everything is started as
        soon as there is room; whatever hasn't finished when the budget runs
        out is stopped and reported as a timeout.
        """
        deadline = self.clock() + budget_s
        pending, inflight, done, errors = list(jobs.items()), {}, {}, {}
        while pending or inflight:
            while pending and len(inflight) < self.max_inflight and self.clock() < deadline:
                key, sql = pending.pop(0)
                try:
                    inflight[self._start(sql)] = key
                except Exception as e:
                    errors[key] = f"start: {type(e).__name__}: {e}"
            if inflight:
                ids = list(inflight)
                execs = []
                for i in range(0, len(ids), 50):
                    execs += self.c.batch_get_query_execution(
                        QueryExecutionIds=ids[i:i + 50]).get("QueryExecutions", [])
                for ex in execs:
                    qid, st = ex["QueryExecutionId"], ex["Status"]["State"]
                    if st not in DONE:
                        continue
                    key = inflight.pop(qid)
                    self.scanned += int((ex.get("Statistics") or {}).get("DataScannedInBytes") or 0)
                    if st == "SUCCEEDED":
                        try:
                            done[key] = self._results(qid)
                        except Exception as e:
                            errors[key] = f"results: {type(e).__name__}: {e}"
                    else:
                        errors[key] = f"{st}: {ex['Status'].get('StateChangeReason', '')}"[:500]
            if self.clock() >= deadline:
                for qid, key in inflight.items():
                    with contextlib.suppress(Exception):
                        self.c.stop_query_execution(QueryExecutionId=qid)
                    errors[key] = "timeout"
                for key, _ in pending:
                    errors[key] = "timeout (not started)"
                return done, errors
            if inflight:
                self.sleep(self.poll_s)
        return done, errors


# ---------------------------------------------------------------- writing

def item(report, day, data, final, now=None):
    """The DynamoDB item, in engine/store.py's shape: the JSON lives in `v`,
    the two flags are also top-level so a condition can read them."""
    now = int(now or time.time())
    body = {"data": data, "generated_at": now, "final": bool(final), "version": VERSION}
    return {"pk": {"S": PK}, "sk": {"S": sk(report, day)},
            "v": {"S": json.dumps(body, separators=(",", ":"), ensure_ascii=False)},
            "generated_at": {"N": str(now)}, "final": {"BOOL": bool(final)}}


def put_summary(ddb, table, report, day, data, final):
    """False if a final summary is already there and this one is partial."""
    kw = {"TableName": table, "Item": item(report, day, data, final)}
    if not final:
        kw["ConditionExpression"] = "attribute_not_exists(#f) OR #f = :no"
        kw["ExpressionAttributeNames"] = {"#f": "final"}
        kw["ExpressionAttributeValues"] = {":no": {"BOOL": False}}
    try:
        ddb.put_item(**kw)
        return True
    except Exception as e:
        if type(e).__name__ == "ConditionalCheckFailedException" or \
                getattr(e, "response", {}).get("Error", {}).get("Code") == \
                "ConditionalCheckFailedException":
            return False
        raise


def put_agg(s3, bucket, report, day, rows, final):
    body = "".join(json.dumps(r, separators=(",", ":"), default=str) + "\n" for r in rows)
    s3.put_object(Bucket=bucket, Key=f"agg/{report}/dt={day}/{report}.jsonl",
                  Body=body.encode(), ContentType="application/x-ndjson",
                  Metadata={"final": "true" if final else "false"})


# ---------------------------------------------------------------- the run

def run(work, *, athena, s3, bucket, ddb=None, table=None, budget_s=240, k=S.MIN_COUNT):
    """Run `work` ([(report, day, final)]). Returns a summary; errors are listed, not raised."""
    jobs = {}
    for report, day, _ in work:
        for qname, sql in Q.queries(report, athena.db, day).items():
            jobs[(report, day, qname)] = sql
    t0 = time.monotonic()
    done, errors = athena.run(jobs, budget_s)
    written, skipped, failed = [], [], {}
    for report, day, final in work:
        keys = [(report, day, q) for q in Q.QUERIES[report]]
        bad = {key[2]: errors[key] for key in keys if key in errors}
        if bad:
            failed[f"{report}#{day}"] = bad
            continue
        try:
            data, rows = S.shape(report, day, {key[2]: done[key] for key in keys}, k)
            if ddb is not None and table and not put_summary(ddb, table, report, day, data, final):
                skipped.append(f"{report}#{day}")
                continue                 # a final one is there; leave its agg/ rows alone too
            put_agg(s3, bucket, report, day, rows, final)
            written.append(f"{report}#{day}")
        except Exception as e:
            failed[f"{report}#{day}"] = {"write": f"{type(e).__name__}: {e}"}
    out = {"queries": len(jobs), "written": written, "skipped_final_exists": skipped,
           "failed": failed, "scanned_bytes": athena.scanned,
           "seconds": round(time.monotonic() - t0, 1)}
    _log("rollups.run", **{k_: v for k_, v in out.items() if k_ != "written"},
         written=len(written))
    return out


def _clients():
    import boto3
    return boto3.client("athena"), boto3.client("s3"), boto3.client("dynamodb")


def handler(event, context=None):
    work = plan(event)
    athena_c, s3, ddb = _clients()
    table = os.environ.get("APP_TABLE") or None
    budget = 240.0
    if context is not None and hasattr(context, "get_remaining_time_in_millis"):
        budget = max(5.0, (context.get_remaining_time_in_millis() - MARGIN_MS) / 1000.0)
    out = run(work, athena=Athena(athena_c, os.environ["WORKGROUP"], os.environ["DATABASE"]),
              s3=s3, bucket=os.environ["LAKE_BUCKET"], ddb=ddb if table else None, table=table,
              budget_s=budget, k=int(os.environ.get("MIN_COUNT") or S.MIN_COUNT))
    if out["failed"]:
        # After the writes, so one bad query costs one report; raised so the
        # Errors metric (and RollupErrorAlarm) sees it.
        raise RuntimeError(f"rollups failed: {json.dumps(out['failed'])[:1500]}")
    return out


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the analytics rollups for past days.")
    ap.add_argument("--from", dest="lo", required=True, help="first day, YYYY-MM-DD (UTC)")
    ap.add_argument("--to", dest="hi", required=True, help="last day, YYYY-MM-DD (UTC)")
    ap.add_argument("--reports", help=f"comma-separated subset of {','.join(Q.REPORTS)}")
    ap.add_argument("--workgroup", default="mysign")
    ap.add_argument("--database", default="mysign_analytics")
    ap.add_argument("--bucket", help="the lake bucket (LakeBucket output)")
    ap.add_argument("--table", help="the app table (TableName output); omit to write S3 only")
    ap.add_argument("--budget", type=float, default=900, help="seconds to wait for Athena")
    ap.add_argument("--dry-run", action="store_true", help="print the SQL and stop")
    a = ap.parse_args(argv)
    event = {"from": a.lo, "to": a.hi}
    if a.reports:
        event["reports"] = [r.strip() for r in a.reports.split(",") if r.strip()]
    work = plan(event)
    if a.dry_run:
        for report, day, final in work:
            for qname, sql in Q.queries(report, a.database, day).items():
                print(f"-- {report}/{qname} {day} final={final}\n{sql};\n")
        return {"planned": len(work)}
    if not a.bucket:
        sys.exit("--bucket is required (or use --dry-run)")
    athena_c, s3, ddb = _clients()
    out = run(work, athena=Athena(athena_c, a.workgroup, a.database), s3=s3, bucket=a.bucket,
              ddb=ddb if a.table else None, table=a.table, budget_s=a.budget)
    print(json.dumps(out, indent=1))
    if out["failed"]:
        sys.exit(1)
    return out


if __name__ == "__main__":
    main()
