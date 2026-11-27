"""
What every model call costs, in the logs, so a CloudWatch alarm can watch it.

AWS bills alarm on AWS spend; the Anthropic bill is separate and, before this,
invisible until the invoice. Every response carries its model and token counts,
so this wraps the SDK's create/parse calls and the streaming final message
once, at import, and writes one `model.usage` line per call with its cost in
US dollars. deploy/mysign_stack.py turns those lines into a ModelSpendUSD
metric, a dashboard graph and a daily-spend alarm.

Prices are first-party API rates per million tokens (engine/models.py):
input / output, with cache reads at 0.1x input and cache writes at 1.25x.
An unknown model is priced as Opus, so a surprise never looks cheap.

Never fatal: metering that raised would take the reply down with it.
"""
import importlib.abc
import importlib.util
import sys

PRICES = {            # USD per million tokens: (input, output)
    "opus": (4.0, 20.0),
    "sonnet": (2.0, 10.0),
    "haiku": (0.10, 0.50),
}
_installed = False


def price(model):
    m = (model or "").lower()
    for key, p in PRICES.items():
        if key in m:
            return p
    return PRICES["opus"]


def cost(model, usage):
    """USD for one response's usage (an object or a dict)."""
    def get(k):
        v = usage.get(k) if isinstance(usage, dict) else getattr(usage, k, None)
        return int(v or 0)
    pin, pout = price(model)
    tin, tout = get("input_tokens"), get("output_tokens")
    cread, cwrite = get("cache_read_input_tokens"), get("cache_creation_input_tokens")
    usd = (tin * pin + tout * pout + cread * pin * 0.1 + cwrite * pin * 1.25) / 1_000_000
    return usd, {"input": tin, "output": tout, "cache_read": cread, "cache_write": cwrite}


def record(resp, via="call"):
    """Log one response's cost. Silently does nothing for anything unexpected."""
    try:
        usage = getattr(resp, "usage", None)
        model = getattr(resp, "model", None)
        if usage is None or not model:
            return None
        usd, tokens = cost(model, usage)
        if not (tokens["input"] or tokens["output"]):
            return None                    # not a real response's usage
        from app import log
        log.emit("model.usage", model=model, via=via, usd=round(usd, 6), **tokens)
        from engine import costs
        costs.add(usd)                     # her month and everyone's day (engine/costs.py)
        return usd
    except Exception:
        return None


def _wrap(owner, name, via):
    fn = getattr(owner, name, None)
    if fn is None or getattr(fn, "_metered", False):
        return

    def metered(self, *a, **kw):
        out = fn(self, *a, **kw)
        record(out, via)
        return out
    metered._metered = True
    metered.__wrapped__ = fn
    setattr(owner, name, metered)


def install():
    """Meter every Anthropic call in this process. Idempotent."""
    global _installed
    if _installed:
        return True
    try:
        from anthropic.resources import messages as m
        for name in ("create", "parse"):
            _wrap(m.Messages, name, name)
        try:
            from anthropic.resources.beta import messages as bm
            for name in ("create", "parse"):
                _wrap(bm.Messages, name, "beta." + name)
        except Exception:
            pass
        # Streams: the final message carries the usage.
        try:
            from anthropic.lib.streaming import MessageStream
            _wrap(MessageStream, "get_final_message", "stream")
        except Exception:
            pass
        try:
            from anthropic.lib.streaming import BetaMessageStream
            _wrap(BetaMessageStream, "get_final_message", "beta.stream")
        except Exception:
            pass
        _installed = True
    except Exception:
        return False
    return True


class _OnImport(importlib.abc.MetaPathFinder):
    """Installs the meter the moment `anthropic` is first imported, so the
    app's cold start doesn't pay for an SDK only some routes use
    (tests/test_coldstart.py)."""

    def find_spec(self, name, path=None, target=None):
        if name != "anthropic":
            return None
        sys.meta_path.remove(self)
        spec = importlib.util.find_spec(name)
        if spec is None or spec.loader is None:
            return spec
        run = spec.loader.exec_module

        def exec_module(module):
            run(module)
            install()
        spec.loader.exec_module = exec_module
        return spec


def install_lazy():
    """install() now if the SDK is already loaded, else on its first import."""
    if "anthropic" in sys.modules:
        return install()
    if not any(isinstance(f, _OnImport) for f in sys.meta_path):
        sys.meta_path.insert(0, _OnImport())
    return True
