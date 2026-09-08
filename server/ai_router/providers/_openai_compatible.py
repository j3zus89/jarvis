"""Shared REST client for OpenAI-Chat-Completions-compatible APIs (Groq, OpenRouter)."""

from __future__ import annotations

import json
from typing import Iterator

import requests

from ..base import BaseProvider, ProviderError


def _classify_http_error(status: int, body: str) -> str:
    if status == 402:
        return "credit_exhausted"
    if status == 429:
        low = body.lower()
        if "quota" in low or "insufficient_quota" in low:
            return "quota"
        return "rate_limit"
    if status in (401, 403):
        return "invalid_key"
    if status >= 500:
        return "unavailable"
    return "permanent"


class OpenAICompatibleProvider(BaseProvider):
    base_url = ""  # set by subclass
    extra_headers: dict = {}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                **self.extra_headers}

    def _post(self, payload: dict, stream: bool):
        try:
            resp = requests.post(f"{self.base_url}/chat/completions", headers=self._headers(),
                                  json=payload, stream=stream, timeout=(10, 60))
        except requests.exceptions.Timeout as exc:
            raise ProviderError(f"{self.name} timeout: {exc}", "timeout") from exc
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"{self.name} unreachable: {exc}", "unavailable") from exc
        if not resp.ok:
            kind = _classify_http_error(resp.status_code, resp.text[:300])
            raise ProviderError(f"{self.name} HTTP {resp.status_code}: {resp.text[:200]}", kind)
        return resp

    def _base_payload(self, messages, system, max_tokens, temperature, stream, model: str | None = None) -> dict:
        payload = {"model": model or self.model, "messages": [{"role": "system", "content": system}] + messages,
                   "max_tokens": max_tokens, "temperature": temperature, "stream": stream}
        # harmony/reasoning models (e.g. Groq's gpt-oss) burn part of max_tokens
        # on hidden chain-of-thought before the visible answer — "low" cuts
        # that overhead noticeably (measured ~40% latency drop) for a voice
        # assistant that wants a fast short answer, not deep reasoning.
        if self.opts.get("reasoning_effort"):
            payload["reasoning_effort"] = self.opts["reasoning_effort"]
        # OpenRouter's own unified shape for reasoning-native models (Claude,
        # Gemini thinking, GPT-6 Astra...) — a nested object, NOT the flat
        # reasoning_effort field above (that one's Groq/harmony-specific).
        # exclude:true keeps the chain-of-thought out of the response (never
        # shown to the user) while still paying for/using it internally.
        if self.opts.get("reasoning"):
            payload["reasoning"] = self.opts["reasoning"]
        return payload

    def chat(self, messages, system, tools, max_tokens, temperature, model: str | None = None) -> tuple[str, list[dict]]:
        payload = self._base_payload(messages, system, max_tokens, temperature, stream=False, model=model)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        resp = self._post(payload, stream=False)
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": call.get("id"), "name": fn.get("name"), "arguments": args})
        return text, calls

    def stream(self, messages, system, max_tokens, temperature, model: str | None = None) -> Iterator[str]:
        payload = self._base_payload(messages, system, max_tokens, temperature, stream=True, model=model)
        resp = self._post(payload, stream=True)
        try:
            for raw in resp.iter_lines():
                if not raw or not raw.startswith(b"data: "):
                    continue
                chunk = raw[len(b"data: "):]
                if chunk.strip() == b"[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                delta = ((data.get("choices") or [{}])[0].get("delta") or {}).get("content")
                if delta:
                    yield delta
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"{self.name} stream dropped: {exc}", "unavailable") from exc
