#!/usr/bin/env python3
"""Hermes LAN voice pipeline server (v3 — sessions, stop, approvals, partials).

WebSocket protocol (client → server):
  {"type":"start", "sample_rate":16000, "format":"pcm_s16le", "channels":1,
   "conversation": "jarvis-main"?}          begin a turn (mid-turn = barge-in)
  <binary int16 16 kHz mono PCM chunks>
  {"type":"stop"}                            end of speech, process turn
  {"type":"stop_run"}                        halt the running agent turn
  {"type":"approval_decision", "run_id":..., "approval_id":..., "decision":"allow"|"deny"}

Server → client JSON events:
  status, transcript, partial_transcript, agent_status{thinking|tool_use|speaking},
  run_started{run_id}, approval_request{...}, error, done{timing}
plus binary 16 kHz mono int16 PCM TTS audio.

Brain: Hermes Agent API server via the Sessions API (/api/sessions/{id}/chat/stream),
which provides persistent conversation memory, run ids (stoppable), tool events,
and approval events. Falls back to direct Anthropic ("basic mode") if unreachable.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import base64
import inspect
import io
import random
import uuid
from email.message import EmailMessage
import html
import json
import os
import re
import shutil
import tempfile
import threading
import time
import subprocess
import urllib.parse
import webbrowser
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Iterator

import edge_tts
import miniaudio
import requests
import uvicorn
import yaml
import numpy as np
from anthropic import Anthropic
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from RealtimeSTT import AudioToTextRecorder

from ai_router import AIRouter, AllProvidersFailedError

try:
    import psutil
except ImportError:  # machines panel degrades gracefully
    psutil = None

ROOT = Path(__file__).resolve().parent


def _load_env_files() -> None:
    """Loads ~/.hermes/.env and server/.env into os.environ if not present."""
    for p in [Path.home() / ".hermes" / ".env", ROOT / ".env"]:
        if not p.is_file():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'\"")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


_load_env_files()

CONFIG_PATH = ROOT / "config" / "server.yaml"
LOG_PATH = ROOT / "logs" / "latency.jsonl"
STATE_PATH = ROOT / "logs" / "hermes_sessions.json"
USAGE_PATH = ROOT / "logs" / "usage_stats.json"
_USAGE_LOCK = threading.Lock()


def _call_fast_llm(prompt: str, max_tokens: int = 300, temperature: float = 0.1) -> str:
    """Ultra-low latency LLM helper for intent extraction / classification.
    Tier 1: Groq qwen/qwen3.8-27b (~0.3-0.5s)
    Tier 2: Gemini 2.5 Flash (~0.8-1.2s)
    Tier 3: Local llama-server (127.0.0.1:8081) with 6s timeout (offline fallback only)
    """
    for k in [os.environ.get("GROQ_API_KEY"), os.environ.get("GROQ_API_KEY_2"), os.environ.get("GROQ_API_KEY_3")]:
        if not k:
            continue
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {k}"},
                json={
                    "model": "qwen/qwen3.8-27b",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=4,
            )
            if resp.ok:
                c = resp.json()["choices"][0]["message"]["content"].strip()
                if c:
                    return c
        except Exception:
            pass

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gemini_key:
        try:
            resp = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                headers={"Authorization": f"Bearer {gemini_key}"},
                json={
                    "model": "gemini-2.5-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=4,
            )
            if resp.ok:
                c = resp.json()["choices"][0]["message"]["content"].strip()
                if c:
                    return c
        except Exception:
            pass

    try:
        resp = requests.post(
            "http://127.0.0.1:8081/v1/chat/completions",
            json={
                "model": "gpt-oss:20b",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": min(max_tokens, 150),
                "temperature": temperature,
                "reasoning_effort": "low",
            },
            timeout=6,
        )
        if resp.ok:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass

    return ""



def _today() -> str:
    return time.strftime("%Y-%m-%d")


def record_usage(llm_in: int = 0, llm_out: int = 0, turns: int = 0, tts_chars: int = 0) -> None:
    """Accumulate token/character usage into logs/usage_stats.json (total + per-day)."""
    with _USAGE_LOCK:
        try:
            data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {"total": {}, "days": {}}
        day = data["days"].setdefault(_today(), {})
        for bucket in (data["total"], day):
            bucket["llm_in"] = bucket.get("llm_in", 0) + llm_in
            bucket["llm_out"] = bucket.get("llm_out", 0) + llm_out
            bucket["turns"] = bucket.get("turns", 0) + turns
            bucket["tts_chars"] = bucket.get("tts_chars", 0) + tts_chars
        # keep last 60 days
        for k in sorted(data["days"])[:-60]:
            del data["days"][k]
        USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
        USAGE_PATH.write_text(json.dumps(data), encoding="utf-8")


def read_usage() -> dict:
    with _USAGE_LOCK:
        try:
            data = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {"total": {}, "days": {}}
    return {"total": data.get("total", {}), "today": data.get("days", {}).get(_today(), {})}

ENV_PATHS = [Path.home() / ".hermes" / ".env", ROOT / ".env"]

HUD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_youtube",
            "description": "Abre YouTube o YouTube Music en este PC. Música, un vídeo, un artista, un tema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Qué buscar. Vacío = abrir YouTube Music.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_browser",
            "description": "Abre el navegador con una web o una búsqueda de Google.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url_or_query": {"type": "string", "description": "https://... o texto a buscar"}
                },
                "required": ["url_or_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_folder",
            "description": "Abre una carpeta o un archivo en el Explorador de Windows. Busca por nombre (CV, CW, cassette, Descargas, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Nombre de carpeta o archivo"}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_cv",
            "description": "Abre y lee el CV de Jesús en este PC para usarlo después (postular, presentarse).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_job_email",
            "description": "Abre Gmail con un borrador de postulación escrito como Jesús. NO envía solo. Él pulsa enviar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "to_email": {"type": "string"},
                    "job_title": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Guarda un hecho sobre Jesús para mañana. Úsalo cuando cuente planes, gustos, trabajo, familia o cualquier dato suyo. No hace falta que diga recuerda.",
            "parameters": {
                "type": "object",
                "properties": {"fact": {"type": "string"}},
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_whatsapp",
            "description": (
                "Envía un WhatsApp de verdad por Hermes. "
                "Si solo hay apodo y no número, NO digas que lo enviaste: pide el móvil con prefijo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Apodo agendado o número E.164 (+34...)",
                    },
                    "message": {"type": "string"},
                },
                "required": ["to", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_gmail",
            "description": (
                "Lee la bandeja de Gmail de Jesús con OAuth ya vinculado. "
                "Nunca pidas contraseña ni que abra el navegador."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
HUD_CHAT_TOOLS = [
    "Gmail — bandeja real (OAuth)",
    "WhatsApp — envío real (hace falta número)",
    "YouTube / navegador",
    "Carpetas y CV de este PC",
    "Borrador Gmail de empleo",
    "Memoria (remember)",
]
SENTENCE_RE = re.compile(r"(.+?[.!?])(?=\s|$)", re.DOTALL)
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
CODEBLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
# Secret-shaped strings are never sent to cloud TTS (privacy filter):
SECRET_RES = [
    re.compile(r"\b(?:api[_-]?key|secret|password|passwd|token|bearer|authorization)\b\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b(?:sk|pk|key|tok|ghp|xox[abp])[-_][A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\b[A-Za-z0-9+/_\-]{36,}\b"),          # long opaque blobs (keys, JWT segments)
    re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL),
]


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


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))



# Cadena de efectos "voz de IA futurista" elegida por Jesús a partir de
# pruebas A/B (compresión + graves + phaser sutil + eco largo = sensación
# de espacio/sala, sin sonar a robot de vocoder barato).
_VOICE_FX_CHAIN = (
    "acompressor=threshold=-18dB:ratio=3:attack=5:release=80,"
    "bass=g=6:f=130:w=0.7,"
    "aphaser=in_gain=0.9:out_gain=0.85:delay=3.5:decay=0.45:speed=0.4,"
    "aecho=0.9:0.85:40|65:0.35|0.2,"
    "equalizer=f=3200:t=q:w=1.5:g=2.5"
)


def _apply_voice_fx(mp3_bytes: bytes) -> bytes:
    """Pasa el MP3 crudo de Edge TTS por la cadena de efectos de voz futurista.
    Si ffmpeg no está disponible o falla, se devuelve el audio sin procesar
    en vez de romper la respuesta de voz."""
    tmpdir = tempfile.mkdtemp(prefix="jarvis-tts-fx-")
    try:
        src = Path(tmpdir) / "in.mp3"
        dst = Path(tmpdir) / "out.mp3"
        src.write_bytes(mp3_bytes)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-af", _VOICE_FX_CHAIN, "-codec:a", "mp3", str(dst)],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0 and dst.exists():
            data = dst.read_bytes()
            if data:
                return data
        print(f"voice fx failed, using raw audio: {result.stderr[-300:]!r}", flush=True)
    except Exception as exc:
        print(f"voice fx error, using raw audio: {exc!r}", flush=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return mp3_bytes


def _jitter_signed(value: str, unit: str, spread: int) -> str:
    """Nudge a signed rate/pitch string ("-18%", "-22Hz") by a small random
    amount so every sentence isn't spoken at the exact same cadence — a fixed
    rate/pitch on every utterance is what makes TTS read as a machine."""
    try:
        num = int(value.rstrip("%Hz"))
    except ValueError:
        num = 0
    num += random.randint(-spread, spread)
    return f"{num:+d}{unit}"


_XTTS_SERVICE_URL = "http://127.0.0.1:8790"


async def _synthesize_xtts_mp3(text: str) -> bytes:
    """Local XTTS-v2 voice clone (server/xtts/tts_service.py, must be running
    separately -- see docs/AI_ROUTER.md). Free, offline, no per-sentence
    network call. Returns the service's WAV re-encoded to MP3 via ffmpeg (same
    binary _apply_voice_fx already depends on) so callers of
    synthesize_edge_mp3 don't need to know which engine answered."""
    resp = await asyncio.to_thread(
        requests.post, f"{_XTTS_SERVICE_URL}/tts", json={"text": text}, timeout=60,
    )
    if not resp.ok:
        raise RuntimeError(f"XTTS service HTTP {resp.status_code}: {resp.text[:200]}")
    wav_bytes = resp.content
    if not wav_bytes:
        raise RuntimeError("XTTS service returned empty audio")
    tmpdir = tempfile.mkdtemp(prefix="jarvis-xtts-")
    try:
        wav_path = Path(tmpdir) / "in.wav"
        mp3_path = Path(tmpdir) / "out.mp3"
        wav_path.write_bytes(wav_bytes)
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path), "-codec:a", "mp3", str(mp3_path)],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0 and mp3_path.exists():
            data = mp3_path.read_bytes()
            if data:
                return data
        print(f"xtts wav->mp3 failed, using raw wav: {result.stderr[-300:]!r}", flush=True)
        return wav_bytes
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _stream_xtts_pcm(text: str) -> AsyncIterator[bytes]:
    """Real streaming, not just chunked-after-the-fact: consumes
    /tts_stream's raw PCM16 chunks as XTTS generates them (first chunk
    ~1.7s in, vs ~12-16s to wait for a whole sentence via /tts). Bridges the
    blocking `requests` stream into an async generator via a thread + queue
    -- asyncio has no native way to iterate a sync HTTP stream without
    blocking the event loop."""
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def worker() -> None:
        try:
            with requests.post(
                f"{_XTTS_SERVICE_URL}/tts_stream", json={"text": text}, stream=True, timeout=60,
            ) as resp:
                if not resp.ok:
                    raise RuntimeError(f"XTTS stream HTTP {resp.status_code}: {resp.text[:200]}")
                for chunk in resp.iter_content(chunk_size=4096):
                    if chunk:
                        loop.call_soon_threadsafe(q.put_nowait, chunk)
        except Exception as exc:  # surfaced to the async side below, not swallowed
            loop.call_soon_threadsafe(q.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        item = await q.get()
        if item is None:
            return
        if isinstance(item, Exception):
            raise item
        yield item


async def synthesize_edge_mp3(text: str) -> bytes:
    """Elena (es-AR) MP3 via Microsoft Edge TTS -- unless voice.provider is
    "xtts" in config, in which case this delegates to the local voice-clone
    service instead. Kept as one entry point (not two call sites branching)
    so both /api/speak and the live voice turn stay untouched."""
    voice = CFG.get("voice") or {}
    if str(voice.get("provider") or "") == "xtts":
        try:
            return await _synthesize_xtts_mp3(text)
        except Exception as exc:
            print(f"[TTS Fallback] XTTS falló ({exc}), usando fallback inmediato Edge-TTS...", flush=True)
    voice_id = str(voice.get("voice_id") or "es-ES-AlvaroNeural")
    rate = _jitter_signed(str(voice.get("rate") or "+0%"), "%", 4)
    pitch = str(voice.get("pitch") or "+0Hz")
    if pitch.endswith("%"):
        pitch = pitch[:-1] + "Hz"
    pitch = _jitter_signed(pitch, "Hz", 6)
    tmpdir = tempfile.mkdtemp(prefix="jarvis-tts-")
    mp3_path = Path(tmpdir) / "speech.mp3"
    try:
        communicate = edge_tts.Communicate(text, voice_id, rate=rate, pitch=pitch)
        await communicate.save(str(mp3_path))
        data = mp3_path.read_bytes()
        if not data:
            raise RuntimeError("Edge TTS returned empty audio")
        if bool(voice.get("futuristic_fx", True)):
            data = await asyncio.to_thread(_apply_voice_fx, data)
        return data
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _mp3_to_pcm16(mp3_path: Path, sample_rate: int = 24000) -> bytes:
    """Decode an Edge TTS MP3 file to mono signed-16 PCM at sample_rate."""
    decoded = miniaudio.decode_file(
        str(mp3_path),
        nchannels=1,
        sample_rate=sample_rate,
        output_format=miniaudio.SampleFormat.SIGNED16,
    )
    samples = decoded.samples
    if isinstance(samples, (bytes, bytearray)):
        return bytes(samples)
    return bytes(memoryview(samples))


@dataclass
class TurnTiming:
    turn_id: int
    audio_start_monotonic: float | None = None
    end_of_speech_monotonic: float | None = None
    stt_start_monotonic: float | None = None
    stt_final_monotonic: float | None = None
    llm_start_monotonic: float | None = None
    llm_first_token_monotonic: float | None = None
    first_sentence_monotonic: float | None = None
    tts_request_start_monotonic: float | None = None
    first_tts_audio_byte_monotonic: float | None = None
    total_done_monotonic: float | None = None
    transcript: str = ""
    response_text: str = ""
    stt_model: str = ""
    llm_provider: str = ""
    llm_model: str = ""
    tts_model: str = ""
    voice_id: str = ""
    run_id: str = ""
    interrupted: bool = False
    tools_used: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        eos = self.end_of_speech_monotonic
        return {
            "turn_id": self.turn_id,
            "transcript": self.transcript,
            "response_text": self.response_text,
            "stt_model": self.stt_model,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "tts_model": self.tts_model,
            "voice_id": self.voice_id,
            "run_id": self.run_id,
            "interrupted": self.interrupted,
            "tools_used": self.tools_used,
            "stt_finalize_seconds": self._delta(self.stt_start_monotonic, self.stt_final_monotonic),
            "llm_time_to_first_token_seconds": self._delta(self.llm_start_monotonic, self.llm_first_token_monotonic),
            "time_to_first_tts_audio_byte_seconds": self._delta(self.tts_request_start_monotonic, self.first_tts_audio_byte_monotonic),
            "end_of_speech_to_first_audio_seconds": self._delta(eos, self.first_tts_audio_byte_monotonic),
            "total_turn_seconds": self._delta(eos, self.total_done_monotonic),
            "errors": self.errors,
        }

    @staticmethod
    def _delta(start: float | None, end: float | None) -> float | None:
        if start is None or end is None:
            return None
        return round(end - start, 4)


# ===================================================================== Hermes


class HermesAPI:
    """Thin client for the Hermes Agent API server (sessions, runs, approvals)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg.get("hermes") or {}

    @property
    def base(self) -> str:
        return (self.cfg.get("base_url") or "http://127.0.0.1:8642").rstrip("/")

    def headers(self) -> dict:
        key = os.environ.get(self.cfg.get("api_key_env", "API_SERVER_KEY"), "")
        if not key:
            raise RuntimeError("Hermes API key not found in environment")
        h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if self.cfg.get("session_key"):
            h["X-Hermes-Session-Key"] = self.cfg["session_key"]
        return h

    # ---- persistent named sessions ----
    def _load_state(self) -> dict:
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state), encoding="utf-8")

    def get_session_id(self, name: str, force_new: bool = False) -> str:
        state = self._load_state()
        sid = state.get(name)
        if sid and not force_new:
            probe = requests.get(f"{self.base}/api/sessions/{sid}", headers=self.headers(), timeout=15)
            if probe.ok:
                return sid
        # Do not send title=name: Hermes rejects duplicate titles with HTTP 400
        # ("Title already in use"). A unique title keeps the HUD mapping local.
        title = f"{name}-{int(time.time())}"
        r = requests.post(f"{self.base}/api/sessions", headers=self.headers(),
                          json={"title": title}, timeout=15)
        if r.status_code == 400:
            reused = self._session_id_by_title(name)
            if reused:
                state[name] = reused
                self._save_state(state)
                return reused
        r.raise_for_status()
        data = r.json()
        sid = (data.get("session") or data).get("id")
        state[name] = sid
        self._save_state(state)
        print(f"Created Hermes session '{name}' -> {sid}", flush=True)
        return sid

    def _session_id_by_title(self, title: str) -> str | None:
        try:
            r = requests.get(f"{self.base}/api/sessions", headers=self.headers(),
                             params={"limit": 50}, timeout=15)
            if not r.ok:
                return None
            data = r.json()
            rows = data.get("sessions") or data.get("data") or []
            if isinstance(data, list):
                rows = data
            for row in rows:
                sess = row.get("session") if isinstance(row, dict) and "session" in row else row
                if not isinstance(sess, dict):
                    continue
                t = str(sess.get("title") or "")
                if t == title or t.startswith(title + "-"):
                    sid = sess.get("id")
                    if sid:
                        return str(sid)
        except Exception:
            return None
        return None

    def stop_run(self, run_id: str) -> dict:
        r = requests.post(f"{self.base}/v1/runs/{run_id}/stop", headers=self.headers(), timeout=15)
        return {"status_code": r.status_code, "body": r.text[:300]}

    def post_approval(self, run_id: str, body: dict) -> dict:
        r = requests.post(f"{self.base}/v1/runs/{run_id}/approval", headers=self.headers(),
                          json=body, timeout=15)
        return {"status_code": r.status_code, "body": r.text[:300]}

    def chat_stream_events(self, session_id: str, input_text: str, timeout: float) -> Iterator[tuple[str, str]]:
        """Yield ("run"|"text"|"tool"|"approval"|"final", value) from a session turn."""
        # Short identity line only. A long manifesto made the 7B repeat it;
        # without any prefix it forgot Jesús and asked who the user was.
        prefix = (self.cfg.get("instructions") or "").strip()
        payload_text = f"{prefix}\n\n{input_text}" if prefix else input_text
        resp = requests.post(
            f"{self.base}/api/sessions/{session_id}/chat/stream",
            headers={**self.headers(), "Accept": "text/event-stream"},
            json={"input": payload_text}, stream=True, timeout=(10, timeout),
        )
        if resp.status_code >= 400:
            resp.close()
            raise RuntimeError(f"Hermes session chat HTTP {resp.status_code}: {resp.text[:300]}")
        resp.encoding = "utf-8"  # SSE has no charset header; requests would assume latin-1 (mojibake)
        try:
            yield from self._parse_sse(resp)
        finally:
            resp.close()  # leaked FDs killed the server once (launchd limit is tiny)

    @staticmethod
    def _parse_sse(resp) -> Iterator[tuple[str, str]]:
        event_name = ""
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            if raw.startswith("event: "):
                event_name = raw[7:].strip()
                continue
            if not raw.startswith("data: "):
                continue
            data_text = raw[6:].strip()
            try:
                data = json.loads(data_text)
            except json.JSONDecodeError:
                continue
            ev = event_name or data.get("event", "")
            if ev == "run.started":
                yield ("run", data.get("run_id") or "")
            elif ev == "assistant.delta":
                d = data.get("delta") or ""
                if d:
                    yield ("text", d)
            elif ev == "tool.started":
                name = data.get("tool_name") or "tool"
                if name.startswith("_"):
                    continue  # internal pseudo-tools like _thinking
                yield ("tool", json.dumps({"name": name, "preview": (data.get("preview") or "")[:200]}))
            elif "approval" in ev:
                yield ("approval", json.dumps(data)[:2000])
            elif ev == "assistant.completed":
                yield ("final", json.dumps({
                    "content": data.get("content") or "",
                    "interrupted": bool(data.get("interrupted")),
                }))
            elif ev in ("run.failed", "error"):
                raise RuntimeError(f"Hermes stream error: {data_text[:300]}")
            elif ev == "run.completed":
                usage = data.get("usage") or {}
                if usage:
                    record_usage(
                        llm_in=int(usage.get("input_tokens") or 0),
                        llm_out=int(usage.get("output_tokens") or 0),
                        turns=1,
                    )
            elif ev == "done":
                pass  # stream closes after this


# ==================================================================== Pipeline


class VoicePipelineServer:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.turn_counter = 0
        self.hermes = HermesAPI(cfg)
        self.ai_router = AIRouter(cfg, LOG_PATH.parent)
        # last_provider/model only live in memory and used to say "Hermes ·
        # gpt-oss:20b (local)" (a stale hardcoded guess) whenever they were
        # None right after a restart — looked like the router had silently
        # reverted to local Hermes when it hadn't. Seed from the latency log
        # so the HUD shows the real last-used provider immediately, not a
        # placeholder that reads like real data.
        self.last_provider, self.last_model = self._read_last_active_provider()
        self.last_turn_ts: float | None = None
        self.stt_lock = asyncio.Lock()
        self._chat_lock = threading.Lock()
        self._mem_dir = Path.home() / ".hermes" / "memories"
        self._hist_path = self._mem_dir / "hud_history.json"
        self._learned_path = self._mem_dir / "LEARNED.md"
        self._wa_contacts_path = self._mem_dir / "wa_contacts.json"
        self._wa_pending_path = self._mem_dir / "wa_pending.json"
        self._gmail_pending_path = self._mem_dir / "gmail_pending.json"
        self._chat_history: dict[str, list[dict]] = self._load_history()
        self._wa_pending: dict | None = self._wa_load_pending()
        self._gmail_pending: dict | None = self._gmail_load_pending()
        self._yt_pending: dict | None = None
        self._folder_pending: str | None = None
        self._media_panel_open: bool = False
        self._cv_text = ""
        threading.Thread(target=self._warm_ollama, name="ollama-warm", daemon=True).start()
        self.recorder = AudioToTextRecorder(
            model=cfg["stt"]["model"],
            use_microphone=False,
            spinner=False,
            device=cfg["stt"].get("device", "cpu"),
            compute_type=cfg["stt"].get("compute_type", "int8"),
            sample_rate=int(cfg["stt"].get("sample_rate", 16000)),
            language=cfg["stt"].get("language", "es"),
            beam_size=1,
            faster_whisper_vad_filter=False,
            no_log_file=True,
        )

    def next_turn_id(self) -> int:
        self.turn_counter += 1
        return self.turn_counter

    async def transcribe(self, audio: bytes, timing: TurnTiming | None = None) -> str:
        if timing:
            timing.stt_start_monotonic = time.perf_counter()
        # 1) GPU worker (if configured and reachable) — big model, ~0.3s
        remote = self.cfg["stt"].get("remote") or {}
        if remote.get("url"):
            text = await asyncio.to_thread(self._remote_stt, audio, remote)
            if text is not None:
                if timing:
                    timing.stt_model = f"remote:{remote.get('name', 'gpu')}"
                    timing.stt_final_monotonic = time.perf_counter()
                return text
        # 2) local Whisper fallback
        sample_rate = int(self.cfg["stt"].get("sample_rate", 16000))
        samples = (np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0).copy()
        try:
            async with self.stt_lock:
                self.recorder.feed_audio(samples, original_sample_rate=sample_rate)
                text = await asyncio.to_thread(self.recorder.perform_final_transcription, samples, True)
                self.recorder.clear_audio_queue()
        except Exception as exc:
            # near-silent audio can make whisper raise ("No clip timestamps found");
            # treat as empty transcript instead of failing the turn
            print(f"local STT error treated as empty transcript: {exc}", flush=True)
            text = ""
        if timing:
            timing.stt_final_monotonic = time.perf_counter()
        return (text or "").strip()

    def _remote_stt(self, audio: bytes, remote: dict) -> str | None:
        """POST raw PCM to the GPU STT worker. None = unavailable (use fallback)."""
        headers = {"Content-Type": "application/octet-stream"}
        token = os.environ.get(remote.get("token_env", "JARVIS_HUD_TOKEN"), "")
        if token:
            headers["X-Jarvis-Token"] = token
        try:
            r = requests.post(remote["url"], data=audio, headers=headers,
                              timeout=float(remote.get("timeout", 6)))
            if r.ok:
                return (r.json().get("text") or "").strip()
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ LLM

    def _warm_ollama(self) -> None:
        """Keep qwen loaded at 4K context so the first HUD turn is not a reload."""
        llm = self.cfg.get("llm") or {}
        if (llm.get("provider") or "") != "ollama":
            return
        base = (llm.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
        try:
            requests.post(
                f"{base}/api/chat",
                json={
                    "model": llm.get("model") or "deepseek-r1:14b",
                    "messages": [{"role": "user", "content": "ok"}],
                    "stream": False,
                    "keep_alive": llm.get("keep_alive", -1),
                    "options": {
                        "num_ctx": int(llm.get("num_ctx") or 4096),
                        "num_predict": 1,
                    },
                },
                timeout=120,
            )
            print("Ollama warmed (4K ctx, keep_alive pin)", flush=True)
        except Exception as exc:
            print(f"Ollama warmup skipped: {exc}", flush=True)

    def clear_chat_history(self, conversation: str) -> None:
        with self._chat_lock:
            self._chat_history.pop(conversation, None)
            self._save_history_locked()

    def _load_history(self) -> dict[str, list[dict]]:
        try:
            data = json.loads(self._hist_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[str, list[dict]] = {}
        for key, rows in data.items():
            if not isinstance(rows, list):
                continue
            clean = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                role = row.get("role")
                content = (row.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    clean.append({"role": role, "content": content})
            if clean:
                out[str(key)] = clean[-80:]
        return out

    def _save_history_locked(self) -> None:
        try:
            self._mem_dir.mkdir(parents=True, exist_ok=True)
            payload = {k: v[-80:] for k, v in self._chat_history.items()}
            tmp = self._hist_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=0), encoding="utf-8")
            tmp.replace(self._hist_path)
        except Exception as exc:
            print(f"hud history save failed: {exc}", flush=True)

    _REMEMBER_CMD = re.compile(
        r"^(?:recuerda(?:\s+que)?|acu[eé]rdate(?:\s+de|\s+que)?|"
        r"no se te olvide(?:\s+que)?|apunta(?:\s+que)?|guarda(?:\s+que)?)\s*[:\-]?\s*(.+)$",
        re.IGNORECASE | re.DOTALL,
    )
    _FACT_LINE = re.compile(
        r"^(?:yo\s+)?(?:soy|estoy|vivo|trabajo|tengo|me llamo|me mudo|me voy|"
        r"odio|prefiero|me gusta|no me gusta|mi pareja|mi trabajo)\b",
        re.IGNORECASE,
    )

    def _maybe_learn(self, user_text: str) -> None:
        text = re.sub(r"\s+", " ", (user_text or "").strip())
        if len(text) < 12 or text.endswith("?"):
            return
        if re.match(r"^(hola|buenas|hey|ponme|abre|youtube)\b", text, re.I):
            return
        m = self._REMEMBER_CMD.match(text)
        fact = (m.group(1).strip() if m else text)
        if not m and not self._FACT_LINE.match(text):
            return
        fact = fact[:240].rstrip(" .")
        if len(fact) < 8:
            return
        day = time.strftime("%Y-%m-%d")
        line = f"- {day}: {fact}"
        try:
            self._mem_dir.mkdir(parents=True, exist_ok=True)
            existing = ""
            if self._learned_path.exists():
                existing = self._learned_path.read_text(encoding="utf-8")
            if fact.lower() in existing.lower():
                return
            if not existing.strip():
                existing = "# Lo que Jesús me ha contado\n\n"
            if not existing.endswith("\n"):
                existing += "\n"
            self._learned_path.write_text(existing + line + "\n", encoding="utf-8")
        except Exception as exc:
            print(f"hud learn save failed: {exc}", flush=True)

    def _learned_block(self) -> str:
        try:
            raw = self._learned_path.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
        lines = [ln for ln in raw.splitlines() if ln.startswith("- ")][-25:]
        if not lines:
            return ""
        return "Lo que me ha contado (no lo recites entero):\n" + "\n".join(lines)

    def _remember_turn(self, conversation: str, user: str, assistant: str) -> None:
        max_hist = int((self.cfg.get("llm") or {}).get("history_turns") or 12)
        keep = max(max_hist * 2, 40)
        self._maybe_learn(user)
        with self._chat_lock:
            stored = self._chat_history.setdefault(conversation, [])
            stored.append({"role": "user", "content": user})
            stored.append({"role": "assistant", "content": assistant})
            overflow = len(stored) - keep
            if overflow > 0:
                del stored[:overflow]
            self._save_history_locked()

    _SPOTIFY_FILLER = re.compile(
        r"\b(hola|jarvis|oye|puedes|podrías|podrias|puedo|"
        r"ponme|poneme|pónme|pon|poner|pongas|ponga|ponla|ponlo|reproduce|play|"
        r"abre|abrir|abrazas|abras|ábrelo|abrelo|escuchemos|escucha|"
        r"necesito|quiero|que|me|te|en|el|la|los|las|un|una|de|por|favor|please|"
        r"spotify|música|musica|canción|cancion|tema|algo|alg[uú]n|alguna|algunos|algunas|"
        r"particular|se|llama|llamada|aquí|ahora|ya)\b",
        re.IGNORECASE,
    )
    _YT_FILLER = re.compile(
        r"\b(hola|jarvis|oye|puedes|podrías|podrias|puedo|"
        r"ponme|poneme|pónme|pon|poner|pongas|ponga|ponla|ponlo|reproduce|play|"
        r"abre|abrir|abrazas|abras|ábrelo|abrelo|escuchemos|escucha|"
        r"necesito|quiero|que|me|te|en|el|la|los|las|un|una|de|por|favor|please|"
        r"youtube|youtu\.be|música|musica|canción|cancion|tema|algo|alg[uú]n|alguna|algunos|algunas|video|vídeo|"
        r"particular|se|llama|llamada|aquí|ahora|ya)\b",
        re.IGNORECASE,
    )
    _SKIP_DIRS = {".git", "node_modules", "AppData", "Windows", "$Recycle.Bin",
                  "Program Files", "Program Files (x86)"}

    def _launch_path(self, path: Path) -> None:
        os.startfile(str(path))  # Windows: folder, html, pdf

    def _file_text(self, path: Path, limit: int = 6000) -> str:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="ignore")
        if path.suffix.lower() in {".html", ".htm"}:
            text = re.sub(r"(?is)<script.*?</script>", " ", text)
            text = re.sub(r"(?is)<style.*?</style>", " ", text)
            text = re.sub(r"<[^>]+>", " ", text)
            text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]

    def _cv_paths(self) -> list[Path]:
        home = Path.home()
        hits = [
            home / "Downloads" / "CV_Jesus_Gonzalez.html",
            home / "Downloads" / "cv_jesus_gonzalez_v6.html",
            home / "Downloads" / "cv_jesus_gonzalez_v6 (1).html",
            home / "Downloads" / "Carta_de_presentacion_Jesus_Gonzalez_Cala.pdf",
            Path(r"C:\cv"),
        ]
        extra: list[Path] = []
        for folder in (home / "Downloads", home / "Desktop", home / "Documents"):
            if not folder.is_dir():
                continue
            try:
                extra.extend(p for p in folder.iterdir()
                             if p.is_file() and re.search(r"cv|curriculum", p.name, re.I))
            except OSError:
                pass
        seen: set[str] = set()
        out: list[Path] = []
        for p in hits + extra:
            key = str(p).lower()
            if key in seen or not p.exists():
                continue
            seen.add(key)
            out.append(p)
        return out

    def _open_and_read_cv(self) -> str:
        paths = self._cv_paths()
        if not paths:
            return "No encuentro el CV en Descargas ni en C:\\cv. Dime la carpeta."
        target = paths[0]
        self._launch_path(target)
        if target.is_file() and target.suffix.lower() in {".html", ".htm", ".txt", ".md"}:
            self._cv_text = self._file_text(target)
        else:
            self._cv_text = f"Archivo abierto: {target.name}"
        if self._cv_text:
            self._maybe_learn("CV leído: técnico de microelectrónica y software, Jesús González Cala.")
        return f"Abro tu CV: {target.name}. Ya lo tengo leído para postular."

    def _search_named(self, name: str) -> Path | None:
        q = re.sub(r"\s+", " ", (name or "").strip()).strip(" .")
        if not q:
            return None
        key = q.lower().replace("-", "").replace(" ", "")
        if key in {"cv", "cw", "cu", "curriculum", "currículum", "micv"}:
            cv_root = Path(r"C:\cv")
            if cv_root.exists():
                return cv_root
            paths = self._cv_paths()
            return paths[0] if paths else None
        exact = Path(q)
        if exact.exists():
            return exact
        roots = [
            Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Downloads",
            Path.home(), Path("C:/"), Path("D:/"), Path(r"C:\cv"), Path(r"C:\ARIA"),
        ]
        needle = q.lower()
        compact = key
        best: Path | None = None
        for root in roots:
            if not root.exists():
                continue
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    depth = Path(dirpath).relative_to(root).parts if dirpath != str(root) else ()
                    if len(depth) > 2:
                        dirnames[:] = []
                        continue
                    dirnames[:] = [d for d in dirnames if d not in self._SKIP_DIRS]
                    for d in dirnames:
                        compact_d = d.lower().replace("-", "").replace(" ", "")
                        if needle in d.lower() or compact in compact_d:
                            return Path(dirpath) / d
                    for f in filenames:
                        if needle in f.lower() or compact in f.lower().replace("-", "").replace(" ", ""):
                            return Path(dirpath) / f
            except OSError:
                continue
        return best

    def _open_named(self, name: str) -> str:
        path = self._search_named(name)
        if not path:
            return f"No encuentro «{name}» en Escritorio, Descargas, C: ni D:. Dime la ruta."
        self._launch_path(path)
        kind = "carpeta" if path.is_dir() else "archivo"
        return f"Abro la {kind} {path.name}."

    def _draft_job_gmail(self, company: str = "", to_email: str = "", job_title: str = "") -> str:
        if not self._cv_text:
            self._open_and_read_cv()
        who = "Jesús González Cala, técnico de microelectrónica y desarrollo de software. Busco empleo estable en Alsasua, Pamplona o Vitoria, sin coche."
        if self._cv_text:
            who = self._cv_text[:900]
        puesto = job_title or "la vacante"
        empresa = company or "su empresa"
        body = (
            f"Hola,\n\nMe llamo Jesús González Cala. Me postulo a {puesto} en {empresa} "
            f"como si fuera yo mismo, no una agencia.\n\n{who}\n\n"
            "Puedo desplazarme en bus, Renfe o bici (radio ~3 km). "
            "CV: https://cvjesusgonzalez.es/\n\nGracias.\nJesús González Cala"
        )
        subject = f"Candidatura: {job_title or 'empleo'} — Jesús González Cala"
        params = urllib.parse.urlencode({"view": "cm", "fs": "1", "to": to_email or "",
                                         "su": subject, "body": body})
        webbrowser.open("https://mail.google.com/mail/?" + params, new=2)
        if to_email:
            return f"Gmail abierto con el borrador para {to_email}. Revisa y pulsa enviar. Yo no lo mando solo."
        return "Gmail abierto con el borrador en tu nombre. Pon el correo de la empresa y pulsa enviar. Yo no lo mando a ciegas."

    def _run_hud_tool(self, name: str, args: dict) -> str:
        args = args or {}
        if name == "open_youtube":
            q = (args.get("query") or "").strip()
            if q:
                webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q), new=2)
                return f"Busco {q} en YouTube."
            webbrowser.open("https://music.youtube.com/", new=2)
            return "Abro YouTube Music."
        if name == "open_browser":
            q = (args.get("url_or_query") or "").strip()
            if q.startswith("http://") or q.startswith("https://"):
                webbrowser.open(q, new=2)
            else:
                webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(q), new=2)
            return "Abierto."
        if name == "open_folder":
            return self._open_named(str(args.get("name") or ""))
        if name == "read_cv":
            return self._open_and_read_cv()
        if name == "draft_job_email":
            return self._draft_job_gmail(
                company=str(args.get("company") or ""),
                to_email=str(args.get("to_email") or ""),
                job_title=str(args.get("job_title") or ""),
            )
        if name == "remember":
            fact = str(args.get("fact") or "").strip()
            if fact:
                self._maybe_learn("recuerda que " + fact)
            return "Queda apuntado."
        if name == "send_whatsapp":
            to = str(args.get("to") or "").strip()
            msg = str(args.get("message") or "").strip()
            return self._wa_tool_send(to, msg)
        if name == "check_gmail":
            return self._gmail_inbox_brief()
        return f"No sé la herramienta {name}."

    def _wa_contacts(self) -> dict:
        try:
            data = json.loads(self._wa_contacts_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _wa_save_contacts(self, data: dict) -> None:
        self._mem_dir.mkdir(parents=True, exist_ok=True)
        self._wa_contacts_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _wa_search_real_contacts(self, query: str) -> list[dict]:
        """Look up a name in the real WhatsApp contacts Baileys has synced
        (bridge /contacts endpoint) — only searched when there's no local
        alias match yet. Never guesses; empty on any failure."""
        try:
            r = requests.get("http://127.0.0.1:3000/contacts", params={"q": query}, timeout=5)
            if not r.ok:
                return []
            return r.json().get("contacts") or []
        except Exception:
            return []

    def _wa_load_pending(self) -> dict | None:
        try:
            data = json.loads(self._wa_pending_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) and data.get("name") else None

    def _wa_set_pending(self, pending: dict | None) -> None:
        self._mem_dir.mkdir(parents=True, exist_ok=True)
        if not pending:
            self._wa_pending = None
            try:
                self._wa_pending_path.unlink()
            except OSError:
                pass
            return
        pending.setdefault("ts", time.time())
        self._wa_pending = pending
        self._wa_pending_path.write_text(
            json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _wa_last_assistant(self, conversation: str) -> str:
        with self._chat_lock:
            hist = list(self._chat_history.get(conversation) or [])
        for row in reversed(hist):
            if row.get("role") == "assistant":
                return str(row.get("content") or "")
        return ""

    def _wa_history_rows(self, conversation: str) -> list[dict]:
        with self._chat_lock:
            return list(self._chat_history.get(conversation) or [])

    @staticmethod
    def _wa_norm_alias(name: str) -> str:
        return re.sub(r"\s+", " ", (name or "").strip().strip("\"'").lower())

    @staticmethod
    def _wa_extract_phone(text: str) -> str | None:
        raw = text or ""
        m = re.search(r"\+\s*(\d[\d\s().-]{6,18}\d)", raw)
        if m:
            digits = re.sub(r"\D", "", m.group(0))
            if 8 <= len(digits) <= 15:
                return "+" + digits
        m = re.search(r"\b00\s*(\d[\d\s().-]{6,18}\d)", raw)
        if m:
            digits = re.sub(r"\D", "", m.group(1))
            if 8 <= len(digits) <= 15:
                return "+" + digits
        m = re.search(r"\b34[\s.\-]*([67]\d(?:[\s.\-]?\d){8})\b", raw)
        if m:
            return "+34" + re.sub(r"\D", "", m.group(1))
        m = re.search(r"(?<!\d)([67]\d{2}[\s.\-]?\d{3}[\s.\-]?\d{3})(?!\d)", raw)
        if m:
            return "+34" + re.sub(r"\D", "", m.group(1))
        return None

    @staticmethod
    def _wa_parse_alias(text: str) -> str | None:
        t = text or ""
        m = re.search(
            r"(?:agendad[oa]\s+como|guardad[oa]\s+como|contacto)\s+[\"']?([^\"'\n,]+?)[\"']?"
            r"(?:\s*[,.]|\s+(?:ponle|dile|escribe|m[áa]ndale|que)\b|$)",
            t,
            re.I,
        )
        if m:
            name = VoicePipelineServer._wa_norm_alias(m.group(1))
            if name and name not in {"mi", "mis", "un", "uno", "una", "el", "la", "mis contactos"}:
                return name
        m = re.search(
            r"(?:wh?ats?app|whasapp|whapsap|wasap|wassap|guasap|\bwsp\b)\s+"
            r"(?:a|al|a la)\s+(?:mi\s+)?(?:contacto\s+)?[\"']?"
            r"([A-Za-zÁÉÍÓÚáéíóúñÑ][\wÁÉÍÓÚáéíóúñÑ.-]{1,40})",
            t,
            re.I,
        )
        if m:
            name = VoicePipelineServer._wa_norm_alias(m.group(1))
            if name not in {"uno", "una", "un", "mis", "mi", "contacto"}:
                return name
        # Reversed order: "...a NOMBRE un/el whatsapp/mensaje" (name before
        # the keyword — just as common in natural Spanish phrasing).
        m = re.search(
            r"\ba\s+(?:mi\s+)?(?:contacto\s+)?[\"']?"
            r"([A-Za-zÁÉÍÓÚáéíóúñÑ][\wÁÉÍÓÚáéíóúñÑ.-]{1,40}?)[\"']?\s+"
            r"(?:un|el|le)?\s*(?:wh?ats?app|whasapp|whapsap|wasap|wassap|guasap|\bwsp\b|mensaje)",
            t,
            re.I,
        )
        if m:
            name = VoicePipelineServer._wa_norm_alias(m.group(1))
            if name not in {"uno", "una", "un", "mis", "mi", "contacto", "la", "el"}:
                return name
        return None

    @staticmethod
    def _wa_parse_message(text: str) -> str | None:
        m = re.search(
            r"(?:ponle|dile(?:\s+que)?|escribe(?:le)?|m[áa]ndale|que\s+diga|mensaje\s*[:]+)\s+(.+)$",
            (text or "").strip(),
            re.I | re.S,
        )
        if not m:
            return None
        msg = m.group(1).strip().strip("\"'")
        msg = re.sub(r"\s+", " ", msg).strip(" .")
        return msg or None

    def _llm_extract_whatsapp(self, text: str) -> tuple[str | None, str | None]:
        """Mismo problema que YouTube/Spotify: la gente pide enviar un
        WhatsApp de mil formas distintas, no solo «whatsapp a X dile Y».
        Se le pregunta al motor local en vez de exigir una frase exacta."""
        prompt = (
            "Un usuario le pidió a su asistente que envíe un WhatsApp por él. "
            "Extrae el NOMBRE del contacto (tal como lo dijo, sin la palabra "
            "'contacto' ni artículos) y el MENSAJE exacto que quiere enviar. "
            "Responde EXACTAMENTE en este formato JSON de una línea:\n"
            '{"nombre": "...", "mensaje": "..."}\n'
            "Si no menciona a quién o no dio un texto concreto para enviar, pon "
            "null en ese campo. No inventes nada que el usuario no dijo.\n\n"
            f"Mensaje: \"{text}\""
        )
        try:
            content = _call_fast_llm(prompt, max_tokens=150, temperature=0.1)
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
            data = json.loads(content)
            name = self._wa_norm_alias(data.get("nombre") or "") or None
            msg = (data.get("mensaje") or "").strip() or None
            return name, msg
        except Exception:
            return None, None


    @staticmethod
    def _wa_is_confirm(text: str) -> bool:
        t = re.sub(r"\s+", " ", (text or "").strip().lower())
        t = t.strip("¡!¿?.")
        if re.search(r"env[ií]a(?:lo|le)?(?:\s+ya)?|m[áa]nda(?:lo|le)?(?:\s+ya)?", t):
            if re.search(r"\b(s[ií]|ok|vale|dale|ya)\b", t) or re.fullmatch(
                r"env[ií]a(?:lo|le)?(?:\s+ya)?|m[áa]nda(?:lo|le)?(?:\s+ya)?", t,
            ):
                return True
        return bool(re.fullmatch(
            r"(s[ií]|ok|vale|dale)(?:[,.]?\s+(?:s[ií]|ok|vale|dale|ya|env[ií]a(?:lo|le)?|m[áa]nda(?:lo|le)?))*",
            t,
        ))

    @staticmethod
    def _wa_is_complaint(text: str) -> bool:
        t = (text or "").lower()
        return bool(re.search(
            r"no\s+(?:me\s+)?(?:lo\s+)?envi[oó]|no\s+mand[oó]|mentira|"
            r"no\s+ha\s+llegado|no\s+me\s+envi[oó]|dijo que s[ií]|no me envio",
            t,
        ))

    @staticmethod
    def _wa_is_intent(text: str) -> bool:
        t = (text or "").lower()
        has_wa = bool(re.search(r"wh?ats?app|whasapp|whapsap|wasap|wassap|guasap|\bwsp\b", t))
        has_send = bool(re.search(r"env[ií]|m[aá]nda|escrib[ei]", t))
        return has_wa and has_send

    def _wa_draft_from_history(self, conversation: str) -> tuple[str | None, str | None]:
        name = None
        message = None
        for row in self._wa_history_rows(conversation):
            content = str(row.get("content") or "")
            if row.get("role") == "user":
                name = self._wa_parse_alias(content) or name
                message = self._wa_parse_message(content) or message
            elif row.get("role") == "assistant":
                quoted = re.search(r'(?:\*\*)?Mensaje(?:\*\*)?:\s*"([^"]+)"', content)
                if quoted:
                    message = quoted.group(1).strip() or message
                alias_m = re.search(r'contacto\s+"([^"]+)"', content, re.I)
                if alias_m:
                    name = self._wa_norm_alias(alias_m.group(1)) or name
        return name, message

    def _wa_hydrate(self, conversation: str) -> dict | None:
        pending = self._wa_pending or self._wa_load_pending()
        if pending:
            self._wa_pending = pending
            return pending
        name, message = self._wa_draft_from_history(conversation)
        if not name:
            return None
        contacts = self._wa_contacts()
        entry = contacts.get(name) if isinstance(contacts.get(name), dict) else None
        phone = (entry or {}).get("phone") if entry else None
        pending = {
            "name": name,
            "phone": phone,
            "message": message or "esto es una prueba",
            "send_now": True,
        }
        self._wa_set_pending(pending)
        return pending

    def _wa_need_number(self, name: str) -> str:
        who = name or "ese contacto"
        return (
            f"No lo envié. WhatsApp de Jarvis no tiene tu agenda: «{who}» "
            f"no es un número. Dime el móvil con prefijo, tipo +34..., y lo mando de verdad."
        )

    def _hermes_send_whatsapp(self, phone: str, message: str) -> tuple[bool, str]:
        hermes = Path.home() / ".hermes" / "bin" / "hermes.exe"
        if not hermes.exists():
            found = shutil.which("hermes")
            hermes = Path(found) if found else None
        if not hermes:
            return False, "No encuentro Hermes en este PC. Sin eso no sale el WhatsApp."
        env = os.environ.copy()
        env["HERMES_HOME"] = str(Path.home() / ".hermes")
        env["PATH"] = str(Path.home() / ".hermes" / "bin") + os.pathsep + env.get("PATH", "")
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix="jarvis-wa-", suffix=".txt", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(message)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.run(
                [str(hermes), "send", "--to", f"whatsapp:{phone}", "--json", "--file", tmp],
                capture_output=True,
                text=True,
                env=env,
                timeout=50,
                creationflags=flags,
            )
        except subprocess.TimeoutExpired:
            return False, "WhatsApp no contestó a tiempo. El puente puede estar caído."
        except Exception as exc:
            return False, f"Falló el envío: {type(exc).__name__}."
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        payload: dict = {}
        raw = (proc.stdout or "").strip()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if proc.returncode == 0 and payload.get("success"):
            return True, "ok"
        err = str(payload.get("error") or proc.stderr or "").strip()
        if "10061" in err or "refused" in err.lower() or "bridge" in err.lower():
            return False, "El puente de WhatsApp no está arriba. Revisa que Hermes siga vinculado."
        if err:
            short = err.splitlines()[0][:180]
            return False, f"No salió. {short}"
        return False, "No salió. Hermes no confirmó el envío."

    def _wa_do_send(self, name: str, phone: str, message: str) -> str:
        ok, detail = self._hermes_send_whatsapp(phone, message)
        if not ok:
            return detail
        contacts = self._wa_contacts()
        contacts[self._wa_norm_alias(name)] = {"phone": phone}
        self._wa_save_contacts(contacts)
        self._wa_set_pending(None)
        tail = phone[-4:] if len(phone) >= 4 else ""
        who = name or "el número"
        return f"Ahora sí: enviado a {who} (…{tail}). «{message}»"

    def _wa_tool_send(self, to: str, message: str) -> str:
        if not message:
            return "¿Qué texto mando?"
        phone = self._wa_extract_phone(to) or (
            (self._wa_contacts().get(self._wa_norm_alias(to)) or {}).get("phone")
            if isinstance(self._wa_contacts().get(self._wa_norm_alias(to)), dict)
            else None
        )
        name = self._wa_norm_alias(to) if not self._wa_extract_phone(to) else to
        if not phone:
            self._wa_set_pending({"name": name or to, "phone": None, "message": message, "send_now": True})
            return self._wa_need_number(name or to)
        return self._wa_do_send(name or to, phone, message)

    def _gmail_token_path(self) -> Path:
        return Path.home() / ".hermes" / "google_token.json"

    def _gmail_access_token(self) -> str:
        path = self._gmail_token_path()
        if not path.exists():
            raise RuntimeError("Gmail no está vinculado en este PC.")
        data = json.loads(path.read_text(encoding="utf-8"))
        token = str(data.get("token") or "")
        expiry_raw = str(data.get("expiry") or "")
        expired = True
        try:
            exp = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
            expired = exp <= datetime.now(timezone.utc) + timedelta(seconds=60)
        except Exception:
            expired = True
        if token and not expired:
            return token
        refresh = data.get("refresh_token")
        if not refresh:
            raise RuntimeError("El token de Gmail caducó y no hay refresh.")
        resp = requests.post(
            data.get("token_uri") or "https://oauth2.googleapis.com/token",
            data={
                "client_id": data.get("client_id"),
                "client_secret": data.get("client_secret"),
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        if not resp.ok:
            raise RuntimeError("Google rechazó renovar Gmail. Hay que volver a vincular.")
        body = resp.json()
        token = str(body.get("access_token") or "")
        if not token:
            raise RuntimeError("Google no devolvió access token.")
        data["token"] = token
        expires_in = int(body.get("expires_in") or 3500)
        data["expiry"] = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return token

    def _email_prefs_path(self) -> Path:
        return Path.home() / ".hermes" / "memories" / "email_prefs.yaml"

    _EMAIL_PREFS_TEMPLATE = """\
# Preferencias de clasificacion de correo para Jarvis. Deja vacio lo que
# no uses -- nunca bloquea, solo ayuda a decidir que es importante.
important_senders: []      # ej: ["jefe@empresa.com", "Maria Perez"]
important_keywords: []     # ej: ["factura", "contrato", "urgente"]
noise_senders: []          # remitentes que se ignoran siempre, ni se mencionan
noise_keywords: []         # ej: ["newsletter", "no-reply", "boletin"]
flag_job_offers: true      # avisar de ofertas de empleo y valorarlas contra tu perfil
"""

    def _load_email_prefs(self) -> dict:
        path = self._email_prefs_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self._EMAIL_PREFS_TEMPLATE, encoding="utf-8")
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
        return {
            "important_senders": raw.get("important_senders") or [],
            "important_keywords": raw.get("important_keywords") or [],
            "noise_senders": raw.get("noise_senders") or [],
            "noise_keywords": raw.get("noise_keywords") or [],
            "flag_job_offers": raw.get("flag_job_offers", True),
        }

    def _mute_email_sender(self, target: str) -> str:
        """Adds `target` to email_prefs.yaml's noise list (noise_senders if
        it looks like a name, noise_keywords if it's a longer phrase) so
        future gmail briefs stop mentioning it — _llm_classify_emails
        already honors these as a hint, this just gives the user a way to
        populate the list by talking instead of hand-editing the YAML.

        Does NOT unsubscribe from anything real (no click on any
        unsubscribe link, no Gmail filter) — only changes what Jarvis
        chooses to mention. Say that distinction out loud if asked."""
        target = (target or "").strip()
        if not target:
            return "¿Qué remitente o tema quieres que deje de mostrarte?"
        path = self._email_prefs_path()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
        bucket = "noise_senders" if len(target.split()) <= 4 else "noise_keywords"
        existing = raw.get(bucket) or []
        if target.lower() in [str(s).lower() for s in existing]:
            return f"Ya tenía silenciado «{target}»."
        existing.append(target)
        raw[bucket] = existing
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return f"Hecho, no te vuelvo a mencionar correos de «{target}» — esto no cancela ninguna suscripción real, solo deja de sacártelos yo."

    def _llm_classify_emails(self, emails: list[dict], prefs: dict) -> list[dict]:
        """One batched local-model call classifies every email at once —
        IMPORTANTE / OFERTA / RUIDO from sender+subject+snippet only, never
        the full body. Job-profile fields are handed in so OFERTA emails get
        judged against the user's real profile in the same call instead of a
        second round trip. Returns [] on any failure — caller falls back to
        the plain unclassified list rather than blocking on this."""
        import job_match
        job_profile = job_match.load_profile()
        profile_hint = (
            f"Roles buscados: {', '.join(job_profile['desired_roles']) or '(sin definir)'}. "
            f"Ubicación: {job_profile['location'] or '(sin definir)'}. "
            f"Salario mínimo: {job_profile['salary_min'] or '(sin definir)'}."
        )
        listing = "\n".join(
            f"{i}| De: {e['sender']} | Asunto: {e['subject']} | Vista previa: {e['snippet'][:200]}"
            for i, e in enumerate(emails)
        )
        prefs_hint = (
            f"Remitentes/temas que el usuario ya marcó como importantes: "
            f"{', '.join(prefs['important_senders'] + prefs['important_keywords']) or '(ninguno definido)'}. "
            f"Remitentes/temas que el usuario ya marcó como ruido a ignorar: "
            f"{', '.join(prefs['noise_senders'] + prefs['noise_keywords']) or '(ninguno definido)'}."
        )
        prompt = (
            "Clasifica cada correo sin leer de la lista en una de tres categorías, "
            "usando solo el remitente/asunto/vista previa (nunca inventes contenido "
            "que no está ahí). Categorías:\n"
            "IMPORTANTE: algo personal, laboral o administrativo que probablemente "
            "le importe al usuario (facturas, contratos, mensajes de personas reales, "
            "avisos administrativos serios).\n"
            f"{'OFERTA: una oferta de empleo o mensaje de un portal de empleo (LinkedIn, InfoJobs, Indeed...). ' if prefs['flag_job_offers'] else ''}"
            "RUIDO: newsletters, notificaciones automáticas, publicidad, redes sociales, "
            "promociones — cualquier cosa que la mayoría de gente ignora.\n\n"
            f"Perfil de empleo del usuario (para juzgar las OFERTA): {profile_hint}\n"
            f"Preferencias ya conocidas del usuario: {prefs_hint}\n\n"
            f"Correos:\n{listing}\n\n"
            "Responde SOLO con una línea por correo, en este formato exacto, sin nada más:\n"
            "indice|CATEGORIA|razon en máximo 12 palabras en español\n"
            "Para OFERTA, la razón debe decir si encaja o no con el perfil y por qué, en pocas palabras."
        )
        out = _call_fast_llm(prompt, max_tokens=350, temperature=0.1)
        if not out:
            return []
        results: list[dict] = []
        for line in out.splitlines():
            m = re.match(r"\s*(\d+)\s*\|\s*(IMPORTANTE|OFERTA|RUIDO)\s*\|\s*(.+)", line, re.I)
            if not m:
                continue
            idx = int(m.group(1))
            if 0 <= idx < len(emails):
                results.append({**emails[idx], "category": m.group(2).upper(), "reason": m.group(3).strip()})
        return results

    def _gmail_inbox_brief(self) -> str:
        try:
            token = self._gmail_access_token()
        except Exception as exc:
            return f"No pude entrar a Gmail: {exc}"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            listed = requests.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params={"q": "is:unread", "maxResults": 10},
                headers=headers,
                timeout=10,
            )
        except Exception:
            return "Gmail no respondió. Red o API caída."
        if listed.status_code == 401:
            return "Gmail rechazó el token. Hay que volver a vincular, yo no te pido la contraseña."
        if not listed.ok:
            return f"Gmail falló ({listed.status_code})."
        payload = listed.json()
        total = int(payload.get("resultSizeEstimate") or 0)
        rows = payload.get("messages") or []
        if not rows:
            return "Bandeja revisada: no hay correos sin leer."

        def _fetch_msg(row: dict) -> dict | None:
            mid = row.get("id")
            if not mid:
                return None
            try:
                got = requests.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                    params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
                    headers=headers,
                    timeout=8,
                )
                if not got.ok:
                    return None
                body = got.json()
                hdrs = {
                    str(h.get("name") or "").lower(): str(h.get("value") or "")
                    for h in ((body.get("payload") or {}).get("headers") or [])
                }
                sender = hdrs.get("from") or "desconocido"
                sender = re.sub(r"\s*<[^>]+>", "", sender).strip().strip('"') or sender
                subj = (hdrs.get("subject") or "(sin asunto)").strip()
                return {"mid": mid, "sender": sender, "subject": subj, "snippet": body.get("snippet") or ""}
            except Exception:
                return None

        emails: list[dict] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            for item in pool.map(_fetch_msg, rows[:10]):
                if item:
                    emails.append(item)
        if not emails:
            return f"Hay unos {total} correos sin leer, pero no pude leer los detalles."

        prefs = self._load_email_prefs()
        classified = self._llm_classify_emails(emails, prefs)
        if not classified:
            # Fallback: la clasificación falló (modelo local caído, etc.) —
            # mejor una lista simple que silencio o un error.
            lines = [f"• {e['sender']}: {e['subject']}" for e in emails[:5]]
            return f"Hay unos {total} correos sin leer. Los últimos:\n" + "\n".join(lines)

        self._gmail_last_important = classified
        important = [e for e in classified if e["category"] in ("IMPORTANTE", "OFERTA")]
        noise_count = len(classified) - len(important)
        if not important:
            return f"Revisé los {len(classified)} correos sin leer: son notificaciones o poco relevantes, nada que parezca importante."
        lines = []
        for i, e in enumerate(important, 1):
            tag = "oferta de trabajo" if e["category"] == "OFERTA" else "correo"
            lines.append(f"{i}. {tag} de {e['sender']} ({e['subject']}): {e['reason']}")
        head = f"Tienes {len(important)} correo(s) que te pueden interesar de los {len(classified)} sin leer:"
        tail = f"\n\nHay {noise_count} más que parecen notificaciones o poco relevantes, no te los menciono." if noise_count else ""
        return head + "\n" + "\n".join(lines) + tail

    def _gmail_load_pending(self) -> dict | None:
        try:
            data = json.loads(self._gmail_pending_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) and data.get("to") else None

    def _gmail_set_pending(self, pending: dict | None) -> None:
        self._gmail_pending = pending
        try:
            self._mem_dir.mkdir(parents=True, exist_ok=True)
            if pending:
                self._gmail_pending_path.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
            elif self._gmail_pending_path.exists():
                self._gmail_pending_path.unlink()
        except Exception:
            pass

    def _llm_extract_email(self, text: str) -> tuple[str | None, str | None]:
        """Igual que YouTube/Spotify/WhatsApp: en vez de exigir la frase
        exacta «envíale un correo a X diciendo Y», se le pregunta al motor
        local a quién y qué quiere enviar, en cualquier forma natural."""
        prompt = (
            "Un usuario le pidió a su asistente que envíe un correo (Gmail) por "
            "él. Extrae a quién quiere que se lo mande (nombre o email, tal "
            "como lo dijo) y qué quiere decir en el cuerpo del mensaje. "
            "Responde EXACTAMENTE en este formato JSON de una línea:\n"
            '{"destinatario": "...", "cuerpo": "..."}\n'
            "Si no pide enviar un correo de verdad (solo habla, o pide revisar "
            "la bandeja de entrada), responde exactamente "
            '{"destinatario": null, "cuerpo": null}. '
            "No inventes contenido que el usuario no dijo.\n\n"
            f"Mensaje: \"{text}\""
        )
        try:
            content = _call_fast_llm(prompt, max_tokens=150, temperature=0.1)
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
            data = json.loads(content)
            to = (data.get("destinatario") or "").strip() or None
            body = (data.get("cuerpo") or "").strip() or None
            return to, body
        except Exception:
            return None, None

    def _llm_extract_mute_target(self, text: str) -> str | None:
        """Igual que el resto de extractores locales (email/WhatsApp/YouTube):
        en vez de una regex ciega, se le pregunta al motor local si el
        usuario pide DEJAR DE VER correos de alguien o de un tema — no que
        revise la bandeja, no que envíe nada."""
        prompt = (
            "Un usuario le habla a su asistente de correo (Gmail). Decide si "
            "está pidiendo DEJAR DE VER / SILENCIAR correos de un remitente o "
            "tema concreto (que el asistente no se los vuelva a mencionar) — "
            "no que revise la bandeja, no que envíe nada.\n"
            "Si es eso, extrae el remitente o tema exacto que quiere "
            "silenciar, tal como lo dijo. Responde EXACTAMENTE en este "
            'formato JSON de una línea: {"silenciar": "..."}. Si NO está '
            'pidiendo eso, responde exactamente {"silenciar": null}.\n\n'
            f"Mensaje: \"{text}\""
        )
        try:
            content = _call_fast_llm(prompt, max_tokens=100, temperature=0.1)
            content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
            data = json.loads(content)
            target = (data.get("silenciar") or "").strip()
            return target or None
        except Exception:
            return None

    def _gmail_people_lookup(self, name: str) -> str | None:
        """Resolve a name to an email via Google Contacts (contacts.readonly,
        already granted). None if not found — never guesses an address."""
        try:
            token = self._gmail_access_token()
        except Exception:
            return None
        try:
            r = requests.get(
                "https://people.googleapis.com/v1/people:searchContacts",
                params={"query": name, "readMask": "names,emailAddresses"},
                headers={"Authorization": f"Bearer {token}"}, timeout=10,
            )
            if not r.ok:
                return None
            for res in r.json().get("results") or []:
                emails = (res.get("person") or {}).get("emailAddresses") or []
                if emails:
                    return emails[0].get("value")
        except Exception:
            pass
        return None

    def _gmail_send(self, to: str, subject: str, body: str) -> bool:
        try:
            token = self._gmail_access_token()
        except Exception:
            return False
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        try:
            r = requests.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"raw": raw_b64}, timeout=20,
            )
            return r.ok
        except Exception:
            return False

    def _try_gmail_action(self, transcript: str, conversation: str) -> str | None:
        raw = (transcript or "").strip()
        if not raw:
            return None
        t = raw.lower()
        last = self._wa_last_assistant(conversation).lower()

        # A full new "envíale un correo a X diciendo Y" always wins over a
        # stale pending confirmation — checked first so "envíale" (the verb
        # used to START a request) can't be misread as confirming an old one.
        # La gente no siempre lo dice con esa frase exacta ("mándale un email
        # a Juan y dile que llego tarde", "escríbele a María sobre la reunión"),
        # así que en vez de una regex rígida se le pregunta al motor, igual
        # que con YouTube/Spotify/WhatsApp.
        sendish_hint = bool(re.search(r"env[ií]a|mand[aá]|escrib", t)) and bool(
            re.search(r"correo|e-?mail|mail|gmail", t)
        )
        target = body = None
        if sendish_hint:
            target, body = self._llm_extract_email(raw)
        m = bool(target and body)
        # Confirm a pending send (GO-gate: never sends on the first ask).
        pending = self._gmail_pending or self._gmail_load_pending()
        if not m and pending and re.search(r"^\s*(s[ií]|confirmo|dale)\s*[.!]?\s*$|\bconfirmo\b", t):
            ok = self._gmail_send(pending["to"], pending.get("subject") or "Mensaje de Jesús", pending["body"])
            self._gmail_set_pending(None)
            return (f"Enviado a {pending['to']}." if ok
                    else f"No lo pude enviar a {pending['to']}. El token de Gmail puede haber caducado.")
        if not m and pending and re.search(r"\b(no|cancela|cancelar|olv[ií]dalo)\b", t):
            self._gmail_set_pending(None)
            return "Cancelado, no lo envío."

        if m:
            target, body = target.strip(), body.strip().strip("\"'.")
            to = target if "@" in target else self._gmail_people_lookup(target)
            if not to:
                return f"No encontré el correo de «{target}» en tus contactos de Google. Dime la dirección directamente."
            self._gmail_set_pending({"to": to, "subject": "Mensaje de Jesús", "body": body, "ts": time.time()})
            return f"¿Envío a {to} esto: «{body}»? Decí «sí» para confirmar."

        sendish = bool(re.search(r"env[ií]e[sn]?|enviar|manda(?:r|s)?", t)) and bool(
            re.search(r"gmail|correo|e-?mail|\bmails?\b", t)
        )
        readish = bool(re.search(r"revisa|mira|nuev|bandeja|entr[oó]|sin leer|le[ií]do", t))
        if sendish and not readish:
            webbrowser.open("https://mail.google.com/mail/?view=cm&fs=1", new=2)
            return (
                "Abro Gmail para escribir. O decime «envíale un correo a X diciendo Y» "
                "y lo mando yo directo."
            )
        if re.search(
            r"(?:env[ií]a|manda|escribe)\s+(?:un\s+)?(?:correo|e-?mail|gmail)",
            t,
        ) and not readish:
            webbrowser.open("https://mail.google.com/mail/?view=cm&fs=1", new=2)
            return (
                "Abro Gmail para escribir. O decime «envíale un correo a X diciendo Y» "
                "y lo mando yo directo."
            )
        mentions = bool(re.search(r"gmail|correo|e-?mail|bandeja|\bmails?\b", t))
        last_mail = bool(re.search(r"gmail|correo|e-?mail|bandeja|contraseña|navegador", last))
        do_it = bool(re.search(r"(?:lo has|h[aá]zlo|hacer|haces)\s+t[uú]", t))
        opened = bool(re.search(r"ya est[aá] abierto|ya (?:lo\s+)?abr[ií]", t))
        if mentions or ((do_it or opened) and last_mail):
            if re.search(r"\b(silencia|deja de|no me muestres|no me hables de|ignora|no quiero ver)\b", t):
                mute_target = self._llm_extract_mute_target(raw)
                if mute_target:
                    return self._mute_email_sender(mute_target)
            return self._gmail_inbox_brief()
        return None

    def _try_whatsapp_action(self, transcript: str, conversation: str) -> str | None:
        """Real WhatsApp send via Hermes. Never claims success without hermes send."""
        raw = (transcript or "").strip()
        if not raw:
            return None
        phone_in = self._wa_extract_phone(raw)
        intent = self._wa_is_intent(raw)
        confirm = self._wa_is_confirm(raw)
        complaint = self._wa_is_complaint(raw)
        has_wa = bool(re.search(r"wh?ats?app|whasapp|whapsap|wasap|wassap|guasap|\bwsp\b", raw, re.I))
        alias = msg = None
        if has_wa:
            alias, msg = self._llm_extract_whatsapp(raw)
        if not alias:
            alias = self._wa_parse_alias(raw)
        if not msg:
            msg = self._wa_parse_message(raw)
        pending = self._wa_pending or self._wa_load_pending()
        last = self._wa_last_assistant(conversation)
        last_was_send = bool(re.search(r"envi|whats|mensaje|contacto", last or "", re.I))

        # Bare "whatsapp" mention with nothing actionable (no send verb, no
        # confirm word, no digits, no parseable name) — let it fall through
        # to normal conversation instead of repeating the pending-request
        # canned reply on every unrelated follow-up that happens to say the
        # word "whatsapp".
        if not (intent or confirm or complaint or phone_in or alias):
            return None
        if confirm and not (pending or last_was_send or intent or complaint or has_wa):
            return None
        if phone_in and not (pending or intent or confirm or alias or last_was_send or has_wa):
            return None

        if complaint or confirm or phone_in:
            pending = pending or self._wa_hydrate(conversation)

        if alias:
            contacts = self._wa_contacts()
            entry = contacts.get(alias) if isinstance(contacts.get(alias), dict) else None
            if not entry and not phone_in:
                hits = self._wa_search_real_contacts(alias)
                if len(hits) == 1:
                    entry = {"phone": hits[0]["phone"]}
                    contacts[alias] = entry
                    self._wa_save_contacts(contacts)
                elif len(hits) > 1:
                    names = "; ".join(f"{h['name'] or h['phone']}" for h in hits[:5])
                    return f"Encontré varios «{alias}» en tus contactos de WhatsApp: {names}. ¿Cuál es?"
            pending = pending or {}
            pending = {
                "name": alias,
                "phone": phone_in or (entry or {}).get("phone") or pending.get("phone"),
                "message": msg or pending.get("message"),
                "send_now": bool(pending.get("send_now")) or confirm,
            }
            self._wa_set_pending(pending)

        if msg and pending:
            pending["message"] = msg
            self._wa_set_pending(pending)

        if phone_in:
            if not pending:
                pending = {"name": alias or phone_in, "phone": phone_in, "message": msg, "send_now": confirm or intent}
            else:
                pending["phone"] = phone_in
            if alias:
                pending["name"] = alias
            contacts = self._wa_contacts()
            contacts[self._wa_norm_alias(pending.get("name") or "")] = {"phone": phone_in}
            if pending.get("name"):
                self._wa_save_contacts(contacts)
            pending["send_now"] = True if (confirm or intent or pending.get("send_now") or last_was_send) else pending.get("send_now")
            self._wa_set_pending(pending)

        if complaint:
            name = (pending or {}).get("name") or "ese contacto"
            if pending:
                pending["send_now"] = True
                self._wa_set_pending(pending)
            if pending and pending.get("phone") and pending.get("message"):
                return self._wa_do_send(pending["name"], pending["phone"], pending["message"])
            return self._wa_need_number(name)

        if confirm:
            # A bare "sí" is ambiguous when more than one action is waiting
            # on confirmation — defer to whichever was asked most recently
            # instead of always winning because this handler runs first.
            gp = self._gmail_pending or self._gmail_load_pending()
            if gp and (not pending or gp.get("ts", 0) > pending.get("ts", 0)):
                return None
            pending = pending or self._wa_hydrate(conversation)
            if not pending:
                return "No tengo a quién ni qué mandar. Dime el contacto y el texto."
            pending["send_now"] = True
            self._wa_set_pending(pending)
            if not pending.get("phone"):
                return self._wa_need_number(pending.get("name") or "ese contacto")
            if not pending.get("message"):
                return f"¿Qué le pongo a {pending.get('name')}?"
            return self._wa_do_send(pending["name"], pending["phone"], pending["message"])

        if pending and pending.get("phone") and pending.get("message") and pending.get("send_now"):
            return self._wa_do_send(pending["name"], pending["phone"], pending["message"])

        if intent or alias or pending:
            pending = pending or {
                "name": alias,
                "phone": phone_in,
                "message": msg,
                "send_now": False,
            }
            if alias:
                pending["name"] = alias
            if msg:
                pending["message"] = msg
            if phone_in:
                pending["phone"] = phone_in
            name = pending.get("name")
            if not name and not pending.get("phone"):
                return "¿A qué contacto? Jarvis no ve tu agenda de WhatsApp. Nombre y número."
            if not pending.get("phone"):
                contacts = self._wa_contacts()
                key = self._wa_norm_alias(name or "")
                entry = contacts.get(key) if isinstance(contacts.get(key), dict) else None
                if entry and entry.get("phone"):
                    pending["phone"] = entry["phone"]
            self._wa_set_pending(pending)
            if not pending.get("phone"):
                return self._wa_need_number(name or "ese contacto")
            if not pending.get("message"):
                return f"Tengo el número de {name}. ¿Qué texto mando?"
            who = name or "ese número"
            return f"Lo mando a {who}: «{pending['message']}». ¿Lo envío ahora?"

        if has_wa:
            pending = self._wa_hydrate(conversation)
            if pending and not pending.get("phone"):
                return self._wa_need_number(pending.get("name") or "ese contacto")
            return (
                "WhatsApp está vinculado, pero no veo tu agenda del móvil. "
                "Dime el número con prefijo (+34...) y el texto, y lo mando de verdad."
            )
        return None

    def _yt_watch_id(self, text: str) -> str | None:
        m = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})", text or "", re.I)
        return m.group(1) if m else None

    def _yt_embeddable(self, vid: str) -> bool:
        """oEmbed only confirms the video exists — a video can pass that and
        still refuse to play in an <iframe> (label/VEVO music and
        age-restricted videos commonly disable embedding while leaving
        oEmbed metadata public). The watch page's own player JSON is the
        one place that carries the real flag without needing an API key.
        Unknown (regex miss, network error) defaults to True so a parsing
        hiccup doesn't wrongly block a video that would have played fine."""
        try:
            resp = requests.get(
                f"https://www.youtube.com/watch?v={vid}",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=6,
            )
            m = re.search(r'"playableInEmbed":(true|false)', resp.text)
            return m.group(1) == "true" if m else True
        except Exception:
            return True

    def _yt_search_first_id(self, query: str) -> str | None:
        """Resuelve una búsqueda a un vídeo real de YouTube para poder abrirlo
        directo — YouTube reproduce solo al entrar a un vídeo por su URL, sin
        necesitar pulsar nada. Prueba varios candidatos porque algunos vídeos
        (sobre todo conciertos/contenido oficial completo) tienen la
        inserción (embed) bloqueada por el dueño y no cargarían en el panel."""
        try:
            url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
        except Exception:
            return None
        seen: list[str] = []
        for vid in ids:
            if vid in seen:
                continue
            seen.append(vid)
            if len(seen) > 5:
                break
            try:
                oe = requests.get(
                    "https://www.youtube.com/oembed",
                    params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                    timeout=5,
                )
                if oe.ok and self._yt_embeddable(vid):
                    return vid
            except Exception:
                continue
        return seen[0] if seen else None

    def _summon_panel(self, media: str, src: str, title: str) -> bool:
        """Muestra el panel holográfico DENTRO del propio HUD (ya existía en
        el front-end, sin usar) en vez de abrir una ventana de navegador
        aparte. Al vivir en la misma página, cerrarlo es 100% fiable —
        Windows no puede bloquear que una página cierre su propio contenido,
        que es justo lo que sí bloqueaba con ventanas externas."""
        try:
            requests.post(
                "http://127.0.0.1:8765/api/summon",
                json={"media": media, "src": src, "title": title, "position": "center"},
                headers={"X-Jarvis-Token": hud_token() or ""},
                timeout=5,
            )
            self._media_panel_open = True
            return True
        except Exception:
            return False

    def _dismiss_panel(self) -> bool:
        if not self._media_panel_open:
            return False
        try:
            requests.post(
                "http://127.0.0.1:8765/api/summon",
                json={"action": "dismiss"},
                headers={"X-Jarvis-Token": hud_token() or ""},
                timeout=5,
            )
            self._media_panel_open = False
            return True
        except Exception:
            return False

    def _llm_media_window_intent(self, text: str) -> str:
        """La gente pide cerrar un panel de mil formas distintas (igual que
        con las búsquedas de canciones) — se le pregunta al motor en vez de
        exigir una palabra exacta."""
        prompt = (
            "Un usuario le habla a su asistente de voz, que tiene un panel "
            "de vídeo o música abierto ahora mismo en pantalla. Decide "
            "qué quiere hacer con ese panel. Responde con EXACTAMENTE una "
            "palabra:\n"
            "CERRAR si quiere cerrarlo, quitarlo, apagarlo o dejar de ver/oírlo.\n"
            "NINGUNA si no está hablando de ese panel (pide otra cosa, sigue "
            "charlando, etc).\n\n"
            f"Mensaje: \"{text}\""
        )
        try:
            resp = requests.post(
                "http://127.0.0.1:8081/v1/chat/completions",
                json={
                    "model": "gpt-oss:20b",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.2,
                    "reasoning_effort": "low",  # faster, no effect on this one-word answer's quality
                },
                timeout=40,
            )
            resp.raise_for_status()
            out = resp.json()["choices"][0]["message"]["content"].strip().upper()
            if "CERRAR" in out:
                return "CLOSE"
        except Exception:
            pass
        return "NONE"

    def _try_media_window_action(self, transcript: str) -> str | None:
        # Solo vale la pena preguntarle al motor si hay algo que cerrar —
        # si no hay ningún panel abierto, no hay nada que hacer y no se
        # gasta una llamada al modelo en cada mensaje normal.
        if not self._media_panel_open:
            return None
        t = (transcript or "").strip()
        if not t:
            return None
        intent = self._llm_media_window_intent(t)
        if intent == "CLOSE":
            if self._dismiss_panel():
                return "Listo, lo cierro."
            return "No tengo ningún vídeo o música en pantalla para cerrar."
        return None

    def _llm_extract_media_query(self, text: str, platform: str) -> str:
        """La gente pide música de mil formas distintas; una lista de palabras
        de relleno nunca las cubre todas. En vez de adivinar con regex, se le
        pregunta al motor local (rápido, sin overhead de Hermes) cuál es la
        mejor búsqueda para lo que pidió."""
        prompt = (
            f"Un usuario le pidió a su asistente de voz que abra algo en {platform}. "
            f"Responde SOLO con la mejor consulta de búsqueda para encontrar lo que "
            f"pide, en pocas palabras, sin mencionar '{platform}' ni 'Jarvis' ni "
            f"palabras de cortesía (ponme, abre, quiero, etc). "
            f"Si el usuario dice que espere, que ya te dirá el título/nombre, o que "
            f"todavía no ha dicho cuál — NO inventes una búsqueda genérica: responde "
            f"exactamente ESPERA. "
            f"Si simplemente no dio detalles (y no pidió esperar), propone algo "
            f"genérico razonable (ej: \"música relajante\", \"canciones populares\"). "
            f"No expliques nada, no uses comillas, solo la consulta o ESPERA.\n\n"
            f"Mensaje: \"{text}\""
        )
        try:
            q = _call_fast_llm(prompt, max_tokens=100, temperature=0.2)
            return q.strip(" .!?,;:\"'«»").split("\n")[0] if q else ""
        except Exception:
            return ""

    def _yt_query(self, text: str) -> str:
        named = re.search(
            r"se llama\s+(.+?)(?:[.!?]|$)",
            (text or "").strip(),
            re.I | re.S,
        )
        if named:
            q = self._YT_FILLER.sub(" ", named.group(1))
            q = re.sub(r"https?://\S+", " ", q)
            q = re.sub(r"\s+", " ", q).strip(" .!?,;:\"'")
            return q
        q = self._llm_extract_media_query(text, "YouTube")
        if q:
            return q
        q = self._YT_FILLER.sub(" ", text or "")
        q = re.sub(r"https?://\S+", " ", q)
        q = re.sub(r"\s+", " ", q).strip(" .!?,;:\"'")
        return q

    def _yt_open(self, query: str, watch: str | None = None) -> str:
        if not watch and (query or "").strip().upper() == "ESPERA":
            self._yt_pending = {"awaiting_title": True}
            return "Vale, dime el título o el nombre de la canción cuando quieras."
        self._yt_pending = {"query": query, "watch": watch}
        if not watch and query:
            watch = self._yt_search_first_id(query)
        if watch:
            self._summon_panel("video", "https://www.youtube.com/watch?v=" + watch, query or "YouTube")
            return f"Reproduciendo {query or 'tu vídeo'} en YouTube."
        if query:
            # La página de resultados de búsqueda no se puede embeber (YouTube
            # la bloquea), así que para ese caso raro se abre en el navegador.
            url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
            webbrowser.open(url, new=2)
            return f"Abro YouTube con «{query}». No encontré un vídeo directo; pulsa el que quieras."
        webbrowser.open("https://music.youtube.com/", new=2)
        return "Abro YouTube Music. ¿Qué canción?"

    def _try_youtube_action(self, transcript: str, conversation: str) -> str | None:
        raw = (transcript or "").strip()
        if not raw:
            return None
        if (self._yt_pending or {}).get("awaiting_title"):
            self._yt_pending = None
            watch2 = self._yt_watch_id(raw)
            q2 = "" if watch2 else self._yt_query(raw)
            return self._yt_open(q2, watch2)
        lower = raw.lower()
        last = self._wa_last_assistant(conversation)
        last_l = last.lower()
        watch = self._yt_watch_id(raw) or (self._yt_pending or {}).get("watch")
        last_yt = bool(re.search(r"youtube|youtu\.be|canci[oó]n|v[ií]deo|reproduc", last_l))
        wants_yt = "youtube" in lower or "youtu.be" in lower
        wants_music = bool(re.search(r"m[uú]sica|cancion|canci[oó]n|\btema\b", lower))
        wants_play = bool(re.search(
            r"\b(ponme|poneme|p[oó]nme|pon|pongas|ponga|ponla|ponlo|reproduce|"
            r"play|abre|abrir|abrazas|escucha|ejecuta|ejecutes)\b",
            lower,
        ))
        play_again = bool(re.search(
            r"no est[aá] reproduc|no me mient|no est[aá] abierto|ejecuta|"
            r"ábrelo|abrelo|abre el v[ií]deo|ponla|ponlo",
            lower,
        ))
        named = bool(re.search(r"se llama\s+\S+", lower))
        if play_again and (watch or self._yt_pending or last_yt):
            q = (self._yt_pending or {}).get("query") or self._yt_query(last) or self._yt_query(raw)
            w = watch or (self._yt_pending or {}).get("watch")
            return self._yt_open(q, w)
        if named and (wants_music or wants_yt or last_yt or wants_play):
            return self._yt_open(self._yt_query(raw), watch)
        if wants_yt or (wants_music and wants_play):
            q = self._yt_query(raw)
            if not q and last_yt:
                q = self._yt_query(last)
            return self._yt_open(q, watch)
        if last_yt and wants_play:
            q = self._yt_query(raw) or (self._yt_pending or {}).get("query") or self._yt_query(last)
            return self._yt_open(q, watch)
        return None

    def _spotify_query(self, text: str) -> str:
        named = re.search(r"se llama\s+(.+?)(?:[.!?]|$)", (text or "").strip(), re.I | re.S)
        if named:
            q = self._SPOTIFY_FILLER.sub(" ", named.group(1))
            q = re.sub(r"https?://\S+", " ", q)
            q = re.sub(r"\s+", " ", q).strip(" .!?,;:\"'")
            return q
        q = self._llm_extract_media_query(text, "Spotify")
        if q:
            return q
        q = self._SPOTIFY_FILLER.sub(" ", text or "")
        q = re.sub(r"https?://\S+", " ", q)
        q = re.sub(r"\s+", " ", q).strip(" .!?,;:\"'")
        return q

    def _try_spotify_action(self, transcript: str, conversation: str) -> str | None:
        """No hay credenciales de Spotify (ni falta): abre la búsqueda en el
        navegador, igual que YouTube — no reproduce solo, pero te deja un
        clic de distancia en vez de nada."""
        raw = (transcript or "").strip()
        if not raw:
            return None
        lower = raw.lower()
        if "spotify" not in lower:
            return None
        wants_play = bool(re.search(
            r"\b(ponme|poneme|p[oó]nme|pon|pongas|ponga|ponla|ponlo|reproduce|"
            r"play|abre|abrir|escucha|ejecuta)\b",
            lower,
        ))
        if not wants_play:
            return None
        q = self._spotify_query(raw)
        if q.strip().upper() == "ESPERA":
            return "Vale, dime el título o el nombre de la canción cuando quieras."
        if q:
            url = "https://open.spotify.com/search/" + urllib.parse.quote_plus(q)
            webbrowser.open(url, new=2)
            return f"Abro Spotify con «{q}». Pulsa play; no tengo credenciales de Spotify para darle yo mismo."
        webbrowser.open("https://open.spotify.com/", new=2)
        return "Abro Spotify. ¿Qué canción?"

    def _try_folder_action(self, transcript: str, conversation: str) -> str | None:
        raw = (transcript or "").strip()
        if not raw:
            return None
        t = raw.lower()
        last = self._wa_last_assistant(conversation).lower()
        wants = bool(re.search(
            r"carpeta|explorador|disco local|en (?:el )?disco|accede|"
            r"acceder|abre(?:la|lo)? la carpeta",
            t,
        ))
        see_it = bool(re.search(
            r"quiero ver|quiero verlo|muestra|ábrela|abrel[ao]|hazlo t[uú]|"
            r"(?:lo has|hacer|haces)\s+t[uú]|c[oó]mo t[uú] lo haces",
            t,
        ))
        last_folder = bool(re.search(r"carpeta|explorador|disco local", last))
        name = None
        path_m = re.search(r"\b([cCdD]:\\[^\s,]+)", raw)
        if path_m:
            name = path_m.group(1).rstrip(" .")
        called = re.search(r"se llama\s+([A-Za-z0-9_\-]{1,40})", raw, re.I)
        if called and wants:
            name = called.group(1)
        elif re.search(r"\bcu\b", t) and (wants or (see_it and last_folder)):
            name = "CU"
        elif re.search(r"\bcv\b", t) and (wants or (see_it and last_folder)):
            name = "CV"
        if not name and last_folder and see_it:
            name = self._folder_pending or "CU"
        if name and (wants or last_folder or see_it):
            self._folder_pending = name
            return self._open_named(name)
        if wants and not name:
            return "¿Cómo se llama la carpeta?"
        return None

    def _try_humanoid_action(self, transcript: str, conversation: str) -> str | None:
        """FASE 5 (interfaz): voice/text toggle for the humanoid particle view.
        Pure UI command, no side effects — the exact reply text below is the
        signal the HUD's JS matches on to flip views (see index.html)."""
        raw = (transcript or "").strip()
        if not raw:
            return None
        t = raw.lower()
        close_pattern = (
            r"(?:cierra|quitar|quita|oculta|cerrar|volver|vuelve|salir|sal\s+de).*(?:humanoide|cara|rostro|holograma)"
            r"|(?:vista\s+normal|dashboard|volver\s+al\s+dashboard|vuelve\s+al\s+dashboard|close\s+humanoid)"
        )
        if re.search(close_pattern, t):
            return "Volviendo a la vista normal."

        open_pattern = (
            r"(?:mu[eé]stra|ens[eé][ñn]a|pon|abre|abr[eií]|activa|activar|ver|d[eé]jame\s+ver|quiero\s+ver).*(?:cara|rostro|humanoide|holograma)"
            r"|(?:modo\s+humanoide|tu\s+cara|tu\s+rostro|c[oó]mo\s+te\s+ves|c[oó]mo\s+eres|show\s+me\s+your\s+face|open\s+humanoid)"
        )
        if re.search(open_pattern, t):
            return "Abriendo el modo humanoide."
        return None

    def _try_pc_action(self, transcript: str, conversation: str = "") -> str | None:
        """Local PC actions. None = not an action, talk instead."""
        raw = (transcript or "").strip()
        if not raw:
            return None
        hv = self._try_humanoid_action(raw, conversation)
        if hv is not None:
            return hv
        # YouTube PLAY ya no se intercepta aquí — se deja pasar a Hermes, que
        # usa youtube_play (hermes-plugin/hud_display) con su propio
        # razonamiento. Pero "cierra el vídeo" SÍ se intercepta aquí de
        # nuevo: dejarlo también en manos de Hermes/el router online no
        # funcionaba de forma fiable (encontrado en vivo: el modelo rápido
        # online no tiene ninguna herramienta para cerrar un panel y
        # contestaba "no dispongo de una herramienta para eso" — cerrar un
        # panel HUD es una acción local pura, sin necesidad de búsqueda ni
        # de un tool-call remoto, así que resolverla aquí es más rápido y
        # más fiable que depender de cualquier proveedor).
        mw = self._try_media_window_action(raw)
        if mw is not None:
            return mw
        wa = self._try_whatsapp_action(raw, conversation)
        if wa is not None:
            return wa
        folder = self._try_folder_action(raw, conversation)
        if folder is not None:
            return folder
        mail = self._try_gmail_action(raw, conversation)
        if mail is not None:
            return mail
        sp = self._try_spotify_action(raw, conversation)
        if sp is not None:
            return sp
        lower = raw.lower()
        url_m = re.search(r"https?://[^\s\)]+", raw, re.I)
        if url_m and re.search(r"\b(abre|abrir|pon|abrelo|ábrelo|ejecuta)\b", lower):
            webbrowser.open(url_m.group(0), new=2)
            return "Abro el enlace en el navegador."
        return None

    def _talk_system_prompt(self) -> str:
        persona = ((self.cfg.get("persona") or {}).get("system_prompt") or "").strip()
        now_path = Path.home() / ".hermes" / "memories" / "JARVIS_NOW.md"
        try:
            profile = now_path.read_text(encoding="utf-8").strip()[:2000]
        except Exception:
            profile = "El usuario es Jesús. Alsasua, técnico y software. No recites ficha."
        learned = self._learned_block()
        if learned:
            return f"{persona}\n\n{profile}\n\n{learned}"
        return f"{persona}\n\n{profile}"

    def _ollama_turn(
        self, transcript: str, timing: TurnTiming, conversation: str,
    ) -> Iterator[tuple[str, str]]:
        llm = self.cfg["llm"]
        base = (llm.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
        model = llm.get("model") or "deepseek-r1:14b"
        max_hist = int(llm.get("history_turns") or 8)
        timing.llm_provider = "ollama"
        timing.llm_model = model
        with self._chat_lock:
            hist = list(self._chat_history.get(conversation) or [])
        messages = [{"role": "system", "content": self._talk_system_prompt()}]
        messages.extend(hist[-(max_hist * 2):])
        messages.append({"role": "user", "content": transcript})
        resp = requests.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "keep_alive": llm.get("keep_alive", -1),
                "options": {
                    "num_ctx": int(llm.get("num_ctx") or 4096),
                    "num_predict": int(llm.get("max_tokens") or 80),
                    "temperature": float(llm.get("temperature") or 0.65),
                },
            },
            stream=True,
            timeout=60,
        )
        resp.raise_for_status()
        parts: list[str] = []
        for raw in resp.iter_lines():
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            chunk = ((data.get("message") or {}).get("content")) or ""
            if not chunk:
                continue
            parts.append(chunk)
            if timing.llm_first_token_monotonic is None:
                timing.llm_first_token_monotonic = time.perf_counter()
            yield ("text", chunk)
        text = "".join(parts).strip()
        if text and re.search(
            r"he enviado|mensaje enviado|lo he mandado|ya (?:est[áa]|fue) enviado|whatsapp enviado|"
            r"contraseña|ingresa tu correo|abre el navegador y ve a tu bandeja|"
            r"por razones de seguridad, necesitar|"
            r"est[aá] reproduciendo|reproduzco|el video est[aá] abierto|"
            r"navego hasta la carpeta|abro el explorador de archivos",
            text,
            re.I,
        ):
            if re.search(r"reproduc|v[ií]deo|youtube", text, re.I):
                watch = self._yt_watch_id(text)
                q = self._yt_query(transcript) or (self._yt_pending or {}).get("query") or ""
                text = self._yt_open(q, watch)
            elif re.search(r"contraseña|ingresa tu correo|bandeja", text, re.I):
                text = self._gmail_inbox_brief()
            elif re.search(r"carpeta|explorador", text, re.I):
                text = self._open_named(self._folder_pending or "CU")
            else:
                text = (
                    "No lo envié. El modelo no puede fingir un WhatsApp. "
                    "Dime el número con prefijo (+34...) y lo mando de verdad."
                )
        if text:
            self._remember_turn(conversation, transcript, text)
        yield ("final", json.dumps({"content": text, "interrupted": False}))

    def stream_llm_events_sync(
        self, transcript: str, timing: TurnTiming, conversation: str,
    ) -> Iterator[tuple[str, str]]:
        llm = self.cfg["llm"]
        provider = llm["provider"]
        timing.llm_start_monotonic = time.perf_counter()
        local = self._try_pc_action(transcript, conversation)
        if local:
            timing.llm_provider = "pc"
            timing.llm_model = "local-action"
            timing.llm_first_token_monotonic = time.perf_counter()
            self._remember_turn(conversation, transcript, local)
            yield ("text", local)
            yield ("final", json.dumps({"content": local, "interrupted": False}))
            return
        if provider == "ollama":
            yield from self._ollama_turn(transcript, timing, conversation)
            return
        if provider == "router":
            forced_hermes = self.ai_router.override == "hermes"
            if not forced_hermes and not self.ai_router.classify_needs_hermes(transcript):
                try:
                    gen = self.ai_router.chat_online(self, transcript, timing, conversation)
                    first = next(gen)
                except StopIteration:
                    return
                except AllProvidersFailedError as exc:
                    print(f"[AI ROUTER] all online providers failed ({exc}); falling back to Hermes", flush=True)
                    timing.errors.append(f"ai_router_fallback: {exc}")
                else:
                    yield first
                    yield from gen
                    return
            provider = "hermes"  # classified as needing Hermes, or every online provider failed
        if provider == "hermes":
            try:
                h = self.cfg.get("hermes") or {}
                session_id = self.hermes.get_session_id(conversation)
                gen = self._hermes_turn(session_id, transcript, timing, h, conversation)
                first = next(gen)
            except StopIteration:
                return
            except Exception as exc:
                fb = (self.cfg.get("hermes") or {}).get("fallback_provider", "anthropic")
                print(f"Hermes unavailable ({type(exc).__name__}: {exc}); fallback={fb}", flush=True)
                timing.errors.append(f"hermes_fallback: {exc}")
                if not fb:
                    raise
                yield ("text", "Agent backend offline. Running in basic mode. ")
                provider = fb
            else:
                yield first
                yield from gen
                return
        timing.llm_provider = provider
        timing.llm_model = llm["model"]
        if provider == "anthropic":
            key = os.environ.get(llm.get("api_key_env", "ANTHROPIC_API_KEY"))
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not found")
            client = Anthropic(api_key=key)
            with client.messages.stream(
                model=llm["model"],
                max_tokens=int(llm.get("max_tokens", 220)),
                temperature=float(llm.get("temperature", 0.3)),
                system=self.cfg["persona"]["system_prompt"],
                messages=[{"role": "user", "content": transcript}],
            ) as stream:
                for text in stream.text_stream:
                    if text and timing.llm_first_token_monotonic is None:
                        timing.llm_first_token_monotonic = time.perf_counter()
                    yield ("text", text)
        else:
            raise RuntimeError(f"Unsupported LLM provider: {provider}")

    def _hermes_turn(
        self, session_id: str, transcript: str, timing: TurnTiming, h: dict, conversation: str,
    ) -> Iterator[tuple[str, str]]:
        timing.llm_provider = "hermes"
        timing.llm_model = "hermes-agent"
        timeout = float(h.get("timeout", 240))
        try:
            it = self.hermes.chat_stream_events(session_id, transcript, timeout)
            for kind, value in it:
                if kind == "text" and timing.llm_first_token_monotonic is None:
                    timing.llm_first_token_monotonic = time.perf_counter()
                yield (kind, value)
        except RuntimeError as exc:
            # stale session id (e.g. Hermes DB reset) -> recreate once
            if "404" in str(exc):
                session_id = self.hermes.get_session_id(conversation, force_new=True)
                for kind, value in self.hermes.chat_stream_events(session_id, transcript, timeout):
                    if kind == "text" and timing.llm_first_token_monotonic is None:
                        timing.llm_first_token_monotonic = time.perf_counter()
                    yield (kind, value)
            else:
                raise

    # ------------------------------------------------------------------ TTS

    async def tts_chunks(self, text: str, timing: TurnTiming) -> AsyncIterator[bytes]:
        """Synthesize one sentence, yield PCM chunks -- XTTS streams for real
        (first chunk ~1.7s in) when voice.provider is "xtts"; Edge TTS is
        chunked only after the fact (waits for the whole file first, then
        slices it for the WS frame size)."""
        voice = self.cfg.get("voice") or {}
        sample_rate = self._tts_sample_rate()
        timing.tts_request_start_monotonic = timing.tts_request_start_monotonic or time.perf_counter()
        record_usage(tts_chars=len(text))

        if str(voice.get("provider") or "") == "xtts":
            timing.tts_model, timing.voice_id = "xtts", "xtts-local"
            async for chunk in _stream_xtts_pcm(text):
                if timing.first_tts_audio_byte_monotonic is None:
                    timing.first_tts_audio_byte_monotonic = time.perf_counter()
                yield chunk
            return

        voice_id = str(voice.get("voice_id") or "es-AR-ElenaNeural")
        timing.tts_model = str(voice.get("model") or "edge-tts")
        timing.voice_id = voice_id
        tmpdir = tempfile.mkdtemp(prefix="jarvis-tts-")
        mp3_path = Path(tmpdir) / "speech.mp3"
        try:
            mp3 = await synthesize_edge_mp3(text)
            mp3_path.write_bytes(mp3)
            pcm = await asyncio.to_thread(_mp3_to_pcm16, mp3_path, sample_rate)
            if not pcm:
                print("Edge TTS decoded to empty PCM", flush=True)
                return
            if timing.first_tts_audio_byte_monotonic is None:
                timing.first_tts_audio_byte_monotonic = time.perf_counter()
            chunk_size = 4096
            for i in range(0, len(pcm), chunk_size):
                yield pcm[i:i + chunk_size]
        except Exception as exc:
            print(f"Edge TTS failed ({voice_id}): {exc}", flush=True)
            raise
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _tts_sample_rate(self) -> int:
        fmt = str((self.cfg.get("voice") or {}).get("output_format") or "pcm_24000")
        if fmt.startswith("pcm_"):
            try:
                return int(fmt.split("_", 1)[1])
            except ValueError:
                return 24000
        return 24000

    # ------------------------------------------------------------- Turn flow

    async def stream_response_audio(
        self, ws: WebSocket, transcript: str, timing: TurnTiming, conn: "ConnState",
    ) -> None:
        pending = ""
        full_response: list[str] = []
        spoken = False
        await ws.send_json({"type": "agent_status", "state": "thinking"})

        q: asyncio.Queue = asyncio.Queue()

        async def forward() -> None:
            try:
                async for item in self._async_llm_events(transcript, timing, conn.conversation):
                    await q.put(item)
                await q.put(None)
            except Exception as exc:
                await q.put(exc)

        forward_task = asyncio.create_task(forward())
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                kind, value = item
                if kind == "run":
                    timing.run_id = value
                    conn.current_run_id = value
                    await ws.send_json({"type": "run_started", "run_id": value})
                    continue
                if kind == "tool":
                    info = json.loads(value)
                    timing.tools_used.append(info.get("name", "tool"))
                    await ws.send_json({"type": "agent_status", "state": "tool_use",
                                        "tool": info.get("name"), "preview": info.get("preview", "")})
                    continue
                if kind == "approval":
                    await ws.send_json({"type": "approval_request", "data": json.loads(value),
                                        "run_id": conn.current_run_id})
                    continue
                if kind == "final":
                    info = json.loads(value)
                    timing.interrupted = info.get("interrupted", False)
                    continue
                # kind == "text"
                full_response.append(value)
                pending += value
                sentences, pending = self._extract_complete_sentences(pending)
                for sentence in sentences:
                    clean = self._clean_for_tts(sentence)
                    if not clean:
                        continue
                    if timing.first_sentence_monotonic is None:
                        timing.first_sentence_monotonic = time.perf_counter()
                    if not spoken:
                        await ws.send_json({"type": "agent_status", "state": "speaking"})
                        spoken = True
                    conn.spoken_sentences.append(clean)
                    await self._send_tts_sentence(ws, clean, timing)
            tail = self._clean_for_tts(pending.strip())
            if tail:
                conn.spoken_sentences.append(tail)
                await self._send_tts_sentence(ws, tail, timing)
        finally:
            if not forward_task.done():
                forward_task.cancel()
        timing.response_text = "".join(full_response).strip()

    async def _async_llm_events(
        self, transcript: str, timing: TurnTiming, conversation: str,
    ) -> AsyncIterator[tuple[str, str]]:
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def worker() -> None:
            try:
                for item in self.stream_llm_events_sync(transcript, timing, conversation):
                    loop.call_soon_threadsafe(q.put_nowait, item)
                loop.call_soon_threadsafe(q.put_nowait, None)
            except Exception as exc:
                loop.call_soon_threadsafe(q.put_nowait, exc)

        worker_task = asyncio.create_task(asyncio.to_thread(worker))
        while True:
            item = await q.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item
        await worker_task

    async def _send_tts_sentence(self, ws: WebSocket, sentence: str, timing: TurnTiming) -> None:
        if not sentence:
            return
        await ws.send_json({"type": "tts_format", "sample_rate": self._tts_sample_rate()})
        async for chunk in self.tts_chunks(sentence, timing):
            await ws.send_bytes(chunk)

    @staticmethod
    def _extract_complete_sentences(text: str) -> tuple[list[str], str]:
        sentences = []
        last_end = 0
        for match in SENTENCE_RE.finditer(text):
            sentences.append(match.group(1).strip())
            last_end = match.end()
        return sentences, text[last_end:]

    @staticmethod
    def _clean_for_tts(text: str) -> str:
        if not text:
            return ""
        text = THINK_RE.sub("", text)
        for pattern in SECRET_RES:                  # privacy: never speak secrets
            text = pattern.sub(" redacted ", text)
        text = CODEBLOCK_RE.sub(" code omitted. ", text)
        text = re.sub(r"`([^`]*)`", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"^[\s>*#-]+", "", text)
        text = re.sub(r"[*_#]{1,3}([^*_#]+)[*_#]{1,3}", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _read_last_active_provider() -> tuple[str | None, str | None]:
        """Tail latency.jsonl for the most recent turn that actually reached
        an LLM, so a restart doesn't blank the HUD's "currently using" panel."""
        try:
            lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return None, None
        for line in reversed(lines[-200:]):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            provider = row.get("llm_provider")
            if provider and provider != "pc":  # "pc" = local intent shortcut, never touched an LLM
                return provider, row.get("llm_model") or None
        return None, None

    def log_turn(self, timing: TurnTiming) -> None:
        if timing.llm_provider and timing.llm_provider != "pc":  # "pc" never touched an LLM
            self.last_provider = timing.llm_provider
            self.last_model = timing.llm_model
            self.last_turn_ts = time.time()
        timing.total_done_monotonic = timing.total_done_monotonic or time.perf_counter()
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        summary = timing.summary()
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        print("TURN TIMING", json.dumps(summary, ensure_ascii=False), flush=True)


load_env()
CFG = load_config()
HERMES = HermesAPI(CFG)   # lightweight API client - independent of the STT pipeline
PIPELINE: VoicePipelineServer | None = None


_PIPELINE_LOCK = threading.Lock()


def get_pipeline() -> VoicePipelineServer:
    """Lock prevents the four uvicorn listeners' startup hooks from racing
    into concurrent recorder inits (which crashed three of the four lifespans
    and silently killed the TLS ports)."""
    global PIPELINE
    if PIPELINE is None:
        with _PIPELINE_LOCK:
            if PIPELINE is None:
                PIPELINE = VoicePipelineServer(CFG)
    return PIPELINE


app = FastAPI(title="Hermes Voice Pipeline")


@app.on_event("startup")
async def warm_pipeline() -> None:
    """Warm the local Whisper fallback in the BACKGROUND, exactly once (this
    hook fires once per uvicorn listener — there are four), and never let a
    warm failure take a listener down."""
    global _WARM_STARTED
    if _WARM_STARTED:
        return
    _WARM_STARTED = True

    async def warm() -> None:
        try:
            await asyncio.to_thread(get_pipeline)
            print("STT pipeline warmed.", flush=True)
        except Exception as exc:
            print(f"STT warm failed (remote STT still available): {exc}", flush=True)

    asyncio.get_running_loop().create_task(warm())


_WARM_STARTED = False


# ------------------------------------------------------------------ Auth

ALLOWED_ORIGIN_HOSTS = {"jarvis.local", "jarvis", "localhost", "127.0.0.1"}
ALLOWED_ORIGIN_HOSTS |= set((CFG.get("security") or {}).get("extra_origin_hosts") or [])


def hud_token() -> str | None:
    env_name = (CFG.get("security") or {}).get("hud_token_env", "JARVIS_HUD_TOKEN")
    return os.environ.get(env_name) or None


def _request_authed(request: Request) -> bool:
    token = hud_token()
    if not token:
        return True
    supplied = (
        request.headers.get("x-jarvis-token")
        or request.cookies.get("jarvis_token")
        or request.query_params.get("token")
    )
    return supplied == token


@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    if request.url.path.startswith("/api/") and not _request_authed(request):
        return Response(status_code=401, content="jarvis auth required")
    return await call_next(request)


def _ws_allowed(ws: WebSocket) -> bool:
    """Browsers send Origin (+cookie); native clients (PTT, tests) send neither."""
    origin = ws.headers.get("origin")
    if not origin:
        return True  # non-browser client on the LAN (Python PTT, e2e tests)
    from urllib.parse import urlparse
    host = (urlparse(origin).hostname or "").lower()
    if host not in ALLOWED_ORIGIN_HOSTS:
        return False
    token = hud_token()
    if not token:
        return True
    return ws.cookies.get("jarvis_token") == token or ws.query_params.get("token") == token


# --------------------------------------------------------------- HUD + proxy

HUD_DIR = ROOT / "hud"
ALLOWED_GET_PATHS = {
    "/health", "/health/detailed", "/v1/capabilities",
    "/v1/skills", "/v1/toolsets", "/api/jobs", "/api/sessions",
}


def _proxy_allowed(method: str, path: str) -> bool:
    if method == "GET":
        return path in ALLOWED_GET_PATHS or (
            path.startswith("/api/sessions/") and path.endswith("/messages")
        )
    if method == "POST":
        return path == "/v1/responses"
    return False


@app.api_route("/api/hermes/{path:path}", methods=["GET", "POST"])
async def hermes_proxy(path: str, request: Request) -> Response:
    target = "/" + path
    if not _proxy_allowed(request.method, target):
        return Response(status_code=403, content="path not allowed")
    hermes = HERMES
    body = await request.body()
    params = dict(request.query_params)

    def do_request() -> requests.Response:
        return requests.request(
            request.method, hermes.base + target, params=params,
            headers=hermes.headers(), data=body if body else None, timeout=300,
        )

    resp = await asyncio.to_thread(do_request)
    return Response(content=resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("Content-Type", "application/json"))


@app.get("/api/hud/tools")
async def hud_tools() -> JSONResponse:
    """Tools the HUD chat actually runs. Hermes skill packs are CLI/Telegram only."""
    return JSONResponse({"chat": HUD_CHAT_TOOLS})


@app.post("/api/chat")
async def hud_chat(request: Request) -> JSONResponse:
    """Typed chat from the HUD — same brain as voice."""
    body = await request.json()
    text = (body.get("input") or "").strip()
    conversation = body.get("conversation") or (CFG.get("hermes") or {}).get("conversation", "jarvis-main")
    if not text:
        return JSONResponse({"error": "empty input"}, status_code=400)
    pipe = get_pipeline()
    if re.match(r"^/(new|reset|clear)\b", text, re.I):
        pipe.clear_chat_history(conversation)
        return JSONResponse({"text": "Listo, conversación nueva.", "tools": [], "run_id": None})
    out: dict = {"text": "", "tools": [], "run_id": None}

    def run_sync() -> None:
        timing = TurnTiming(turn_id=pipe.next_turn_id())
        parts: list[str] = []
        for kind, value in pipe.stream_llm_events_sync(text, timing, conversation):
            if kind == "text":
                parts.append(value)
            elif kind == "tool":
                out["tools"].append(json.loads(value))
            elif kind == "run":
                out["run_id"] = value
            elif kind == "final":
                info = json.loads(value)
                if info.get("content"):
                    parts = [info["content"]]
        out["text"] = "".join(parts).strip()
        pipe.log_turn(timing)

    try:
        await asyncio.to_thread(run_sync)
        return JSONResponse(out)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@app.post("/api/speak")
async def hud_speak(request: Request) -> Response:
    """Elena Argentina MP3 for typed chat and the first-click greeting."""
    body = await request.json()
    raw = (body.get("text") or "").strip()
    text = VoicePipelineServer._clean_for_tts(raw)
    if not text:
        return JSONResponse({"error": "empty text"}, status_code=400)
    try:
        mp3 = await synthesize_edge_mp3(text[:2500])
        record_usage(tts_chars=len(text))
        return Response(content=mp3, media_type="audio/mpeg",
                        headers={"Cache-Control": "no-store"})
    except Exception as exc:
        print(f"/api/speak failed: {exc}", flush=True)
        return JSONResponse({"error": str(exc)[:200]}, status_code=503)


# --------------------------------------------------------- chat attachments
# Extract everything to plain text server-side (images via Gemini vision
# description, PDF/DOCX via text extraction) and hand it back to the HUD as
# text the caller folds into the next /api/chat turn. Deliberately NOT wired
# into Hermes' native inline-image content parts or a multipart chat payload:
# that path only exists for images (not PDF/DOCX) and only when Hermes itself
# answers the turn, not the AI Router's Groq/Gemini fast lane — one text-only
# pipeline works identically either way, which is what "no quiero errores"
# actually needs. Capped hard (see FASE 1's Groq 413 incident in
# docs/JOB_AGENT.md): never inject unbounded text into a turn.
_ATTACH_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_ATTACH_MAX_CHARS = 12000
_ATTACH_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".log",
    ".py", ".js", ".ts", ".html", ".css", ".xml", ".ini", ".cfg", ".toml",
}
_ATTACH_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}


