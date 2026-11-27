"""
The app moved from / to /app; / is the website now.

FastAPI answers / the way deploy/edge/router.js does at the edge, so local dev
and CloudFront with staticFromS3 off agree with it on. Links the server makes
go straight to /app; old links still arrive through the redirect. The service
worker keeps its whole-origin scope (push subscriptions belong to it) but must
never hand the website's visitors the cached app.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import app.server as server
from tests.test_app import sign_in  # sets the dev-auth env too

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
STATIC = ROOT / "app" / "static"


@pytest.fixture
def anon():
    return TestClient(server.app, follow_redirects=False)


# ------------------------------------------------------------------ / and /app

def test_root_is_the_website_for_strangers(anon):
    for url in ("/", "/?utm_source=tiktok&utm_campaign=x"):
        r = anon.get(url)
        assert r.status_code == 200
        assert r.text == (SITE / "index.html").read_text()
        assert r.headers["cache-control"] == "no-cache"


def test_signed_in_root_goes_to_the_app_keeping_the_query(anon):
    sign_in(anon, "move-root@x.com")
    r = anon.get("/?utm_source=push")
    assert r.status_code == 302
    assert r.headers["location"] == "/app?utm_source=push"
    assert r.headers["cache-control"] == "no-store"
    assert anon.get("/").headers["location"] == "/app"


@pytest.mark.parametrize("query", ["invite=abc_DEF-123", "p=9a8c2c7887ac", "sky=events",
                                   "invite=x%2By&utm_source=tiktok", "p"])
def test_old_app_links_still_land_in_the_app(anon, query):
    r = anon.get(f"/?{query}")
    assert r.status_code == 302
    assert r.headers["location"] == f"/app?{query}"


def test_the_app_shell_is_at_app(anon):
    r = anon.get("/app?invite=abc")
    assert r.status_code == 200 and 'id="aiOk"' in r.text


@pytest.mark.parametrize("name", server.SITE_FILES)
def test_site_assets_are_served_from_the_root(anon, name):
    r = anon.get(f"/{name}")
    assert r.status_code == 200
    assert r.content == (SITE / name).read_bytes()


def test_every_file_the_website_loads_is_served(anon):
    page = (SITE / "index.html").read_text()
    for path in set(re.findall(r'(?:src|href)="(/[^"/][^"]*)"', page)):
        assert anon.get(path).status_code in (200, 302), path


def test_tooling_in_site_is_not_served(anon):
    for path in ("/serve.py", "/README.md", "/og.html"):
        assert anon.get(path).status_code == 404


# ------------------------------------------------------------------ PWA

def test_manifest_starts_at_app_and_keeps_its_identity():
    m = json.loads((STATIC / "manifest.webmanifest").read_text())
    # id "/" is what installs from before the move were given (it defaulted
    # to their start_url), so the browser sees one app, not a second.
    assert m["start_url"] == "/app" and m["id"] == "/" and m["scope"] == "/"


_SW = """
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const ORIGIN = 'https://mysignapp.com';
const on = {};
const store = new Map();
const keyOf = k => { const u = new URL(typeof k === 'string' ? k : k.url, ORIGIN); return u.pathname + u.search; };
const cache = { put: async (k, v) => { store.set(keyOf(k), v); }, addAll: async () => {} };
const caches = { open: async () => cache, match: async k => store.get(keyOf(k)),
                 keys: async () => [], delete: async () => true };
let wins = [], log = {};
const clients = {
  matchAll: async () => wins.map((url, i) => ({ url, focus: async () => { log.focused = i; },
                                               postMessage: m => { log.posted = [i, m.url]; } })),
  openWindow: async url => { log.opened = url; },
  claim: async () => {},
};
let online = false;
const fetch = async req => {
  if (!online) throw new TypeError('offline');
  return { ok: true, type: 'basic', redirected: false, body: 'NET ' + keyOf(req), clone() { return this.body; } };
};
const self = { addEventListener: (t, f) => { on[t] = f; }, location: new URL(ORIGIN),
               registration: {}, clients, skipWaiting: async () => {} };
new Function('self', 'caches', 'clients', 'fetch', 'Response', src)(self, caches, clients, fetch,
  { error: () => 'ERROR' });

(async () => {
  const out = [];
  for (const c of JSON.parse(fs.readFileSync(0, 'utf8'))) {
    store.clear(); log = {};
    for (const [k, v] of Object.entries(c.cache || {})) store.set(k, v);
    online = !!c.online; wins = (c.windows || []).map(p => ORIGIN + p);
    if (c.op === 'fetch') {
      let p = null;
      on.fetch({ request: { url: ORIGIN + c.url, method: 'GET', mode: c.mode || 'navigate' },
                 respondWith: x => { p = x; } });
      const res = p ? await p : 'PASSTHROUGH';
      await new Promise(r => setTimeout(r, 0));
      out.push({ res: typeof res === 'string' ? res : res.body, cached: Object.fromEntries(store) });
    } else {
      let p = null;
      on.notificationclick({ notification: { close() {}, data: { url: c.url } }, waitUntil: x => { p = x; } });
      await p;
      out.push(log);
    }
  }
  console.log(JSON.stringify(out));
})();
"""


def _sw(*cases):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", _SW, str(STATIC / "sw.js")], input=json.dumps(cases),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_offline_the_app_falls_back_to_its_shell_and_the_website_never_does():
    shell = {"/app": "APP SHELL"}
    app_view, site, site_cached = _sw(
        {"op": "fetch", "url": "/app/people?p=1", "cache": shell},
        {"op": "fetch", "url": "/", "cache": shell},
        {"op": "fetch", "url": "/", "cache": {**shell, "/": "SITE"}})
    assert app_view["res"] == "APP SHELL"
    assert site["res"] == "ERROR"
    assert site_cached["res"] == "SITE"


def test_online_every_app_view_refreshes_the_one_shell():
    res, = _sw({"op": "fetch", "url": "/app/people?p=1", "online": True})
    assert res["cached"] == {"/app": "NET /app/people?p=1"}


def test_a_push_click_opens_the_app_not_the_website():
    # Notifications already in the tray from before the move carry '/?p=…'.
    to_app, fresh = _sw(
        {"op": "click", "url": "/?p=7", "windows": ["/", "/app"]},
        {"op": "click", "url": "/?sky=events", "windows": ["/", "/privacy"]})
    assert to_app == {"posted": [1, "/app?p=7"], "focused": 1}
    assert fresh == {"opened": "/app?sky=events"}
