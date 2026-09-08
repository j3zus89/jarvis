# AI Router

Cascades through free-tier online LLM APIs before falling back to Hermes —
solves the "reasoning is weak on the local 14B model, but I can't pay for
API access" problem without giving up Hermes' tools/memory or paying anyone.

## Architecture

```
llm.provider: router
        │
        ▼
classify_needs_hermes(transcript)?
        │
   ┌────┴────┐
  yes         no
   │           │
   ▼           ▼
Hermes    classify_reasoning_level(transcript)?  (FREE local classifier)
                │
           ┌────┴────┐
          high      low/medium
           │           │
           ▼           ▼
     openrouter_    groq → gemini → openrouter_free → openrouter_paid → (all failed) → Hermes
     reasoning            │                                  │
     (tried first,   real money — hard-capped, see      real money — hard-capped,
     falls through   "Cost protection" below             see "Cost protection" below
     to the normal
     cascade if it
     fails/no budget)
```

`classify_needs_hermes` is a keyword heuristic (`server/ai_router/router.py`,
`DEFAULT_FORCE_HERMES_KEYWORDS`) — anything that smells like terminal/shell,
real web search, file read/write, cron/kanban, or skills skips straight to
Hermes, because those capabilities only exist inside Hermes' own agent loop
and no raw chat API can fake them. Everything else races through the online
providers first for speed.

Local PC actions (WhatsApp, Gmail send, folders, Spotify, humanoid view) are
intercepted even earlier, in `VoicePipelineServer._try_pc_action()` — before
any provider (Hermes, router, ollama, anthropic) is even considered. They
were already provider-agnostic; the router doesn't touch them.

The one gap that switching away from Hermes *would* have opened is YouTube /
media-panel control (previously only reachable via Hermes' own reasoning +
the `hud_display` plugin). The router closes it with a small function-calling
tool set (`ROUTER_TOOLS`) executed locally — see "Tools" below.

## Providers

| Provider | Model (default) | Why | Docs to re-check before relying on limits |
|---|---|---|---|
| Groq | `openai/gpt-oss-120b` | ~500 tok/s, 131K ctx, tool-calling, no card required | console.groq.com/docs/models, console.groq.com/settings/limits |
| Gemini | `gemini-2.5-flash` | fast, 1M ctx, stable free tier, tool-calling | ai.google.dev/gemini-api/docs/rate-limits, aistudio.google.com/rate-limit |
| OpenRouter (free) | `openrouter/free` | $0, self-heals when individual `:free` models rotate out (they do constantly) | openrouter.ai/docs/api-reference/limits |
| OpenRouter (paid fallback) | `openai/gpt-oss-20b` | last online resort — real money, only reached once every free tier above is unavailable. Hard-capped, see "Cost protection" below | openrouter.ai/models, `GET openrouter.ai/api/v1/credits` for live balance |
| Hermes | (your Hermes config) | full agent: tools, memory, skills, cron. Final fallback, and forced target for anything the online models can't do. | n/a — already yours |

Note: `openrouter/auto:free` is **not** a real model id (verified live against
`GET openrouter.ai/api/v1/models` on 2026-09-01) — `openrouter/auto` exists
but is a paid variable-cost auto-router, and the free alias is just
`openrouter/free`. Don't configure `openrouter/auto:free`, it'll 404.

## Cost protection (OpenRouter paid fallback)

Jesús has an existing $3.79 prepaid OpenRouter balance (no card, no
auto-recharge) that he's fine spending, but never wants exceeded and never
wants topped up automatically. `server/ai_router/cost_guard.py`
(`CostCappedProvider`) enforces that:

1. **Pre-flight refusal, not post-hoc accounting.** Before a paid call is
   ever made, it computes the *worst case* cost (`input_tokens × price_in +
   max_tokens × price_out` — `max_tokens` is the hard ceiling we already
   pass, so this is a true upper bound) and checks
   `already_spent + worst_case > max_spend`. If that's true, it raises
   immediately — the real OpenRouter API is never called. This is what makes
   it a real limit instead of a log line: nothing can overspend by the time
   we'd notice.
2. **Persisted, lifetime, never pruned.** Spend is tracked in
   `server/logs/ai_router_usage.json` under a `spend` key, separate from the
   daily-reset request/token counters — a prepaid balance doesn't reset at
   midnight. Survives restarts.
3. **Cross-checked against the real balance, best-effort.** If
   `GET openrouter.ai/api/v1/credits` is reachable, its reported remaining
   balance tightens the cap further (`min(configured max_spend, spent + real
   remaining)`) — cached 5 minutes to avoid a network round-trip on every
   turn. If that call fails, it falls back to the local ledger alone — it
   never fails open (a broken balance check can only make the cap *stricter*
   by being ignored, never looser).
4. **No auto top-ups, ever.** There is no code path that increases
   `max_spend` or purchases credit. Once the cap is hit it stays hit until a
   human edits `max_spend` in config.