def _attach_truncate(text: str) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= _ATTACH_MAX_CHARS:
        return text, False
    return text[:_ATTACH_MAX_CHARS], True


def _extract_pdf_text(data: bytes) -> str:
    import pypdf
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"No pude abrir el PDF: {exc}") from exc
    if reader.is_encrypted:
        raise ValueError("El PDF está protegido con contraseña, no puedo leerlo.")
    pages = [(p.extract_text() or "") for p in reader.pages]
    text = "\n\n".join(t for t in pages if t.strip())
    if not text.strip():
        raise ValueError("No encontré texto en el PDF (puede ser un escaneo sin capa de texto).")
    return text


def _extract_docx_text(data: bytes) -> str:
    import docx
    try:
        doc = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"No pude abrir el documento Word: {exc}") from exc
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if not text.strip():
        raise ValueError("El documento Word no tiene texto legible.")
    return text


def _describe_attached_image(data: bytes, mime: str) -> str:
    from ai_router.base import ProviderError
    gemini = get_pipeline().ai_router.providers.get("gemini")
    if gemini is None:
        raise ValueError("No tengo un modelo con visión disponible ahora mismo para leer imágenes.")
    b64 = base64.b64encode(data).decode("ascii")
    try:
        text = gemini.describe_image(
            b64,
            "Describe en detalle qué hay en esta imagen: texto visible (transcríbelo tal cual), "
            "objetos, personas, contexto. Sé completo, esto sustituye a la imagen real para quien "
            "no puede verla.",
            max_tokens=700, mime_type=mime,
        )
    except ProviderError as exc:
        raise ValueError(f"No pude leer la imagen (fallo del modelo de visión: {exc.kind}).") from exc
    if not text.strip():
        raise ValueError("No pude describir la imagen.")
    return text


