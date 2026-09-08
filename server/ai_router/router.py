"""AI Router: try fast free-tier online models before falling back to Hermes.

Priority cascade (config-driven, default groq -> gemini -> openrouter_free
-> openrouter_paid -> hermes). openrouter_paid spends real money (Jesús'
existing prepaid OpenRouter balance) and is deliberately the LAST online
resort, tried only once every free tier is unavailable — see cost_guard.py
for the hard, persisted spend cap that makes this a real limit, not a
documented one. Any turn that plausibly needs a Hermes-exclusive capability
(terminal, real web search, file ops, cron/kanban, skills) skips the whole
cascade and goes straight to Hermes — those capabilities cannot be
reproduced by a raw chat API call, so trying to be "fast" there would just
mean answering wrong or pretending. Everything else races through the
online providers first.

Local PC actions (WhatsApp, Gmail send, folders, Spotify, humanoid view) are
already intercepted upstream by VoicePipelineServer._try_pc_action() before
this module is ever reached — provider-agnostic today, untouched here. The
one local capability that previously required Hermes' own reasoning was
YouTube/media-panel control (see hud_display plugin) — this router restores
that for online providers via a small function-calling tool set executed
locally, so switching providers doesn't lose it.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Iterator

import requests

from .base import ProviderError
from .cost_guard import CostCappedProvider
from .providers.gemini import GeminiProvider
from .providers.groq import GroqProvider
from .providers.openrouter import OpenRouterProvider
from .quota import ProviderQuota

def _load_env_files() -> None:
    for p in [Path.home() / ".hermes" / ".env", Path(__file__).resolve().parent.parent / ".env"]:
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

PROVIDER_CLASSES = {"groq": GroqProvider, "gemini": GeminiProvider,
                    "openrouter_free": OpenRouterProvider, "openrouter_paid": OpenRouterProvider}

DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-120b",
    "gemini": "gemini-2.5-flash",
    "openrouter_free": "openrouter/free",
    "openrouter_paid": "openai/gpt-oss-20b",  # ~$0.03/$0.13 per 1M tok — verify at openrouter.ai/models
    "openrouter_reasoning": "anthropic/claude-opus-5-20260723",  # deep-reasoning tier, see below
}

# $/1M tokens per paid tier, verified live against openrouter.ai/api/v1/models.
# Re-check if you change either model — these are what CostCappedProvider
# uses to estimate worst-case cost before ever making the network call.
DEFAULT_PAID_PRICE_PER_MTOK = {
    "openrouter_paid": {"input": 0.03, "output": 0.13},  # verified 2026-09-01, openai/gpt-oss-20b
    # Claude Opus 5 via OpenRouter, verified 2026-09-07 (anthropic/claude-opus-5-20260723).
    # Swap to GPT-6 Astra later (openai/gpt-6-astra, $10/$50 per 1M — exactly
    # 2x Opus 5's price) by changing ai_router.openrouter.reasoning.model and
    # these two numbers in config, nothing else.
    "openrouter_reasoning": {"input": 5.0, "output": 25.0},
}

# Measured live (2026-09-01): "low" cuts openai/gpt-oss-120b's hidden
# reasoning tokens ~4x and latency ~40% vs default effort, same answer
# quality for short conversational replies. Re-verify if you change the
# default Groq model away from a gpt-oss/harmony one.
DEFAULT_REASONING_EFFORT = {"groq": "low"}

# Measured live (2026-09-01): Gemini 2.5's "thinking" is on by default and
# was truncating short answers (ate the output budget) at ~1.8s; budget 0
# gave a clean, complete answer at ~0.7s. Raise per-provider in config for
# turns that need real reasoning depth over speed.
DEFAULT_THINKING_BUDGET = {"gemini": 0}

# Fast, zero-latency shortcuts for the clearest, lowest-variance cases —
# fixed vocabulary (terminal, cron, kanban...) where a keyword essentially
# can't have a different meaning. This list is deliberately NOT extended for
# open-ended natural-language categories like weather/real-time info anymore
# — found live, repeatedly: "cómo ESTABA el tiempo", then "el tiempo COMO
# va a estar" (different word order), each one breaking the previous exact
# pattern. Patching one phrasing at a time doesn't converge; classify_needs_hermes()
# below now falls back to a free local-LLM classifier for exactly that kind
# of open-ended case instead of growing this list further.
DEFAULT_FORCE_HERMES_KEYWORDS = [
    r"\b(terminal|comando|ejecuta(?:r)?|c[oó]digo|script|instala|compila)\b",
    r"busca(?:me)?\s+(?:en\s+internet|en\s+la\s+web|en\s+google)|investiga\b",
    r"\b(cron|programa(?:r)?\s+una\s+tarea|tarea\s+recurrente|agenda\s+algo)\b",
    r"\b(kanban|tablero|tarjeta\s+de\s+tarea)\b",
    r"\b(lee|escribe|crea|borra)\s+(?:este\s+)?archivo\b",
    r"\bskills?\b|\bhabilidad(?:es)?\b",
    r"\bcalendar(?:io)?\b",
    # "can you...?" / "do you have access to...?" — only Hermes actually
    # knows its own real tools/OAuth state; an online model has no visibility
    # into that and will confidently guess wrong (found live: it told the
    # user it had no Google Calendar access when Hermes genuinely does).
    r"tienes\s+acceso|puedes\s+acceder|acceso\s+a\b|qu[eé]\s+puedes\s+hacer|de\s+qu[eé]\s+eres\s+capaz",
    # cheap, unambiguous weather shortcuts — anything less exact than this
    # goes through the LLM fallback below rather than growing this regex.
    r"\bclima\b|qu[eé]\s+tiempo|va\s+a\s+llover",
    # JOB_AGENT (docs/instrucciones herrameintas.md): búsqueda de empleo
    # necesita web search real + las herramientas job_profile/job_match
    # (hermes-plugin/job_search) — ningún modelo online las tiene.
    r"(?:busca(?:me)?|encu[eé]ntrame)\s+(?:un\s+|una\s+)?(?:trabajo|empleo|ofertas?)|"
    r"ofertas?\s+de\s+(?:trabajo|empleo)|empleo\s+cerca|trabajos?\s+cerca|"
    r"trabajos?\s+que\s+encaj(?:e|en)|ofertas?\s+seg[uú]n\s+mi\s+perfil",
]

# Words/idioms where "tiempo" does NOT mean weather — gates the free-text
# LLM classifier below away from an unnecessary round trip on the common
# non-weather uses of the word, without needing to enumerate every WAY of
# asking about weather (that's the part regex can't scale to).
_TIEMPO_NONWEATHER_RE = re.compile(
    r"tengo\s+tiempo|cu[aá]nto\s+tiempo|al\s+mismo\s+tiempo|tiempo\s+libre|"
    r"tiempo\s+de\s+sobra|tiempo\s+suficiente|(?:pierdo|perder|ganar)\s+tiempo|"
    r"hace\s+tiempo|(?:mucho|poco)\s+tiempo|tiempo\s+real|tiempo\s+de\s+descanso|"
    r"tiempo\s+de\s+reacci[oó]n",
    re.I,
)

LOCAL_MODEL_URL = "http://127.0.0.1:8081/v1/chat/completions"


def _llm_needs_hermes(text: str) -> bool:
    """Free local-model fallback for open-ended phrasing the keyword list
    above can't enumerate (weather being the recurring live example — see
    DEFAULT_FORCE_HERMES_KEYWORDS comment). Same local model/endpoint
    server.py already uses for WhatsApp/YouTube intent extraction, so this
    costs zero cloud tokens. Defaults to True (send to Hermes) on any
    failure/timeout — a wrong "I can't check that" from an online model is
    worse than one turn taking the slightly slower Hermes path."""
    prompt = (
        "Un asistente de voz decide si puede responder él mismo (charla, "
        "opiniones, explicaciones generales, saludos) o si debe delegar en "
        "un agente completo con herramientas reales: tiempo/clima, noticias, "
        "precios actuales, resultados deportivos, o cualquier dato que "
        "cambie con el tiempo y que no pueda saber de memoria. El mensaje "
        "puede venir de un dictado por voz con erratas de transcripción "
        "(nombres de sitios mal transcritos, letras de más o de menos, "
        "acentos ausentes) — juzga la intención real, no la ortografía "
        "exacta ni el orden de las palabras.\n\n"
        f"Mensaje: \"{text}\"\n\n"
        "Responde con EXACTAMENTE una palabra: AGENTE si necesita el agente "
        "completo, o CHARLA si el asistente puede responder él mismo."
    )
    # Fast path: Groq / Gemini (0.3-0.5s instead of 10s CPU timeout)
    for k in [os.environ.get("GROQ_API_KEY"), os.environ.get("GROQ_API_KEY_2"), os.environ.get("GROQ_API_KEY_3")]:
        if not k:
            continue
        try:
            r = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {k}"},
                json={"model": "qwen/qwen3.8-27b", "messages": [{"role": "user", "content": prompt}], "max_tokens": 10, "temperature": 0},
                timeout=2,
            )
            if r.ok:
                out = r.json()["choices"][0]["message"]["content"].strip().upper()
                return "AGENTE" in out
        except Exception:
            pass

    gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gemini_key:
        try:
            r = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                headers={"Authorization": f"Bearer {gemini_key}"},
                json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": prompt}], "max_tokens": 10, "temperature": 0},
                timeout=2,
            )
            if r.ok:
                out = r.json()["choices"][0]["message"]["content"].strip().upper()
                return "AGENTE" in out
        except Exception:
            pass

    try:
        resp = requests.post(
            LOCAL_MODEL_URL,
            json={"model": "gpt-oss:20b", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 80, "temperature": 0, "reasoning_effort": "low"},
            timeout=5,
        )
        resp.raise_for_status()
        out = resp.json()["choices"][0]["message"]["content"].strip().upper()
        return "AGENTE" in out
    except Exception:
        return True


# ---------------------------------------------------- deep-reasoning escalation
# Paid tier (Claude Opus 5 via OpenRouter today; swap the model in config
# later, e.g. to GPT-6 Astra once its price drops — everything below is
# model-agnostic on purpose). Reserved for problems that genuinely need it:
# multi-step planning, debugging, contradictions, real ambiguity, high-stakes
# decisions. Gated behind a FREE classifier so the paid model is never asked
# whether it's needed — that would spend money to decide whether to spend
# money.
REASONING_PROVIDER_KEY = "openrouter_reasoning"

_REASONING_TRIGGER_RE = re.compile(
    r"\bpi[eé]nsa(?:lo|te)?\b|\braz[oó]na(?:lo)?\b|analiza\s+a\s+fondo|"
    r"profundiza|reflexiona|con\s+calma\s+y\s+detalle|pi[eé]nsalo\s+bien|"
    r"esto\s+es\s+dif[ií]cil|es\s+complicado|necesito\s+que\s+razones",
    re.I,
)


def _llm_reasoning_level(text: str) -> str:
    """Free local-model classification -> "low"|"medium"|"high", same
    pattern/endpoint as _llm_needs_hermes (zero cloud cost). Defaults to
    "low" (never escalate) on any failure/timeout: an unwanted escalation
    spends real money, silence must never accidentally trigger that."""
    prompt = (
        "Clasifica cuánta dificultad de razonamiento necesita este mensaje "
        "para responder bien: baja, media o alta.\n"
        "ALTA: problemas con varios pasos, decisiones entre varias "
        "alternativas, contradicciones, ambigüedad real, debugging difícil, "
        "planificación estratégica, análisis de errores complejos, muchas "
        "restricciones a la vez.\n"
        "MEDIA: preguntas normales que requieren pensar un poco pero no son "
        "un problema complejo.\n"
        "BAJA: saludos, charla, preguntas simples, comandos, cualquier cosa "
        "rutinaria.\n\n"
        f"Mensaje: \"{text}\"\n\n"
        "Responde con EXACTAMENTE una palabra: ALTA, MEDIA o BAJA."
    )
    try:
        resp = requests.post(
            LOCAL_MODEL_URL,
            json={"model": "gpt-oss:20b", "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 10, "temperature": 0, "reasoning_effort": "low"},
            timeout=10,
        )
        resp.raise_for_status()
        out = resp.json()["choices"][0]["message"]["content"].strip().upper()
        if "ALTA" in out:
            return "high"
        if "MEDIA" in out:
            return "medium"
    except Exception:
        pass
    return "low"


def classify_reasoning_level(text: str) -> str:
    """Explicit trigger words short-circuit straight to "high" (free, no
    round trip at all); everything else goes through the local classifier."""
    if _REASONING_TRIGGER_RE.search(text or ""):
        return "high"
    return _llm_reasoning_level(text or "")


def _build_reasoning_messages(pipeline, transcript: str, conversation: str) -> list[dict]:
    """Compressed package for the reasoning tier: a short recent window, NOT
    the router's normal history_turns history. A deep-reasoning problem
    hinges on the current ask, not a long backlog — keeping this short is
    most of the cost saving (full RAG-style relevant-memory retrieval is a
    future upgrade, not built here — see docs/AI_ROUTER.md)."""
    with pipeline._chat_lock:
        hist = list(pipeline._chat_history.get(conversation) or [])
    return hist[-4:] + [{"role": "user", "content": transcript}]


# Small, deliberately-curated subset of HUD_TOOLS (server.py) that CAN be
# executed without Hermes — everything else in that list either already goes
# through _try_pc_action before reaching here, or needs Hermes machinery
# (send_whatsapp, draft_job_email) and is intentionally left off: giving an
# online model a tool it can only half-execute is worse than no tool at all.
ROUTER_TOOLS = [
    {"type": "function", "function": {
        "name": "open_youtube",
        "description": "Abre YouTube o YouTube Music en este PC. Música, un vídeo, un artista, un tema.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Qué buscar. Vacío = abrir YouTube Music."}}},
    }},
    {"type": "function", "function": {
        "name": "open_browser",
        "description": "Abre el navegador con una web o una búsqueda de Google.",
        "parameters": {"type": "object", "properties": {
            "url_or_query": {"type": "string", "description": "https://... o texto a buscar"}},
            "required": ["url_or_query"]},
    }},
    {"type": "function", "function": {
        "name": "open_folder",
        "description": "Abre una carpeta o un archivo en el Explorador de Windows por nombre.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Nombre de carpeta o archivo"}},
            "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "check_gmail",
        "description": (
            "Lee la bandeja de Gmail de Jesús con OAuth ya vinculado. SOLO para leer/"
            "resumir correo nuevo — si piden dejar de ver, silenciar o desuscribirse de "
            "algo, usa mute_email_sender en su lugar, nunca esta."
        ),
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "mute_email_sender",
        "description": (
            "Deja de mencionar en los resúmenes de bandeja los correos de un remitente "
            "o tema concreto. Úsalo cuando pidan dejar de ver, silenciar, ignorar o "
            "'desuscribirse' (en el sentido de que Jarvis no los saque más) de correos "
            "de alguien o de un tema — NO cancela ninguna suscripción real, ni toca Gmail, "
            "solo cambia lo que Jarvis elige mencionar."
        ),
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string",
                       "description": "Remitente (nombre o parte del email) o tema/asunto a silenciar"}},
            "required": ["target"]},
    }},
    {"type": "function", "function": {
        "name": "look_with_camera",
        "description": (
            "Activa la cámara web de este PC, toma una foto y describe qué hay en ella. "
            "Úsalo cuando pidan mirar algo con la cámara, activarla, o qué ve Jarvis."
        ),
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string",
                         "description": "Qué buscar o describir en la imagen. Vacío = descripción general."}}},
    }},
]

# only pay for the extra non-streaming tool-detection round trip when the
# turn actually looks like it might need one of the tools above.
_TOOL_HINT_RE = re.compile(
    r"youtube|v[ií]deo|m[uú]sica|canci[oó]n|\btema\b|reproduc|navegador|"
    r"busca en google|abre google|carpeta|explorador|gmail|bandeja|correo|"
    r"c[aá]mara|qu[eé] ves|mira esto|activa la c[aá]mara",
    re.I,
)


class AllProvidersFailedError(Exception):
    pass


DEFAULT_PRIORITY = ["groq", "gemini", "openrouter_free", "openrouter_paid"]


class AIRouter:
    def __init__(self, cfg: dict, logs_dir: Path):
        ai_cfg = cfg.get("ai_router") or {}
        self.enabled = bool(ai_cfg.get("enabled", False))
        self.priority = [p for p in (ai_cfg.get("priority") or DEFAULT_PRIORITY) if p in PROVIDER_CLASSES]
        self.threshold_pct = float(ai_cfg.get("quota_threshold_pct", 80))
        patterns = ai_cfg.get("force_hermes_keywords") or DEFAULT_FORCE_HERMES_KEYWORDS
        self._force_hermes_re = re.compile("|".join(patterns), re.I)
        self.max_tokens = int((cfg.get("llm") or {}).get("max_tokens") or 220)
        self.temperature = float((cfg.get("llm") or {}).get("temperature") or 0.4)
        self.history_turns = int((cfg.get("llm") or {}).get("history_turns") or 12)
        # manual pin from the HUD ("use groq no matter what" / "hermes" /
        # None=auto). "hermes" is handled one level up, in server.py — it
        # isn't a member of self.providers. See set_override().
        self.override: str | None = None

        or_cfg = ai_cfg.get("openrouter") or {}
        or_key_env = or_cfg.get("api_key_env") or "OPENROUTER_API_KEY"
        or_key = os.environ.get(or_key_env, "")
        reasoning_cfg = {**(or_cfg.get("reasoning") or {}), "api_key_env": or_key_env}
        per_name_cfg = {
            "groq": ai_cfg.get("groq") or {},
            "gemini": ai_cfg.get("gemini") or {},
            "openrouter_free": {**(or_cfg.get("free") or {}), "api_key_env": or_key_env},
            "openrouter_paid": {**(or_cfg.get("paid_fallback") or {}), "api_key_env": or_key_env},
            REASONING_PROVIDER_KEY: reasoning_cfg,
        }
        daily_limits = {name: c.get("daily_request_limit") for name, c in per_name_cfg.items()}
        self.quota = ProviderQuota(logs_dir / "ai_router_usage.json",
                                    daily_request_limits={k: v for k, v in daily_limits.items() if v})

        self.providers = {}
        for name in self.priority:
            pcfg = per_name_cfg.get(name, {})
            if name in ("openrouter_free", "openrouter_paid") and pcfg.get("enabled") is False:
                print(f"[AI ROUTER] {name}: disabled in config, skipping", flush=True)
                continue
            key = or_key if name.startswith("openrouter") else os.environ.get(
                pcfg.get("api_key_env") or f"{name.upper()}_API_KEY", "")
            if not key:
                print(f"[AI ROUTER] {name}: no API key set ({pcfg.get('api_key_env')}), skipping", flush=True)
                continue
            model = pcfg.get("model") or DEFAULT_MODELS[name]
            effort = pcfg.get("reasoning_effort", DEFAULT_REASONING_EFFORT.get(name))
            budget = pcfg.get("thinking_budget", DEFAULT_THINKING_BUDGET.get(name))
            provider = PROVIDER_CLASSES[name](model=model, api_key=key, reasoning_effort=effort, thinking_budget=budget)
            if name == "openrouter_paid":
                provider = self._wrap_cost_capped(provider, name, pcfg, key)
                if provider is None:
                    continue
            self.providers[name] = provider

        # Reasoning tier — deliberately NOT in `priority` (it's injected
        # per-turn by classify_reasoning_level, not tried in the normal
        # linear order). Built separately so it exists even for an installer
        # that never added it to their priority list.
        if reasoning_cfg.get("enabled", True) and reasoning_cfg.get("model"):
            if or_key:
                provider = OpenRouterProvider(
                    model=reasoning_cfg["model"], api_key=or_key,
                    reasoning={"effort": reasoning_cfg.get("effort", "low"), "exclude": True},
                )
                provider = self._wrap_cost_capped(provider, REASONING_PROVIDER_KEY, reasoning_cfg, or_key)
                if provider is not None:
                    self.providers[REASONING_PROVIDER_KEY] = provider
            else:
                print(f"[AI ROUTER] {REASONING_PROVIDER_KEY}: no API key set ({or_key_env}), skipping", flush=True)

    def _wrap_cost_capped(self, provider, name: str, pcfg: dict, api_key: str):
        max_spend = float(pcfg.get("max_spend", 0) or 0)
        if max_spend <= 0:
            print(f"[AI ROUTER] {name}: no max_spend configured (or 0), skipping paid fallback entirely "
                  f"— set ai_router.openrouter.paid_fallback.max_spend to enable it", flush=True)
            return None
        prices = DEFAULT_PAID_PRICE_PER_MTOK.get(name, DEFAULT_PAID_PRICE_PER_MTOK["openrouter_paid"])
        try:
            return CostCappedProvider(
                inner=provider, quota=self.quota, provider_key=name, authorized_model=provider.model,
                max_spend=max_spend,
                price_per_mtok_input=float(pcfg.get("price_per_mtok_input", prices["input"])),
                price_per_mtok_output=float(pcfg.get("price_per_mtok_output", prices["output"])),
                balance_api_key=api_key,
                daily_max_spend=(float(pcfg["daily_max_spend"]) if pcfg.get("daily_max_spend") else None),
                max_cost_per_request=(float(pcfg["max_cost_per_request"]) if pcfg.get("max_cost_per_request") else None),
            )
        except ProviderError as exc:
            print(f"[AI ROUTER] {name}: refused to build paid provider ({exc}), skipping", flush=True)
            return None

    def classify_needs_hermes(self, transcript: str) -> bool:
        text = transcript or ""
        if self._force_hermes_re.search(text):
            return True
        # "tiempo" not in a non-weather idiom: could be any phrasing/word
        # order/STT-mangled place name asking about weather — let the free
        # local model judge intent instead of trying to regex-match every
        # way of saying it (see _llm_needs_hermes docstring).
        if re.search(r"\btiempo\b", text, re.I) and not _TIEMPO_NONWEATHER_RE.search(text):
            return _llm_needs_hermes(text)
        return False

    def set_override(self, name: str | None) -> None:
        self.override = name

    def valid_override_targets(self) -> list[str]:
        return [*self.priority, REASONING_PROVIDER_KEY, "hermes"]

    def status(self) -> dict:
        names = [*self.priority, REASONING_PROVIDER_KEY]
        counters = self.quota.status(names)
        out = {}
        for name in names:
            available = name in self.providers
            cooling = self.quota.in_cooldown(name)
            out[name] = {
                "state": "OFFLINE (no key)" if not available else ("COOLDOWN" if cooling else "ONLINE"),
                "model": self.providers[name].model if available else None,
                **counters.get(name, {}),
            }
        return out

    # ------------------------------------------------------------- cascade

    def chat_online(self, pipeline, transcript: str, timing, conversation: str) -> Iterator[tuple[str, str]]:
        system = pipeline._talk_system_prompt()
        with pipeline._chat_lock:
            hist = list(pipeline._chat_history.get(conversation) or [])
        messages = hist[-(self.history_turns * 2):] + [{"role": "user", "content": transcript}]
        use_tools = bool(_TOOL_HINT_RE.search(transcript or ""))

        # a manual pin from the HUD tries only that one provider (still
        # falls through to Hermes if it fails — override picks a preference,
        # not a guarantee). override == "hermes" never reaches this method;
        # server.py routes it straight to the hermes branch instead.
        names = [self.override] if (self.override and self.override != "hermes") else list(self.priority)

        # Deep-reasoning escalation (paid, real money): a free local-model
        # classifier decides BEFORE any online call — never ask a paid model
        # whether it's needed, that defeats the point. Jumps the queue only
        # for this one turn; if it fails/has no budget, the normal cascade
        # below (groq/gemini/...) still runs exactly as before.
        reasoning_messages = None
        if not self.override and REASONING_PROVIDER_KEY in self.providers:
            level = classify_reasoning_level(transcript)
            if level == "high":
                names = [REASONING_PROVIDER_KEY] + [n for n in names if n != REASONING_PROVIDER_KEY]
                reasoning_messages = _build_reasoning_messages(pipeline, transcript, conversation)
                print(f"[AI ROUTER] reasoning_level=high -> trying {REASONING_PROVIDER_KEY} first", flush=True)

        for name in names:
            provider = self.providers.get(name)
            if provider is None:
                continue
            if self.quota.in_cooldown(name):
                print(f"[AI ROUTER] {name} -> COOLDOWN, skip", flush=True)
                continue
            if self.quota.over_threshold(name, self.threshold_pct):
                print(f"[AI ROUTER] {name} -> over {self.threshold_pct}% quota threshold, skip", flush=True)
                continue
            start = time.perf_counter()
            turn_messages = reasoning_messages if (name == REASONING_PROVIDER_KEY and reasoning_messages) else messages
            try:
                text = yield from self._run_one(pipeline, provider, name, turn_messages, system, use_tools, timing)
            except ProviderError as exc:
                seconds = self.quota.cool_down(name, exc.kind)
                self.quota.record_error(name, exc.kind)
                print(f"[AI ROUTER] {name} -> {exc.kind} ({exc}); cooldown {seconds}s", flush=True)
                continue
            latency = time.perf_counter() - start
            print(f"[AI ROUTER] {name} -> SUCCESS  Latency: {latency:.2f}s", flush=True)
            pipeline._remember_turn(conversation, transcript, text)
            yield ("final", json.dumps({"content": text, "interrupted": False}))
            return
        raise AllProvidersFailedError(f"all online providers failed or unavailable: {names}")

    def _run_one(self, pipeline, provider, name, messages, system, use_tools, timing) -> Iterator[tuple[str, str]]:
        """Generator that yields ("tool"/"text", ...) and returns the final text (via `yield from`'s value)."""
        timing.llm_provider = name
        timing.llm_model = provider.model
        input_chars = len(system) + sum(len(m.get("content", "")) for m in messages)

        if use_tools:
            text, calls = provider.chat(messages, system, ROUTER_TOOLS, self.max_tokens, self.temperature)
            if calls:
                # These local actions (YouTube, folders, Gmail) already return a
                # complete, ready-to-speak confirmation — no second LLM pass
                # needed to "narrate" it (matches how _try_pc_action results
                # are used elsewhere: spoken directly, untouched by any model).
                results = []
                for call in calls:
                    preview = json.dumps(call["arguments"], ensure_ascii=False)[:120]
                    yield ("tool", json.dumps({"name": call["name"], "preview": preview}))
                    results.append(_execute_tool(pipeline, call["name"], call["arguments"],
                                                  self.providers.get("gemini"), self.quota))
                spoken = " ".join(r for r in results if r)
                if timing.llm_first_token_monotonic is None:
                    timing.llm_first_token_monotonic = time.perf_counter()
                yield ("text", spoken)
                self._record_usage(name, provider.model, input_chars, len(spoken))
                return spoken
            if text:
                # model chose to answer directly instead of calling a tool —
                # already have the full text from the non-streaming call.
                if timing.llm_first_token_monotonic is None:
                    timing.llm_first_token_monotonic = time.perf_counter()
                yield ("text", text)
                self._record_usage(name, provider.model, input_chars, len(text))
                return text

        parts: list[str] = []
        for chunk in provider.stream(messages, system, self.max_tokens, self.temperature):
            if timing.llm_first_token_monotonic is None:
                timing.llm_first_token_monotonic = time.perf_counter()
            parts.append(chunk)
            yield ("text", chunk)
        full = "".join(parts).strip()
        self._record_usage(name, provider.model, input_chars, len(full))
        return full

    def _record_usage(self, provider: str, model: str, input_chars: int, output_chars: int) -> None:
        # ponytail: chars/4 token estimate, not a real tokenizer count — good
        # enough for the "avoid the absolute limit" dashboard; the real
        # enforcement is the 429/quota-exceeded -> cooldown path above, which
        # doesn't depend on this number. Upgrade path: read `usage` from each
        # provider's JSON response if per-provider precision matters later.
        self.quota.record_success(provider, model, input_chars // 4, output_chars // 4)


def _execute_tool(pipeline, tool_name: str, args: dict, gemini_provider=None, quota=None) -> str:
    if tool_name == "open_youtube":
        return pipeline._yt_open(args.get("query") or "")
    if tool_name == "open_browser":
        target = args.get("url_or_query") or ""
        if target.startswith("http"):
            webbrowser.open(target, new=2)
        else:
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote_plus(target), new=2)
        return f"Abrí {target} en el navegador."
    if tool_name == "open_folder":
        return pipeline._open_named(args.get("name") or "")
    if tool_name == "check_gmail":
        return pipeline._gmail_inbox_brief()
    if tool_name == "mute_email_sender":
        return pipeline._mute_email_sender(args.get("target") or "")
    if tool_name == "look_with_camera":
        return _look_with_camera(args.get("question") or "", gemini_provider, quota)
    return f"No sé ejecutar la herramienta {tool_name}."


def _look_with_camera(question: str, gemini_provider, quota) -> str:
    if gemini_provider is None:
        return "No tengo un modelo con visión disponible ahora mismo para usar la cámara."
    if quota is not None and quota.in_cooldown("gemini"):
        return "La visión con cámara usa Gemini y está en cooldown ahora mismo — probá de nuevo en un momento."
    from camera import capture_jpeg_base64  # local import: keeps opencv off the hot path for turns that never touch the camera
    try:
        image_b64 = capture_jpeg_base64()
    except RuntimeError as exc:
        return str(exc)
    try:
        text = gemini_provider.describe_image(image_b64, question) or "No pude describir lo que veo."
    except ProviderError as exc:
        if quota is not None:
            quota.cool_down("gemini", exc.kind)
            quota.record_error("gemini", exc.kind)
        return f"La cámara funcionó pero no pude describir la imagen ({exc.kind})."
    if quota is not None:
        quota.record_success("gemini", gemini_provider.model, len(question) // 4, len(text) // 4)
    return text
