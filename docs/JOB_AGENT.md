# JOB_AGENT

Job search, matching, and ranking as a thin orchestration layer on top of
capabilities Hermes Agent already has — no scraper, no second agent
framework, no duplicated web search. See `docs/instrucciones herrameintas.md`
for the original spec this implements.

## Why it's built this way

Hermes already does real web search/browsing, memory, and reasoning. The one
thing it can't do reliably on its own: compute real distance in km, compare
salaries, or catch a "vehículo propio" requirement against your transport
preference — an LLM doing that math from prose is exactly the kind of thing
that quietly hallucinates. So this plugin adds exactly two tools and nothing
else:

```
USER: "búscame trabajo cerca de casa que encaje con mi perfil"
        │
        ▼
router forces Hermes (job keywords in DEFAULT_FORCE_HERMES_KEYWORDS,
server/ai_router/router.py) — never reaches Groq/Gemini's fast lane,
so this costs zero online-router tokens by construction
        │
        ▼
Hermes' own reasoning decides the plan:
   job_profile   → location/radius/skills/salary/transport (structured,
                    server/job_match.py reads ~/.hermes/memories/job_profile.yaml)
   web_search /
   web_extract   → Hermes' own existing tools find real postings
   job_match     → dedupe + geocode + distance + hard filters + scoring,
                    all in Python, zero extra model tokens
        │
        ▼
compact ranked JSON back to Hermes → natural-language answer to the user
```

## Files

| File | Role |
|---|---|
| `server/job_match.py` | Profile loading, Nominatim geocoding (cached), Haversine distance, dedupe, hard filters (distance/vehicle/salary), scoring |
| `server/server.py` `/api/job_profile`, `/api/job_match` | HTTP endpoints the plugin calls, same pattern as `/api/yt_play` |
| `hermes-plugin/job_search/` | The Hermes plugin (`job_profile`, `job_match` tools) — install per `docs/SETUP.md` |
| `~/.hermes/memories/job_profile.yaml` | Structured user profile, auto-created empty on first `job_profile` call — **never committed, never hardcoded**, lives outside this repo |

## The profile

Auto-created with every field empty/commented on first use. Fields that are
empty or `null` are simply skipped when filtering — an unfilled profile never
blocks a search, it just can't distance-filter or salary-filter yet:

```yaml
location: ""              # your base city, e.g. "Alsasua, Navarra, España"
radius_km: 30
desired_roles: []
experience_years: null
skills: []
education: ""
languages: []
salary_min: null
schedule: ""
contract_types: []
transport:
  has_vehicle: false
  public_transport_ok: true
preferences: []
restrictions: []
```

Only the fields a given search actually needs ever leave this file — nothing
sends the whole file to any model as a blob. When the profile is empty for a
field Hermes needs, it's instructed to ask you rather than invent a value; in
practice it's also seen falling back to what it already knows about you from
its own memory (SOUL.md/USER.md) and saying so explicitly, which is a
reasonable second line of defense, not a bug.

## What `job_match` guarantees, deterministically (zero LLM tokens)

- **Dedup**: by URL (query string stripped) or normalized title+company.
- **Hard discards** (never shown as a match, listed separately with the reason): outside `radius_km`, requires a vehicle you don't have and isn't reachable by public transport when `public_transport_ok`, below `salary_min`.
- **Scoring** (0-100, only for what survives the discards): role/title match, skills overlap, proximity, contract-type match. Returned with `matching_reasons` / `missing_requirements` per posting so Hermes can explain a result without re-searching.
- **Geocoding**: OpenStreetMap Nominatim, no API key, cached at `server/logs/geocode_cache.json` (never re-geocodes the same city twice).

## Routing

Job-search phrasing is matched in `server/ai_router/router.py`
(`DEFAULT_FORCE_HERMES_KEYWORDS`) so it never reaches the Groq/Gemini fast
lane — those models don't have `job_profile`/`job_match` and would either
hallucinate postings or (observed live) tell the user to go search
InfoJobs/LinkedIn themselves, which isn't an answer.

## Known limitation

Getting Hermes to reliably chain **three** tool calls in the right order
(`job_profile` → `web_search`/`web_extract` → `job_match`) — instead of
answering straight from raw search results, or skipping the match step —
depends heavily on the model backing Hermes' own reasoning
(`model:` in `~/.hermes/config.yaml`). This was unreliable on the local
20B model even with "MANDATORY"/"STOP" wording in the tool description.

**Tried and reverted, then fixed for real (2026-09-07):** first tried Groq
(`openai/gpt-oss-120b`) as Hermes' primary model. Verified broken in
practice: Groq's free tier caps every plain text model (gpt-oss-120b/20b,
qwen3.6/3.8) at 8,000 tokens/minute per org — Hermes' own system prompt +
tool schemas alone already request ~15K tokens on a single turn, so real
usage hit HTTP 413 immediately, and Hermes does not fail over to
`fallback_providers` on a 413 (it tries to compress the request instead,
which fails the same way).

Landed on **Gemini 2.5 Flash** (via its OpenAI-compatible endpoint,
`https://generativelanguage.googleapis.com/v1beta/openai/`) as primary
instead — free tier gives ~250K TPM (vs Groq's 8K), comfortably clears
Hermes' real payload size. Verified live: `hermes chat` answers naturally
in the user's actual register (no more robotic markdown-manual replies),
and a real tool-calling turn (terminal command → read result → answer)
completed in 6s with zero errors. `fallback_providers` now tries `local`
(`gpt-oss:20b`, no rate ceiling) then `groq` in that order, for when Gemini
itself is unavailable. This is the config to keep; don't revert to Groq
primary without first getting Groq's paid Dev Tier (removes the 8K TPM
cap) or switching to `groq/compound`/`compound-mini` (70K TPM, but that's
Groq's own bundled agentic system with built-in tools — untested here,
risks double tool-calling / bypassing Hermes' own GO-gate from FASE 3).

## Testing

No dedicated test file — `server/job_match.py`'s scoring/dedupe/distance
logic is pure, side-effect-free functions; exercise it directly:

```bash
cd server
.venv/Scripts/python.exe -c "
import job_match
print(job_match.match_jobs([
    {'title': 'Técnico Informático', 'company': 'X', 'url': 'https://e.com/a',
     'location': 'Pamplona, España', 'description': 'requiere vehículo propio'},
], profile={**job_match._DEFAULT_PROFILE, 'location': 'Alsasua, Navarra, España', 'radius_km': 30}))
"
```

End-to-end: `POST /api/job_match` with a `postings` array (see
`hermes-plugin/job_search/schemas.py` for the exact shape) — protected by the
same `X-Jarvis-Token` header as every other `/api/*` route.