@app.post("/api/attach")
async def attach_file(request: Request) -> JSONResponse:
    """Multipart upload (field name "file") from the HUD's chat attach button.
    Returns {"filename", "kind", "text", "truncated"} — the HUD prepends
    "text" to the next /api/chat turn; nothing here touches the chat/LLM
    pipeline directly, so it works the same whether Hermes or the AI Router
    ends up answering."""
    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", None):
        return JSONResponse({"error": "no se recibió ningún archivo"}, status_code=400)
    filename = upload.filename
    ext = Path(filename).suffix.lower()
    data = await upload.read()
    if not data:
        return JSONResponse({"error": f"«{filename}» está vacío."}, status_code=400)
    if len(data) > _ATTACH_MAX_UPLOAD_BYTES:
        mb = _ATTACH_MAX_UPLOAD_BYTES // (1024 * 1024)
        return JSONResponse({"error": f"«{filename}» pesa demasiado (máximo {mb} MB)."}, status_code=400)

    try:
        if ext == ".pdf":
            kind, raw_text = "pdf", await asyncio.to_thread(_extract_pdf_text, data)
        elif ext == ".docx":
            kind, raw_text = "docx", await asyncio.to_thread(_extract_docx_text, data)
        elif ext in _ATTACH_IMAGE_MIME:
            kind = "image"
            raw_text = await asyncio.to_thread(_describe_attached_image, data, _ATTACH_IMAGE_MIME[ext])
        elif ext in _ATTACH_TEXT_EXTS or ext == "":
            try:
                raw_text = data.decode("utf-8")
            except UnicodeDecodeError:
                return JSONResponse(
                    {"error": f"«{filename}» no es texto legible (¿binario con otra extensión?)."},
                    status_code=400,
                )
            kind = "text"
        else:
            return JSONResponse({
                "error": f"Tipo de archivo no soportado ({ext or 'sin extensión'}). "
                         "Puedo leer PDF, Word (.docx), imágenes (png/jpg/webp/gif/bmp) "
                         "y archivos de texto/código.",
            }, status_code=400)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception as exc:
        print(f"/api/attach failed for {filename!r}: {exc}", flush=True)
        return JSONResponse({"error": f"No pude procesar «{filename}»: {exc}"[:300]}, status_code=500)

    text, truncated = _attach_truncate(raw_text)
    _log_activity("attachment", {"filename": filename, "kind": kind, "chars": len(text), "truncated": truncated})
    return JSONResponse({"filename": filename, "kind": kind, "text": text, "truncated": truncated})


