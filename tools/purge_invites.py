"""
Clear out invite records from before invites carried a ttl and an owner index.

    AWS_PROFILE=mysign AWS_REGION=us-east-2 MYSIGN_TABLE=<table> python tools/purge_invites.py
    AWS_PROFILE=mysign AWS_REGION=us-east-2 MYSIGN_TABLE=<table> python tools/purge_invites.py --write

Until the security fixes (audit M4), `_invites / inv-<token>` was saved with no
ttl and no index under its owner, so expired links stayed forever and account
deletion could not find them: each still held the inviter's uid and first name
and the uid of everyone who joined. New links expire on their own and are
deleted with the account. This handles the ones written before that:

  deleted   expired, or the inviter's account is gone
  updated   still live: given its ttl, its owner's index entry, and the
            single-use rule's bookkeeping (accepted list), so the owner can
            see and cancel it and deletion finds it
  kept      already in the new shape

A dry run (the default) reads and counts and writes nothing. Idempotent.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import store


def run(write=False, now=None):
    now = now or time.time()
    counts = {"deleted": 0, "updated": 0, "kept": 0}
    for kind in store.keys("_invites"):
        if not kind.startswith("inv-"):
            continue
        rec = store.load("_invites", kind, None)
        if rec is None:
            continue
        owner = rec.get("from")
        gone = rec.get("expires", 0) < now or not owner or \
            store.load(owner, "profile", None) is None
        if gone:
            counts["deleted"] += 1
            if write:
                store.delete("_invites", kind)
            continue
        if "max_uses" in rec and "accepted" in rec:
            counts["kept"] += 1
            continue
        counts["updated"] += 1
        if write:
            rec.setdefault("accepted", [])
            store.save("_invites", kind, rec, ttl=int(rec["expires"]))
            token = kind[len("inv-"):]
            mine = store.load(owner, "invites", []) or []
            if token not in mine:
                store.save(owner, "invites", mine + [token])
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="actually delete / update")
    print(run(write=ap.parse_args().write))
