"""
System health for the operator console: Lambda, the app's own metrics, email
and cold starts, read from CloudWatch for a date range.

    report(a, b) -> {from, to, period, generated_at, cached, functions, app,
                     email, cold_starts, edge}
                  | {unavailable: True, reason}

Names come from the environment the stack sets (deploy/mysign_stack.py):

    AWS_LAMBDA_FUNCTION_NAME / MYSIGN_FN_NAME   the app function (its own name)
    MYSIGN_CRON_FN_NAME                         the scheduler function
    MYSIGN_METRIC_NS                            the app's metric namespace
    MYSIGN_APP_LOG_GROUP                        the app's log group (cold starts)
    MYSIGN_SITE_DISTRIBUTION                    the CloudFront distribution (edge traffic)

One GetMetricData call (paged) per range, and one Logs Insights query over the
app's REPORT lines for cold starts. Answers are cached in-process for five
minutes per range; a Logs query still running after a few seconds is kept and
asked again by the next request rather than waited on. Locally — no function
name, no credentials, no network — it says unavailable, quickly, and never
raises: short timeouts and a single attempt per call.
"""
import datetime as dt
import os
import threading
import time

from app import log

TTL_S = 300
FAIL_TTL_S = 60
MAX_DAYS = 90
POLL_S = 6.0
_cache = {}
_lock = threading.Lock()

LAMBDA = [("invocations", "Invocations", "Sum"), ("errors", "Errors", "Sum"),
          ("throttles", "Throttles", "Sum"), ("duration_p95_ms", "Duration", "p95"),
          ("concurrency_max", "ConcurrentExecutions", "Maximum")]
APP = [("messages", "Messages"), ("quota_refused", "QuotaRefused"), ("red_tier", "RedTier"),
       ("sms_cap", "SmsCap"), ("app_errors", "AppErrorLines"),
       ("alert_unrouted", "AlertUnrouted"), ("auth_unavailable", "AuthUnavailable")]
SES = [("send", "Send", "Sum"), ("delivery", "Delivery", "Sum"), ("bounce", "Bounce", "Sum"),
       ("complaint", "Complaint", "Sum"),
       ("bounce_rate", "Reputation.BounceRate", "Maximum"),
       ("complaint_rate", "Reputation.ComplaintRate", "Maximum")]
# CloudFront publishes to us-east-1 only, whatever region the app is in.
EDGE_REGION = "us-east-1"
EDGE = [("requests", "Requests", "Sum"), ("bytes", "BytesDownloaded", "Sum"),
        ("rate_4xx", "4xxErrorRate", "Average"), ("rate_5xx", "5xxErrorRate", "Average")]
COLD_QUERY = ('filter @type = "REPORT" and ispresent(@initDuration) '
              '| stats count(*) as cold, avg(@initDuration) as init_ms, '
              'max(@initDuration) as init_max by bin({bin})')


def config():
    return {
        "app_fn": os.environ.get("MYSIGN_FN_NAME") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"),
        "cron_fn": os.environ.get("MYSIGN_CRON_FN_NAME"),
        "ns": os.environ.get("MYSIGN_METRIC_NS") or "MySign",
        "log_group": os.environ.get("MYSIGN_APP_LOG_GROUP"),
        "distribution": os.environ.get("MYSIGN_SITE_DISTRIBUTION"),
        "region": os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"),
    }


def _client(service, region):
    """A client that gives up fast: two seconds to connect, one try."""
    import boto3
    from botocore.config import Config
    return boto3.client(service, region_name=region, config=Config(
        connect_timeout=2, read_timeout=5, retries={"max_attempts": 1, "mode": "standard"}))


def reset():
    with _lock:
        _cache.clear()


def _unavailable(reason):
    return {"unavailable": True, "reason": reason}


# ------------------------------------------------------------------ metrics

