# Architecture & Protocols

## How a voice turn flows

1. Browser captures mic at 16 kHz mono (AudioContext created at 16 kHz —
   avoids resampling artifacts that wreck Whisper accuracy) and streams int16
   PCM over WebSocket `/ws`.
2. While you speak, the server runs incremental Whisper passes every ~1.2 s
   and emits `partial_transcript` events (live captions).
3. On stop: final transcription — the server first tries the optional **GPU
   STT worker** (`stt.remote`, big model, ~0.2 s) and falls back to local
   Whisper if it's unreachable — then the transcript goes to Hermes via its **Sessions
   API** (`POST /api/sessions/{id}/chat/stream`, SSE). Session ids persist in
   `logs/hermes_sessions.json` per conversation name, so memory survives
   restarts. Typed chat (`/api/chat`) uses the *same* session.
4. SSE events parsed: `run.started` (run id → STOP support), `assistant.delta`
   (text), `tool.started` (name + preview → HUD activity), `assistant.completed`
   (incl. `interrupted` flag), `run.completed` (token usage), `*approval*`
   (→ HUD approval cards).
5. Text is sentence-split, markdown/think-block-stripped, **secret-redacted**,
   then each sentence streams through ElevenLabs back to the client as raw PCM
   while generation continues.

Why the Sessions API and not `/v1/responses` or `/v1/runs`: on Hermes v0.16,
sessions are the only surface that combines named persistent memory, run ids,
tool events, and approval events in one stream.

## WebSocket protocol (voice clients)

Client → server (JSON + binary):

```
{"type":"start","sample_rate":16000,"format":"pcm_s16le","channels":1,"conversation":"jarvis-main"?}
<binary int16 PCM chunks>
{"type":"stop"}
{"type":"stop_run"}
{"type":"approval_decision","run_id":...,"approval_id":...,"decision":"allow"|"deny"}
```

`start` during an active turn = barge-in: the server cancels the turn, stops
the Hermes run, records the last spoken sentence, and prefixes the next turn's
input with an interruption note so the agent's memory matches what you heard.

Server → client:

```
status · partial_transcript · transcript · run_started{run_id}
agent_status{state: thinking|tool_use(+tool,preview)|speaking|stopped}
approval_request{data,run_id} · error · done{timing}
<binary TTS PCM — frames split at arbitrary byte boundaries; buffer odd bytes>
```

## HTTP endpoints (voice server)

| Endpoint | Purpose |
|---|---|
| `/hud/` | the HUD (static, single file) |
| `/api/hermes/{path}` | **allowlist** proxy to Hermes API, injects the bearer key (GET: health, capabilities, skills, toolsets, jobs, sessions; POST: v1/responses only) |
| `/api/chat` | typed chat turn on the shared voice session |
| `/api/machines` | host psutil stats + remote workers from config |
| `/api/usage` | local token/char tally + ElevenLabs quota (needs user_read on the key) |
| `/api/summon` | broadcasts a holographic media panel (`{media, src, title, position}` or `{action:"dismiss"}`) to every connected HUD over its WebSocket — this is what the bundled `hud_display` Hermes plugin calls |
| port 9443 (separate app) | TLS reverse proxy of the Hermes dashboard with WebSocket bridge and frame-header stripping, so the HTTPS HUD can iframe it |

## Voice engine: local XTTS-v2 clone (2026-09-07)

`voice.provider` in `server/config/server.yaml` picks the TTS engine:
`edge-tts` (Microsoft, free, the original engine, config still intact) or
`xtts` (local voice clone, now the default). Both are called through the
same single entry point, `synthesize_edge_mp3()` in server.py — it branches
internally, so neither call site (`/api/speak`, the live voice turn) needed
to change.

`xtts` requires a **separate persistent process**,
`server/xtts/tts_service.py`, running on `127.0.0.1:8790` — it is NOT
started by `server.py` itself. `start_jarvis.ps1` / `parar_jarvis.ps1`
already manage it alongside everything else; to run it by hand:
`server/xtts/.venv/Scripts/python.exe tts_service.py`.

Why a separate venv (`server/xtts/.venv`, Python 3.11, not the main
`server/.venv`): XTTS-v2 needs `torch` + `coqui-tts`, a heavy ML stack that
would otherwise pull unrelated version constraints into the main server's
dependencies. The service loads the model once at startup (~10s) and stays
resident — server.py just POSTs `{"text": ...}` to `/tts` and gets WAV back,
which gets re-encoded to MP3 via the same `ffmpeg` binary `_apply_voice_fx`
already uses.

