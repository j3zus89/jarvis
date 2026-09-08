"""Hard, persisted spend cap for a paid provider.

This is the actual protection — not a comment, not a dashboard number. A
call is refused *before* it happens if its worst-case cost would push the
lifetime total past max_spend. The cap survives restarts (persisted via
ProviderQuota) and never resets on its own: once hit, it stays hit until a
human raises max_spend in config. No auto top-ups, no silent bypass.
"""

from __future__ import annotations

import time
from typing import Iterator

import requests

from .base import BaseProvider, ProviderError
from .quota import ProviderQuota

_BALANCE_CACHE_SECONDS = 300  # avoid hitting OpenRouter's own accounting on every turn


def _estimate_tokens(messages: list[dict], system: str) -> int:
    chars = len(system) + sum(len(m.get("content", "")) for m in messages)
    return chars // 4  # ponytail: chars/4, not a real tokenizer — see router.py's _record_usage


class CostCappedProvider(BaseProvider):
    """Wraps a paid BaseProvider with a lifetime USD spend ceiling.

    `authorized_model` must match `inner.model` exactly — this is a second,
    explicit check (on top of the model being fixed at construction time,
    never chosen per-request) that a rogue config change can't quietly point
    this wrapper at a different, unreviewed paid model.
    """

    name = "openrouter_paid"

    def __init__(self, inner: BaseProvider, quota: ProviderQuota, provider_key: str,
                 authorized_model: str, max_spend: float,
                 price_per_mtok_input: float, price_per_mtok_output: float,
                 balance_api_key: str | None = None,
                 daily_max_spend: float | None = None,
                 max_cost_per_request: float | None = None):
        if inner.model != authorized_model:
            raise ProviderError(
                f"refusing to wrap unauthorized paid model {inner.model!r} "
                f"(configured authorized model is {authorized_model!r})",
                "unauthorized",
            )
        self.inner = inner
        self.model = inner.model
        self.quota = quota
        self.provider_key = provider_key
        self.max_spend = max_spend
        # both optional: a lifetime-only cap (the existing openrouter_paid
        # tier) leaves these None and behaves exactly as before.
        self.daily_max_spend = daily_max_spend
        self.max_cost_per_request = max_cost_per_request
        self.price_in = price_per_mtok_input / 1_000_000
        self.price_out = price_per_mtok_output / 1_000_000
        self._balance_api_key = balance_api_key
        self._balance_cache: tuple[float, float] | None = None  # (checked_at, remaining_usd)

    def _effective_cap(self) -> float:
        """min(configured max_spend, OpenRouter's own reported remaining balance)
        when that's fetchable — best-effort, never loosens the local cap."""
        if not self._balance_api_key:
            return self.max_spend
        now = time.monotonic()
        if self._balance_cache and now - self._balance_cache[0] < _BALANCE_CACHE_SECONDS:
            remaining = self._balance_cache[1]
        else:
            remaining = self._fetch_real_remaining()
            self._balance_cache = (now, remaining) if remaining is not None else self._balance_cache
            if remaining is None:
                return self.max_spend
        spent_locally = self.quota.total_spent(self.provider_key)
        # remaining is OpenRouter's live number; translate to "cap on our own
        # ledger" so the two checks compose: whichever is stricter wins.
        return min(self.max_spend, spent_locally + remaining)

    def _fetch_real_remaining(self) -> float | None:
        try:
            r = requests.get("https://openrouter.ai/api/v1/credits",
                              headers={"Authorization": f"Bearer {self._balance_api_key}"}, timeout=5)
            if not r.ok:
                return None
            d = r.json().get("data") or {}
            total = d.get("total_credits")
            used = d.get("total_usage")
            if total is None or used is None:
                return None
            return max(0.0, float(total) - float(used))
        except requests.exceptions.RequestException:
            return None  # network hiccup — fall back to the local cap, never fail open on cost

    def _authorize_or_raise(self, input_tokens: int, max_tokens: int) -> None:
        worst_case = input_tokens * self.price_in + max_tokens * self.price_out
        if self.max_cost_per_request is not None and worst_case > self.max_cost_per_request:
            raise ProviderError(
                f"per-request cap reached: ${worst_case:.4f} worst-case "
                f"> ${self.max_cost_per_request:.4f} max_cost_per_request", "spend_cap",
            )
        spent = self.quota.total_spent(self.provider_key)
        cap = self._effective_cap()
        if spent + worst_case > cap:
            raise ProviderError(
                f"spend cap reached: ${spent:.4f} spent + ${worst_case:.4f} worst-case "
                f"> ${cap:.4f} cap", "spend_cap",
            )
        if self.daily_max_spend is not None:
            spent_today = self.quota.total_spent_today(self.provider_key)
            if spent_today + worst_case > self.daily_max_spend:
                raise ProviderError(
                    f"daily spend cap reached: ${spent_today:.4f} spent today + "
                    f"${worst_case:.4f} worst-case > ${self.daily_max_spend:.4f} daily cap",
                    "spend_cap",
                )

    def chat(self, messages, system, tools, max_tokens, temperature) -> tuple[str, list[dict]]:
        input_tokens = _estimate_tokens(messages, system)
        self._authorize_or_raise(input_tokens, max_tokens)
        text, calls = self.inner.chat(messages, system, tools, max_tokens, temperature)
        self._record(input_tokens, len(text) // 4)
        return text, calls

    def stream(self, messages, system, max_tokens, temperature) -> Iterator[str]:
        input_tokens = _estimate_tokens(messages, system)
        self._authorize_or_raise(input_tokens, max_tokens)
        out_chars = 0
        for chunk in self.inner.stream(messages, system, max_tokens, temperature):
            out_chars += len(chunk)
            yield chunk
        self._record(input_tokens, out_chars // 4)

    def _record(self, input_tokens: int, output_tokens: int) -> None:
        cost = input_tokens * self.price_in + output_tokens * self.price_out
        self.quota.record_spend(self.provider_key, cost)