5. **Authorized model only.** `CostCappedProvider` is constructed with an
   `authorized_model` and refuses (`ProviderError(kind="unauthorized")`) if
   the wrapped provider's model doesn't match exactly — the paid model is
   fixed at startup from config, never chosen per-request, and this is a
   second explicit check on top of that.

Set `ai_router.openrouter.paid_fallback.max_spend` to `0` (or omit it) to
disable the paid tier entirely — it's then skipped at startup and never
attempted, same as any other provider with no key configured.

Model/limit numbers move fast and official docs don't always publish them in
scrapeable text — `quota_threshold_pct` and `daily_request_limit` in the
config are deliberately approximate; the real enforcement is the
429/quota-exceeded → cooldown path, not the counter.

## Deep-reasoning escalation (2026-09-07)

A second, separate paid tier — `openrouter_reasoning` — for problems that
genuinely need strong reasoning (multi-step planning, debugging,
contradictions, high-stakes decisions), not routine conversation. Jesús has
~$3.20 (3€) total, no auto-recharge, fine to exhaust it — the goal is
**maximum useful calls out of that fixed amount**, not preserving it.

**Why a second tier instead of reusing `openrouter_paid`:** different job.
`openrouter_paid` is a last-resort safety net (only reached when every free
tier fails). This one is a deliberate escalation *ahead of* the free tiers
when the problem warrants it, even if groq/gemini would have succeeded.
Different trigger, different budget, same OpenRouter account/API key (no
reason to create a second key — both tiers' `CostCappedProvider` instances
check the same live account balance independently, see "Cost protection"
above; they can't combine to overspend the real account even with two
separate code-side ledgers).

**Model: Claude Opus 5** (`anthropic/claude-opus-5-20260723`), not GPT-6
Astra. Verified live against `openrouter.ai/api/v1/models` (2026-09-07):

| | $/1M in | $/1M out | ARC-AGI-3 |
|---|---|---|---|
| Claude Opus 5 | $5 | $25 | 30.2% |
| GPT-6 Astra | $10 | $50 | 62.7% (ARC Prize verified) |

Astra reasons meaningfully better on the exact benchmark that motivated this
feature, but at 2x the price — with $3.20 total, that's the difference
between ~140 calls and ~70. Chose to optimize for *volume of usable
escalations* now; revisit once Astra's price drops (swap
`ai_router.openrouter.reasoning.model` + the two price fields, nothing else
— the provider/cost-guard/classifier code is model-agnostic on purpose).

**Gating — `classify_reasoning_level()`** (`router.py`): explicit trigger
words ("piensa", "razona", "analiza a fondo"...) short-circuit straight to
`"high"` for free. Everything else goes through the same FREE local-model
classifier pattern as `classify_needs_hermes` (zero cloud cost) — the paid
model is **never** asked whether it's needed, that would spend money to
decide whether to spend money. Defaults to `"low"` (never escalate) on any
classifier failure/timeout.

**Compressed context, not full history** (`_build_reasoning_messages()`):
the reasoning tier gets the last 4 messages + the current turn, not the
router's normal `history_turns` window — a deep-reasoning problem hinges on
the current ask, not a long backlog, and this is most of the cost saving.
Full RAG-style relevant-memory retrieval (pull in only specific old facts
that matter) is a real future upgrade, not built yet — today it's just "keep
it short."

**Reasoning tokens stay hidden.** OpenRouter's `reasoning: {"exclude": true}`
(the *nested* param — different from Groq's flat `reasoning_effort` string,
see `_openai_compatible.py`) lets the model think internally without ever
returning that chain-of-thought in the response. It still counts as billed
output tokens either way — `exclude` only controls what's shown, not cost.

**Three independent caps** (`cost_guard.py`, all optional, all additive to
the existing lifetime `max_spend`):
- `max_cost_per_request` — a single call's worst-case estimate can never
  authorize past this, checked before the network call.
- `daily_max_spend` — a safety net against a runaway loop burning the whole
  budget in one bad session; separate from the lifetime cap, which the user
  is fine exhausting entirely over time.
- `max_spend` — the lifetime ceiling (existing mechanism, reused as-is).

**Where it plugs into the cascade** (`chat_online()`): tried FIRST, ahead of
`priority`, only for the one turn that classified `"high"`. If it fails
(cap reached, rate limit, network error) the exact same
`except ProviderError: continue` loop that already handles every other
provider falls through to the normal groq → gemini → openrouter_free →
openrouter_paid order — no special-cased fallback logic needed, it's the
same loop.

**Not wired in:** Hermes' own native model/reasoning (only this AI Router's
fast lane gets access to it — `~/.hermes/config.yaml` is untouched), and
tool-calling for the reasoning tier (`use_tools` still applies but
`ROUTER_TOOLS` is the same small local-action set as everywhere else — no
Hermes Tool Gateway integration was requested or built).

