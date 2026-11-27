"""
The public site (site/): a static page with no build step, so nothing but
these tests notices a broken link, a missing meta tag, an id the script needs
but the markup lost, or an auth body the server stopped accepting.
"""
import json
import os
import pathlib
import re
import struct
import sys
from html.parser import HTMLParser

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SITE))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

import serve  # site/serve.py

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,59}$")
# Paths the page may link to that the app, not site/, answers.
APP_ROUTES = {"/app", "/privacy", "/terms", "/refunds", "/support", "/cookies", "/accuracy", "/shop", "/check", "/gifts"}
FONT_HOSTS = ()


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []          # (tag, attrs)
        self.stack = []
        self.jsonld = []
        self._in_ld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.tags.append((tag, a))
        if tag == "script" and a.get("type") == "application/ld+json":
            self._in_ld = True
            self.jsonld.append("")
        if tag not in {"meta", "link", "br", "img", "input", "source", "use", "path", "circle",
                       "stop", "hr", "wbr"}:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_ld = False
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self._in_ld:
            self.jsonld[-1] += data

    def all(self, tag, **attrs):
        return [a for t, a in self.tags if t == tag
                and all(a.get(k) == v for k, v in attrs.items())]

    def meta(self, key):
        found = self.all("meta", name=key) + self.all("meta", property=key)
        return found[0].get("content") if found else None


@pytest.fixture(scope="module")
def page():
    p = Page()
    p.feed((SITE / "index.html").read_text())
    p.close()
    return p


@pytest.fixture(scope="module")
def html():
    return (SITE / "index.html").read_text()


# ------------------------------------------------------------------ markup

def test_landmarks_and_one_h1(page):
    for tag in ("header", "main", "footer", "nav", "dialog"):
        assert page.all(tag), f"no <{tag}>"
    assert len(page.all("h1")) == 1
    assert page.all("html")[0].get("lang") == "en"
    assert page.all("main", id="main") and page.all("a", href="#main"), "skip link"


def test_head_for_search_and_sharing(page):
    assert page.meta("description") and 50 < len(page.meta("description")) <= 170
    assert page.meta("viewport") and "width=device-width" in page.meta("viewport")
    assert page.all("link", rel="canonical")[0]["href"] == "https://mysignapp.com/"
    for k in ("og:title", "og:description", "og:type", "twitter:title", "twitter:description"):
        assert page.meta(k), k
    assert page.meta("og:url") == "https://mysignapp.com/"
    assert page.meta("og:image") == page.meta("twitter:image") == "https://mysignapp.com/og.png"
    assert page.meta("twitter:card") == "summary_large_image"
    # Night only: one theme colour, and the browser is told the page is dark.
    themes = page.all("meta", name="theme-color")
    assert [(t.get("media"), t.get("content")) for t in themes] == [(None, "#090B17")]
    assert page.meta("color-scheme") == "dark"
    assert page.all("link", rel="apple-touch-icon")
    assert page.all("title")


def test_structured_data_parses(page):
    types = set()
    for block in page.jsonld:
        data = json.loads(block)
        for node in data.get("@graph", [data]):
            types.add(node["@type"])
    assert {"WebSite", "SoftwareApplication"} <= types


def test_share_image_is_1200_by_630():
    raw = (SITE / "og.png").read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", raw[16:24]) == (1200, 630)


# ------------------------------------------------------------------ links

def _local_refs(page):
    for tag, a in page.tags:
        for attr in ("href", "src"):
            v = a.get(attr)
            if v and not v.startswith(("#", "http://", "https://", "mailto:", "data:")):
                yield tag, v


def test_every_local_reference_resolves(page):
    from app import server
    app_paths = {getattr(r, "path", None) for r in server.app.routes}
    for tag, ref in _local_refs(page):
        path = ref.split("?")[0].split("#")[0]
        if path.startswith("/static/"):
            assert (ROOT / "app" / path.lstrip("/")).is_file(), ref
        elif path in APP_ROUTES:
            # /app arrives with the app move; the rest exist today.
            assert path == "/app" or path in app_paths, ref
        elif path == "/":
            continue
        else:
            assert serve.site_file(path) is not None, f"<{tag}> {ref} is not a file in site/"


