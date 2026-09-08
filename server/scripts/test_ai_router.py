#!/usr/bin/env python3
"""AI Router checks — no real API calls, no pytest (matches ws_e2e_test.py style).

Run: python server/scripts/test_ai_router.py
"""
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # server/ on sys.path

from ai_router.base import BaseProvider, ProviderError
from ai_router.cost_guard import CostCappedProvider
from ai_router.quota import ProviderQuota
from ai_router.router import AIRouter, AllProvidersFailedError

PASS = []
FAIL = []


def check(name: str, fn) -> None:
    try:
        fn()
        PASS.append(name)
        print(f"  ok  {name}")
    except AssertionError as exc:
        FAIL.append((name, str(exc)))
        print(f"FAIL  {name}: {exc}")


class FakeProvider(BaseProvider):
    """Scripted provider: raises `error` if set, else returns `text`/`calls`/streams `chunks`."""

    def __init__(self, model="fake-model", text="hola", chunks=None, calls=None, error: ProviderError | None = None):
        super().__init__(model=model, api_key="fake-key")
        self.text = text
        self.chunks = chunks if chunks is not None else [text]
        self.calls = calls or []
        self.error = error
        self.chat_calls = 0
        self.stream_calls = 0

    def chat(self, messages, system, tools, max_tokens, temperature):
        self.chat_calls += 1
        if self.error:
            raise self.error
        return self.text, self.calls

    def stream(self, messages, system, max_tokens, temperature):
        self.stream_calls += 1
        if self.error:
            raise self.error
        yield from self.chunks


class FakeTiming:
    def __init__(self):
        self.llm_provider = None
        self.llm_model = None
        self.llm_first_token_monotonic = None
        self.errors = []


class FakePipeline:
    def __init__(self):
        self._chat_history = {}
        self._chat_lock = threading.Lock()
        self.remembered = []
        self.opened = []

    def _talk_system_prompt(self):
        return "eres jarvis"

    def _yt_open(self, query, watch=None):
        self.opened.append(("youtube", query))
        return f"Reproduciendo {query}."

    def _open_named(self, name):
        self.opened.append(("folder", name))
        return f"Abro {name}."

    def _gmail_inbox_brief(self):
        return "Tenés 3 correos nuevos."

    def _remember_turn(self, conversation, user, assistant):
        self.remembered.append((conversation, user, assistant))


def make_router(**providers) -> AIRouter:
    # fresh dir per router so quota counters never leak between tests
    tmp_dir = Path(tempfile.mkdtemp(prefix="jarvis-ai-router-test-"))
    router = AIRouter({"ai_router": {"enabled": True}}, tmp_dir)
    router.providers = providers
    router.priority = list(providers.keys())
    return router


def drain(gen):
    """Run a chat_online generator to completion, return (events, final_text)."""
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration:
        pass
    final = [json.loads(v)["content"] for k, v in events if k == "final"]
    return events, (final[0] if final else None)


def test_single_provider_success():
    router = make_router(groq=FakeProvider(text="respuesta de groq", chunks=["respuesta ", "de groq"]))
    pipeline, timing = FakePipeline(), FakeTiming()
    events, final = drain(router.chat_online(pipeline, "hola", timing, "c1"))
    assert final == "respuesta de groq", final
    assert timing.llm_provider == "groq"
    assert pipeline.remembered, "should have recorded the turn"


def test_streaming_yields_incremental_chunks():
    router = make_router(gemini=FakeProvider(chunks=["a", "b", "c"]))
    events, final = drain(router.chat_online(FakePipeline(), "hola, como estas", FakeTiming(), "c1"))
    text_events = [v for k, v in events if k == "text"]
    assert text_events == ["a", "b", "c"], text_events
    assert final == "abc", final


def test_failover_groq_to_gemini():
    groq = FakeProvider(error=ProviderError("429", "rate_limit"))
    gemini = FakeProvider(text="gemini responde", chunks=["gemini responde"])
    router = make_router(groq=groq, gemini=gemini)
    events, final = drain(router.chat_online(FakePipeline(), "hola", FakeTiming(), "c1"))
    assert final == "gemini responde", final
    assert router.quota.in_cooldown("groq"), "groq should be on cooldown after 429"


def test_timeout_fails_over():
    groq = FakeProvider(error=ProviderError("slow", "timeout"))
    gemini = FakeProvider(chunks=["ok"])
    router = make_router(groq=groq, gemini=gemini)
    _, final = drain(router.chat_online(FakePipeline(), "hola", FakeTiming(), "c1"))
    assert final == "ok"