@app.get("/api/usage")
async def usage() -> JSONResponse:
    """LLM token usage (local tally). TTS is Edge (no quota)."""
    u = read_usage()
    cost_cfg = CFG.get("usage") or {}
    cin = float(cost_cfg.get("llm_cost_per_mtok_input", 0) or 0)
    cout = float(cost_cfg.get("llm_cost_per_mtok_output", 0) or 0)

    def est(b: dict) -> float | None:
        if not (cin or cout):
            return None
        return round(b.get("llm_in", 0) / 1e6 * cin + b.get("llm_out", 0) / 1e6 * cout, 4)

    return JSONResponse({
        "llm": {
            "today": u["today"], "total": u["total"],
            "today_cost": est(u["today"]), "total_cost": est(u["total"]),
        },
        "elevenlabs": None,
        "tts": {"provider": "edge-tts", "voice": (CFG.get("voice") or {}).get("voice_id")},
    })


@app.get("/api/ai_status")
async def ai_status() -> JSONResponse:
    """Per-provider state (ONLINE/COOLDOWN/OFFLINE) + today's counters for the AI router,
    plus the actually-active provider/model from the most recent turn and any manual override."""
    if PIPELINE is None:
        return JSONResponse({"error": "pipeline not started"}, status_code=503)
    router = PIPELINE.ai_router
    return JSONResponse({
        "enabled": router.enabled,
        "priority": router.priority,
        "override": router.override,
        "override_options": router.valid_override_targets(),
        "active": {"provider": PIPELINE.last_provider, "model": PIPELINE.last_model,
                   "ts": PIPELINE.last_turn_ts},
        "providers": router.status(),
    })


