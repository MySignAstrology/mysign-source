"""
The anonymous event collector: website visits, and app screens before sign-in.

Mounted by CloudFront at /e (POST only, never cached). Anything signed in goes
through the app's /api/activity instead, where the server knows who it is; this
function never learns a uid and ignores one if a client sends it.

What it keeps per event is in docs/analytics.md, and the rules are the app's
own — events.py here is a symlink to app/events.py, followed when CDK packages
the asset, so both paths enforce one set of privacy rules. From the request it
takes the country CloudFront derived from the IP (never the IP), CloudFront's
device-class headers, and an OS / browser family read from the User-Agent
(never the string). Sec-GPC: 1 or DNT: 1 drops the device and session ids, so
the visit is counted and can't be linked to any other.

Like app/edge.py, it refuses anything without CloudFront's x-origin-auth header
once a token is configured: either MYSIGN_ORIGIN_TOKEN, or the app's secret
(MYSIGN_SECRET_ARN, key origin_token), read once per container.

Always 204 for a well-formed POST, kept or not — a client learns nothing about
which of its events passed. boto3 only, from the runtime.
"""
import base64
import hmac
import json
import os
import sys
import time

import events

STREAM = os.environ.get("STREAM_NAME", "")
OWN = tuple(h.strip().lower() for h in os.environ.get("SITE_HOSTS", "").split(",") if h.strip())
SECRET_RETRY_S = 60

_client = None
_token = {"value": None, "at": 0.0, "ok": False}

# Abuse ceilings (audit L4). The collector is anonymous, so anyone could
# inflate pageviews, campaigns and funnels with forged events at whatever rate
# the WAF allowed. It has no store (by design: its only grant is Firehose), so
# these are per container, per minute: a device id gets a person's worth of
# events and the container as a whole a busy minute's worth; past either the
# events are dropped (still a 204 — a client learns nothing). With reserved
# concurrency 10 the service-wide ceiling is ten times CONTAINER_PER_MIN. The
# WAF's per-IP general-rate is the edge copy.
WINDOW_S = 60
ANON_PER_MIN = 120
CONTAINER_PER_MIN = 3000
_window = {"start": 0.0, "total": 0, "anon": {}}


def admit(envs, now=None):
    """The envelopes this minute's budget still has room for."""
    now = time.time() if now is None else now
    if now - _window["start"] >= WINDOW_S:
        _window.update(start=now, total=0, anon={})
    out, dropped = [], 0
    for e in envs:
        a = e.get("anon_id")
        used = _window["anon"].get(a, 0) if a else 0
        if _window["total"] >= CONTAINER_PER_MIN or (a and used >= ANON_PER_MIN):
            dropped += 1
            continue
        if a:
            if a not in _window["anon"] and len(_window["anon"]) >= CONTAINER_PER_MIN:
                dropped += 1          # a flood of fresh ids is the container budget's job
                continue
            _window["anon"][a] = used + 1
        _window["total"] += 1
        out.append(e)
    if dropped:
        _log("collect.limited", dropped=dropped)
    return out


def _log(event, **fields):
    sys.stdout.write(json.dumps({"event": event, "service": "mysign-collect", **fields}) + "\n")


def _firehose():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config
        _client = boto3.client("firehose", config=Config(
            connect_timeout=1, read_timeout=2, retries={"max_attempts": 2, "mode": "standard"}))
    return _client


def origin_token():
    """(configured, token). Configured-with-no-token refuses everything — a
    secret that can't be read yet, or no token configured at all: an open
    collector would let anyone write junk into the numbers."""
    tok = os.environ.get("MYSIGN_ORIGIN_TOKEN")
    if tok:
        return True, tok
    arn = os.environ.get("MYSIGN_SECRET_ARN")
    if not arn:
        # Unconfigured used to mean open: a deploy that forgot -c originToken
        # left a public Function URL accepting writes from anyone who found
        # it, past CloudFront and the WAF. Open is now something you ask for,
        # for local development only.
        if os.environ.get("MYSIGN_COLLECT_OPEN") == "1":
            return False, None
        return True, None
    now = time.time()
    if not _token["ok"] and now - _token["at"] > SECRET_RETRY_S:
        _token["at"] = now
        try:
            import boto3
            raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
            _token["value"] = (json.loads(raw) or {}).get("origin_token") or None
            _token["ok"] = True
        except Exception as e:
            _log("collect.secret_unreadable", error_type=type(e).__name__)
    return True, _token["value"]


def _resp(status):
    return {"statusCode": status, "headers": {"cache-control": "no-store"}, "body": ""}


def _body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw)
        except ValueError:
            return None, 400
    if isinstance(raw, str):
        raw = raw.encode("utf-8", "replace")
    if len(raw) > events.MAX_BODY:
        return None, 413
    try:
        data = json.loads(raw or b"{}")
    except ValueError:
        return None, 400
    return (data, 0) if isinstance(data, dict) else (None, 400)


def build(data, headers, now=None):
    """The envelopes for one batch. Pure, so the rules can be tested without AWS."""
    now = time.time() if now is None else now
    if events.is_bot(headers.get("user-agent")):
        return []
    source = data.get("source") if data.get("source") in events.ANON_SOURCES else "site"
    private = events.no_ids(headers)
    anon = None if private else data.get("anon_id")
    session = None if private else data.get("session")
    ctx = events.request_ctx(headers, data.get("ctx"), OWN)
    evs = data.get("events") if isinstance(data.get("events"), list) else []
    out = []
    for e in evs[:events.MAX_BATCH]:
        c = events.clean_event(e, now, events.CLIENT_TYPES)
        if c:
            # uid is never read from the body: an anonymous endpoint that took
            # one on trust would let anyone write into someone else's history.
            out.append(events.envelope(c, source=source, received_at=now, anon_id=anon,
                                       session_id=session, app_version=data.get("app_version"),
                                       ctx=ctx))
    return out


def handler(event, context=None):
    http = (event.get("requestContext") or {}).get("http") or {}
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    configured, token = origin_token()
    if configured and not (token and hmac.compare_digest(
            headers.get("x-origin-auth", "").encode(), token.encode())):
        return _resp(403)
    if http.get("method", "POST") != "POST":
        return _resp(405)

    data, err = _body(event)
    if err:
        return _resp(err)
    envs = admit(build(data, headers))
    if envs and STREAM:
        # A raise here is a Lambda error, which is what the alarm counts.
        failed = events.put(_firehose(), STREAM, envs)
        if failed:
            _log("collect.forward_failed", failed=failed, count=len(envs))
    return _resp(204)
