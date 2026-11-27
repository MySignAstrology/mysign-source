"""
The indexes the operator console pages through, so no /ops request walks
every account.

People — partition `_uidx`, one small item per person, newest activity first:

    sk  "r#<9999999999 - ts, 10 digits>#<uid>"     v  {"uid", "ts"}

`ts` is her last_seen, or created_at before she has one. Written at sign-up
(app/auth.py) and moved when last_seen moves (app/activity.py, at most every
five minutes): the new row is written before the account, the old one deleted
after, so a crash leaves a duplicate rather than a gap. A reader checks each
row against the account — the account is the truth — and drops rows that are
older than it, so duplicates heal on the way past.

Flags — partition `_flags`, one item per red/amber reply or report:

    sk  "<message at>#<uid>#<message id>"         v  {"uid", "at", "tier", "id", "kind"}

Written beside the thread (app/server.py) and kept 400 days, like the audit
trail. No text, only where to look.

Accounts made before these indexes existed are added by `backfill`: either
`python tools/backfill_ops_index.py`, or lazily — each /ops list request runs
a bounded step (LAZY_BUDGET people) until the marker says done. The number of
accounts is a counter beside the rows, corrected once when the backfill ends.
"""
import base64
import binascii
import datetime as dt
import re
import time

from app import log
from engine import store
from engine.store import load, save

PK = "_uidx"
ROW = "r#"
FLAGS = "_flags"
STATS = "stats"
META = "meta"
MAX_TS = 9_999_999_999
FLAG_DAYS = 400
LAZY_BUDGET = 100
SK_RE = re.compile(r"^r#\d{10}#[A-Za-z0-9_-]{4,64}$")
FLAG_SK_RE = re.compile(r"^[0-9TZ:.+-]{10,40}#[A-Za-z0-9_-]{4,64}#[A-Za-z0-9_-]{1,40}$")

_done = False                       # the backfill marker, once seen, per process