@app.post("/api/ai_router/override")
async def set_ai_router_override(request: Request) -> JSONResponse:
    """Manually pin the next turns to one provider ({"provider": "groq"}), or
    clear the pin back to automatic cascade ({"provider": null})."""
    if PIPELINE is None:
        return JSONResponse({"error": "pipeline not started"}, status_code=503)
    body = await request.json()
    name = body.get("provider") or None
    valid = set(PIPELINE.ai_router.valid_override_targets())
    if name is not None and name not in valid:
        return JSONResponse(
            {"error": f"invalid provider {name!r}, must be one of {sorted(valid)} or null"},
            status_code=400,
        )
    PIPELINE.ai_router.set_override(name)
    print(f"[AI ROUTER] override set to {name!r} (manual, from HUD)", flush=True)
    return JSONResponse({"override": name})


WS_CLIENTS: set = set()

# FASE 3 (GO-gate): standing orders / loops propose an action and block here
# until a human clicks ALLOW/DENY on the existing approval-card UI, or the
# request times out. Keyed by a "standing:<uuid>" id so the WS handler can
# tell these apart from Hermes' own shell-approval run_ids.
_STANDING_APPROVALS: dict[str, asyncio.Future] = {}


async def _request_standing_approval(title: str, description: str, timeout_s: float = 600) -> tuple[str, int]:
    """Push a GO/NO-GO card to every connected HUD and wait for a decision.

    Returns (decision, sent) where decision is one of
    "allow"|"deny"|"timeout"|"no_clients". Core of the HTTP endpoint below and
    of the in-process risk gate (`run_gated`) — both must not act on anything
    but "allow".
    """
    approval_id = f"standing:{uuid.uuid4().hex}"
    payload = {
        "type": "approval_request",
        "run_id": approval_id,
        "data": {
            "approval_id": approval_id,
            "title": title or "Standing order pide autorización",
            "description": description or "",
        },
    }
    sent = 0
    for client in list(WS_CLIENTS):
        try:
            await client.send_json(payload)
            sent += 1
        except Exception:
            WS_CLIENTS.discard(client)
    if not sent:
        return "no_clients", 0
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    _STANDING_APPROVALS[approval_id] = fut
    try:
        decision = await asyncio.wait_for(fut, timeout=timeout_s)
    except asyncio.TimeoutError:
        decision = "timeout"
    finally:
        _STANDING_APPROVALS.pop(approval_id, None)
    _log_activity("go_gate_decision", {
        "approval_id": approval_id, "title": title,
        "description": description, "decision": decision, "sent_to": sent,
    })
    return decision, sent