## Configuration

`server/config/server.yaml`:

```yaml
llm:
  provider: router

ai_router:
  enabled: true
  priority: [groq, gemini, openrouter_free, openrouter_paid]
  quota_threshold_pct: 80
  groq: {model: openai/gpt-oss-120b, api_key_env: GROQ_API_KEY, daily_request_limit: 1000, reasoning_effort: low}
  gemini: {model: gemini-2.5-flash, api_key_env: GEMINI_API_KEY, daily_request_limit: 1000, thinking_budget: 0}
  openrouter:
    api_key_env: OPENROUTER_API_KEY   # shared by free and paid_fallback
    free:
      enabled: true
      model: openrouter/free
    paid_fallback:
      enabled: true
      model: openai/gpt-oss-20b
      max_spend: 3.79                 # hard lifetime cap in USD — see "Cost protection" below
      price_per_mtok_input: 0.03
      price_per_mtok_output: 0.13
```

## API keys

Set in `%USERPROFILE%\.hermes\.env` (already the first path `load_env()`
checks) — never in this repo. See `.env.example` for the variable names. A
provider with no key set is skipped silently at startup (logged once, not
per turn).

## Failover & cooldown

Any `ProviderError` (rate limit, quota exceeded, invalid key, timeout,
5xx, connection error) moves to the next provider in `priority` and puts the
failed one on cooldown — no error reaches the user, no conversation breaks.
Cooldown length depends on the failure kind (`server/ai_router/quota.py`,
`COOLDOWN_SECONDS`): a 429 clears in a minute, a "quota exceeded" in 15
minutes, an invalid key or permanent config error in an hour. A cooled-down
provider is retried automatically once its cooldown expires — no restart
needed.

## Tools

`ROUTER_TOOLS` in `router.py` is a deliberately small subset of the
`HUD_TOOLS` schema already declared in `server.py`: `open_youtube`,
`open_browser`, `open_folder`, `check_gmail`, `look_with_camera`. These are
the only ones that can be *fully* executed without Hermes.

`look_with_camera` (`server/camera.py`, OpenCV) grabs one frame from the
local webcam and asks Gemini to describe it — the only online provider that's
actually multimodal today (Groq/OpenRouter's configured gpt-oss models are
text-only). Whichever provider is answering the turn can still *call* the
tool; the image description itself always goes through the Gemini adapter's
`describe_image()`. No frame is ever saved to disk or kept in memory past
that one call. `send_whatsapp` / `draft_job_email` /
`read_cv` / `remember` are left off on purpose — giving an online model a
tool it can only half-execute (e.g. WhatsApp send actually goes through
Hermes' own plugin) is worse than no tool, and `remember` already happens
automatically on every turn via `_remember_turn` → `_maybe_learn`, regardless
of provider.

To add a tool: add its schema to `ROUTER_TOOLS` and a branch in
`_execute_tool()` in `router.py`.

## Logs

Console, prefixed `[AI ROUTER]`:

```
[AI ROUTER] groq -> rate_limit (...); cooldown 60s
[AI ROUTER] gemini -> SUCCESS  Latency: 1.18s
```

Usage counters (requests/tokens/errors per provider per day) persist to
`server/logs/ai_router_usage.json`. Never logs key values.

## Status

`GET /api/ai_status` → `{"enabled", "priority", "providers": {name: {state, model, requests, tokens_in, tokens_out, errors, ...}}}`.

## Adding a new provider (e.g. Cerebras, Mistral)

1. `server/ai_router/providers/<name>.py` — subclass `BaseProvider`
   (`chat()` for one non-streaming call with optional tools, `stream()` for
   the streaming call). If it's OpenAI-Chat-Completions-compatible, subclass
   `OpenAICompatibleProvider` instead — see `groq.py`/`openrouter.py` for a
   two-line example.
2. Register it in `PROVIDER_CLASSES` and `DEFAULT_MODELS` in `router.py`.
3. Add it to `priority` in config, with its `..._API_KEY` env var.

No changes to `server.py`, the classifier, or the tool-calling loop needed.

## Troubleshooting

- **A provider is always OFFLINE in `/api/ai_status`**: no API key set for
  it — check `%USERPROFILE%\.hermes\.env` has the right variable name
  (`api_key_env` in config, defaults to `<NAME>_API_KEY`).
- **Everything falls through to Hermes immediately**: check the console for
  `[AI ROUTER] <name> -> ...` lines — the failure kind tells you why
  (`invalid_key` = bad/expired key, `quota`/`rate_limit` = hit the free
  tier, `unavailable` = network/5xx).
- **A turn that clearly needed a tool (terminal, web search, files) got
  answered wrong by an online model**: `classify_needs_hermes` missed it —
  add the phrase's keyword to `ai_router.force_hermes_keywords` in config
  (overrides `DEFAULT_FORCE_HERMES_KEYWORDS`).
