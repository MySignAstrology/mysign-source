"""
DynamoDB-backed store. Same functions as the JSON store, same shapes, same
semantics — see engine/store.py for the surface.

One item per (user, kind). That keeps reads to a single GetItem and keeps the
code identical to local development, at the cost of DynamoDB's 400KB item
ceiling.

The conversation used to be the thing that would hit it — see engine/thread.py,
which shards by month and spills within a month. `_check_size` stays as a
backstop for every other kind: it fails loudly rather than letting a write
silently truncate someone's history.

Two shapes don't fit "one JSON blob per kind", and get their own items:
  - counters (sk = f"{kind}#ctr", native Number attributes) because an atomic
    ADD can't reach inside a JSON string, and read-modify-write loses updates;
  - sets (pk = f"_set#{name}", one item per member) because a roster in one
    item would eventually hit the ceiling, and concurrent adds would clobber.

`ttl` is written as a Number attribute for the table's TTL sweep, but the sweep
can lag by up to 48 hours, so reads also treat an expired item as absent.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

_table = None
LIMIT = 380_000          # leave headroom under DynamoDB's 400KB item limit
CTR = "#ctr"
_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,200}$")
_RESERVED = {"pk", "sk", "v", "ttl"}


_local = threading.local()


def table():
    """
    One Table per thread. boto3 resources are not thread-safe, and the morning
    cron composes pushes on a thread pool. `_table`, when set (tests), wins.
    """
    if _table is not None:
        return _table
    t = getattr(_local, "table", None)
    if t is None:
        t = _local.table = boto3.session.Session().resource("dynamodb").Table(
            os.environ["MYSIGN_TABLE"])
    return t


def dumps(obj):
    """UTF-8, not \\u escapes. ensure_ascii made a Cyrillic or CJK message
    three to six times its real size, and the item ceiling arrived that much
    sooner for anyone who doesn't write in English."""
    return json.dumps(obj, default=str, ensure_ascii=False)


def _check_size(user_id, kind, blob):
    n = len(blob.encode("utf-8"))           # DynamoDB counts bytes, not characters
    if n > LIMIT:
        raise ValueError(
            f"{user_id}/{kind} is {n} bytes, over the {LIMIT} write ceiling. "
            f"Shard this kind before it grows further — see engine/store_dynamo.py.")


def _valid(name, what):
    """Same rule as the file backend, so a name that works locally works here."""
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError(f"invalid {what}: {name!r}")
    return name


def _expired(item):
    return item.get("ttl") is not None and float(item["ttl"]) <= time.time()


def _query(pk, **kw):
    """Every item under one partition key; Query pages at 1MB, so follow the cursor."""
    kw = {"KeyConditionExpression": Key("pk").eq(pk), **kw}
    while True:
        r = table().query(**kw)
        yield from r.get("Items", [])
        if "LastEvaluatedKey" not in r:
            return
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]


def load(user_id, kind, default):
    r = table().get_item(Key={"pk": user_id, "sk": kind}).get("Item")
    if not r or _expired(r):
        return default
    return json.loads(r["v"])


def save(user_id, kind, obj, ttl=None):
    if obj is None:
        delete(user_id, kind)
        return obj
    blob = dumps(obj)
    _check_size(user_id, kind, blob)
    item = {"pk": user_id, "sk": kind, "v": blob}
    if ttl is not None:
        item["ttl"] = int(ttl)
    table().put_item(Item=item)
    return obj


def keys(user_id):
    items = _query(user_id, ProjectionExpression="sk, #t",
                   ExpressionAttributeNames={"#t": "ttl"})
    return sorted(i["sk"] for i in items if not _expired(i))


def load_range(user_id, lo, hi):
    """One Query on the partition with the sort key between lo and hi (inclusive)."""
    items = _query(user_id, KeyConditionExpression=Key("pk").eq(user_id) & Key("sk").between(lo, hi),
                   ProjectionExpression="sk, v, #t", ExpressionAttributeNames={"#t": "ttl"})
    return [(i["sk"], json.loads(i["v"])) for i in items if not _expired(i)]