Voice source: `server/xtts/reference.wav`, a Fish Audio "Jarvis (MCU)"
sample. Tried and rejected before landing here: raising XTTS's `temperature`
(sounded worse, "muy artificial" — cloning a clone amplifies synthetic
artifacts, more creativity in sampling doesn't fix that), F5-TTS (12x slower
on CPU, ~2min/sentence, and its default checkpoint isn't actually trained on
Spanish — came out sounding Portuguese), and the user's own recorded voice
(technically higher-fidelity clone, but doesn't carry any of the
Jarvis-character energy the user wants, and default settings had a
noticeably slow/dragging cadence). No NVIDIA GPU on this machine — DirectML
(the only AMD-GPU acceleration path for PyTorch on Windows) is
Microsoft-abandoned and capped at torch 2.3, incompatible with what
XTTS/coqui-tts need — so this runs CPU-only. ~10-12s to generate one short
sentence; acceptable for a voice assistant, not instant.

Getting the *exact* movie JARVIS voice (Paul Bettany's performance) isn't
something to build here even if a "better" reference clip existed: it's a
real actor's protected voice, not a technical problem this repo should
solve by scraping/ripping film audio. Fish Audio's own paid "Jarvis (MCU)"
voice (`612b878b113047d9a770c069c8b4fdfe`) is the legitimate way to get
that specific voice — needs paid API credit on fish.audio, not available in
this budget right now.

## Auth model

`JARVIS_HUD_TOKEN` (env) gates `/api/*`, the dashboard proxy, and
browser-originated WebSockets (Origin allowlist + cookie). Native clients (the
PTT client, test scripts) send no Origin header and are exempt — the
threat model is a malicious *website* doing cross-origin requests against your
LAN, not your own processes. The Hermes API key never reaches any browser.

## Latency profile (Apple Silicon, small.en on CPU)

STT finalize ~0.7 s · Hermes first token 1–3 s (more with tools) · first
audible audio ~3 s · total simple turn ~4 s. The biggest lever is the LLM
behind Hermes; the second is the Whisper model size.

## Gotchas encoded in this repo (learned the hard way)

1. **SSE charset**: Hermes' event stream has no charset header; Python
   `requests` then decodes as Latin-1 → mojibake. The client forces UTF-8.
2. **macOS port 443**: non-root binds only work on the wildcard address
   (`0.0.0.0`), not a specific IP.
3. **launchd + external drives**: see launchd/*.plist comments (TCC hang on
   `getcwd`, EX_CONFIG from external log paths, venv-python invocation).
4. **Browser mic**: requires a secure context — hence the self-signed TLS and
   the per-device cert trust.
5. **ElevenLabs frames** arrive at arbitrary byte boundaries; decode only
   complete int16 pairs and carry the leftover byte.
6. **Near-silent audio** makes faster-whisper raise ("No clip timestamps
   found") — both STT paths treat any transcription exception as an empty
   transcript rather than failing the turn.
7. **Windows venv + NVIDIA pip wheels**: locate the cuBLAS/cuDNN DLLs via
   `sys.prefix` (`site.getsitepackages()` misses the venv) and
   `os.add_dll_directory` them before importing ctranslate2.
8. **Startup**: listeners open immediately; the local Whisper fallback warms
   in the background, exactly once and race-locked (the startup hook fires
   once per uvicorn listener — concurrent recorder inits crash lifespans).
9. **FD limits**: launchd grants 256 file descriptors by default and the
   server idles at ~244 — set NumberOfFiles=8192 in the plist (shipped) and
   close streaming responses in `finally` (done) or it dies within hours.
10. **Orphaned STT children steal ports**: the recorder's child process has a
    cmdline without "server.py", survives naive pkills, and can inherit the
    listen socket — the next spawn then fails its first bind and uvicorn's
    SystemExit cancels ALL listeners. Stop by PORT ownership
    (`lsof -ti tcp:PORT -sTCP:LISTEN | xargs kill -9`) — shipped in
    `scripts/jarvis-stop.sh`.
11. **Agent tool-choice**: SOUL.md prose cannot redirect the model away from
    attractive built-in tools (it kept "showing" videos in its own invisible
    browser); a first-class plugin tool with an explicit schema wins
    instantly. Hence `hermes-plugin/hud_display`.