def test_in_page_anchors_exist(page):
    ids = {a["id"] for _, a in page.tags if "id" in a}
    for tag, a in page.tags:
        if tag in ("a", "use") and (a.get("href") or "").startswith("#") and a["href"] != "#":
            assert a["href"][1:] in ids, a["href"]


def test_no_third_party_scripts_or_styles(page, html):
    for s in page.all("script"):
        src = s.get("src")
        assert src is None or src.startswith("/"), f"external script {src}"
    for link in page.all("link", rel="stylesheet"):
        assert link["href"].startswith("/") or link["href"].startswith(FONT_HOSTS), link["href"]
    assert "<iframe" not in html.lower()
    for js in ("site.js", "track.js", "sky.js"):
        src = (SITE / js).read_text()
        assert not re.search(r"https?://", re.sub(r"//[^\n]*|/\*.*?\*/", "", src, flags=re.S)), \
            f"{js} reaches off-site"


def test_scripts_find_their_elements(page):
    ids = {a["id"] for _, a in page.tags if "id" in a}
    src = (SITE / "site.js").read_text()
    wanted = set(re.findall(r"""\$\(['"]#([A-Za-z][\w-]*)['"]""", src))
    assert wanted and wanted <= ids, wanted - ids


def test_start_sheet_fields(page):
    code = page.all("input", id="sCode")[0]
    assert code.get("inputmode") == "numeric" and code.get("autocomplete") == "one-time-code"
    assert any(a.get("id") == "sErr" and a.get("role") == "alert" for _, a in page.tags)
    assert page.all("dialog", id="start")[0].get("aria-labelledby")
    assert page.all("label", **{"for": "sId"})


def test_landing_is_one_question(page):
    # The landing page asks for a birthday and nothing else; the detail lives
    # on /accuracy, /privacy and the rest, linked from the footer.
    assert page.all("form", id="free") and page.all("input", id="frDate")
    assert not [a for a in page.all("details") if (a.get("data-track") or "").startswith("faq_open:")]


# ------------------------------------------------------------------ analytics

def test_tracked_names_are_valid(page):
    names = [a["data-track"] for _, a in page.tags if "data-track" in a]
    names += ["section_view:" + a["data-section"] for _, a in page.tags if "data-section" in a]
    names += ["code_submit", "start_code_sent", "start_verified", "home"]
    assert names
    for n in names:
        assert NAME_RE.match(n), n
    js = (SITE / "track.js").read_text()
    assert NAME_RE.pattern in js, "track.js and the collector must agree on names"


# ------------------------------------------------------------------ brand

def _tokens(css):
    return dict(re.findall(r"(--[\w-]+):\s*([^;}]+)", css))


def test_night_overrides_only_known_tokens():
    """The night values come after the old day palette and apply to everyone."""
    css = (SITE / "brand.css").read_text()
    day, night = css.split("@media all{:root{", 1)
    dt, nt = _tokens(day), _tokens(night.split("}}", 1)[0])
    assert nt and set(nt) <= set(dt), set(nt) - set(dt)
    assert nt["--ground"].strip() == "#090B17"


SCREENS = sorted([*SITE.glob("*.html"), *(SITE / "gifts").glob("*.html"),
                  *(ROOT / "app" / "static").glob("*.html")])


@pytest.mark.parametrize("path", SCREENS, ids=lambda p: p.name)
def test_every_screen_is_night_at_every_hour(path):
    """No page, script or stylesheet may switch looks on the phone's theme."""
    text = path.read_text()
    assert "prefers-color-scheme" not in text, path.name
    if path.name in ("ops.html", "admin.html"):
        # Internal, and dressed like the social media tool: light at every hour instead.
        assert re.findall(r"color-scheme:\s*([\w ]+)", text) == ["light"]
        return
    assert 'content="light dark"' not in text and "color-scheme:light" not in text.replace(" ", ""), path.name


def test_shared_files_do_not_follow_the_phone_theme():
    for name in ("brand.css", "sky.js", "consent.js"):
        text = (SITE / name).read_text()
        assert "prefers-color-scheme" not in text and "color-scheme:light" not in text, name
    app = (ROOT / "app" / "static" / "index.html").read_text()
    assert _tokens(app.split("@media all{:root{")[-1].split("}}", 1)[0])["--ground"].strip() == "#090B17"