@app.post("/api/standing/request_approval")
async def standing_request_approval(request: Request) -> JSONResponse:
    """Body: {"title": "...", "description": "...", "timeout_seconds": 600}
    Returns {"decision": "allow"|"deny"|"timeout"|"no_clients"}. A standing
    order (cron job, loop) calls this BEFORE taking any impactful action —
    it must not act on "timeout" or "no_clients", only on "allow"."""
    body = await request.json()
    decision, _sent = await _request_standing_approval(
        body.get("title") or "", body.get("description") or "",
        float(body.get("timeout_seconds") or 600),
    )
    return JSONResponse({"decision": decision})


# FASE 3 (GO-gate por niveles de riesgo, no binario): cada standing order/loop
# declara su propio nivel al registrarse -- nunca se infiere en caliente, eso
# sería otra alucinación de la que protegerse.
#   low      -> corre sin pausa (leer correo, consultar RAM/CPU)
#   medium   -> corre, y se notifica después sin bloquear (escribir un
#               archivo local, actualizar el kanban)
#   high     -> approval card ANTES de ejecutar (enviar un email, un comando)
#   critical -> approval card ANTES de ejecutar; si se deniega, el llamador no
#               debe reintentar automáticamente (borrar algo, tocar
#               credenciales/tokens, dinero real si algún día existe)
RISK_LEVELS = ("low", "medium", "high", "critical")


