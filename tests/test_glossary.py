"""
Plain words: every astrology term the app uses is explained for a beginner.
"""
import os
import pathlib
import re
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-gloss-"))

from app import server
from engine import compat, glossary, placement, profile, reading


def test_every_term_says_what_it_is_and_what_it_means_for_her():
    for t in glossary.terms():
        assert t["match"] and t["what"] and t["you"], t["term"]
        assert len(t["what"]) <= 220 and len(t["you"]) <= 220, t["term"]
        assert t["what"].rstrip("'’\"")[-1] in ".!?" and t["you"].rstrip("'’\"")[-1] in ".!?", t["term"]


def test_no_match_word_belongs_to_two_terms():
    seen = {}
    for t in glossary.terms():
        for m in t["match"]:
            assert m.lower() not in seen, f"{m!r} in {seen.get(m.lower())} and {t['term']}"
            seen[m.lower()] = t["term"]


def test_everyday_words_are_not_underlined():
    """'meeting', 'water', 'rules', 'terms' and friends would underline plain
    English — and 'Terms of Service' — all over the app."""
    words = {m.lower() for t in glossary.terms() for m in t["match"]}
    for plain in ("meeting", "meets", "water", "air", "fire", "earth", "rules", "terms",
                  "fixed", "opposite", "mode", "pattern", "patterns", "spark", "ease"):
        assert plain not in words, plain


def test_the_words_the_app_actually_prints_are_covered():
    """Every technical word the engine writes into readings has a card."""
    words = {m.lower() for t in glossary.terms() for m in t["match"]}
    for needed in ("rising sign", "transit", "retrograde", "square", "trine", "sextile",
                   "conjunction", "opposition", "house", "decan", "term of", "ruler chain",
                   "composite", "synastry", "progressed", "orb", "midheaven", "north node",
                   "chiron", "eclipse", "new moon", "full moon", "lot of fortune"):
        assert needed in words, needed


def test_every_model_prompt_asks_for_beginner_words():
    for p in (profile.PROMPT, reading.OPENING, placement.PROMPT, compat.NARRATE):
        assert glossary.PLAIN_RULE in p
    system = (pathlib.Path(__file__).parent.parent / "prompts" / "system-prompt.md").read_text()
    assert "complete beginner" in system


def test_the_route_is_public_and_cacheable():
    r = TestClient(server.app).get("/api/glossary")
    assert r.status_code == 200
    assert "max-age" in r.headers["cache-control"]
    terms = r.json()["terms"]
    assert any(t["term"] == "rising sign" for t in terms)
    assert all(re.fullmatch(r"[^<>]+", t["what"]) for t in terms), "no markup in the copy"