def test_invalid_key_fails_over_and_long_cooldown():
    groq = FakeProvider(error=ProviderError("bad key", "invalid_key"))
    gemini = FakeProvider(chunks=["ok"])
    router = make_router(groq=groq, gemini=gemini)
    drain(router.chat_online(FakePipeline(), "hola", FakeTiming(), "c1"))
    left = router.quota.status().get("groq", {}).get("cooldown_seconds_left", 0)
    assert left > 300, f"invalid_key cooldown should be long, got {left}s"


def test_all_online_providers_fail_raises():
    router = make_router(
        groq=FakeProvider(error=ProviderError("x", "unavailable")),
        gemini=FakeProvider(error=ProviderError("x", "unavailable")),
    )
    gen = router.chat_online(FakePipeline(), "hola", FakeTiming(), "c1")
    try:
        while True:
            next(gen)
        raised = False
    except AllProvidersFailedError:
        raised = True
    except StopIteration:
        raised = False
    assert raised, "should raise AllProvidersFailedError when every provider fails"


def test_cooldown_skips_provider_without_calling_it():
    groq = FakeProvider(chunks=["should not be used"])
    gemini = FakeProvider(chunks=["gemini"])
    router = make_router(groq=groq, gemini=gemini)
    router.quota.cool_down("groq", "rate_limit")
    _, final = drain(router.chat_online(FakePipeline(), "hola", FakeTiming(), "c1"))
    assert final == "gemini"
    assert groq.stream_calls == 0, "groq should have been skipped, not called"


def test_provider_recovers_after_cooldown_expires():
    groq = FakeProvider(chunks=["groq de nuevo"])
    router = make_router(groq=groq)
    router.quota._cooldowns["groq"] = time.monotonic() - 1  # already expired
    _, final = drain(router.chat_online(FakePipeline(), "hola", FakeTiming(), "c1"))
    assert final == "groq de nuevo"


def test_classify_needs_hermes_for_tool_keywords():
    router = make_router()
    assert router.classify_needs_hermes("abre una terminal y ejecuta ls")
    assert router.classify_needs_hermes("busca en internet el precio del dólar")
    assert not router.classify_needs_hermes("qué tal el día, jarvis")


def test_tool_call_executes_locally_and_speaks_result():
    calls = [{"id": "1", "name": "open_youtube", "arguments": {"query": "arc reactor"}}]
    router = make_router(groq=FakeProvider(text="", calls=calls))
    pipeline = FakePipeline()
    events, final = drain(router.chat_online(pipeline, "ponme un video de youtube de arc reactor", FakeTiming(), "c1"))
    tool_events = [json.loads(v) for k, v in events if k == "tool"]
    assert tool_events and tool_events[0]["name"] == "open_youtube", tool_events
    assert pipeline.opened == [("youtube", "arc reactor")]
    assert "Reproduciendo arc reactor" in final


def test_token_usage_recorded_on_success():
    router = make_router(groq=FakeProvider(chunks=["respuesta larga de prueba"]))
    drain(router.chat_online(FakePipeline(), "hola como estas hoy", FakeTiming(), "c1"))
    day = router.quota.status().get("groq", {})
    assert day.get("requests") == 1, day
    assert day.get("tokens_out", 0) > 0, day


# ------------------------------------------------------------- cost guard
# OpenRouter free -> OpenRouter paid (Jesús' existing $3.79 prepaid balance,
# no top-ups) -> Hermes. These are the 8 scenarios Jesús asked for by name.

AUTHORIZED_PAID_MODEL = "openai/gpt-oss-20b"


def make_quota(**daily_limits) -> ProviderQuota:
    tmp_dir = Path(tempfile.mkdtemp(prefix="jarvis-ai-router-test-"))
    return ProviderQuota(tmp_dir / "usage.json", daily_request_limits=daily_limits)


def make_capped(quota=None, max_spend=1.0, inner_error=None, price_in=1.0, price_out=1.0, balance_api_key=None):
    """price_in/out default to $1/tok on purpose — makes the math obvious in assertions."""
    inner = FakeProvider(model=AUTHORIZED_PAID_MODEL, chunks=["ok"], error=inner_error)
    capped = CostCappedProvider(
        inner=inner, quota=quota or make_quota(), provider_key="openrouter_paid",
        authorized_model=AUTHORIZED_PAID_MODEL, max_spend=max_spend,
        price_per_mtok_input=price_in * 1_000_000, price_per_mtok_output=price_out * 1_000_000,
        balance_api_key=balance_api_key,
    )
    return capped, inner


