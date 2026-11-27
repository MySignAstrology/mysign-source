"""
The conversation, sharded by month.

It used to be one item. A message pair is ~560 bytes, so at twenty messages a
day a user crosses DynamoDB's 400KB item ceiling in about ten weeks — and the
old behaviour there was a hard error on save, meaning every message that user
sent would fail from then on, permanently. The lockout would have hit the most
engaged users first.

One item per user per month instead — and a month that gets unusually heavy
spills into a continuation shard, so no single item can grow without bound.
Monthly alone was not enough: a year at twenty messages a day already fills a
shard to half the ceiling, and a chattier month with longer messages would put
it back over. Spilling turns "mostly fixed" into fixed.

Reads that only need recent history walk backwards and stop as soon as they have
enough, so the common path touches one shard regardless of how long someone has
been here.

This module is the only place that knows the layout. Everything else asks for
messages.
"""
import datetime as dt
import json

from engine.store import load, save

INDEX = "thread_shards"          # ordered shard keys, oldest first
LEGACY = "thread"                # the single pre-shard item
LEGACY_INDEX = "thread_months"   # the month-only index that preceded spilling

# Well under DynamoDB's 400KB item limit. A shard that reaches this starts a
# continuation rather than growing.
SOFT_CAP = 150_000


def _key(month, n=0):
    return f"thread-{month}" if n == 0 else f"thread-{month}.{n}"


def _month_of_key(key):
    return key[len("thread-"):].split(".")[0]


def _shards(uid):
    keys = load(uid, INDEX, None)
    if keys is not None:
        return keys
    # Upgrade the month-only index in place.
    months = load(uid, LEGACY_INDEX, []) or []
    keys = [_key(m) for m in sorted(months)]
    if keys:
        save(uid, INDEX, keys)
        save(uid, LEGACY_INDEX, None)
    return keys


def _month_of(msg):
    at = msg.get("at") or dt.datetime.now().isoformat()
    return at[:7]


def migrate(uid):
    """
    Split a pre-shard thread into months, once, on first touch.

    Without this, everything written before sharding becomes invisible — which
    is the memory the whole product is built on.
    """
    legacy = load(uid, LEGACY, None)
    if not legacy:
        return 0
    buckets = {}
    for m in legacy:
        buckets.setdefault(_month_of(m), []).append(m)
    save(uid, LEGACY, None)
    for month in sorted(buckets):
        for msg in buckets[month]:
            _put(uid, msg)
    return len(legacy)


def _put(uid, msg):
    """Append to the newest shard for this message's month, spilling if full."""
    month = _month_of(msg)
    keys = _shards(uid)
    mine = [k for k in keys if _month_of_key(k) == month]
    key = mine[-1] if mine else _key(month)

    current = load(uid, key, []) or []
    if len(json.dumps(current, default=str)) >= SOFT_CAP:
        key = _key(month, len(mine))          # thread-2026-08.1, .2, ...
        current = []
    save(uid, key, current + [msg])

    if key not in keys:
        # Keep chronological order: month first, continuation suffix second.
        keys = sorted(set(keys + [key]),
                      key=lambda k: (_month_of_key(k), int(k.split(".")[1]) if "." in k else 0))
        save(uid, INDEX, keys)
    return msg


def append(uid, msg):
    """Add one message. Shard choice and spilling are handled here."""
    migrate(uid)
    return _put(uid, msg)


def recent(uid, limit=100):
    """
    The most recent `limit` messages, oldest first.

    Walks shards newest to oldest and stops once it has enough, so a two-year-old
    account costs the same read as a two-day-old one.
    """
    migrate(uid)
    out = []
    for key in reversed(_shards(uid)):
        out = (load(uid, key, []) or []) + out
        if limit and len(out) >= limit:
            break
    return out[-limit:] if limit else out


def for_date(uid, iso_date):
    """Every message on one day. Used by the nightly write-up."""
    migrate(uid)
    month = iso_date[:7]
    out = []
    for key in _shards(uid):
        if _month_of_key(key) != month:
            continue
        out += [m for m in (load(uid, key, []) or [])
                if (m.get("at") or "")[:10] == iso_date]
    return out


def dates(uid):
    """Every day that has messages, oldest first."""
    migrate(uid)
    seen = set()
    for key in _shards(uid):
        for m in load(uid, key, []) or []:
            if m.get("at"):
                seen.add(m["at"][:10])
    return sorted(seen)


def rewrite(uid, fn):
    """
    Pass every message to `fn`; where it returns a string, that becomes the
    message's text, in place, in its shard. The message keeps its slot, role and
    time so the conversation around it still reads in order; only the words
    change. Walks the whole thread — it serves deletion, which is rare and has
    to be complete. Returns how many messages changed.
    """
    migrate(uid)
    n = 0
    for key in _shards(uid):
        msgs = load(uid, key, []) or []
        hit = False
        for m in msgs:
            new = fn(m)
            if isinstance(new, str) and new != m.get("text"):
                m["text"], hit, n = new, True, n + 1
        if hit:
            save(uid, key, msgs)
    return n


def keys(uid):
    """Every storage key this user's thread occupies — deletion needs all of them."""
    return [INDEX, LEGACY, LEGACY_INDEX] + _shards(uid)


def clear(uid):
    for k in keys(uid):
        save(uid, k, None)