def test_tokens_match_the_app():
    """brand.css is the app's palette, not an approximation of it."""
    app = (ROOT / "app" / "static" / "index.html").read_text()
    mine = _tokens((SITE / "brand.css").read_text().split("@media", 1)[0])
    root = re.search(r":root\{(.*?)\n\}", app, re.S)
    if root is None:  # the app has started importing brand.css itself
        pytest.skip("app no longer carries its own tokens")
    theirs = _tokens(root.group(1))
    for k in ("--ground", "--ink", "--accent", "--accent-2", "--display", "--sans", "--mono", "--r"):
        assert mine[k].strip() == theirs[k].strip(), k


def test_every_font_face_has_its_file_and_its_licence():
    """A face named in fonts.css that 404s falls back without a word."""
    css = (SITE / "fonts" / "fonts.css").read_text()
    licences = (SITE / "fonts" / "LICENSES.txt").read_text()
    files = set(re.findall(r"url\(/fonts/([\w.-]+)\)", css))
    assert files and all((SITE / "fonts" / f).is_file() for f in files)
    for family in set(re.findall(r"font-family:'([^']+)'", css)):
        assert f"=== {family} ===" in licences, family
    assert "IM Fell Double Pica" in css      # the flourish face (--flair)
    for text in ((SITE / "brand.css").read_text(), (ROOT / "app" / "static" / "index.html").read_text()):
        assert "--flair:'IM Fell Double Pica'" in text


# ------------------------------------------------------------------ robots

def test_robots_and_sitemap():
    robots = (SITE / "robots.txt").read_text()
    for rule in ("Disallow: /app", "Disallow: /api/", "Disallow: /ops", "Disallow: /bt/"):
        assert rule in robots
    assert "Sitemap: https://mysignapp.com/sitemap.xml" in robots
    locs = re.findall(r"<loc>https://mysignapp\.com(/[^<]*)</loc>",
                      (SITE / "sitemap.xml").read_text())
    core = [x for x in locs if not x.startswith(("/couples", "/gifts"))]
    assert sorted(core) == ["/", "/accuracy", "/check", "/cookies", "/privacy", "/refunds", "/support", "/terms"]
    # Every celebrity couple page, written by tools/couples_build.py.
    from engine import couples
    assert sorted(x for x in locs if x.startswith("/couples")) == sorted(
        ["/couples"] + [f"/couples/{c['slug']}" for c in couples.couples()])


# ------------------------------------------------------------------ dev server

@pytest.mark.parametrize("path,app", [
    ("/", False), ("/brand.css", False), ("/api/auth/request", True), ("/app", True),
    ("/app/people", True), ("/apple", False), ("/e", True), ("/events", False),
    ("/static/icons/icon-192.png", True), ("/privacy", True), ("/ops/x", True),
    ("/couples", True), ("/couples/beyonce-and-jay-z", True),
])
def test_serve_routes(path, app):
    assert serve.owned_by_app(path) is app


@pytest.mark.parametrize("path", ["/serve.py", "/README.md", "/og.html", "/../README.md",
                                  "/..%2fapp/server.py", "/.git/config", "/nope.css"])
def test_serve_never_exposes_tooling_or_escapes(path):
    assert serve.site_file(path) is None


# ------------------------------------------------------------------ the server's side

def test_auth_accepts_anon_id_alongside_email():
    """The sheet sends anon_id in both auth bodies. If a model ever forbids
    extra fields, sign-in from the site breaks — this is the tripwire."""
    from fastapi.testclient import TestClient

    from app import server
    c = TestClient(server.app)
    anon = "01JAXQ5ZK3V8M2N4P6R7S9T0WX"
    email = "site-anon@example.test"
    r = c.post("/api/auth/request", json={"email": email, "anon_id": anon})
    assert r.status_code == 200, r.text
    code = r.json()["dev_code"]
    r = c.post("/api/auth/verify", json={"email": email, "code": code, "anon_id": anon})
    assert r.status_code == 200, r.text
    assert c.get("/api/state").json().get("signed_in") is True


# ------------------------------------------------------------------ /ig

@pytest.fixture(scope="module")
def ig():
    p = Page()
    p.feed((SITE / "ig.html").read_text())
    p.close()
    return p


def test_ig_tags_the_visit_before_the_tracker_runs():
    html = (SITE / "ig.html").read_text()
    tag = html.index("q.set('utm_source','instagram')")
    assert tag < html.index('<script src="/track.js?v=2" defer>'), "must run before track.js reads the URL"
    assert "if(q.get('utm_source'))return;" in html, "tags already on the link win"
    assert 'data-page="ig"' in html


