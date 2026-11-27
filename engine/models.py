"""
Which model answers which call, in one place.

Every call used to be Opus 5.5. At ~$0.037 a message that made a typical Plus
subscriber cost $11–13 a month against a $9.99 price — every paying customer
lost money, and a free user cost ~$3.80 for nothing (docs/data-inventory.md §7).

The split now follows what each call needs:

- The conversation is the product, so Plus talks to Sonnet 5.5. Free talks to
  Haiku 5.5: the same prompt, the same tools, the same sky — a lighter voice,
  at about a hundredth of the cost, which is what makes a free tier survivable.
- Calls whose output she never reads as prose (the safety classifier, the
  nightly memory extraction, the suggested-reply chips, the sky-style push)
  run on Haiku 5.5.
- One-off writing she keeps (the natal reading, compatibility narration) and
  the weekly pattern proposals — which reason over her whole history — run on
  Sonnet 5.5.

- The red reply — someone may be in danger — runs on Opus 5.5 whatever her
  plan. It is the turn where judgment matters most and the rarest one: a few
  hundred tokens on maybe one message in a thousand, so the strongest model
  costs pennies a month in total. Low effort keeps the wait short; a canned
  reply covers a timeout (engine/redreply.py).

Each can be overridden by environment variable without a deploy of code, e.g.
MYSIGN_MODEL_CHAT_PLUS=claude-opus-5-5 to put Plus back on Opus. Prices are
first-party API rates per million tokens (input / output), as of Oct 2026:
Opus 5.5 $4/$20 · Sonnet 5.5 $2/$10 · Haiku 5.5 $0.10/$0.50.
"""
import os

OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"


def _env(name, default):
    return os.environ.get(f"MYSIGN_MODEL_{name}", default)


CHAT_PLUS = _env("CHAT_PLUS", SONNET)
CHAT_FREE = _env("CHAT_FREE", HAIKU)
CLASSIFIER = _env("CLASSIFIER", HAIKU)
EXTRACT = _env("EXTRACT", HAIKU)
PATTERNS = _env("PATTERNS", SONNET)
READING = _env("READING", SONNET)
PUSH = _env("PUSH", HAIKU)
CHIPS = _env("CHIPS", HAIKU)
CRISIS = _env("CRISIS", OPUS)
CRISIS_EFFORT = os.environ.get("MYSIGN_CRISIS_EFFORT", "low")

# Chat runs at medium effort: enough thought to pick the one transit that
# matters and tie it to her week, not so much that a reply waits on it.
CHAT_EFFORT = os.environ.get("MYSIGN_CHAT_EFFORT", "medium")
# An everyday (green) turn thinks less before it speaks. Thinking is most of the
# wait before the first word — the audit measured 9.2s to first text at p50 —
# and an ordinary "what's my moon sign" does not need medium. Amber turns, where
# the reply must hold a line, keep medium. (audit MEDIUM-8)
CHAT_EFFORT_GREEN = os.environ.get("MYSIGN_CHAT_EFFORT_GREEN", "low")


def chat_effort(tier):
    return CHAT_EFFORT_GREEN if tier == "green" else CHAT_EFFORT


def chat_model(tier):
    """The conversation model for her plan. Unknown tiers get the free model."""
    return CHAT_PLUS if tier == "plus" else CHAT_FREE


def fallback_kwargs(model):
    """
    Server-side refusal fallback for the Sonnet and Opus routes: if a safety
    classifier declines a turn, the API re-runs it on a model chosen by refusal
    category inside the same call, instead of the turn simply stopping. Haiku
    has no server-side fallback, so it gets nothing.
    """
    if model in (SONNET, OPUS):
        return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    return {}