def test_1_openrouter_free_works_never_touches_paid():
    free = FakeProvider(chunks=["respuesta gratis"])
    paid = FakeProvider(chunks=["NUNCA debería usarse"])
    router = make_router(openrouter_free=free, openrouter_paid=paid)
    _, final = drain(router.chat_online(FakePipeline(), "hola", FakeTiming(), "c1"))
    assert final == "respuesta gratis", final
    assert paid.stream_calls == 0 and paid.chat_calls == 0, "paid must never be touched while free works"


def test_2_openrouter_free_fails_falls_to_paid():
    free = FakeProvider(error=ProviderError("500", "unavailable"))
    paid = FakeProvider(chunks=["respondo de pago"])
    router = make_router(openrouter_free=free, openrouter_paid=paid)
    _, final = drain(router.chat_online(FakePipeline(), "hola", FakeTiming(), "c1"))
    assert final == "respondo de pago", final


def test_3_paid_success_records_real_cost():
    quota = make_quota()
    capped, inner = make_capped(quota=quota, max_spend=10.0, price_in=1.0, price_out=1.0)
    out = "".join(capped.stream([{"role": "user", "content": "1234"}], "sys", max_tokens=8, temperature=0.1))
    assert out == "ok"
    spent = quota.total_spent("openrouter_paid")
    assert spent > 0, "a successful paid call must record nonzero spend"


def test_4_max_spend_reached_blocks_paid():
    quota = make_quota()
    capped, inner = make_capped(quota=quota, max_spend=0.000001, price_in=1.0, price_out=1.0)  # ~0, any call exceeds it
    try:
        list(capped.stream([{"role": "user", "content": "hola"}], "sys", max_tokens=50, temperature=0.1))
        raised = False
    except ProviderError as exc:
        raised = exc.kind == "spend_cap"
    assert raised, "call over the cap must be refused with kind=spend_cap"
    assert inner.stream_calls == 0, "must refuse BEFORE calling the real API, not after"


def test_5_paid_blocked_falls_to_hermes_ie_all_providers_failed():
    quota = make_quota()
    capped, _ = make_capped(quota=quota, max_spend=0.000001, price_in=1.0, price_out=1.0)
    router = make_router(openrouter_paid=capped)
    gen = router.chat_online(FakePipeline(), "hola", FakeTiming(), "c1")
    try:
        while True:
            next(gen)
        raised = False
    except AllProvidersFailedError:
        raised = True
    assert raised, "with only the (blocked) paid tier configured, exhausting it must surface as AllProvidersFailedError so server.py falls to Hermes"


def test_6_insufficient_real_balance_blocks_even_under_local_cap():
    quota = make_quota()
    capped, inner = make_capped(quota=quota, max_spend=10.0, price_in=1.0, price_out=1.0, balance_api_key="fake")
    capped._fetch_real_remaining = lambda: 0.0  # OpenRouter itself reports $0 left
    try:
        list(capped.stream([{"role": "user", "content": "hola"}], "sys", max_tokens=10, temperature=0.1))
        raised = False
    except ProviderError as exc:
        raised = exc.kind == "spend_cap"
    assert raised, "real remaining balance of $0 must block even though the local ledger alone would allow it"
    assert inner.stream_calls == 0


def test_7_never_exceeds_max_spend_across_many_calls():
    quota = make_quota()
    max_spend = 0.01
    capped, inner = make_capped(quota=quota, max_spend=max_spend, price_in=0.0002, price_out=0.0002)
    successes, blocked = 0, False
    for _ in range(50):
        try:
            list(capped.stream([{"role": "user", "content": "x" * 40}], "sys", max_tokens=20, temperature=0.1))
            successes += 1
        except ProviderError as exc:
            assert exc.kind == "spend_cap"
            blocked = True
            break
    assert successes > 0, "should allow at least one call before the cap kicks in"
    assert blocked, "and eventually hit the cap — otherwise this test isn't exercising accumulation"
    assert quota.total_spent("openrouter_paid") <= max_spend, "ledger must never exceed max_spend"


def test_8_unauthorized_paid_model_rejected():
    inner = FakeProvider(model="some-other-model-nobody-approved")
    try:
        CostCappedProvider(
            inner=inner, quota=make_quota(), provider_key="openrouter_paid",
            authorized_model=AUTHORIZED_PAID_MODEL, max_spend=10.0,
            price_per_mtok_input=1.0, price_per_mtok_output=1.0,
        )
        raised = False
    except ProviderError as exc:
        raised = exc.kind == "unauthorized"
    assert raised, "wrapping a provider whose model doesn't match the configured authorized model must be refused"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"Running {len(tests)} AI router checks...\n")
    for t in tests:
        check(t.__name__, t)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
