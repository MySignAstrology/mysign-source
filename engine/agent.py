"""
The agent loop.

Streams a reply, and when the model asks for a fact it does not have, pauses,
computes it, and resumes — so a question about Thursday gets Thursday's sky
instead of a refusal.

Yields events rather than returning a string, because the pause while a tool
runs is the most persuasive second in the product. It is the difference between
a confident guess and a consultation, and the UI should show it.

    ("delta", "text chunk")
    ("tool", {"name": ..., "input": {...}})
    ("tool_result", {"name": ..., "ok": bool, "summary": "..."})
    ("done", {"text": ..., "grounding": ..., "tools_used": [...], "stopped": ...})

`grounding` is the original briefing plus every tool result, serialized. The
eval's grounded check runs against it: a fact the model looked up is legitimately
citable even though it was not in the opening block, and without this every
tool-assisted answer would score as a hallucination.
"""
import json

from engine import models

MODEL = models.CHAT_PLUS    # the default; the server passes the model for her plan
MAX_ITERATIONS = 6          # a loop that can call tools forever is a bill
MAX_TOKENS = 8000


def _summarize(name, result):
    """One line for the UI. Never the whole payload."""
    if isinstance(result, dict) and result.get("error"):
        return result["error"][:120]
    if name == "get_sky":
        return f"{result.get('weekday','')} {result.get('date','')}"
    if name == "find_next":
        return (result.get("date", "nothing within a year")
                if result.get("found") else "nothing within a year")
    if name == "get_retrogrades":
        nxt = next((p for p in result.get("periods", [])), None)
        return f"{result.get('planet', '')} " + (f"from {nxt['starts'].get('date')}" if nxt else "none soon")
    if name == "get_lunations":
        return f"{len(result.get('lunations', []))} lunations"
    if name == "search_memory":
        return f"{result.get('found', 0)} episodes"
    if name == "get_synastry":
        return "compared charts"
    if name == "get_person_day":
        return (result.get("person") or {}).get("name") or "done"
    if name == "compare_connections":
        return (f"ranked {result.get('people', 0)} people"
                if result.get("ok") else "not enough saved")
    return "done"


def _quiet(name, result):
    """A lookup of someone she never saved. Nothing went wrong and there is
    nothing to show, so the UI drops the step instead of "nothing there"; the
    model still gets the error and her saved names."""
    return (name in ("get_person_day", "search_memory") and isinstance(result, dict)
            and str(result.get("error", "")).startswith("No saved person"))


PRELOAD_ACK = "Looked that up before your message."


def _preloaded(block, message, preload):
    """
    Facts the server already knows she needs, as a real tool exchange.

    A person named in the message used to arrive as a JSON block inside the
    context. The model could cite it, but nothing it cited could be traced to a
    tool result, so the grounded check had to trust the system context. Here
    the same lookup is a tool_use / tool_result pair the model reads exactly as
    if it had asked.

    The exchange is CLOSED — tool_use, tool_result, a short assistant line — and
    her message follows as a fresh turn. Left open, the synthetic tool_use would
    be the last assistant turn of a live tool loop, and with thinking on the API
    requires that turn to begin with a thinking block we cannot fabricate.
    """
    calls = [{"type": "tool_use", "id": f"preload_{i}", "name": p["name"],
              "input": p["input"]} for i, p in enumerate(preload)]
    results = [{"type": "tool_result", "tool_use_id": c["id"],
                "content": json.dumps(p["out"], default=str)}
               for c, p in zip(calls, preload)]
    return [
        {"role": "user", "content": f"<context>\n{block}\n</context>"},
        {"role": "assistant", "content": calls},
        {"role": "user", "content": results},
        {"role": "assistant", "content": PRELOAD_ACK},
        {"role": "user", "content": message},
    ]


def _cache_point(messages):
    """
    Mark the end of the conversation so far as a cache breakpoint.

    The system prompt was cached; the sixteen messages of history after it were
    re-read from scratch every turn, and inside a tool loop every round re-read
    the round before. Caching the prefix up to the newest stable message cuts
    the time to the first word on long threads. Returns a copy; at most one
    breakpoint per call besides the system prompt's, well inside the API's four.
    """
    if not messages:
        return messages
    out = [dict(m) for m in messages]
    for m in out:
        if isinstance(m.get("content"), list):
            m["content"] = [{k: v for k, v in b.items() if k != "cache_control"}
                            if isinstance(b, dict) else b for b in m["content"]]
    last = out[-1]
    content = last["content"]
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if content and isinstance(content[-1], dict):
        content = list(content[:-1]) + [{**content[-1], "cache_control": {"type": "ephemeral"}}]
        last["content"] = content
    return out


