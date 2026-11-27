"""
The psychic's voice: ElevenLabs text-to-speech for the reader in the room.

The words are never hers to choose. The room asks /api/message exactly as the
chat does; this module only turns what the reply already said into sound. So
the endpoint speaks nothing but:
  - text taken from one of her latest replies in her own thread, or
  - the room's two fixed lines (the greeting and the voice preview),
and each user has a daily character budget, because every character costs.

Voices: app/data/psychic_voices.json. Each reader has a description written
for ElevenLabs Voice Design (tools/design_psychic_voices.py turns it into a
voice of ours and writes its id back) and a stock ElevenLabs voice to use
until then. No ELEVENLABS_API_KEY: the room falls back to the device's voices.
"""
import json
import os
import pathlib
import re
import time

API = "https://api.elevenlabs.io/v1"
MODEL = os.environ.get("MYSIGN_TTS_MODEL", "eleven_flash_v2_5")   # lowest latency
MAX_CHARS = 1200                       # one request: a reply is a few sentences
# Characters a day. Free answers are capped at three, so this is generous there.
DAILY = {"free": 6000, "plus": 30000}
VOICES_FILE = pathlib.Path(__file__).parent / "data" / "psychic_voices.json"

# The room's own lines. The name is hers to choose (max 40 characters).
FIXED = [re.compile(r"Come in\. I'm [^\n]{1,40}\. Ask me what's on your mind\."),
         re.compile(r"I am [^\n]{1,40}\. The stars are listening\.")]


def enabled():
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def voices():
    try:
        return json.loads(VOICES_FILE.read_text())["voices"]
    except (OSError, ValueError, KeyError):
        return []


def public_voices():
    """What the room may show: names and blurbs, never ids or settings."""
    return [{"key": v["key"], "label": v["label"], "blurb": v.get("blurb", "")} for v in voices()]


def voice(key):
    return next((v for v in voices() if v["key"] == key), None)


def _norm(t):
    # The room shows replies through plain(): no markdown marks, no bullets.
    t = re.sub(r"[*_#`•]", "", t or "")
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip().lower()


def allowed(text, recent_replies):
    """Is this something the reader actually said?"""
    t = (text or "").strip()
    if not t or len(t) > MAX_CHARS:
        return False
    if any(rx.fullmatch(t) for rx in FIXED):
        return True
    n = _norm(t)
    return bool(n) and any(n in _norm(r) for r in recent_replies)


def claim(uid, tier, day, chars):
    """Spend today's budget. False when it is used up."""
    from engine import store
    limit = DAILY.get(tier, DAILY["free"])
    got = store.incr(uid, f"tts-{day}", "n", by=chars, limit=limit,
                     ttl=int(time.time()) + 3 * 86400)
    return got is not None


def settings(v, rate=1.0):
    s = dict(v.get("settings") or {})
    s.setdefault("stability", 0.5)
    s.setdefault("similarity_boost", 0.75)
    s.setdefault("style", 0.3)
    # ElevenLabs accepts 0.7 to 1.2.
    s["speed"] = max(0.7, min(1.2, float(rate)))
    return s


async def open_stream(v, text, rate=1.0):
    """
    Start the audio and check ElevenLabs said yes before anything is sent to
    her, so a refusal is a clean error, not half an MP3. Returns the upstream
    response (read it with aiter_bytes) and a closer for when it is done.
    """
    import httpx
    client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5))
    req = client.build_request(
        "POST", f"{API}/text-to-speech/{v['voice_id']}/stream",
        params={"output_format": "mp3_44100_128"},
        json={"text": text, "model_id": MODEL, "voice_settings": settings(v, rate)},
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "accept": "audio/mpeg"})
    try:
        r = await client.send(req, stream=True)
    except Exception:
        await client.aclose()
        raise

    async def close():
        await r.aclose()
        await client.aclose()
    if r.status_code != 200:
        await close()
        raise RuntimeError(f"elevenlabs {r.status_code}")
    return r, close
