"""
Storage.

JSON files locally, DynamoDB when MYSIGN_TABLE is set. The dispatch happens once
at import, so nothing downstream knows or cares which is behind it —
`from engine.store import load, save` behaves identically either way.

Both backends export the same surface, with the same semantics, so a test that
passes against files means the same thing in production:

    load(uid, kind, default)              save(uid, kind, obj, ttl=None)
    keys(uid)                             delete(uid, kind)        delete_all(uid)
    load_range(pk, lo, hi)                [(kind, obj)] for lo <= kind <= hi, sorted
    load_page(pk, lo, hi, limit, after=None, reverse=False)
                                          (up to `limit` of those after `after`, next cursor or None)
    incr(uid, kind, field, by=1, limit=None, ttl=None)             counter(uid, kind, field)
    add_member(set_name, member)          remove_member(...)       members(set_name)

`save(uid, kind, None)` deletes — callers already use it that way, and storing
a literal "null" would leave an item behind that keys() and delete_all() count.
`ttl` is absolute epoch seconds; an expired item reads as absent in both
backends, because DynamoDB's own TTL sweep can lag by up to two days.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import pathlib
import re
import shutil
import tempfile
import threading
import time

BACKEND = "dynamodb" if os.environ.get("MYSIGN_TABLE") else "files"

ROOT = pathlib.Path(os.environ.get("MYSIGN_DATA", "./data"))

CTR = "#ctr"                       # counters live beside their kind, never inside it
_TTL, _VAL = "__ttl", "__v"        # file envelope used only when a ttl is set
_NAME = re.compile(r"^[A-Za-z0-9_.@-]{1,200}$")
_RESERVED = {"pk", "sk", "v", "ttl"}   # attribute names the dynamo backend owns
_lock = threading.Lock()           # flock is per open-file, not per thread; this covers threads


def _valid(name, what):
    """Set names and members become path components; anything odd is refused, not escaped."""
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError(f"invalid {what}: {name!r}")
    return name


def _expired(ttl):
    return ttl is not None and float(ttl) <= time.time()


def _p(user_id, kind):
    return ROOT / user_id / f"{kind}.json"


def _read(p):
    """(obj, ttl) for a file, or None if it isn't there."""
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if isinstance(raw, dict) and set(raw) == {_TTL, _VAL}:
        return raw[_VAL], raw[_TTL]
    return raw, None


def _write(p, obj, ttl):
    """Write-then-rename, so a crash or a concurrent reader never sees half a file."""
    p.parent.mkdir(parents=True, exist_ok=True)
    body = obj if ttl is None else {_TTL: ttl, _VAL: obj}
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(body, indent=1, default=str, ensure_ascii=False))
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _file_load(user_id, kind, default):
    got = _read(_p(user_id, kind))
    if got is None or _expired(got[1]):
        return default
    return got[0]


def _file_save(user_id, kind, obj, ttl=None):
    if obj is None:
        _file_delete(user_id, kind)
        return obj
    _write(_p(user_id, kind), obj, ttl)
    return obj


def _file_keys(user_id):
    d = ROOT / user_id
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        got = _read(p)
        if got is not None and not _expired(got[1]):
            out.append(p.stem)
    return out


def _file_stems(user_id, lo, hi):
    """Sorted keys in [lo, hi] by file name alone: nothing outside the range is read."""
    d = ROOT / user_id
    if not d.is_dir():
        return []
    return sorted(k for k in (p.stem for p in d.glob("*.json")) if lo <= k <= hi)


def _file_load_range(user_id, lo, hi):
    out = []
    for k in _file_stems(user_id, lo, hi):
        got = _read(_p(user_id, k))
        if got is not None and not _expired(got[1]):
            out.append((k, got[0]))
    return out


def _file_load_page(user_id, lo, hi, limit, after=None, reverse=False):
    keys = _file_stems(user_id, lo, hi)
    if reverse:
        keys.reverse()
    if after is not None:
        keys = [k for k in keys if (k < after if reverse else k > after)]
    out = []
    for i, k in enumerate(keys):
        if len(out) >= limit:
            return out, keys[i - 1]
        got = _read(_p(user_id, k))
        if got is not None and not _expired(got[1]):
            out.append((k, got[0]))
    return out, None


def _file_delete(user_id, kind):
    with contextlib.suppress(FileNotFoundError):
        _p(user_id, kind).unlink()


def _file_delete_all(user_id):
    d = ROOT / user_id
    if not d.is_dir():
        return 0
    n = len(list(d.glob("*.json")))
    shutil.rmtree(d, ignore_errors=True)
    return n


@contextlib.contextmanager
def _user_lock(user_id):
    """Thread lock for this process, flock for every other process sharing the disk."""
    d = ROOT / user_id
    d.mkdir(parents=True, exist_ok=True)
    with _lock, open(d / ".lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _file_incr(user_id, kind, field, by=1, limit=None, ttl=None):
    if field in _RESERVED:
        raise ValueError(f"counter field {field!r} is reserved")
    p = _p(user_id, kind + CTR)
    with _user_lock(user_id):
        got = _read(p)
        obj, old_ttl = got if got and not _expired(got[1]) else ({}, None)
        cur = int(obj.get(field, 0))
        if limit is not None and cur >= limit:
            return None
        obj[field] = cur + by
        _write(p, obj, ttl if ttl is not None else old_ttl)
        return obj[field]


def _file_counter(user_id, kind, field):
    return int(_file_load(user_id, kind + CTR, {}).get(field, 0))


def _file_counters(user_id, kind):
    """Every counter field on `kind`, for when the field names aren't known ahead."""
    return {k: int(v) for k, v in (_file_load(user_id, kind + CTR, {}) or {}).items()}


def _set_dir(set_name):
    return ROOT / f"_set#{_valid(set_name, 'set name')}"


def _file_add_member(set_name, member):
    d = _set_dir(set_name)
    d.mkdir(parents=True, exist_ok=True)
    (d / _valid(member, "member")).touch()


def _file_remove_member(set_name, member):
    with contextlib.suppress(FileNotFoundError):
        (_set_dir(set_name) / _valid(member, "member")).unlink()


def _file_members(set_name):
    d = _set_dir(set_name)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if _NAME.match(p.name))


if BACKEND == "dynamodb":
    from engine.store_dynamo import (
        add_member,
        counter,
        counters,
        delete,
        delete_all,
        incr,
        keys,
        load,
        load_page,
        load_range,
        members,
        remove_member,
        save,
    )
else:
    load, save, keys, load_range = _file_load, _file_save, _file_keys, _file_load_range
    load_page = _file_load_page
    delete, delete_all = _file_delete, _file_delete_all
    incr, counter, counters = _file_incr, _file_counter, _file_counters
    add_member, remove_member, members = _file_add_member, _file_remove_member, _file_members


def append_episode(user_id, episode):
    eps = load(user_id, "episodes", [])
    eps.append(episode)
    return save(user_id, "episodes", eps)


def log_mention(user_id, date, keys):
    """Record which sky facts we actually surfaced today. Drives novelty decay,
    which only looks back a few weeks — so only a few weeks are kept."""
    log = load(user_id, "mentions", {})
    log[date] = sorted(set(log.get(date, []) + list(keys)))
    keep = sorted(log)[-45:]
    return save(user_id, "mentions", {d: log[d] for d in keep})