def _epoch(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    if isinstance(v, str) and v:
        try:
            t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            return int(t.timestamp())
        except ValueError:
            return 0
    return 0


def when(acct):
    """The time a person sorts by: last seen, else joined."""
    acct = acct or {}
    return _epoch(acct.get("last_seen")) or _epoch(acct.get("created_at"))


def _enc(sk):
    """Cursors are opaque and URL-safe; the sort key underneath is not."""
    return None if sk is None else base64.urlsafe_b64encode(sk.encode()).decode().rstrip("=")


def _dec(cursor, pattern):
    if cursor is None:
        return None
    try:
        sk = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError("bad cursor") from None
    if not pattern.match(sk):
        raise ValueError("bad cursor")
    return sk


def row_sk(uid, ts):
    return f"{ROW}{MAX_TS - max(0, min(int(ts), MAX_TS)):010d}#{uid}"


def _parse(sk):
    _, rev, uid = sk.split("#", 2)
    return uid, MAX_TS - int(rev)


# ------------------------------------------------------------------ writes
# All of these swallow their own failures: an index is never worth a failed
# sign-up or a lost chat reply. A missing row is repaired by the backfill tool.

def _quiet(what, uid, fn):
    try:
        fn()
    except Exception as e:
        log.emit("opsindex.write_failed", level="warn", what=what, uid=uid,
                 error=type(e).__name__)


def put(uid, ts):
    _quiet("put", uid, lambda: save(PK, row_sk(uid, ts), {"uid": uid, "ts": int(ts)}))


def drop(uid, ts):
    _quiet("drop", uid, lambda: store.delete(PK, row_sk(uid, ts)))


def joined(uid, created_at):
    """A new account: its row, and one more on the count."""
    put(uid, created_at)
    _quiet("count", uid, lambda: store.incr(PK, STATS, "users"))


def moved(uid, old_ts, new_ts):
    """Call after the account carrying `new_ts` is saved (the new row is put
    before it by `put`)."""
    if int(old_ts) != int(new_ts):
        drop(uid, old_ts)


def flag(uid, at, tier, mid, kind="tier"):
    """A red or amber reply, or a reply she reported."""
    sk = f"{at}#{uid}#{mid}"
    if not FLAG_SK_RE.match(sk):
        return
    _quiet("flag", uid, lambda: save(
        FLAGS, sk, {"uid": uid, "at": at, "tier": tier, "id": mid, "kind": kind},
        ttl=int(time.time()) + FLAG_DAYS * 86400))


def _flags_of(uid):
    """(at, tier, id, kind) for everything in her record that the queue lists."""
    from engine import thread
    out = [(m.get("at"), m["tier"], m.get("id"), "tier") for m in thread.recent(uid, 0)
           if m.get("tier") in ("red", "amber") and m.get("at") and m.get("id")]
    out += [(r.get("at"), "report", r.get("id"), "report")
            for r in load(uid, "reports", []) or [] if r.get("at") and r.get("id")]
    return out


def forget(uid, acct):
    """Account deletion: her row, her flags, and one fewer on the count."""
    drop(uid, when(acct))
    _quiet("count", uid, lambda: store.incr(PK, STATS, "users", by=-1))
    try:
        for at, _, mid, _ in _flags_of(uid):
            store.delete(FLAGS, f"{at}#{uid}#{mid}")
    except Exception as e:
        log.emit("opsindex.write_failed", level="warn", what="forget_flags", uid=uid,
                 error=type(e).__name__)


# ------------------------------------------------------------------ backfill

def status():
    return load(PK, META, None) or {}


def backfill(budget=None):
    """
    Index accounts from before the index: `budget` people per call (None: all),
    resuming after the last one done. Idempotent — rows and flags have fixed
    keys — so two callers racing only repeat work.
    """
    global _done
    from app import auth
    meta = status()
    if meta.get("done"):
        _done = True
        return {"done": True, "indexed": 0, "total": meta.get("total")}
    everyone = auth.all_users()
    cursor = meta.get("cursor") or ""
    todo = [u for u in everyone if u > cursor]
    batch = todo if budget is None else todo[:budget]
    for uid in batch:
        acct = load(uid, "account", None)
        if acct:
            put(uid, when(acct))
            for at, tier, mid, kind in _flags_of(uid):
                flag(uid, at, tier, mid, kind)
    done = len(batch) == len(todo)
    meta = {"cursor": batch[-1] if batch else cursor, "done": done,
            "at": int(time.time()), "total": len(everyone)}
    if done:
        # The counter has counted every sign-up since the index shipped; this
        # is what it missed before then.
        meta["base"] = len(everyone) - store.counter(PK, STATS, "users")
        _done = True
    save(PK, META, meta)
    log.emit("opsindex.backfill", indexed=len(batch), done=done, total=len(everyone))
    return {"done": done, "indexed": len(batch), "total": len(everyone)}


def ensure():
    """A bounded backfill step until it's finished; a no-op after that."""
    if _done:
        return {"done": True}
    try:
        return backfill(LAZY_BUDGET)
    except Exception as e:
        log.emit("opsindex.backfill_failed", level="warn", error=type(e).__name__)
        return {"done": False}


def users_total():
    """Accounts that exist, or None while the backfill hasn't finished."""
    meta = status()
    if not meta.get("done"):
        return None
    return max(0, store.counter(PK, STATS, "users") + int(meta.get("base") or 0))


# ------------------------------------------------------------------ reads

def page(cursor=None, limit=50, accept=None, budget=None):
    """
    ([(uid, account)], next_cursor): people newest activity first, `limit` at
    most, reading at most `budget` index rows (default 4×limit, ≥200) so a
    filter that matches nobody still answers quickly — with a cursor to go on.
    `accept(uid, acct)` filters. Stale rows met on the way are dropped.
    """
    after = _dec(cursor, SK_RE)
    budget = budget or max(limit * 4, 200)
    out, seen, accts, read = [], set(), {}, 0
    while len(out) < limit and read < budget:
        rows, nxt = store.load_page(PK, ROW, ROW + "~", min(max(limit, 50), budget - read),
                                    after=after)
        consumed = True
        for i, (sk, _) in enumerate(rows):
            read += 1
            after = sk
            uid, ts = _parse(sk)
            if uid not in accts:
                accts[uid] = load(uid, "account", None)
            acct = accts[uid]
            if not acct:
                drop(uid, ts)
                continue
            cur = when(acct)
            if ts < cur:
                drop(uid, ts)           # she has moved on; this row is history
                continue
            if ts > cur and store.load(PK, row_sk(uid, cur), None):
                drop(uid, ts)           # a racing write's leftover; the real row exists
                continue
            if uid in seen:
                continue
            seen.add(uid)
            if accept and not accept(uid, acct):
                continue
            out.append((uid, acct))
            if len(out) >= limit:
                consumed = i == len(rows) - 1
                break
        if consumed:
            if nxt is None:
                return out, None
            after = nxt
    return out, _enc(after)


def active_since(since_ts, cap=5000):
    """Distinct people seen since `since_ts`, reading only their rows (at most `cap`)."""
    hi = f"{ROW}{MAX_TS - int(since_ts):010d}#~"
    uids, after = set(), None
    while len(uids) < cap:
        rows, after = store.load_page(PK, ROW, hi, 500, after=after)
        uids.update(_parse(sk)[0] for sk, _ in rows)
        if after is None:
            break
    return min(len(uids), cap)


def flagged(since, limit=200, cursor=None):
    """
    ([{uid, at, tier, id, kind}], next_cursor): newest first, `since` an ISO
    date or datetime. People whose account is gone are skipped (and their
    flags dropped).
    """
    rows, nxt = store.load_page(FLAGS, since, "9999~", limit, after=_dec(cursor, FLAG_SK_RE),
                                reverse=True)
    alive, out = {}, []
    for sk, v in rows:
        uid = v.get("uid")
        if uid not in alive:
            alive[uid] = load(uid, "account", None) is not None
        if not alive[uid]:
            _quiet("drop_flag", uid, lambda sk=sk: store.delete(FLAGS, sk))
            continue
        out.append(v)
    return out, _enc(nxt)


def pmap(fn, items, workers=8):
    """fn over a page of people, a few at a time: each is several reads."""
    items = list(items)
    if len(items) < 2:
        return [fn(x) for x in items]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))
