"""Standalone Fish Audio TTS test — synthesizes a sample line with the
"Jarvis (MCU) J.A.R.V.I.S" voice, saves output.mp3, plays it immediately.

Secret handling: FISH_AUDIO_API_KEY lives in %USERPROFILE%\\.hermes\\.env,
same place as every other key this project uses (GROQ_API_KEY,
GEMINI_API_KEY, ...) -- never hardcoded, never in this repo. No new
dependency for that: this project's own server.py already parses .env files
with nothing but stdlib, this script does the same tiny thing standalone.

Playback uses `miniaudio` (already a project dependency, used elsewhere in
server.py for decoding) instead of pygame -- one less thing to install.

Usage: server/.venv/Scripts/python.exe scripts/tts_jarvis.py ["texto a decir"]
Voice: set FISH_AUDIO_VOICE_ID in the same .env to use a different model;
defaults to the public "Jarvis (MCU) J.A.R.V.I.S" voice (612b878b113047d9a770c069c8b4fdfe),
found via GET https://api.fish.audio/model?title=jarvis.
"""
import os
import sys
from pathlib import Path

import requests

DEFAULT_VOICE_ID = "612b878b113047d9a770c069c8b4fdfe"  # "Jarvis (MCU) J.A.R.V.I.S", public
ENV_PATHS = [Path.home() / ".hermes" / ".env", Path(__file__).resolve().parent.parent / ".env"]
OUTPUT_PATH = Path(__file__).resolve().parent / "output.mp3"


def load_env() -> None:
    for path in ENV_PATHS:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def synthesize(text: str, api_key: str, voice_id: str) -> bytes:
    resp = requests.post(
        "https://api.fish.audio/v1/tts",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "model": "s1",
        },
        json={"text": text, "reference_id": voice_id, "format": "mp3"},
        timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"Fish Audio HTTP {resp.status_code}: {resp.text[:300]}")
    if not resp.content:
        raise RuntimeError("Fish Audio devolvió una respuesta vacía.")
    return resp.content


def play(path: Path) -> None:
    import miniaudio
    info = miniaudio.get_file_info(str(path))
    stream = miniaudio.stream_file(str(path))
    device = miniaudio.PlaybackDevice()
    device.start(stream)
    try:
        import time
        time.sleep(info.duration + 0.3)
    finally:
        device.stop()


def main() -> int:
    load_env()
    api_key = os.environ.get("FISH_AUDIO_API_KEY", "")
    if not api_key:
        print("FISH_AUDIO_API_KEY no está en %USERPROFILE%\\.hermes\\.env", file=sys.stderr)
        return 1
    voice_id = os.environ.get("FISH_AUDIO_VOICE_ID") or DEFAULT_VOICE_ID
    text = " ".join(sys.argv[1:]) or "Hola, Jesús. Soy Jarvis, probando la voz de Fish Audio."

    print(f"Sintetizando con la voz {voice_id}...")
    audio = synthesize(text, api_key, voice_id)
    OUTPUT_PATH.write_bytes(audio)
    print(f"Guardado en {OUTPUT_PATH} ({len(audio)} bytes). Reproduciendo...")
    play(OUTPUT_PATH)
    print("Listo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
