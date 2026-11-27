"""
Design the psychic room's voices with ElevenLabs Voice Design.

Each reader in app/data/psychic_voices.json has a "design" description. This
asks Voice Design for previews of it, saves them so you can listen, and keeps
one as a voice in our ElevenLabs account, writing its id back to the file.

    ELEVENLABS_API_KEY=... python tools/design_psychic_voices.py --preview
        Makes previews for every reader not designed yet and saves the MP3s
        to data-dev/psychic-previews/. Creates nothing.
    ELEVENLABS_API_KEY=... python tools/design_psychic_voices.py --keep lune=2 silas=1
        Keeps preview 2 for Madame Lune and 1 for Old Silas (numbers as saved).
    ELEVENLABS_API_KEY=... python tools/design_psychic_voices.py --all
        Previews and keeps the first preview for every reader not designed yet.

Add --force to redo readers that are already designed.
"""
import argparse
import base64
import json
import os
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOICES = ROOT / "app" / "data" / "psychic_voices.json"
OUT = ROOT / "data-dev" / "psychic-previews"
API = "https://api.elevenlabs.io/v1"
# Spoken by every preview, so they can be compared like for like.
SAMPLE = ("Come in, sit down. I've been looking at your sky this morning, and I can tell you "
          "this much already: the restlessness you feel has a reason, and it has an end. "
          "The Moon is crossing the part of your chart that rules how you think and talk, "
          "so give yourself a little room today. Now, tell me what's really on your mind.")


def headers():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("Set ELEVENLABS_API_KEY first.")
    return {"xi-api-key": key}


def previews(v):
    r = requests.post(f"{API}/text-to-voice/design", headers=headers(), timeout=120, json={
        "voice_description": v["design"], "model_id": "eleven_multilingual_ttv_v2", "text": SAMPLE})
    r.raise_for_status()
    out = []
    OUT.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(r.json()["previews"], 1):
        path = OUT / f"{v['key']}-{i}.mp3"
        path.write_bytes(base64.b64decode(p["audio_base_64"]))
        (OUT / f"{v['key']}-{i}.id").write_text(p["generated_voice_id"])
        out.append((path, p["generated_voice_id"]))
    return out


def keep(v, generated_id):
    r = requests.post(f"{API}/text-to-voice", headers=headers(), timeout=60, json={
        "voice_name": f"My Sign · {v['label']}", "voice_description": v["design"],
        "generated_voice_id": generated_id})
    r.raise_for_status()
    return r.json()["voice_id"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--keep", nargs="*", default=[], metavar="KEY=N")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    data = json.loads(VOICES.read_text())
    todo = [v for v in data["voices"] if a.force or not v.get("designed")]
    picks = dict(k.split("=", 1) for k in a.keep)

    if a.preview or a.all:
        for v in todo:
            print(f"{v['label']}:")
            for path, _ in previews(v):
                print(f"  {path.relative_to(ROOT)}")
            if a.all:
                picks.setdefault(v["key"], "1")

    changed = False
    for v in data["voices"]:
        if v["key"] not in picks:
            continue
        idfile = OUT / f"{v['key']}-{picks[v['key']]}.id"
        if not idfile.exists():
            sys.exit(f"No preview {picks[v['key']]} for {v['key']}; run --preview first.")
        v["voice_id"], v["designed"] = keep(v, idfile.read_text().strip()), True
        changed = True
        print(f"Kept {v['label']} as {v['voice_id']}")
    if changed:
        VOICES.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"Updated {VOICES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
