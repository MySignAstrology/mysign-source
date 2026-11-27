"""
Suggested replies: two or three things she might tap instead of typing.

Written in her voice, not ours — "wait, is he a scorpio" rather than "Tell me
more about compatibility" — because chips that read like menu items make the
whole chat feel automated. One reacts to the question the reply asked (if it
asked one), one goes deeper, one changes direction. None of them may say
something about her life she hasn't said: a tapped chip is stored as hers.

Haiku writes them from the last exchange only: a fraction of a cent, and
nothing about her history leaves for this call beyond what she just said.
Without a key a small fixed set stands in so the UI behaves the same in demo
mode; if the call fails there are no chips, and the failure is logged.
"""
import os
import re
from typing import List

from pydantic import BaseModel, Field

from engine import models

MAX_CHIPS = 3
MAX_CHARS = 48
MAX_WORDS = 7


class Chips(BaseModel):
    replies: List[str] = Field(description="Two or three short replies she might tap next.")


PROMPT = """She just said:
{user}

The app replied:
{reply}
{people}
Write three short things SHE might tap to reply next. Rules:
- Her voice: lowercase, casual, the way she texts. Under 7 words each. No emoji.
  {lang}
- Reactions and questions only. Never state a fact about her life, her day or
  another person that she has not said above: no events, no names, no feelings
  she didn't report. A chip she taps becomes something she told us, so a made-up
  "he cancelled on me" is a false memory.
- If the reply asked her something, the first chip reacts to that question in
  words specific to it (a feeling about it, or a question back about it) — not
  an invented answer, and not a stock phrase that would fit any reply.
- One chip asks for more on what the reply said.
- One asks about her chart, a saved person, or timing.
- Use names, not he/she, for anyone whose pronoun isn't in the text above.
- Nothing negative she didn't raise (no "should i just skip it", "should i cancel").
- Never "tell me more", "thanks", or anything a menu would say."""

# She is leaving. Chips under "goodnight" invite her to stay, which is the
# hook the prompt's goodbye rule forbids (audit MEDIUM-3).
_BYE = re.compile(r"\b(good ?night|night night|nite|night\s*[.!x]*$|bye|goodbye|gtg|gotta go|ttyl|see you|"
                  r"talk (?:to you )?tomorrow|going to bed|off to bed|wish me luck|"
                  r"buenas noches|adi[oó]s|chao|hasta mañana|me voy)\b", re.I)
_REPLY_BYE = re.compile(r"\b(good ?night|sleep well|night\b|good luck|go get it|bye)\b", re.I)


def is_goodbye(user_text, reply):
    """She is signing off and the reply let her go."""
    short = len((user_text or "").strip()) <= 80
    return bool(short and _BYE.search(user_text or "")
                and not (reply or "").rstrip().endswith("?"))


def _clean(items):
    out, seen = [], set()
    for c in items:
        c = " ".join(str(c).split()).strip(" \"'")
        # "Under 7 words" was a rule the model broke often; it is kept here.
        if 2 <= len(c) <= MAX_CHARS and len(c.split()) <= MAX_WORDS and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out[:MAX_CHIPS]


def heuristic(reply):
    """No model (demo mode): a fixed set that still fits the shape of the
    reply. Questions, never claims, so nothing tapped is a made-up fact."""
    if reply.rstrip().endswith("?"):
        return ["hm, why do you ask", "what does my chart say"]
    return ["wait, why though", "when does this end"]


def _people(uid):
    """Who she has saved, with what they are to her: so 'jess' is not 'he'
    and a hens party is read as one."""
    if not uid:
        return ""
    from engine.store import load
    rows = load(uid, "connections", []) or []
    named = [f"{r.get('name')} ({r.get('relation')})" if r.get("relation") else r.get("name")
             for r in rows[:6] if r.get("name")]
    return f"\nPeople she has saved: {', '.join(named)}.\n" if named else ""


LANG = {"es": "Write them in Spanish, the way she writes.",
        "en": "Write them in the language she wrote in."}


def suggest(user_text, reply, client=None, uid=None, lang=None):
    if not reply.strip() or is_goodbye(user_text, reply):
        return []
    if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
        return heuristic(reply)
    try:
        if client is None:
            import anthropic
            client = anthropic.Anthropic(timeout=15, max_retries=1)
        r = client.messages.parse(
            model=models.CHIPS, max_tokens=1000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": PROMPT.format(
                user=user_text[:1000], reply=reply[:2000], people=_people(uid),
                lang=LANG.get(lang or "en", LANG["en"]))}],
            output_format=Chips,
        )
        return _clean(r.parsed_output.replies) if r.parsed_output else []
    except Exception as e:
        # No chips beats canned ones: the fixed set read as a bot, and it was
        # swallowed silently (audit MEDIUM-3). Logged so a failure rate shows.
        from app import log
        log.error("chips.failed", exc=e, uid=uid)
        return []