async def run_gated(*, risk: str, title: str, description: str, action) -> dict:
    """Runs `action` (sync or async, zero-arg) per its declared risk level.
    Returns {"executed", "risk", "decision", "result"} -- "decision" is None
    for low/medium (never gated), otherwise the ALLOW/DENY/timeout outcome.
    """
    if risk not in RISK_LEVELS:
        raise ValueError(f"unknown risk level {risk!r}, must be one of {RISK_LEVELS}")
    decision = None
    if risk in ("high", "critical"):
        decision, _sent = await _request_standing_approval(title, description)
        if decision != "allow":
            return {"executed": False, "risk": risk, "decision": decision, "result": None}
    result = action()
    if inspect.isawaitable(result):
        result = await result
    if risk == "medium":
        await _broadcast_announcement(f"{title}: hecho.")
    return {"executed": True, "risk": risk, "decision": decision, "result": result}


@app.post("/api/summon")
async def summon(request: Request) -> JSONResponse:
    """Broadcast a holographic media panel to all connected HUD clients.

    Body: {"media": "video"|"iframe"|"image", "src": "...", "title": "...",
           "position": "center"|"left"|"right"}  or  {"action": "dismiss"}
    Hermes can call this (curl with X-Jarvis-Token) to display media on the HUD.
    """
    body = await request.json()
    if body.get("action") == "dismiss":
        payload = {"type": "dismiss_panels"}
    else:
        payload = {"type": "summon_panel",
                   "media": body.get("media") or body.get("type") or "iframe",
                   "src": body.get("src", ""),
                   "title": body.get("title", "INCOMING FEED"),
                   "position": body.get("position", "center")}
    sent = 0
    for client in list(WS_CLIENTS):
        try:
            await client.send_json(payload)
            sent += 1
        except Exception:
            WS_CLIENTS.discard(client)
    return JSONResponse({"sent_to": sent})


@app.post("/api/yt_play")
async def yt_play(request: Request) -> JSONResponse:
    """Busca en YouTube y reproduce el primer resultado como panel del HUD.

    Body: {"query": "..."}. Pensado para que lo llame Hermes como herramienta
    (youtube_play) — reutiliza la misma búsqueda y el mismo panel embebido
    que ya usa el atajo local de server.py, así no hay dos implementaciones.
    """
    body = await request.json()
    query = (body.get("query") or "").strip()
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)
    pipe = get_pipeline()
    video_id = await asyncio.to_thread(pipe._yt_search_first_id, query)
    if not video_id:
        return JSONResponse({"error": f"No encontré ningún vídeo para «{query}»."})
    ok = await asyncio.to_thread(
        pipe._summon_panel, "video", "https://www.youtube.com/watch?v=" + video_id, query,
    )
    if not ok:
        return JSONResponse({"error": "No pude mostrar el panel en el HUD."})
    return JSONResponse({"ok": True, "video_id": video_id, "query": query})


@app.get("/api/weather")
async def weather_endpoint(location: str, day: str = "today") -> JSONResponse:
    """Direct Open-Meteo lookup (server/weather.py) — Hermes web_search +
    web_extract for weather was taking 40+ seconds for a simple lookup;
    this is a single free API call, ~1-2s. Query params: location, day
    ('today'|'tomorrow')."""
    import weather
    result = await asyncio.to_thread(weather.get_weather, location, day)
    return JSONResponse(result)


@app.get("/api/job_profile")
async def job_profile() -> JSONResponse:
    """Perfil estructurado para JOB_AGENT (server/job_match.py), leido de
    ~/.hermes/memories/job_profile.yaml. Pensado para que lo llame Hermes
    como herramienta (job_profile) antes de buscar empleo — le da los campos
    exactos (ubicacion, radio, salario minimo...) sin tener que adivinar
    parseando texto libre."""
    import job_match
    return JSONResponse(job_match.load_profile())


@app.post("/api/job_match")
async def job_match_endpoint(request: Request) -> JSONResponse:
    """Puntua, filtra, deduplica y ordena ofertas de empleo ya encontradas.

    Body: {"postings": [{title, url, company?, location?, salary?, contract?,
    schedule?, description?, requirements?, source?}, ...]}. No busca nada
    por si mismo — Hermes usa su propia busqueda web para encontrar las
    ofertas reales (LinkedIn, InfoJobs, Indeed, Google...); esta herramienta
    solo hace el trabajo de comparacion con el perfil en Python puro, sin
    gastar tokens del modelo en el calculo.
    """
    import job_match
    body = await request.json()
    postings = body.get("postings")
    if not isinstance(postings, list) or not postings:
        return JSONResponse({"error": "postings (non-empty list) is required"}, status_code=400)
    result = await asyncio.to_thread(job_match.match_jobs, postings)
    return JSONResponse(result)


async def _broadcast_announcement(text: str) -> int:
    """Push a standing-order message to every connected HUD (text bubble +
    spoken via the client's existing /api/speak path, no new TTS plumbing)."""
    payload = {"type": "announce", "text": text}
    sent = 0
    for client in list(WS_CLIENTS):
        try:
            await client.send_json(payload)
            sent += 1
        except Exception:
            WS_CLIENTS.discard(client)
    return sent


_GMAIL_FAILURE_MARKERS = (
    "No pude entrar a Gmail", "Gmail no respondió", "Gmail rechazó el token",
    "Gmail falló", "no pude leer los detalles",
)


def _classify_standing_result(text: str) -> str:
    """FASE 2's mandatory minimum check, deterministic (zero LLM tokens):
    distinguish "ok" (real content), "ok_vacio" (genuinely nothing to
    report), and "fallo_silencioso" (the call itself broke -- token expired,
    network down -- which must never be reported the same as "all quiet")."""
    if any(m in text for m in _GMAIL_FAILURE_MARKERS):
        return "fallo_silencioso"
    if "no hay correos sin leer" in text:
        return "ok_vacio"
    return "ok"


