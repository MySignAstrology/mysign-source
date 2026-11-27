"""
Isolate storage before anything imports it.

engine.store reads MYSIGN_DATA once, at import. test_app.py set it in its own
module body, which only worked while no earlier test file imported the store —
add one that sorts first and every test silently shares ./data with the last
run, and fails depending on what that run left behind.
"""
import os
import tempfile

os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-test-"))
