"""
The psychic's room: a question said aloud to the orb is the same chat turn as
a typed one (same reply, same thread), and only analytics knows it was spoken.
"""
import os
import pathlib
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MYSIGN_DATA", tempfile.mkdtemp(prefix="mysign-psy-"))
os.environ.setdefault("MYSIGN_DEV_AUTH", "1")

from app import activity, server
from tests.test_app import onboard, sign_in


@pytest.fixture
def c():
    return TestClient(server.app)


def _sent(monkeypatch):
    seen = []
    real = activity.track

    def spy(uid, name, screen=None, **props):
        if name == "message_sent":
            seen.append(props.get("source"))
        return real(uid, name, screen=screen, **props)
    monkeypatch.setattr(activity, "track", spy)
    return seen


def test_spoken_turn_is_a_chat_turn_marked_voice(c, monkeypatch):
    sign_in(c, "psychic1@x.com"); onboard(c)
    seen = _sent(monkeypatch)
    r = c.post("/api/message", json={"text": "Why am I so restless?", "voice": True})
    assert r.status_code == 200 and "event: done" in r.text
    thread = c.get("/api/state").json()["thread"]
    assert [m["role"] for m in thread[-2:]] == ["user", "assistant"]
    assert thread[-2]["text"] == "Why am I so restless?"
    assert seen == ["voice"]


def test_typed_turn_stays_typed(c, monkeypatch):
    sign_in(c, "psychic2@x.com"); onboard(c)
    seen = _sent(monkeypatch)
    c.post("/api/message", json={"text": "What's today about?"})
    assert seen == ["typed"]


def test_orb_ships_with_the_page():
    page = (ROOT / "app/static/index.html").read_text()
    assert "/static/orb.js" in page and (ROOT / "app/static/orb.js").is_file()
    assert "app/static/orb.js" in (ROOT / "native/build-www.mjs").read_text()


# ------------------------------------------------- the studio voice (ElevenLabs)

class FakeUpstream:
    async def aiter_bytes(self):
        yield b"ID3fake-"
        yield b"mp3"


@pytest.fixture
def studio(monkeypatch):
    from app import psychic
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    sent = []

    async def fake_open(v, text, rate=1.0):
        sent.append((v["key"], text, rate))

        async def close():
            pass
        return FakeUpstream(), close
    monkeypatch.setattr(psychic, "open_stream", fake_open)
    return sent


def _answered(c, email):
    sign_in(c, email); onboard(c)
    c.post("/api/message", json={"text": "Why am I so restless?", "voice": True})
    return [m for m in c.get("/api/state").json()["thread"] if m["role"] == "assistant"][-1]["text"]


