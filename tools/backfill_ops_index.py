"""
Index every existing account for the operator console (app/opsindex.py), once.

    AWS_PROFILE=mysign AWS_REGION=us-east-2 MYSIGN_TABLE=<table> python tools/backfill_ops_index.py
    python tools/backfill_ops_index.py                # the local file store
    python tools/backfill_ops_index.py --batch 500    # in steps, printing progress
    python tools/backfill_ops_index.py --again        # forget the marker and redo it

Writes one `_uidx` row per account (sorted by last seen) and one `_flags` row
per red/amber reply or report still in a thread, then the `_uidx/meta` marker
that turns off the lazy backfill /ops otherwise does a hundred people per
request at a time. Idempotent: rows have fixed keys, so a rerun rewrites the
same items. Safe while the app is serving — a person who signs up or comes
back mid-run is written by the app as well.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import opsindex
from engine import store

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--batch", type=int, default=None, help="people per step (default: all at once)")
ap.add_argument("--again", action="store_true", help="clear the done marker first")
a = ap.parse_args()

if a.again:
    store.delete(opsindex.PK, opsindex.META)
while True:
    r = opsindex.backfill(a.batch)
    print(f"  indexed {r['indexed']:>6}   of {r.get('total')}   done={r['done']}")
    if r["done"]:
        break
print(f"  accounts: {opsindex.users_total()}")
