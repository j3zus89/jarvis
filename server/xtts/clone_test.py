"""One-off test: clone the Jarvis voice from reference.wav and speak a line
with it, using XTTS-v2 (free, local, no API key, no payment).

Isolated venv (server/xtts/.venv, Python 3.11) so PyTorch/coqui-tts never
touch the main server's dependencies -- this is a heavy, experimental stack
kept separate on purpose.

reference.wav was converted from the user's downloaded MP3 sample via
miniaudio (already a server/.venv dependency) -- XTTS wants a clean WAV.

Usage: .venv/Scripts/python.exe clone_test.py ["texto a decir"]
"""
import sys
import time
from pathlib import Path

DEFAULT_REFERENCE_WAV = Path(__file__).resolve().parent / "reference.wav"
OUTPUT_DIR = Path(__file__).resolve().parent

# name -> (temperature, repetition_penalty, speed). Higher temperature = more
# prosody variation (less flat/robotic); lower repetition_penalty lets
# natural micro-repeats/pauses through instead of forcing every phrase into
# the same shape.
VARIANTS = {
    "default": (0.75, 10.0, 1.0),
    "expresivo": (0.9, 3.0, 0.97),
}


def main() -> int:
    from TTS.api import TTS

    if not REFERENCE_WAV.exists():
        print(f"Falta {REFERENCE_WAV}", file=sys.stderr)
        return 1

    args = sys.argv[1:]
    variant = "default"
    if args and args[0] in VARIANTS:
        variant, args = args[0], args[1:]
    text = " ".join(args) or "Hola Jesus, soy Jarvis. Esta es una prueba de clonacion de voz local, sin pagar nada."
    temperature, repetition_penalty, speed = VARIANTS[variant]
    output_path = OUTPUT_DIR / f"clone_output_{variant}.wav"

    print("Cargando XTTS-v2 (queda en cache local tras la primera vez)...")
    t0 = time.time()
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
    print(f"Modelo cargado en {time.time() - t0:.1f}s")

    print(f"Generando variante '{variant}' (temperature={temperature}, repetition_penalty={repetition_penalty}, speed={speed})...")
    t0 = time.time()
    tts.tts_to_file(
        text=text,
        speaker_wav=str(REFERENCE_WAV),
        language="es",
        file_path=str(output_path),
        temperature=temperature,
        repetition_penalty=repetition_penalty,
        speed=speed,
    )
    elapsed = time.time() - t0
    print(f"Generado en {elapsed:.1f}s -> {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
