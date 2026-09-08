"""Persistent local XTTS-v2 TTS service — keeps the model resident in memory
(the ~9s load cost is paid once, at startup, not per sentence) so server.py
can call it like any other TTS backend over HTTP.

Voice: reference.wav (the Fish Audio "Jarvis (MCU)" sample) with XTTS-v2
defaults -- confirmed by the user as the best-sounding combination tried
(2026-09-07), after comparing against a higher-temperature variant, F5-TTS,
and the user's own voice as reference.

Isolated on purpose: runs in server/xtts/.venv (heavy ML deps: torch,
coqui-tts) so the main server/.venv never needs them, matching the FASE 6
instruction to keep the TTS integration point isolated when touched.

Two endpoints:
  /tts        whole-file WAV (used by /api/speak -- fine to wait, not a live turn).
  /tts_stream raw PCM16 mono 24kHz, chunked as generated (used by the live
              voice loop) -- first chunk lands in ~1.7s on this CPU instead
              of waiting ~12-16s for the whole sentence (measured live,
              2026-09-07: inference_stream's first chunk at 1.74s vs
              tts_to_file's ~12s to return anything at all).

Usage: server/xtts/.venv/Scripts/python.exe tts_service.py
Listens on 127.0.0.1:8790 (loopback only, same as every other internal
service in this project -- server.py is the only caller).
"""
import os
import tempfile
from pathlib import Path

import numpy as np
import torch

os.environ.setdefault("COQUI_TOS_AGREED", "1")  # already agreed once interactively; see docs

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

REFERENCE_WAV = Path(__file__).resolve().parent / "reference.wav"
PORT = 8790

# XTTS-v2 samples stochastically by default (do_sample=True in Xtts.inference)
# -- the exact same text/reference/params sounds noticeably different on
# every call otherwise (found live: the same sentence went from 7.3s to 9.2s
# between two "identical" generations). Reset before every request so the
# same text always comes out the same way -- no more gambling per sentence.
SEED = 2024  # elegida por el usuario entre 5 candidatas, 2026-09-07

app = FastAPI()
_tts = None  # loaded once, on startup
_gpt_cond_latent = None  # reference-audio conditioning, computed once (not per request)
_speaker_embedding = None


class TTSRequest(BaseModel):
    text: str


@app.on_event("startup")
def _load_model() -> None:
    global _tts, _gpt_cond_latent, _speaker_embedding
    from TTS.api import TTS
    torch.set_num_threads(6)
    print("Cargando XTTS-v2 (optimizado para Ryzen 6 cores fijos para evitar saturación de CPU)...", flush=True)
    _tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
    print("Calculando condicionamiento de la voz de referencia...", flush=True)
    _gpt_cond_latent, _speaker_embedding = _tts.synthesizer.tts_model.get_conditioning_latents(
        audio_path=str(REFERENCE_WAV),
    )
    print("XTTS-v2 listo.", flush=True)


@app.get("/health")
def health() -> dict:
    return {"ok": _tts is not None}


@app.post("/tts")
def synthesize(req: TTSRequest) -> Response:
    if _tts is None:
        return JSONResponse({"error": "modelo aún cargando"}, status_code=503)
    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": "texto vacío"}, status_code=400)
    torch.manual_seed(SEED)
    with tempfile.TemporaryDirectory(prefix="xtts-") as tmpdir:
        out_path = Path(tmpdir) / "out.wav"
        _tts.tts_to_file(text=text, speaker_wav=str(REFERENCE_WAV), language="es", file_path=str(out_path))
        data = out_path.read_bytes()
    return Response(content=data, media_type="audio/wav")


@app.post("/tts_stream")
def synthesize_stream(req: TTSRequest):
    if _tts is None:
        return JSONResponse({"error": "modelo aún cargando"}, status_code=503)
    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": "texto vacío"}, status_code=400)

    def gen():
        torch.manual_seed(SEED)
        model = _tts.synthesizer.tts_model
        for chunk in model.inference_stream(text, "es", _gpt_cond_latent, _speaker_embedding):
            pcm16 = (chunk.squeeze().detach().cpu().numpy() * 32767.0).astype(np.int16).tobytes()
            if pcm16:
                yield pcm16

    return StreamingResponse(gen(), media_type="application/octet-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
