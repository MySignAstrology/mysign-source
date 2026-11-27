"""
Model spend in the logs (engine/meter.py) and browser errors reported to the
server (/api/client-error) — the two things nobody could see before.
"""
import json
import os
import pathlib
import sys
import tempfile
import types

from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-meter-"))

from app import server
from engine import meter


def test_prices_follow_the_model_family():
    usd, tok = meter.cost("claude-sonnet-5-5", {"input_tokens": 1000, "output_tokens": 500,
                                                "cache_read_input_tokens": 2000,
                                                "cache_creation_input_tokens": 400})
    # 1000*2 + 500*10 + 2000*0.2 + 400*2.5 = 8400 per million
    assert abs(usd - 0.0084) < 1e-9 and tok["cache_write"] == 400
    assert meter.price("claude-haiku-5-5") == (0.10, 0.50)
    assert meter.price("something-new") == meter.PRICES["opus"], "unknown is never cheap"


def test_record_logs_one_line_with_the_cost(capsys):
    resp = types.SimpleNamespace(model="claude-haiku-5-5",
                                 usage=types.SimpleNamespace(input_tokens=2000, output_tokens=1000))
    usd = meter.record(resp, via="create")
    line = next(json.loads(x) for x in capsys.readouterr().out.splitlines()
                if '"model.usage"' in x)
    assert line["model"] == "claude-haiku-5-5" and line["via"] == "create"
    assert line["usd"] == round(usd, 6) and line["input"] == 2000 and line["output"] == 1000


def test_metering_never_breaks_a_reply():
    assert meter.record(object()) is None
    assert meter.record(types.SimpleNamespace(model="x", usage="garbage")) is None


def test_install_wraps_every_sdk_entry_point_once():
    """In a clean process: other tests swap SDK methods for fakes."""
    import subprocess
    code = ("import sys; sys.path.insert(0, '.'); from engine import meter;"
            "assert meter.install() and meter.install();"
            "from anthropic.lib.streaming import MessageStream;"
            "from anthropic.resources import messages as m;"
            "fns = (m.Messages.create, m.Messages.parse, MessageStream.get_final_message);"
            "assert all(getattr(f, '_metered', False) for f in fns);"
            "assert not any(getattr(f.__wrapped__, '_metered', False) for f in fns); print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=pathlib.Path(__file__).parent.parent)
    assert out.stdout.strip() == "ok", out.stderr


def test_a_browser_error_is_logged_without_anything_identifying(capsys):
    r = TestClient(server.app).post("/api/client-error", json={
        "msg": "TypeError: x is undefined for maya@example.com",
        "src": "https://mysignapp.com/app?invite=SECRET#frag", "line": 812, "col": 9,
        "view": "vReading"})
    assert r.status_code == 204
    line = next(json.loads(x) for x in capsys.readouterr().out.splitlines()
                if '"client.error"' in x)
    assert line["level"] == "warn", "its own alarm, not the app-error page"
    assert "maya@example.com" not in line["msg"] and "<email>" in line["msg"]
    assert line["src"] == "/app" and line["line"] == 812 and line["view"] == "vReading"


def test_a_browser_error_cannot_flood_with_huge_bodies():
    r = TestClient(server.app).post("/api/client-error", json={"msg": "x" * 5000})
    assert r.status_code == 422


def test_lazy_install_meters_once_the_sdk_is_imported():
    """The app starts without the SDK; the meter attaches on first import."""
    import subprocess
    code = ("import sys; sys.path.insert(0, '.'); from engine import meter; meter.install_lazy();"
            "assert 'anthropic' not in sys.modules;"
            "import anthropic; from anthropic.resources import messages as m;"
            "assert getattr(m.Messages.create, '_metered', False); print('ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         cwd=pathlib.Path(__file__).parent.parent)
    assert out.stdout.strip() == "ok", out.stderr
