"""Gemini REST adapter (generateContent / streamGenerateContent, not OpenAI-shaped)."""

from __future__ import annotations

import json
from typing import Iterator

import requests

from ..base import BaseProvider, ProviderError

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _classify_http_error(status: int, body: str) -> str:
    if status == 429:
        return "quota" if "quota" in body.lower() else "rate_limit"
    if status in (401, 403) or "API_KEY_INVALID" in body:
        return "invalid_key"
    if status >= 500:
        return "unavailable"
    return "permanent"


def _to_gemini_tools(tools: list[dict] | None) -> list[dict] | None:
    if not tools:
        return None
    decls = []
    for t in tools:
        fn = t.get("function") or t
        decls.append({"name": fn["name"], "description": fn.get("description", ""),
                      "parameters": fn.get("parameters") or {"type": "object", "properties": {}}})
    return [{"functionDeclarations": decls}]


def _to_gemini_contents(messages: list[dict]) -> list[dict]:
    contents = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    return contents


class GeminiProvider(BaseProvider):
    name = "gemini"

    def _generation_config(self, max_tokens: int, temperature: float) -> dict:
        cfg = {"maxOutputTokens": max_tokens, "temperature": temperature}
        # Gemini 2.5 "thinking" burns part of maxOutputTokens on a hidden
        # pass by default — measured: budget 0 cut a short answer from 1.84s
        # (and truncated mid-sentence) to 0.68s clean. thinking_budget in
        # config lets you raise it back for turns that need real reasoning.
        budget = self.opts.get("thinking_budget", 0)
        if budget is not None:
            cfg["thinkingConfig"] = {"thinkingBudget": budget}
        return cfg

    def _url(self, method: str) -> str:
        suffix = "&alt=sse" if method == "streamGenerateContent" else ""
        return f"{BASE}/{self.model}:{method}?key={self.api_key}{suffix}"

    def _request(self, method: str, body: dict, stream: bool):
        try:
            resp = requests.post(self._url(method), json=body, stream=stream, timeout=(10, 60))
        except requests.exceptions.Timeout as exc:
            raise ProviderError(f"gemini timeout: {exc}", "timeout") from exc
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"gemini unreachable: {exc}", "unavailable") from exc
        if not resp.ok:
            kind = _classify_http_error(resp.status_code, resp.text[:300])
            raise ProviderError(f"gemini HTTP {resp.status_code}: {resp.text[:200]}", kind)
        return resp

    def chat(self, messages, system, tools, max_tokens, temperature) -> tuple[str, list[dict]]:
        body = {
            "contents": _to_gemini_contents(messages),
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": self._generation_config(max_tokens, temperature),
        }
        gemini_tools = _to_gemini_tools(tools)
        if gemini_tools:
            body["tools"] = gemini_tools
        resp = self._request("generateContent", body, stream=False)
        data = resp.json()
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        calls = [
            {"id": p["functionCall"]["name"], "name": p["functionCall"]["name"],
             "arguments": p["functionCall"].get("args") or {}}
            for p in parts if "functionCall" in p
        ]
        return text, calls

    def stream(self, messages, system, max_tokens, temperature) -> Iterator[str]:
        body = {
            "contents": _to_gemini_contents(messages),
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": self._generation_config(max_tokens, temperature),
        }
        resp = self._request("streamGenerateContent", body, stream=True)
        try:
            for raw in resp.iter_lines():
                if not raw or not raw.startswith(b"data: "):
                    continue
                try:
                    data = json.loads(raw[len(b"data: "):])
                except json.JSONDecodeError:
                    continue
                parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
                for p in parts:
                    if "text" in p:
                        yield p["text"]
        except requests.exceptions.RequestException as exc:
            raise ProviderError(f"gemini stream dropped: {exc}", "unavailable") from exc

    def describe_image(self, image_b64: str, question: str, max_tokens: int = 300,
                       mime_type: str = "image/jpeg") -> str:
        """One-shot vision call — used by the camera tool and chat attachments.
        Not part of the BaseProvider interface (only Gemini among the
        configured online providers is multimodal today; gpt-oss on
        Groq/OpenRouter is text-only)."""
        body = {
            "contents": [{"role": "user", "parts": [
                {"text": question or "Describe brevemente qué ves en esta imagen."},
                {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            ]}],
            "generationConfig": self._generation_config(max_tokens, 0.3),
        }
        resp = self._request("generateContent", body, stream=False)
        data = resp.json()
        parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if "text" in p).strip()
