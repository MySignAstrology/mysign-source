"""
Structured logs.

Written after debugging the first deploy meant grepping raw CloudWatch streams
for tracebacks with no request id, nothing tying a failed HTTP call to the
exception behind it, and no way to ask "what happened to this person". That was
survivable for someone who wrote the code. It will not be during a pilot with
ten real people.

One JSON object per line, so CloudWatch Insights can actually query it.

**Logs record shape, never content.** This application holds people's messages
about their relationships, their families and their worst days. None of that
belongs in an operational log, and a convention would not hold — so the
redaction list is enforced in `emit`, and there is a test that fails if a
message body ever reaches a log line.
"""
import json
import os
import sys
import time
import traceback
import uuid
from contextvars import ContextVar

SERVICE = "mysign"
REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")

# Field names that must never appear in a log line. Anything matching is
# replaced with a length, which is all an operator ever needs.
REDACT = {
    "text", "message", "body", "reply", "content", "excerpt", "her_words",
    "summary", "note", "transcript", "prompt", "checkin",
    "email", "phone", "address", "name", "birth", "birth_local",
    "code", "secret", "token", "key", "password", "api_key",
}


def _scrub(k, v):
    if k.lower() in REDACT or any(r in k.lower() for r in ("secret", "token", "password")):
        if v is None:
            return None
        return f"<{len(str(v))} chars redacted>"
    return v


def emit(event, level="info", **fields):
    """One line. Never raises — logging must not be able to break a request."""
    try:
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "level": level, "event": event, "service": SERVICE,
            "request_id": REQUEST_ID.get(),
        }
        for k, v in fields.items():
            rec[k] = _scrub(k, v)
        sys.stdout.write(json.dumps(rec, default=str) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def error(event, exc=None, **fields):
    # The type only. str(exc) carried her words: a validation error on memory
    # extraction quotes the input it rejected, and that was her day.
    if exc is not None:
        fields["error_type"] = type(exc).__name__
    emit(event, level="error", **fields)


def exception(event, **fields):
    """Where it failed — frames and the exception type — never the message,
    which can contain whatever was being processed."""
    import sys
    etype, _, tb = sys.exc_info()
    frames = "".join(traceback.format_tb(tb))[-2000:] if tb else ""
    emit(event, level="error", error_type=etype.__name__ if etype else None,
         traceback=frames, **fields)


NAMESPACE = "MySign"


def metric(name, value=1, unit="Count", **dimensions):
    """
    A CloudWatch metric, emitted as a log line.

    Embedded Metric Format: CloudWatch parses these out of the log stream and
    turns them into real metrics with no SDK, no PutMetricData call in the
    request path, and no extra latency. The guidelines ask for Powertools
    metrics; this is the same mechanism Powertools uses underneath.

    Dimensions are low-cardinality on purpose — tier and band, never uid. A
    per-user dimension would create a metric per person and a bill to match.
    """
    try:
        dims = {k: str(v) for k, v in dimensions.items() if v is not None}
        rec = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [{
                    "Namespace": NAMESPACE,
                    "Dimensions": [list(dims)] if dims else [[]],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }],
            },
            name: value,
            # Same envelope as every other line. EMF ignores extra fields, and a
            # log query that has to special-case metric lines is a log query
            # people get wrong.
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "level": "info",
            "event": "metric",
            "service": SERVICE,
            "request_id": REQUEST_ID.get(),
            **dims,
        }
        sys.stdout.write(json.dumps(rec) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def install(app):
    """Request ids, timings, and a 500 that tells the person what to quote."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def _logged(request: Request, call_next):
        # Lambda gives every invocation a trace id; reuse it so a log line can be
        # matched to an X-Ray trace and to the platform's own REPORT record.
        rid = (request.headers.get("x-amzn-trace-id", "").split("Root=")[-1].split(";")[0]
               or uuid.uuid4().hex[:12])
        REQUEST_ID.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            exception("request.failed", method=request.method,
                      path=getattr(request.scope.get("route"), "path", None) or "unmatched",
                      ms=round((time.perf_counter() - started) * 1000))
            # The id is the point: it is what someone can quote back to us.
            return JSONResponse(status_code=500,
                                content={"detail": "Something went wrong on our end.",
                                         "request_id": rid})
        took = round((time.perf_counter() - started) * 1000)
        # The route template, not the path: /api/connections/{cid} is one
        # metric, where the raw path made one per person she saved — a bill,
        # and an id in a dimension.
        route = getattr(request.scope.get("route"), "path", None) or "unmatched"
        emit("request", method=request.method, path=route,
             status=response.status_code, ms=took)
        metric("RequestLatency", took, "Milliseconds",
               path=route, status=response.status_code)
        response.headers["x-request-id"] = rid
        return response

    emit("boot", demo=not os.environ.get("ANTHROPIC_API_KEY"),
         mail=bool(os.environ.get("MYSIGN_FROM_EMAIL")),
         store="dynamodb" if os.environ.get("MYSIGN_TABLE") else "files")
    return app
