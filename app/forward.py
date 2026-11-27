"""
Events to the analytics lake, alongside DynamoDB.

A dual write. DynamoDB keeps what /ops reads today; the same cleaned events
also go to the Firehose stream named in MYSIGN_EVENTS_STREAM, which lands them
as Parquet in S3 for Athena (deploy/analytics_stack.py, docs/analytics.md).
Unset — local dev, tests, a deploy without the analytics stack — and this does
nothing at all.

Analytics must never break the thing it watches: `send` never raises, and the
client has short timeouts so a slow Firehose costs a request a second or two at
worst, not the Lambda's whole budget. One PutRecordBatch per call; callers pass
a request's events together.
"""
import os
import threading

from app import events, log

_client = None
_lock = threading.Lock()


def stream():
    return os.environ.get("MYSIGN_EVENTS_STREAM") or None


def client():
    # Created on first use, not at import: most requests carry no events, and
    # boto3 client creation is tens of milliseconds of a cold start.
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                import boto3
                from botocore.config import Config
                _client = boto3.client("firehose", config=Config(
                    connect_timeout=1, read_timeout=2,
                    retries={"max_attempts": 2, "mode": "standard"}))
    return _client


def send(envs):
    """Forward envelopes. Returns how many Firehose accepted."""
    name = stream()
    if not name or not envs:
        return 0
    try:
        failed = events.put(client(), name, envs)
    except Exception as e:
        failed, why = len(envs), type(e).__name__
    else:
        why = "refused" if failed else None
    if failed:
        # A warning and a metric, not an error line: a lost analytics batch
        # shouldn't page anyone, but a run of them should show on a graph.
        log.emit("activity.forward_failed", level="warn", failed=failed,
                 count=len(envs), error_type=why)
        log.metric("EventsForwardFailed", failed)
    return len(envs) - failed