def run(client, system, block, message, history, uid, nat, tools_module,
        model=MODEL, max_iterations=MAX_ITERATIONS, today=None, preload=None, effort=None):
    """
    Generator over the whole turn, including any tool round-trips.

    The caller owns the client so this stays testable without a network.
    `preload`: [{"name", "input", "out"}] — lookups the server ran from
    tools_module before the turn (see _preloaded). They are reported, counted
    and grounded exactly like calls the model makes itself.
    """
    grounding = [block]
    tools_used = []
    text_parts = []
    stopped = "end_turn"

    if preload:
        messages = list(history) + _preloaded(block, message, preload)
        for p in preload:
            failed = isinstance(p["out"], dict) and bool(p["out"].get("error"))
            yield ("tool", {"name": p["name"], "input": p["input"]})
            tools_used.append({"name": p["name"], "input": p["input"], "ok": not failed,
                               **({"quiet": True} if _quiet(p["name"], p["out"]) else {})})
            grounding.append(f"[{p['name']}] {json.dumps(p['out'], default=str)}")
            yield ("ground", grounding[-1])
            yield ("tool_result", {"name": p["name"], "ok": not failed,
                                   "summary": _summarize(p["name"], p["out"]),
                                   **({"quiet": True} if _quiet(p["name"], p["out"]) else {})})
    else:
        messages = list(history) + [
            {"role": "user", "content": f"<context>\n{block}\n</context>\n\n{message}"}]

    # The Sonnet route carries a server-side refusal fallback, which lives on
    # the beta surface; every other model uses the plain one.
    fb = models.fallback_kwargs(model)
    beta = getattr(client, "beta", None)
    if not (fb and beta):
        fb = {}                      # a client without the beta surface: plain route
    open_stream = beta.messages.stream if fb else client.messages.stream

    # The history is the stable prefix on the first round (this turn's
    # context block and any preloaded lookup are new every turn, so caching
    # past them would only ever write); on later rounds, everything up to the
    # last tool result.
    stable = len(history)
    for _ in range(max_iterations):
        sent = (_cache_point(messages[:stable]) + messages[stable:]) if stable > 0 else messages
        with open_stream(
            model=model,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort or models.CHAT_EFFORT},
            tools=tools_module.DEFINITIONS,
            messages=sent,
            **fb,
        ) as stream:
            for chunk in stream.text_stream:
                text_parts.append(chunk)
                yield ("delta", chunk)
            final = stream.get_final_message()

        stopped = final.stop_reason
        if stopped != "tool_use":
            break

        calls = [b for b in final.content if b.type == "tool_use"]
        messages.append({"role": "assistant", "content": final.content})

        # Every result goes back in ONE user message. Splitting them across
        # several silently trains the model out of making parallel calls.
        results = []
        for call in calls:
            # Inputs are parsed JSON, never string-matched — escaping varies.
            args = call.input if isinstance(call.input, dict) else json.loads(call.input)
            yield ("tool", {"name": call.name, "input": args})

            out = tools_module.run(call.name, uid, nat, args, today=today)
            failed = isinstance(out, dict) and bool(out.get("error"))
            tools_used.append({"name": call.name, "input": args, "ok": not failed,
                               **({"quiet": True} if _quiet(call.name, out) else {})})
            grounding.append(f"[{call.name}] {json.dumps(out, default=str)}")
            # For engine/guard.py: what the rest of the reply may now cite.
            yield ("ground", grounding[-1])

            yield ("tool_result", {"name": call.name, "ok": not failed,
                                   "summary": _summarize(call.name, out),
                                   **({"quiet": True} if _quiet(call.name, out) else {})})
            results.append({"type": "tool_result", "tool_use_id": call.id,
                            "content": json.dumps(out, default=str),
                            **({"is_error": True} if failed else {})})
        messages.append({"role": "user", "content": results})
        stable = len(messages)
    else:
        # Ran out of iterations with the model still asking for tools.
        stopped = "max_iterations"

    yield ("done", {
        "text": "".join(text_parts).strip(),
        "grounding": "\n".join(grounding),
        "tools_used": tools_used,
        "stopped": stopped,
    })
