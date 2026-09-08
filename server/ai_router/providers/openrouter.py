from ._openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """Plain OpenAI-compatible adapter. The router builds two instances of
    this — one for the free model, one wrapped in CostCappedProvider for the
    paid fallback — see router.py. No paid/free logic lives here anymore."""

    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    # identifies the app to OpenRouter — not required, but their dashboard
    # attributes usage per app when it's set, useful for debugging quota.
    extra_headers = {"X-Title": "Jarvis HUD"}
