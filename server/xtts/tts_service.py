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
import io
import os
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

# DirectML compatibility: disable inference_mode so DML doesn't fail on version_counter
torch.inference_mode = torch.no_grad

try:
    import torch_directml
    _dml_available = True
except ImportError:
    _dml_available = False

os.environ.setdefault("COQUI_TOS_AGREED", "1")  # already agreed once interactively; see docs

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

REFERENCE_WAV = Path(__file__).resolve().parent / "reference.wav"
PORT = 8790

# XTTS-v2 samples stochastically by default (do_sample=True in Xtts.inference)
# -- the exact same text/reference/params sounds noticeably different on
# every call otherwise. Reset before every request so the same text always
# comes out the same way.
SEED = 2024  # elegida por el usuario entre 5 candidatas, 2026-09-07

app = FastAPI()
_tts = None  # loaded once, on startup
_gpt_cond_latent = None  # reference-audio conditioning, computed once (not per request)
_speaker_embedding = None
_device = "cpu"


class TTSRequest(BaseModel):
    text: str


@app.on_event("startup")
def _load_model() -> None:
    global _tts, _gpt_cond_latent, _speaker_embedding, _device
    from TTS.api import TTS
    torch.set_num_threads(6)
    print("Iniciando XTTS-v2...", flush=True)
    _tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")

    print("Calculando condicionamiento de la voz de referencia...", flush=True)
    model = _tts.synthesizer.tts_model
    _gpt_cond_latent, _speaker_embedding = model.get_conditioning_latents(
        audio_path=str(REFERENCE_WAV),
    )

    if _dml_available:
        try:
            dml = torch_directml.device()
            print(f"Acelerando XTTS-v2 en GPU AMD Radeon ({dml})...", flush=True)
            _tts.to(dml)
            _gpt_cond_latent = _gpt_cond_latent.to(dml)
            _speaker_embedding = _speaker_embedding.to(dml)
            _device = str(dml)
            print("XTTS-v2 listo en GPU DirectML.", flush=True)
            return
        except Exception as e:
            print(f"Error cargando en DirectML ({e}), usando fallback CPU...", flush=True)

    _tts.to("cpu")
    _device = "cpu"
    print("XTTS-v2 listo en CPU.", flush=True)


@app.get("/health")
def health() -> dict:
    return {"ok": _tts is not None, "device": _device}


@app.post("/tts")
def synthesize(req: TTSRequest) -> Response:
    if _tts is None:
        return JSONResponse({"error": "modelo aún cargando"}, status_code=503)
    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": "texto vacío"}, status_code=400)
    torch.manual_seed(SEED)
    try:
        with torch.no_grad():
            model = _tts.synthesizer.tts_model
            out = model.inference(
                text=text,
                language="es",
                gpt_cond_latent=_gpt_cond_latent,
                speaker_embedding=_speaker_embedding,
            )
            wav = out["wav"]
            bio = io.BytesIO()
            sf.write(bio, wav, 24000, format="WAV")
            data = bio.getvalue()
        return Response(content=data, media_type="audio/wav")
    except Exception as exc:
        print(f"Error en /tts: {exc}", flush=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/tts_stream")
def synthesize_stream(req: TTSRequest):
    if _tts is None:
        return JSONResponse({"error": "modelo aún cargando"}, status_code=503)
    text = (req.text or "").strip()
    if not text:
        return JSONResponse({"error": "texto vacío"}, status_code=400)

    def gen():
        torch.manual_seed(SEED)
        with torch.no_grad():
            model = _tts.synthesizer.tts_model
            for chunk in model.inference_stream(text, "es", _gpt_cond_latent, _speaker_embedding):
                pcm16 = (chunk.squeeze().detach().cpu().numpy() * 32767.0).astype(np.int16).tobytes()
                if pcm16:
                    yield pcm16

    return StreamingResponse(gen(), media_type="application/octet-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)

