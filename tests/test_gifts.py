"""The gift guide: built from what can be bought, and reachable."""
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

import gift_guide

GIFTS = ROOT / "site" / "gifts"


def test_committed_pages_match_the_catalog():
    for name, text in gift_guide.build().items():
        assert (GIFTS / name).read_text() == text, f"{name}: run python tools/gift_guide.py"


def test_every_link_is_an_available_variant_in_that_sign():
    products = {p["handle"]: p for p in gift_guide.catalog()}
    for sign in gift_guide.SIGNS:
        links = re.findall(r'/shop/products/([^?"]+)\?variant=(\d+)', (GIFTS / f"{sign}.html").read_text())
        assert links, sign
        for handle, variant in links:
            v = next(x for x in products[handle]["variants"] if str(x["id"]) == variant)
            assert v["available"] and v["signs"] == sign


def test_a_silver_only_pendant_is_left_out():
    pendant = {"handle": "zodiac-pendant-necklace", "vendor": "R", "images": ["x"], "variants": [
        {"id": 1, "option1": "Silver", "price": 25.0, "available": True, "signs": "scorpio", "image": "x"}]}
    assert gift_guide.picks("scorpio", [pendant]) == []
    pendant["variants"][0]["option1"] = "Gold"
    assert [c["variant"] for c in gift_guide.picks("scorpio", [pendant])] == [1]


def test_no_supplier_claims_or_invented_ratings():
    for page in GIFTS.glob("*.html"):
        text = page.read_text().lower()
        for bad in ("best seller", "bestseller", "strong seller", "aggregaterating", "guaranteed", "arrives by"):
            assert bad not in text, (page.name, bad)


def test_structured_data_parses():
    for page in GIFTS.glob("*.html"):
        block = re.search(r'<script type="application/ld\+json">(.*?)</script>', page.read_text(), re.S).group(1)
        assert json.loads(block)["itemListElement"]


def test_routes():
    from fastapi.testclient import TestClient

    from app import server
    c = TestClient(server.app)
    assert "Zodiac gifts by sign" in c.get("/gifts").text
    assert "Gifts for a Scorpio" in c.get("/gifts/scorpio").text
    assert c.get("/gifts/notasign").status_code == 404
    assert c.get("/gifts/..%2Fserver.py").status_code == 404


def test_sign_dates_match_the_shop():
    shop = (ROOT / "site" / "shop.html").read_text()
    for sign, s in gift_guide.SIGNS.items():
        m = re.search(rf'id:"{sign}".*?from:\[(\d+),(\d+)\]', shop)
        assert (int(m.group(1)), int(m.group(2))) == s["start"], sign


def test_grammar_before_vowel_signs():
    assert "Gifts for an Aries" in (GIFTS / "aries.html").read_text()
    assert "Gifts for an Aquarius" in (GIFTS / "aquarius.html").read_text()
    assert "for a Aries" not in (GIFTS / "aries.html").read_text()
