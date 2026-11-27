"""
Cold start.

Lambda runs the whole import graph of app.server before it serves anything, and
a new instance does it with a person waiting. Most requests are a page, the
state, a city lookup — none of them need the model SDK or the web-push chain,
which together were a third of the import. These keep them off the path.
"""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).parent.parent

# Loaded on first use, never at import. Each one here was made lazy on purpose;
# an eager `import` anywhere under app.server puts it back on every cold start.
DEFERRED = ["anthropic", "pywebpush", "py_vapid", "aiohttp", "cryptography", "requests",
            "httpx", "h2"]


def test_importing_the_app_does_not_import_what_only_some_routes_use(tmp_path):
    env = {**os.environ, "MYSIGN_DATA": str(tmp_path), "MYSIGN_TABLE": "coldstart-test",
           "AWS_DEFAULT_REGION": "us-east-2", "PYTHONPATH": str(ROOT)}
    env.pop("MYSIGN_SECRET_ARN", None)          # no network at import
    probe = ("import json, sys; import app.server; "
             f"print(json.dumps([m for m in {DEFERRED!r} if m in sys.modules]))")
    out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT, env=env,
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out.strip().splitlines()[-1]) == []


def test_push_still_resolves_its_libraries_on_first_use():
    from app import push
    assert push.WebPushException.__module__.startswith("pywebpush")
    assert len(push.public_key()) > 80


def test_the_city_index_serves_without_its_source(monkeypatch):
    """The Lambda zip ships cities.sqlite and not cities.tsv.gz."""
    from app import cities
    cities.count()                               # built here if it is not already
    monkeypatch.setattr(cities, "DATA", cities.DATA.with_name("absent.tsv.gz"))
    monkeypatch.setattr(cities._local, "con", None)
    assert cities.search("thibodaux")[0]["name"] == "Thibodaux"
