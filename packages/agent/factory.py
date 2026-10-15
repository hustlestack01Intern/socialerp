"""Build the default LLM gateway from application settings (ADR 0012)."""

from __future__ import annotations

from socialerp.settings import Settings, get_settings

from packages.agent.gateway import LLMGateway, build_gateway
from packages.agent.http_gateway import OpenAICompatibleGateway


def build_default_gateway(settings: Settings | None = None) -> LLMGateway:
    """Construct the configured gateway with safety wrappers.

    ``LLM_PROVIDER=local`` targets Ollama's OpenAI-compatible endpoint. Hosted
    providers share the same wire shape, so they use the same adapter.
    """
    s = settings or get_settings()
    inner = OpenAICompatibleGateway(base_url=s.LLM_BASE_URL, api_key=s.LLM_API_KEY)
    return build_gateway(inner=inner, daily_cap_usd=s.LLM_PER_TENANT_DAILY_CAP_USD)


__all__ = ["build_default_gateway"]
