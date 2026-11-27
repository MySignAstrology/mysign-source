"""
Populate _auth/users for accounts created before the roster index existed.

    python tools/backfill_roster.py

The scheduler iterates that index — listing directories works locally and not
at all on DynamoDB — so accounts predating it would silently never be pushed to.
Local (file) backend only; on DynamoDB, run a one-off scan for pk beginning
with "u" instead.
"""
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.store import BACKEND, ROOT, load, save

if BACKEND != "files":
    sys.exit("This backfill only handles the file backend. See the docstring.")

found = sorted(d.name for d in pathlib.Path(ROOT).iterdir()
               if d.is_dir() and d.name.startswith("u") and (d / "account.json").exists())
roster = load("_auth", "users", [])
added = [u for u in found if u not in roster]
save("_auth", "users", roster + added)
print(f"  roster: {len(roster)} -> {len(roster) + len(added)}   added {added or 'nothing'}")
