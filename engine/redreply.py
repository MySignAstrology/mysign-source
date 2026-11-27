"""
The red reply, written by a model.

Red used to mean one canned paragraph, the same for a friend's overdose, a
stalker, a lithium question and a 15-year-old: "Are you thinking about hurting
yourself?" plus 988. It came back word for word on every later red message, so
a woman who answered "no im not suicidal?? hes following me" was asked again.
The good RED rules in the system prompt never ran, because red never reached a
model.

Now a dedicated safety prompt (prompts/crisis.md) writes it, given the thread,
the red kind, and her country's lines. The canned text in engine/crisis.py is
what she gets when the model cannot answer in time — the API is down, it
timed out, it refused — and it is chosen by kind and country too.

Never raises. Every failure path ends in text she can act on.
"""
import pathlib

from engine import crisis, models

PROMPT = (pathlib.Path(__file__).parent.parent / "prompts" / "crisis.md").read_text()

# A person in crisis is waiting. The SDK timeout bounds the connect and each
# gap between streamed chunks; no retries, because a retry doubles the wait
# and the fallback is good on its own.
TIMEOUT = 12
MAX_TOKENS = 4000
HISTORY_TURNS = 8


def block(kind, country, figurative=False, episode=None):
    """The data the prompt is told to expect, at the top of her turn."""
    ep = f"flagged red at {episode['last_red']}" if episode and episode.get("last_red") else "none"
    return "\n".join([f"KIND: {kind}", f"FIGURATIVE: {'true' if figurative else 'false'}",
                      f"EPISODE: {ep}", "RESOURCES"]
                     + ["  " + x.strip() for x in crisis.context_lines(country)])


def messages(history, text, kind, country, figurative=False, episode=None):
    """
    The request body's messages. History is the thread before this message,
    oldest first; empty turns are dropped (the API rejects them) and it must
    open on a user turn.
    """
    hist = [{"role": x["role"], "content": x["text"]}
            for x in (history or [])[-HISTORY_TURNS:]
            if x.get("role") in ("user", "assistant") and (x.get("text") or "").strip()]
    while hist and hist[0]["role"] != "user":
        hist.pop(0)
    return hist + [{"role": "user", "content":
                    f"<app>\n{block(kind, country, figurative, episode)}\n</app>\n\n{text}"}]


def _client():
    import anthropic
    return anthropic.Anthropic(timeout=TIMEOUT, max_retries=0)


def stream(history, text, kind, country, figurative=False, episode=None,
           repeat=False, client=None):
    """
    Yields ("delta", str) chunks, then ("done", {"text", "source"}).
    source is "model" or "fallback". repeat: the last assistant turn was
    already a canned red reply, so a fallback must not be the same words.
    """
    canned = crisis.fallback(kind, country, repeat=repeat)
    parts = []
    try:
        client = client or _client()
        fb = models.fallback_kwargs(models.CRISIS)
        beta = getattr(client, "beta", None)
        open_stream = beta.messages.stream if fb and beta else client.messages.stream
        with open_stream(
            model=models.CRISIS, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={"effort": models.CRISIS_EFFORT},
            messages=messages(history, text, kind, country, figurative, episode),
            **(fb if beta else {}),
        ) as s:
            for chunk in s.text_stream:
                parts.append(chunk)
                yield ("delta", chunk)
            final = s.get_final_message()
        if final.stop_reason in ("refusal", "max_tokens") and not "".join(parts).strip():
            raise RuntimeError(f"stopped: {final.stop_reason}")
    except Exception as e:
        from app import log
        log.error("redreply.failed", exc=e, kind=kind, partial=bool(parts))
        if not "".join(parts).strip():
            yield ("delta", canned)
            yield ("done", {"text": canned, "source": "fallback"})
            return
        # Cut off mid-reply: never leave her with half a sentence and no number.
        tail = f"\n\n{crisis.resource_line(country)} In an emergency, call {crisis.emergency(country)}."
        parts.append(tail)
        yield ("delta", tail)
    full = "".join(parts).strip()
    if not full:
        yield ("delta", canned)
        yield ("done", {"text": canned, "source": "fallback"})
        return
    yield ("done", {"text": full, "source": "model"})