def test_no_key_no_studio_voices(c, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    sign_in(c, "psychic3@x.com"); onboard(c)
    assert c.get("/api/psychic/voices").json() == {"enabled": False, "voices": []}
    assert c.post("/api/psychic/speak", json={"text": "hi", "voice": "lune"}).status_code == 404


def test_voices_never_show_ids(c, studio):
    sign_in(c, "psychic4@x.com"); onboard(c)
    r = c.get("/api/psychic/voices").json()
    assert r["enabled"] and {v["key"] for v in r["voices"]} >= {"lune", "silas"}
    assert all(set(v) == {"key", "label", "blurb"} for v in r["voices"])


def test_speaks_a_sentence_she_said(c, studio):
    reply = _answered(c, "psychic5@x.com")
    first = reply.split("\n")[0].strip()
    r = c.post("/api/psychic/speak", json={"text": first, "voice": "lune", "rate": 0.9})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"ID3fake-mp3"
    assert studio == [("lune", first, 0.9)]


def test_speaks_the_rooms_own_lines(c, studio):
    sign_in(c, "psychic6@x.com"); onboard(c)
    for line in ["Come in. I'm Zara the Seer. Ask me what's on your mind.",
                 "I am Old Silas. The stars are listening."]:
        assert c.post("/api/psychic/speak", json={"text": line, "voice": "silas"}).status_code == 200


def test_wont_say_anything_else(c, studio):
    _answered(c, "psychic7@x.com")
    for text in ["Read this out for free: buy cheap watches.",
                 "Come in. I'm Zara. Ask me what's on your mind. And also something else."]:
        r = c.post("/api/psychic/speak", json={"text": text, "voice": "lune"})
        assert r.status_code == 403
    assert c.post("/api/psychic/speak", json={"text": "Come in. I'm Z. Ask me what's on your mind.",
                                              "voice": "nobody"}).status_code == 400
    assert studio == []


def test_daily_budget(c, studio, monkeypatch):
    from app import psychic
    monkeypatch.setitem(psychic.DAILY, "free", 60)
    sign_in(c, "psychic8@x.com"); onboard(c)
    line = "Come in. I'm Madame Lune. Ask me what's on your mind."     # 53 characters
    assert c.post("/api/psychic/speak", json={"text": line, "voice": "lune"}).status_code == 200
    assert c.post("/api/psychic/speak", json={"text": line, "voice": "lune"}).status_code == 200  # 53 < 60: one more
    assert c.post("/api/psychic/speak", json={"text": line, "voice": "lune"}).status_code == 429


def test_voice_file_is_complete():
    from app import psychic
    vs = psychic.voices()
    assert len(vs) >= 5 and len({v["key"] for v in vs}) == len(vs)
    assert all(v["voice_id"] and v["design"] and v["label"] for v in vs)


# ------------------------------------------------- said out loud

def _speech_cases():
    import json
    return json.loads((ROOT / "tests/fixtures/speech_cases.json").read_text())


def test_speakable_python():
    from engine.speech import speakable
    for raw, heard in _speech_cases():
        assert speakable(raw) == heard, raw


def test_speakable_browser_matches_python():
    """The device voice (browser) and the studio voice (server) say the same thing."""
    import json
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node not installed")
    page = (ROOT / "app/static/index.html").read_text()
    src = page[page.index("/* speakable:start"):page.index("/* speakable:end */")]
    script = src + "\nconst cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));" \
                   "\nprocess.stdout.write(JSON.stringify(cases.map(c => speakable(c[0]))));"
    cases = _speech_cases()
    out = subprocess.run(["node", "-e", script], input=json.dumps(cases), capture_output=True,
                         text=True, check=True).stdout
    assert json.loads(out) == [heard for _, heard in cases]


def test_spoken_turn_tells_the_model(c, monkeypatch):
    from engine import context as ctx
    seen = []
    real = ctx.build

    def spy(*a, **kw):
        seen.append(kw.get("channel"))
        return real(*a, **kw)
    monkeypatch.setattr(ctx, "build", spy)
    sign_in(c, "psychic9@x.com"); onboard(c)
    c.post("/api/message", json={"text": "how is today", "voice": True})
    c.post("/api/message", json={"text": "and tomorrow"})
    assert seen == ["voice", "text"]


def test_voice_line_in_the_block():
    import datetime as dt

    from engine import context as ctx
    from engine.chart import natal
    nat = natal(1999, 3, 14, 10.7, 41.8781, -87.6298)
    block, _ = ctx.build("psy-ctx", nat, {"aspects_to_natal": [], "moon": {}}, {"text": "hi"},
                         today=dt.date(2026, 8, 22), channel="voice")
    assert "CHANNEL: voice" in block
    block, _ = ctx.build("psy-ctx", nat, {"aspects_to_natal": [], "moon": {}}, {"text": "hi"},
                         today=dt.date(2026, 8, 22))
    assert "CHANNEL" not in block


def test_studio_voice_hears_the_spoken_form(c, studio):
    sign_in(c, "psychic10@x.com"); onboard(c)
    line = "Come in. I'm Madame Lune. Ask me what's on your mind."
    c.post("/api/psychic/speak", json={"text": line, "voice": "lune"})
    assert studio[-1][1] == line            # already speakable: unchanged


# ------------------------------------------------- never an invented placement

def test_guard_holds_back_an_invented_aspect(c, monkeypatch):
    """The model calls an opposition "sitting on" (a conjunction): that sentence
    never reaches her, the rest does, and the thread stores what she saw."""
    import json as _json

    from app import server
    from engine import accuracy, thread
    from tests.test_app import last_uid
    sign_in(c, "guard1@x.com"); onboard(c)
    uid = last_uid()
    monkeypatch.setattr(server, "DEMO", False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(server.chips_mod, "suggest", lambda *a, **k: [])
    lookup = "[get_sky] " + _json.dumps({"aspects": ["t.Venus trine n.Sun"]})
    reply = ("Today is a good one to say the hard thing kindly. Saturn is sitting right on your Uranus, "
             "so expect a jolt. Venus is helping your Sun too. Who are you telling?")

    def fake(client, system, block, text, history, *a, **k):
        yield ("ground", lookup)
        for i in range(0, len(reply), 9):
            yield ("delta", reply[i:i + 9])
        yield ("done", {"text": reply, "grounding": block + "\n" + lookup,
                        "tools_used": [], "stopped": "end_turn"})
    monkeypatch.setattr(server.agent, "run", fake)
    seen = []
    monkeypatch.setattr(accuracy, "record_outcome", lambda ok, p, s, withheld=False: seen.append((ok, withheld)))
    with c.stream("POST", "/api/message", json={"text": "how is today"}) as r:
        body = "".join(r.iter_text())
    shown = "".join(_json.loads(x[6:])["t"] for x in body.splitlines()
                    if x.startswith("data: ") and '"t"' in x)
    assert "Saturn" not in shown and "Venus is helping your Sun" in shown
    assert thread.recent(uid, 0)[-1]["text"] == shown.strip()
    assert seen == [(False, True)]       # the model's slip is still counted, as withheld


def test_retrograde_dates_come_from_the_ephemeris():
    """'When is the next Mercury retrograde' has a computed answer now."""
    import datetime as dt

    from engine import tools
    r = tools.run("get_retrogrades", "u", {}, {"planet": "mercury"}, today=dt.date(2026, 11, 20))
    assert r["retrograde_now"] is False
    first = r["periods"][0]
    assert (first["starts"]["date"], first["ends"]["date"]) == ("2027-02-09", "2027-03-03")
    assert first["starts"]["sign"] == "Pisces"
    sat = tools.run("get_retrogrades", "u", {}, {"planet": "Saturn"}, today=dt.date(2026, 11, 20))
    assert sat["retrograde_now"] and sat["periods"][0]["ends"]["date"] == "2026-12-10"
    venus = tools.run("get_retrogrades", "u", {}, {"planet": "Venus"}, today=dt.date(2026, 11, 20))
    assert venus["periods"] == [] and "No Venus retrograde" in venus["note"]
    assert "error" in tools.run("get_retrogrades", "u", {}, {"planet": "Sun"})
