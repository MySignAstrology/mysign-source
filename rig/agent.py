"""Draft one outbound message. Never sends — drafting and sending are separate
by design, because every message in this study passes a human first."""
import pathlib

from engine import context

MODEL = "claude-opus-5-5"
_client = None
SYSTEM = (pathlib.Path(__file__).parent.parent / "prompts" / "system-prompt.md").read_text()


def client():
    """Lazy, so the module imports and the queue stays inspectable without creds."""
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def draft(user_id, natal_chart, sky, checkin, history, tier="green",
          surface="chat", today=None):
    block, verdict = context.build(user_id, natal_chart, sky, checkin,
                                   today=today, tier=tier, surface=surface)
    msgs = list(history) + [{"role": "user", "content":
                             f"<context>\n{block}\n</context>\n\n{checkin.get('text','')}"}]
    r = client().messages.create(
        model=MODEL,
        max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=msgs,
    )
    text = "".join(b.text for b in r.content if b.type == "text").strip()
    return {"text": text, "context_block": block, "verdict": verdict}