@app.post("/api/standing/gmail_brief")
async def standing_gmail_brief() -> JSONResponse:
    """Cron entry point (FASE 2): reuses _gmail_inbox_brief() unchanged,
    runs it through the FASE 3 risk gate (low -- reading email has no side
    effects), and pushes the result to every connected HUD."""
    pipe = get_pipeline()
    outcome = await run_gated(
        risk="low", title="Brief de correo",
        description="Revisar Gmail y resumir correo nuevo.",
        action=lambda: asyncio.to_thread(pipe._gmail_inbox_brief),
    )
    text = outcome["result"]
    status = _classify_standing_result(text)
    sent = await _broadcast_announcement(text)
    _log_activity("gmail_brief", {
        "text": text, "sent_to": sent, "status": status,
        "risk": outcome["risk"], "approval_required": outcome["decision"] is not None,
    })
    return JSONResponse({"text": text, "sent_to": sent, "status": status})


# ------------------------------------------------------------ FASE 4: audit

_ACTIVITY_LOG_PATH = ROOT / "logs" / "activity_log.jsonl"


def _log_activity(kind: str, detail: dict) -> None:
    """Append-only record of autonomous actions (standing orders, GO-gate
    decisions). Separate from self-check: this is history, not health."""
    row = {"ts": time.time(), "when": datetime.now().isoformat(timespec="seconds"),
           "kind": kind, "detail": detail}
    try:
        _ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ACTIVITY_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"activity log write failed: {exc}", flush=True)


@app.get("/api/activity")
async def get_activity(limit: int = 50) -> JSONResponse:
    """Last N autonomous-action log entries, newest first."""
    rows: list[dict] = []
    try:
        lines = _ACTIVITY_LOG_PATH.read_text(encoding="utf-8").splitlines()
        for line in lines[-max(1, min(limit, 500)):]:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except FileNotFoundError:
        pass
    rows.reverse()
    return JSONResponse({"entries": rows})


@app.get("/api/selfcheck")
async def selfcheck() -> JSONResponse:
    """Honest health check — each item can actually fail and say so."""
    checks: list[dict] = []

    h = (CFG.get("hermes") or {})
    try:
        key = os.environ.get(h.get("api_key_env", "API_SERVER_KEY"), "")
        r = await asyncio.to_thread(
            requests.get, f"{(h.get('base_url') or 'http://127.0.0.1:8642').rstrip('/')}/health/detailed",
            headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=8,
        )
        checks.append({"name": "hermes_api", "ok": r.ok, "detail": f"HTTP {r.status_code}"})
    except Exception as exc:
        checks.append({"name": "hermes_api", "ok": False, "detail": str(exc)[:200]})

    try:
        pipe = get_pipeline()
        await asyncio.to_thread(pipe._gmail_access_token)
        checks.append({"name": "gmail_oauth", "ok": True, "detail": "token válido"})
    except Exception as exc:
        checks.append({"name": "gmail_oauth", "ok": False, "detail": str(exc)[:200]})

    try:
        gs = json.loads((Path.home() / ".hermes" / "gateway_state.json").read_text(encoding="utf-8"))
        platforms = gs.get("platforms") or {}
        wa = (platforms.get("whatsapp") or {}).get("state")
        checks.append({"name": "whatsapp", "ok": wa == "connected",
                        "detail": wa or "no configurado"})
    except Exception as exc:
        checks.append({"name": "whatsapp", "ok": False, "detail": str(exc)[:200]})

    try:
        res = await asyncio.to_thread(
            subprocess.run, ["hermes", "cron", "status"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        out = (res.stdout or "") + (res.stderr or "")
        running = "Gateway is running" in out
        checks.append({"name": "cron_gateway", "ok": running, "detail": out.strip()[:300]})
    except Exception as exc:
        checks.append({"name": "cron_gateway", "ok": False, "detail": str(exc)[:200]})

    try:
        err_log = Path.home() / ".hermes" / "logs" / "errors.log"
        cutoff = time.time() - 900  # last 15 minutes
        recent = 0
        if err_log.exists():
            for line in err_log.read_text(encoding="utf-8", errors="ignore").splitlines()[-500:]:
                m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if not m or " WARNING " in line:
                    continue
                try:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                except ValueError:
                    continue
                if ts >= cutoff:
                    recent += 1
        checks.append({"name": "recent_errors", "ok": recent == 0,
                        "detail": f"{recent} error(es) en los últimos 15 min"})
    except Exception as exc:
        checks.append({"name": "recent_errors", "ok": False, "detail": str(exc)[:200]})

    healthy = all(c["ok"] for c in checks)
    return JSONResponse({"healthy": healthy, "checks": checks, "checked_at": datetime.now().isoformat(timespec="seconds")})


_WORKER_CACHE: dict = {"ts": 0.0, "data": [], "refreshing": False}


@app.get("/api/machines")
async def machines() -> JSONResponse:
    """Local (Mac) stats + configured remote workers.

    Worker polls can take seconds when a worker is offline, so they run in a
    background refresh; the endpoint always answers instantly from cache.
    """
    result: list[dict] = []
    mac: dict = {"name": "ESTE PC · JARVIS", "online": True}
    if psutil:
        mac.update({
            "cpu": psutil.cpu_percent(interval=0.1),
            "mem": psutil.virtual_memory().percent,
            "disk": psutil.disk_usage(str(ROOT)).percent,
        })
    result.append(mac)

    def poll_worker(w: dict) -> dict:
        info = {"name": w.get("name", w.get("host", "worker")), "online": False}
        url = w.get("stats_url")
        if url:
            try:
                r = requests.get(url, timeout=2)
                if r.ok:
                    info.update(r.json())
                    info["online"] = True
                    return info
            except Exception:
                pass
        import socket
        try:
            with socket.create_connection((w.get("host"), int(w.get("ping_port", 445))), timeout=1.5):
                info["online"] = True
                info["note"] = "online (no stats agent)"
        except Exception:
            pass
        return info

    workers = CFG.get("machines") or []
    now = time.time()
    if workers and now - _WORKER_CACHE["ts"] > 10 and not _WORKER_CACHE["refreshing"]:
        _WORKER_CACHE["refreshing"] = True

        async def refresh() -> None:
            try:
                data = [await asyncio.to_thread(poll_worker, w) for w in workers]
                _WORKER_CACHE.update(ts=time.time(), data=data)
            finally:
                _WORKER_CACHE["refreshing"] = False

        asyncio.get_running_loop().create_task(refresh())
    result.extend(_WORKER_CACHE["data"] or
                  [{"name": w.get("name", "worker"), "online": False, "note": "checking..."} for w in workers])
    return JSONResponse({"machines": result})


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/hud/")


if HUD_DIR.exists():
    app.mount("/hud", StaticFiles(directory=str(HUD_DIR), html=True), name="hud")


# ----------------------------------------------- Hermes dashboard TLS proxy
# The HUD (https) cannot iframe the plain-http dashboard (mixed content), so
# this second app reverse-proxies the entire dashboard over TLS, stripping
# frame-blocking headers. Served on its own port (see server.dashboard_proxy).

dash_app = FastAPI(title="Hermes Dashboard TLS Proxy")
_STRIP_HEADERS = {"x-frame-options", "content-security-policy", "content-length",
                  "transfer-encoding", "connection", "content-encoding"}


@dash_app.middleware("http")
async def dash_auth_middleware(request: Request, call_next):
    if not _request_authed(request):
        return Response(status_code=401, content="jarvis auth required")
    return await call_next(request)


def _dash_target() -> str:
    return ((CFG.get("server") or {}).get("dashboard_proxy") or {}).get(
        "target", "http://127.0.0.1:9119").rstrip("/")


@dash_app.websocket("/{path:path}")
async def dash_ws_proxy(ws: WebSocket, path: str) -> None:
    import websockets as wslib
    token = hud_token()
    if token and ws.cookies.get("jarvis_token") != token:
        await ws.close(code=4401)
        return
    await ws.accept()
    target = _dash_target().replace("http://", "ws://").replace("https://", "wss://")
    uri = f"{target}/{path}" + (f"?{ws.url.query}" if ws.url.query else "")
    try:
        async with wslib.connect(uri, max_size=None) as backend:
            async def client_to_backend() -> None:
                while True:
                    m = await ws.receive()
                    if m.get("text") is not None:
                        await backend.send(m["text"])
                    elif m.get("bytes") is not None:
                        await backend.send(m["bytes"])
                    elif m.get("type") == "websocket.disconnect":
                        break

            async def backend_to_client() -> None:
                async for m in backend:
                    if isinstance(m, str):
                        await ws.send_text(m)
                    else:
                        await ws.send_bytes(m)

            done, pending_t = await asyncio.wait(
                [asyncio.create_task(client_to_backend()),
                 asyncio.create_task(backend_to_client())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending_t:
                t.cancel()
    except Exception:
        pass


@dash_app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def dash_http_proxy(path: str, request: Request) -> Response:
    body = await request.body()
    fwd_headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ("host", "accept-encoding", "connection")}

    def do_request() -> requests.Response:
        params = {k: v for k, v in request.query_params.items() if k != "token"}
        return requests.request(
            request.method, f"{_dash_target()}/{path}",
            params=params, headers=fwd_headers,
            data=body if body else None, timeout=60, allow_redirects=False,
        )

    try:
        resp = await asyncio.to_thread(do_request)
    except Exception as exc:
        return Response(
            content=f"Hermes dashboard offline ({exc}).",
            status_code=502,
        )
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _STRIP_HEADERS}
    return Response(content=resp.content, status_code=resp.status_code, headers=out_headers)


# ------------------------------------------------------------------ WebSocket


@dataclass
class ConnState:
    audio_chunks: list = field(default_factory=list)
    recording: bool = False
    timing: TurnTiming | None = None
    turn_task: asyncio.Task | None = None
    current_run_id: str | None = None
    conversation: str = "jarvis-main"
    spoken_sentences: list = field(default_factory=list)
    interrupt_note: str | None = None
    partial_task: asyncio.Task | None = None
    last_partial_bytes: int = 0


async def _run_turn(ws: WebSocket, pipeline: VoicePipelineServer, conn: ConnState) -> None:
    timing = conn.timing
    assert timing is not None
    audio = b"".join(conn.audio_chunks)
    conn.audio_chunks = []
    try:
        transcript = await pipeline.transcribe(audio, timing)
        timing.transcript = transcript
        await ws.send_json({"type": "transcript", "text": transcript})
        if not transcript:
            await ws.send_json({"type": "error", "message": "No transcript detected."})
        else:
            if conn.interrupt_note:
                transcript_sent = (
                    f"[note: your previous spoken reply was cut off by the user after you said: "
                    f"\"{conn.interrupt_note}\"]\n{transcript}"
                )
                conn.interrupt_note = None
            else:
                transcript_sent = transcript
            conn.spoken_sentences = []
            await pipeline.stream_response_audio(ws, transcript_sent, timing, conn)
            timing.total_done_monotonic = time.perf_counter()
            await ws.send_json({"type": "done", "turn_id": timing.turn_id, "timing": timing.summary()})
    except asyncio.CancelledError:
        timing.errors.append("turn cancelled (barge-in or stop)")
        raise
    except Exception as exc:
        timing.errors.append(f"{type(exc).__name__}: {exc}")
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        timing.total_done_monotonic = timing.total_done_monotonic or time.perf_counter()
        pipeline.log_turn(timing)
        conn.timing = None
        conn.current_run_id = None


async def _cancel_active_turn(ws: WebSocket, pipeline: VoicePipelineServer, conn: ConnState,
                              stop_remote: bool = True) -> None:
    run_id = conn.current_run_id  # capture BEFORE cancel: turn cleanup clears it
    turn_was_active = conn.turn_task is not None and not conn.turn_task.done()
    if turn_was_active:
        if conn.spoken_sentences:
            conn.interrupt_note = conn.spoken_sentences[-1]
        conn.turn_task.cancel()
        try:
            await conn.turn_task
        except (asyncio.CancelledError, Exception):
            pass
    if stop_remote and run_id and turn_was_active:
        conn.current_run_id = None
        try:
            res = await asyncio.to_thread(pipeline.hermes.stop_run, run_id)
            # 404 = session runs not in the runs registry on this Hermes build;
            # dropping the SSE stream (above) still cuts the turn off.
            msg = "Run halted." if res["status_code"] in (200, 202, 404) else f"Stop returned {res['status_code']}."
            await ws.send_json({"type": "status", "message": msg})
        except Exception as exc:
            await ws.send_json({"type": "status", "message": f"Stop failed: {exc}"})


def _maybe_schedule_partial(ws: WebSocket, pipeline: VoicePipelineServer, conn: ConnState) -> None:
    stt_cfg = CFG.get("stt") or {}
    if not stt_cfg.get("partials", True) or not conn.recording:
        return
    if conn.partial_task and not conn.partial_task.done():
        return
    buf = b"".join(conn.audio_chunks)
    min_new = int(16000 * 2 * float(stt_cfg.get("partial_interval", 1.2)))
    if len(buf) < 16000 or len(buf) - conn.last_partial_bytes < min_new or len(buf) > 16000 * 2 * 30:
        return
    conn.last_partial_bytes = len(buf)

    async def run() -> None:
        try:
            text = await pipeline.transcribe(buf)
            if text and conn.recording:
                await ws.send_json({"type": "partial_transcript", "text": text})
        except Exception:
            pass

    conn.partial_task = asyncio.create_task(run())


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if not _ws_allowed(ws):
        await ws.close(code=4401)
        return
    await ws.accept()
    WS_CLIENTS.add(ws)
    pipeline = get_pipeline()
    conn = ConnState(conversation=(CFG.get("hermes") or {}).get("conversation", "jarvis-main"))
    await ws.send_json({"type": "status", "message": "Hermes voice server connected."})
    try:
        while True:
            message = await ws.receive()
            if "text" in message and message["text"] is not None:
                event = json.loads(message["text"])
                etype = event.get("type")
                if etype == "start":
                    await _cancel_active_turn(ws, pipeline, conn)  # barge-in
                    if event.get("conversation"):
                        conn.conversation = str(event["conversation"])
                    conn.audio_chunks = []
                    conn.last_partial_bytes = 0
                    conn.recording = True
                    conn.timing = TurnTiming(turn_id=pipeline.next_turn_id())
                    conn.timing.audio_start_monotonic = time.perf_counter()
                    conn.timing.stt_model = CFG["stt"]["model"]
                    await ws.send_json({"type": "status", "message": f"Turn {conn.timing.turn_id} recording started."})
                elif etype == "stop":
                    if conn.timing is None:
                        await ws.send_json({"type": "error", "message": "Received stop before start."})
                        continue
                    conn.recording = False
                    conn.timing.end_of_speech_monotonic = time.perf_counter()
                    conn.turn_task = asyncio.create_task(_run_turn(ws, pipeline, conn))
                elif etype == "stop_run":
                    # PARAR pressed mid-recording (no "stop" sent yet, so no
                    # turn_task exists for _cancel_active_turn to cancel) —
                    # without this, conn.recording stayed True forever: the
                    # server kept silently accumulating audio_chunks from a
                    # client that had already reset its own UI on the next
                    # "start", and the client-side mic UI (see stopRun() in
                    # index.html) had nothing server-side telling it this
                    # recording was actually abandoned.
                    if conn.recording:
                        conn.recording = False
                        conn.audio_chunks = []
                        if conn.partial_task and not conn.partial_task.done():
                            conn.partial_task.cancel()
                    await _cancel_active_turn(ws, pipeline, conn)
                    await ws.send_json({"type": "agent_status", "state": "stopped"})
                elif etype == "approval_decision":
                    run_id = event.get("run_id") or conn.current_run_id
                    if isinstance(run_id, str) and run_id.startswith("standing:"):
                        fut = _STANDING_APPROVALS.get(run_id)
                        decision = event.get("decision", "deny")
                        if fut and not fut.done():
                            fut.set_result(decision)
                        await ws.send_json({"type": "status", "message": f"Standing order: {decision}."})
                        continue
                    if not run_id:
                        await ws.send_json({"type": "error", "message": "No run for approval."})
                        continue
                    decision = event.get("decision", "deny")
                    body = {
                        "decision": decision,
                        "approved": decision == "allow",
                        "approval_id": event.get("approval_id"),
                    }
                    res = await asyncio.to_thread(pipeline.hermes.post_approval, run_id, body)
                    await ws.send_json({"type": "status", "message": f"Approval sent ({res['status_code']})."})
                else:
                    await ws.send_json({"type": "error", "message": f"Unknown event type: {etype}"})
            elif "bytes" in message and message["bytes"] is not None:
                if conn.recording:
                    conn.audio_chunks.append(message["bytes"])
                    _maybe_schedule_partial(ws, pipeline, conn)
    except WebSocketDisconnect:
        if conn.turn_task and not conn.turn_task.done():
            conn.turn_task.cancel()
        print("Client disconnected", flush=True)
    finally:
        WS_CLIENTS.discard(ws)


def main() -> int:
    server = CFG["server"]
    host = server.get("host", "0.0.0.0")
    port = int(server.get("port", 8765))
    tls_ports = server.get("tls_ports") or ([server["tls_port"]] if server.get("tls_port") else [])
    cert = server.get("tls_cert")
    key = server.get("tls_key")
    print(f"Starting Hermes voice server on ws://{host}:{port}/ws", flush=True)
    if tls_ports and cert and key and (ROOT / cert).exists() and (ROOT / key).exists():
        servers = [uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))]
        for tp in tls_ports:
            print(f"HUD available on https://{host}:{tp}/hud/", flush=True)
            servers.append(uvicorn.Server(uvicorn.Config(
                app, host=host, port=int(tp), log_level="info",
                ssl_certfile=str(ROOT / cert), ssl_keyfile=str(ROOT / key),
            )))

        dp = server.get("dashboard_proxy") or {}
        if dp.get("port"):
            print(f"Dashboard proxy on https://{host}:{dp['port']}/", flush=True)
            servers.append(uvicorn.Server(uvicorn.Config(
                dash_app, host=host, port=int(dp["port"]), log_level="warning",
                ssl_certfile=str(ROOT / cert), ssl_keyfile=str(ROOT / key),
            )))

        async def serve_all() -> None:
            await asyncio.gather(*[s.serve() for s in servers])

        asyncio.run(serve_all())
    else:
        uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