def load_page(user_id, lo, hi, limit, after=None, reverse=False):
    """
    At most `limit` live items of load_range, starting after the sort key
    `after` (descending when `reverse`), and the cursor for the next page —
    None once the range is done. Reads only the page, not the partition.
    """
    kw = {"KeyConditionExpression": Key("pk").eq(user_id) & Key("sk").between(lo, hi),
          "ProjectionExpression": "sk, v, #t", "ExpressionAttributeNames": {"#t": "ttl"},
          "ScanIndexForward": not reverse}
    if after is not None:
        kw["ExclusiveStartKey"] = {"pk": user_id, "sk": after}
    out = []
    while len(out) < limit:
        r = table().query(Limit=limit - len(out), **kw)
        out += [(i["sk"], json.loads(i["v"])) for i in r.get("Items", []) if not _expired(i)]
        if "LastEvaluatedKey" not in r:
            return out, None
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return out, kw["ExclusiveStartKey"]["sk"]


def delete(user_id, kind):
    table().delete_item(Key={"pk": user_id, "sk": kind})


def delete_all(user_id):
    sks = [i["sk"] for i in _query(user_id, ProjectionExpression="sk")]
    with table().batch_writer() as b:
        for sk in sks:
            b.delete_item(Key={"pk": user_id, "sk": sk})
    return len(sks)


def incr(user_id, kind, field, by=1, limit=None, ttl=None):
    """
    Atomic ADD on a native Number. A live-item condition rides along so an
    expired-but-unswept counter isn't silently continued: if that's why the
    write was refused, clear the stale item and try once more.
    """
    if field in _RESERVED:
        raise ValueError(f"counter field {field!r} is reserved")
    key = {"pk": user_id, "sk": kind + CTR}
    names = {"#f": field, "#t": "ttl"}
    cond = "(attribute_not_exists(#t) OR #t > :now)"
    if limit is not None:
        cond += " AND (attribute_not_exists(#f) OR #f < :limit)"
    update = "ADD #f :by" + (" SET #t = :ttl" if ttl is not None else "")
    for _ in range(2):
        values = {":by": by, ":now": int(time.time())}
        if limit is not None:
            values[":limit"] = limit
        if ttl is not None:
            values[":ttl"] = int(ttl)
        try:
            r = table().update_item(
                Key=key, UpdateExpression=update, ConditionExpression=cond,
                ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                ReturnValues="UPDATED_NEW")
            return int(r["Attributes"][field])
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        cur = table().get_item(Key=key, ConsistentRead=True).get("Item")
        if not cur or not _expired(cur):
            return None                      # at the limit, not stale
        try:                                 # only the stale item; a racer may have reset it
            table().delete_item(Key=key, ConditionExpression="#t <= :now",
                                ExpressionAttributeNames={"#t": "ttl"},
                                ExpressionAttributeValues={":now": int(time.time())})
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    return None


def counter(user_id, kind, field):
    r = table().get_item(Key={"pk": user_id, "sk": kind + CTR}).get("Item")
    if not r or _expired(r):
        return 0
    return int(r.get(field, 0))


def counters(user_id, kind):
    r = table().get_item(Key={"pk": user_id, "sk": kind + CTR}).get("Item")
    if not r or _expired(r):
        return {}
    return {k: int(v) for k, v in r.items() if k not in _RESERVED}


def _set_pk(set_name):
    return f"_set#{_valid(set_name, 'set name')}"


def add_member(set_name, member):
    table().put_item(Item={"pk": _set_pk(set_name), "sk": _valid(member, "member")})


def remove_member(set_name, member):
    table().delete_item(Key={"pk": _set_pk(set_name), "sk": _valid(member, "member")})


def members(set_name):
    return sorted(i["sk"] for i in _query(_set_pk(set_name), ProjectionExpression="sk"))