def _queries(cfg, period, whole):
    """(query list, layout) for one GetMetricData call."""
    qs, layout = [], {}

    def add(qid, ns, name, stat, dims, p):
        qs.append({"Id": qid, "ReturnData": True, "MetricStat": {
            "Metric": {"Namespace": ns, "MetricName": name,
                       "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()]},
            "Period": p, "Stat": stat}})

    for role in ("app", "cron"):
        fn = cfg[f"{role}_fn"]
        if not fn:
            continue
        for key, name, stat in LAMBDA:
            add(f"{role}_{key}", "AWS/Lambda", name, stat, {"FunctionName": fn}, period)
            add(f"{role}_{key}_all", "AWS/Lambda", name, stat, {"FunctionName": fn}, whole)
        layout[role] = fn
    for key, name in APP:
        add(f"app_m_{key}", cfg["ns"], name, "Sum", {}, period)
    for key, name, stat in SES:
        add(f"ses_{key}", "AWS/SES", name, stat, {}, period)
        if stat != "Sum":
            add(f"ses_{key}_all", "AWS/SES", name, stat, {}, whole)
    return qs, layout


def _fetch(cw, qs, start, end):
    """{id: {epoch: value}} over every page of the answer."""
    out, token = {}, None
    for _ in range(20):
        kw = {"MetricDataQueries": qs, "StartTime": start, "EndTime": end,
              "ScanBy": "TimestampAscending"}
        if token:
            kw["NextToken"] = token
        r = cw.get_metric_data(**kw)
        for m in r.get("MetricDataResults", []):
            vals = out.setdefault(m["Id"], {})
            for t, v in zip(m.get("Timestamps", []), m.get("Values", [])):
                vals[int(t.timestamp())] = v
        token = r.get("NextToken")
        if not token:
            break
    return out


def _r(v, n=1):
    return None if v is None else round(v, n)


def _series(data, ids, buckets, sums):
    rows = []
    for t in buckets:
        row = {"t": dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat().replace("+00:00", "Z")}
        for key, qid in ids.items():
            v = data.get(qid, {}).get(t)
            row[key] = (v or 0) if key in sums else _r(v)
        rows.append(row)
    return rows


def _one(data, qid):
    vals = list(data.get(qid, {}).values())
    return max(vals) if vals else None


def _metrics(cw, cfg, start, end, period):
    whole = max(60, int((end - start).total_seconds()) // 60 * 60)
    qs, layout = _queries(cfg, period, whole)
    data = _fetch(cw, qs, start, end)
    buckets = list(range(int(start.timestamp()), int(end.timestamp()), period))
    sums = {"invocations", "errors", "throttles"}
    functions = {}
    for role, fn in layout.items():
        ids = {k: f"{role}_{k}" for k, _, _ in LAMBDA}
        totals = {k: sum(data.get(f"{role}_{k}", {}).values()) for k in sums}
        totals["duration_p95_ms"] = _r(_one(data, f"{role}_duration_p95_ms_all"))
        totals["concurrency_max"] = _one(data, f"{role}_concurrency_max_all")
        totals["error_rate"] = (round(totals["errors"] / totals["invocations"], 4)
                                if totals["invocations"] else None)
        functions[role] = {"name": fn, "totals": totals,
                           "series": _series(data, ids, buckets, sums)}
    app_ids = {k: f"app_m_{k}" for k, _ in APP}
    app = {"namespace": cfg["ns"],
           "totals": {k: sum(data.get(q, {}).values()) for k, q in app_ids.items()},
           "series": _series(data, app_ids, buckets, set(app_ids))}
    ses_sums = {k for k, _, s in SES if s == "Sum"}
    ses_ids = {k: f"ses_{k}" for k in ses_sums}
    email = {"totals": {k: sum(data.get(f"ses_{k}", {}).values()) for k in ses_sums},
             "series": _series(data, ses_ids, buckets, ses_sums)}
    for k, _, s in SES:
        if s != "Sum":
            email["totals"][k] = _one(data, f"ses_{k}_all")
    return functions, app, email


# ------------------------------------------------------------------ edge

def _edge(cfg, start, end, period):
    """
    Every request CloudFront answered: assets, bots and uptime probes too, so
    it's load, not people. On its own, so a failure here costs only this card.
    """
    if not cfg["distribution"]:
        return {"status": "unavailable", "reason": "no distribution configured"}
    whole = max(60, int((end - start).total_seconds()) // 60 * 60)
    qs = [{"Id": f"edge_{key}{suffix}", "ReturnData": True, "MetricStat": {
              "Metric": {"Namespace": "AWS/CloudFront", "MetricName": name, "Dimensions": [
                  {"Name": "DistributionId", "Value": cfg["distribution"]},
                  {"Name": "Region", "Value": "Global"}]},
              "Period": p, "Stat": stat}}
          for key, name, stat in EDGE for suffix, p in (("", period), ("_all", whole))]
    data = _fetch(_client("cloudwatch", EDGE_REGION), qs, start, end)
    buckets = list(range(int(start.timestamp()), int(end.timestamp()), period))
    sums = {k for k, _, s in EDGE if s == "Sum"}
    totals = {k: sum(data.get(f"edge_{k}", {}).values()) for k in sums}
    for k, _, s in EDGE:
        if s != "Sum":                    # CloudFront's rates are percentages; ours are shares
            v = _one(data, f"edge_{k}_all")
            totals[k] = None if v is None else round(v / 100, 4)
    return {"status": "complete", "totals": totals,
            "series": _series(data, {"requests": "edge_requests"}, buckets, {"requests"})}


# ------------------------------------------------------------------ cold starts

def _cold_rows(results):
    by, total, init_sum, init_max = [], 0, 0.0, None
    for row in results or []:
        f = {c["field"]: c.get("value") for c in row}
        t = next((v for k, v in f.items() if k.startswith("bin(")), None)
        cold = int(float(f.get("cold") or 0))
        avg = float(f["init_ms"]) if f.get("init_ms") not in (None, "") else None
        mx = float(f["init_max"]) if f.get("init_max") not in (None, "") else None
        by.append({"t": (t or "")[:19].replace(" ", "T") + "Z" if t else None,
                   "cold": cold, "init_ms": _r(avg)})
        total += cold
        if avg is not None:
            init_sum += avg * cold
        if mx is not None:
            init_max = mx if init_max is None else max(init_max, mx)
    by.sort(key=lambda r: r["t"] or "")
    return {"status": "complete", "total": total,
            "init_ms_avg": _r(init_sum / total) if total else None,
            "init_ms_max": _r(init_max), "series": by}


def _poll(logs, qid, wait_s):
    """The finished cold-start summary, or None while the query still runs."""
    deadline = time.monotonic() + wait_s
    while True:
        r = logs.get_query_results(queryId=qid)
        st = r.get("status")
        if st == "Complete":
            return _cold_rows(r.get("results"))
        if st in ("Failed", "Cancelled", "Timeout", "Unknown"):
            return {"status": "unavailable", "reason": f"query {st.lower()}"}
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.5)


def _cold_start(logs, cfg, start, end, period):
    if not cfg["log_group"]:
        return {"status": "unavailable", "reason": "no log group configured"}, None
    q = logs.start_query(logGroupName=cfg["log_group"], startTime=int(start.timestamp()),
                         endTime=int(end.timestamp()), limit=1000,
                         queryString=COLD_QUERY.format(bin="1h" if period < 86400 else "1d"))
    qid = q["queryId"]
    done = _poll(logs, qid, POLL_S)
    return (done, None) if done else ({"status": "pending"}, qid)


# ------------------------------------------------------------------ the report

def report(a, b):
    """Health for days a..b (dates, UTC), at most MAX_DAYS. Never raises."""
    if (b - a).days + 1 > MAX_DAYS:
        a = b - dt.timedelta(days=MAX_DAYS - 1)
    key = (a.isoformat(), b.isoformat())
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit["at"] < hit.get("ttl", TTL_S):
        out = hit["data"]
        if hit.get("qid"):
            try:
                cold = _poll(hit["logs"], hit["qid"], 0)
            except Exception as e:
                cold = {"status": "unavailable", "reason": type(e).__name__}
            if cold:
                out = {**out, "cold_starts": cold}
                with _lock:
                    hit.update(data=out, qid=None, logs=None)
        return {**out, "cached": True}
    out, extra = _build(a, b)
    failed = out.pop("cacheable", False)
    if not out.get("unavailable") or failed:
        with _lock:
            if len(_cache) > 32:
                _cache.pop(min(_cache, key=lambda k: _cache[k]["at"]))
            _cache[key] = {"at": now, "data": out, "ttl": FAIL_TTL_S if failed else TTL_S,
                           **extra}
    return {**out, "cached": False}


def _build(a, b):
    cfg = config()
    if not cfg["app_fn"]:
        return _unavailable("Not running on AWS Lambda: no function name configured."), {}
    start = dt.datetime.combine(a, dt.time(), dt.timezone.utc)
    end = dt.datetime.combine(b + dt.timedelta(days=1), dt.time(), dt.timezone.utc)
    period = 3600 if (b - a).days < 2 else 86400
    t0 = time.monotonic()
    try:
        cw = _client("cloudwatch", cfg["region"])
        functions, app, email = _metrics(cw, cfg, start, end, period)
    except Exception as e:
        log.emit("ops.health_unavailable", level="warn", error=type(e).__name__)
        out = _unavailable(f"CloudWatch didn't answer ({type(e).__name__}).")
        out["cacheable"] = True        # don't retry a dead endpoint on every click
        return out, {}
    extra = {}
    try:
        logs = _client("logs", cfg["region"])
        cold, qid = _cold_start(logs, cfg, start, end, period)
        if qid:
            extra = {"qid": qid, "logs": logs}
    except Exception as e:
        cold = {"status": "unavailable", "reason": type(e).__name__}
    try:
        edge = _edge(cfg, start, end, period)
    except Exception as e:
        edge = {"status": "unavailable", "reason": type(e).__name__}
    log.emit("ops.health", days=(b - a).days + 1, ms=int((time.monotonic() - t0) * 1000),
             cold=cold.get("status"))
    return {"from": a.isoformat(), "to": b.isoformat(), "period": period,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "functions": functions, "app": app, "email": email, "cold_starts": cold,
            "edge": edge}, extra