def test_ig_page_shape(ig):
    for tag in ("header", "main", "footer", "dialog"):
        assert ig.all(tag), f"no <{tag}>"
    assert len(ig.all("h1")) == 1
    assert ig.all("link", rel="canonical")[0]["href"] == "https://mysignapp.com/ig"
    assert ig.meta("robots") == "noindex"
    assert ig.all("button", **{"data-open": "start"}), "a way to sign up"
    assert not ig.jsonld, "the homepage's structured data describes /, not this page"


def test_ig_scripts_find_their_elements(ig):
    ids = {a["id"] for _, a in ig.tags if "id" in a}
    src = (SITE / "site.js").read_text()
    wanted = set(re.findall(r"""\$\(['"]#([A-Za-z][\w-]*)['"]""", src))
    assert wanted <= ids, wanted - ids
    assert "igCouples" in ids


def test_ig_links_resolve(ig):
    from app import server
    from engine import couples
    slugs = {c["slug"] for c in couples.listing()["couples"]}
    app_paths = {getattr(r, "path", None) for r in server.app.routes}
    for tag, ref in _local_refs(ig):
        path = ref.split("?")[0].split("#")[0]
        if path.startswith("/static/"):
            assert (ROOT / "app" / path.lstrip("/")).is_file(), ref
        elif path.startswith("/couples/"):
            assert path.split("/")[2] in slugs, ref
        elif path in APP_ROUTES | {"/couples"}:
            assert path == "/app" or path in app_paths, ref
        elif path == "/":
            continue
        else:
            assert serve.site_file(path) is not None, f"<{tag}> {ref} is not a file in site/"
    ids = {a["id"] for _, a in ig.tags if "id" in a}
    for tag, a in ig.tags:
        if tag in ("a", "use") and (a.get("href") or "").startswith("#") and a["href"] != "#":
            assert a["href"][1:] in ids, a["href"]


def test_ig_tracked_names_are_valid(ig):
    for _, a in ig.tags:
        if "data-track" in a:
            assert NAME_RE.match(a["data-track"]), a["data-track"]


def test_ig_couple_cards_match_the_engine():
    from engine import couples
    by = {c["slug"]: c for c in couples.listing()["couples"]}
    html = (SITE / "ig.html").read_text()
    for slug, score, band in re.findall(
            r'href="/couples/([a-z0-9-]+)"[^>]*><b>(\d+)</b><span class="nm">[^<]*</span><span class="bd">([^<]+)</span>', html):
        assert by[slug]["overall"] == int(score) and by[slug]["band"] == band, slug


def test_ig_prices_match_the_plan():
    from engine import plan
    html = (SITE / "ig.html").read_text()
    assert plan.PRICES["monthly"]["price"] in html and plan.PRICES["annual"]["price"] in html
    assert f"{plan.FREE_MESSAGES_PER_DAY} AI questions a day" in html
    assert f'{plan.PRICES["trial_days"]} days free' in html


def test_ig_free_reading_is_the_homepage_form():
    def grab(html, a, b):
        return html[html.index(a):html.index(b, html.index(a))]
    home, ig = (SITE / "index.html").read_text(), (SITE / "ig.html").read_text()
    for a, b in (('<form class="free card', "</form>"), ('<section class="freeres"', "</section>")):
        assert grab(home, a, b) == grab(ig, a, b)


# ---- /shop season section ----

def _shop_handles():
    return set(re.findall(r'"handle":"([^"]+)"', (SITE / "shop-catalog.js").read_text()))


def test_shop_gift_edit_names_real_products():
    """The holiday gift edit and sign set must only name products the catalog still sells."""
    html = (SITE / "shop.html").read_text()
    for const in ("GIFT_FIRST", "SIGN_SET"):
        names = re.findall(r'"([^"]+)"', re.search(rf"const {const} = \[(.*?)\];", html).group(1))
        assert names and set(names) <= _shop_handles(), const


def test_shop_collection_images_are_in_the_catalog():
    """A collection tile whose product left the catalog renders with a blank image."""
    html = (SITE / "shop.html").read_text()
    block = re.search(r"const COLLECTIONS = \[(.*?)\];", html, re.S).group(1)
    assert set(re.findall(r'img:"([^"]+)"', block)) <= _shop_handles()
