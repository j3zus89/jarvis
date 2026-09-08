"""Per-provider usage counters + cooldown tracking for the AI router.

Persisted so restarts don't forget today's counts. Cooldown state itself is
kept in memory only (a stale "still cooling down" after a restart is fine —
worst case we retry a provider a little early and it 429s again).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

# kind -> base cooldown seconds before retrying that provider
COOLDOWN_SECONDS = {
    "rate_limit": 60,
    "quota": 900,       # daily/monthly cap — no point retrying soon
    "timeout": 30,
    "unavailable": 30,
    "permanent": 3600,  # bad config (e.g. bad model id) — needs a human, not a retry loop
    "invalid_key": 3600,
    "credit_exhausted": 3600,
    # spend cap reached — stays blocked until the user raises max_spend in
    # config; retrying costs nothing (CostCappedProvider checks the ledger
    # before ever making a network call), so the exact cooldown length here
    # barely matters.
    "spend_cap": 3600,
}


class ProviderQuota:
    def __init__(self, state_path: Path, daily_request_limits: dict[str, int] | None = None):
        self._path = state_path
        self._lock = threading.Lock()
        self._cooldowns: dict[str, float] = {}  # provider -> monotonic time it clears
        self._daily_limits = daily_request_limits or {}

    # ---- cooldown ----

    def in_cooldown(self, provider: str) -> bool:
        until = self._cooldowns.get(provider)
        return bool(until and time.monotonic() < until)

    def cool_down(self, provider: str, kind: str) -> float:
        seconds = COOLDOWN_SECONDS.get(kind, 60)
        self._cooldowns[provider] = time.monotonic() + seconds
        return seconds

    def clear_cooldown(self, provider: str) -> None:
        self._cooldowns.pop(provider, None)

    # ---- quota threshold (proactive skip before hitting the real 429) ----

    def over_threshold(self, provider: str, threshold_pct: float) -> bool:
        limit = self._daily_limits.get(provider)
        if not limit:
            return False
        today = self._today_bucket(provider)
        return (today.get("requests", 0) / limit) * 100 >= threshold_pct

    # ---- usage recording ----

    def record_success(self, provider: str, model: str, tokens_in: int, tokens_out: int) -> None:
        self._update(provider, requests=1, tokens_in=tokens_in, tokens_out=tokens_out,
                     last_model=model, last_used=time.time())
        self.clear_cooldown(provider)

    def record_error(self, provider: str, kind: str) -> None:
        self._update(provider, errors=1, last_error=kind, last_error_ts=time.time())
        if kind == "rate_limit":
            self._update(provider, rate_limits=1)

    # ---- lifetime spend (real money, never pruned, separate from the daily
    # "days" buckets above — a prepaid balance doesn't reset at midnight) ----

    def record_spend(self, provider: str, usd: float) -> None:
        with self._lock:
            data = self._load()
            spend = data.setdefault("spend", {})
            spend[provider] = round(spend.get(provider, 0.0) + usd, 6)
            # also bucket into today, for providers that want a DAILY $ cap
            # on top of the lifetime one (e.g. a reasoning-tier model where
            # "don't blow the whole budget in one bad session" matters more
            # than the lifetime ceiling, which the user is fine exhausting).
            day = data.setdefault("days", {}).setdefault(self._today(), {})
            bucket = day.setdefault(provider, {
                "requests": 0, "tokens_in": 0, "tokens_out": 0,
                "errors": 0, "rate_limits": 0, "last_used": None,
                "last_error": None, "last_error_ts": None, "last_model": None,
            })
            bucket["spend_usd"] = round(bucket.get("spend_usd", 0.0) + usd, 6)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data), encoding="utf-8")

    def total_spent(self, provider: str) -> float:
        return self._load().get("spend", {}).get(provider, 0.0)

    def total_spent_today(self, provider: str) -> float:
        return self._today_bucket(provider).get("spend_usd", 0.0)

    def status(self, provider_names: list[str] | None = None) -> dict:
        """`provider_names` (e.g. the router's configured priority list) makes sure
        every known provider gets a real zero-baseline row today, even ones that
        haven't been called yet — otherwise a provider that's never lost the
        cascade (Gemini sitting behind Groq, say) just never appears at all."""
        data = self._load()
        today = data.get("days", {}).get(self._today(), {})
        spend = data.get("spend", {})
        names = set(today) | set(spend) | set(self._cooldowns) | set(provider_names or [])
        out = {}
        for provider in names:
            bucket = today.get(provider, {})
            limit = self._daily_limits.get(provider)
            requests = bucket.get("requests", 0)
            out[provider] = {
                "requests": requests, "tokens_in": bucket.get("tokens_in", 0),
                "tokens_out": bucket.get("tokens_out", 0), "errors": bucket.get("errors", 0),
                "rate_limits": bucket.get("rate_limits", 0), "last_used": bucket.get("last_used"),
                "last_error": bucket.get("last_error"), "last_error_ts": bucket.get("last_error_ts"),
                "last_model": bucket.get("last_model"),
                "daily_request_limit": limit,
                "request_pct": round(requests / limit * 100, 1) if limit else None,
                "total_spent_usd": spend.get(provider, 0.0),
                "spent_today_usd": bucket.get("spend_usd", 0.0),
                "cooldown_seconds_left": max(0, round(self._cooldowns.get(provider, 0) - time.monotonic()))
                if provider in self._cooldowns else 0,
            }
        return out

    # ---- internals ----

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d")

    def _today_bucket(self, provider: str) -> dict:
        data = self._load()
        return data.get("days", {}).get(self._today(), {}).get(provider, {})

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {"days": {}}

    def _update(self, provider: str, **deltas) -> None:
        with self._lock:
            data = self._load()
            day = data.setdefault("days", {}).setdefault(self._today(), {})
            bucket = day.setdefault(provider, {
                "requests": 0, "tokens_in": 0, "tokens_out": 0,
                "errors": 0, "rate_limits": 0, "last_used": None,
                "last_error": None, "last_error_ts": None, "last_model": None,
            })
            for key, value in deltas.items():
                if key in ("requests", "tokens_in", "tokens_out", "errors", "rate_limits"):
                    bucket[key] = bucket.get(key, 0) + value
                else:
                    bucket[key] = value
            for k in sorted(data["days"])[:-14]:
                del data["days"][k]
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data), encoding="utf-8")
