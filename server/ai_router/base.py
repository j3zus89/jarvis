"""Common interface every online provider adapter implements."""

from __future__ import annotations

from typing import Iterator


class ProviderError(Exception):
    """A provider call failed. `kind` decides the cooldown length (see quota.py)."""

    def __init__(self, message: str, kind: str = "unavailable"):
        # kind: "rate_limit" | "quota" | "invalid_key" | "timeout" | "unavailable" | "permanent"
        super().__init__(message)
        self.kind = kind


class BaseProvider:
    """REST adapter for one online free-tier LLM API.

    chat() does ONE non-streaming call (used only when tool-calling might be
    needed) and returns (text, tool_calls). stream() does the real streaming
    call for the final spoken answer. Both raise ProviderError on any
    failure — the router decides what to do next, adapters never retry.
    """

    name = "base"
    supports_tools = True

    def __init__(self, model: str, api_key: str, **opts):
        self.model = model
        self.api_key = api_key
        self.opts = opts

    def chat(
        self, messages: list[dict], system: str, tools: list[dict] | None,
        max_tokens: int, temperature: float,
    ) -> tuple[str, list[dict]]:
        """Non-streaming call. Returns (text, tool_calls) — tool_calls is
        [{"id":..., "name":..., "arguments": {...}}, ...], empty if none."""
        raise NotImplementedError

    def stream(
        self, messages: list[dict], system: str,
        max_tokens: int, temperature: float,
    ) -> Iterator[str]:
        """Streaming call. Yields text chunks as they arrive."""
        raise NotImplementedError
