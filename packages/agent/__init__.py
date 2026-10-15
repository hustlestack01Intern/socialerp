"""The agent subsystem: LLM gateway, intent, policy and the agent loop (Phase 5)."""

from packages.agent.agent import (
    Agent,
    AgentOutcome,
    BusinessFacts,
    CatalogItem,
    InMemoryCatalog,
)
from packages.agent.errors import AgentError, IntentParseError
from packages.agent.gateway import (
    CachingGateway,
    LLMError,
    LLMGateway,
    LLMMessage,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUsage,
    RetryingGateway,
    ScriptedGateway,
    SpendCapError,
    SpendCapGateway,
    build_gateway,
)
from packages.agent.http_gateway import OpenAICompatibleGateway
from packages.agent.intent import (
    HeuristicIntentExtractor,
    Intent,
    IntentResult,
    ItemSlot,
    LLMIntentExtractor,
    detect_injection,
    extract_intent,
)
from packages.agent.policy import (
    ALWAYS_HUMAN_ACTIONS,
    ALWAYS_HUMAN_INTENTS,
    AutonomyLevel,
    PolicyAction,
    PolicyConfig,
    PolicyDecision,
    PolicyEngine,
    RiskLevel,
)

__all__ = [
    "ALWAYS_HUMAN_ACTIONS",
    "ALWAYS_HUMAN_INTENTS",
    "Agent",
    "AgentError",
    "AgentOutcome",
    "AutonomyLevel",
    "BusinessFacts",
    "CachingGateway",
    "CatalogItem",
    "HeuristicIntentExtractor",
    "InMemoryCatalog",
    "Intent",
    "IntentParseError",
    "IntentResult",
    "ItemSlot",
    "LLMError",
    "LLMGateway",
    "LLMIntentExtractor",
    "LLMMessage",
    "LLMProviderError",
    "LLMRequest",
    "LLMResponse",
    "LLMTimeoutError",
    "LLMUsage",
    "OpenAICompatibleGateway",
    "PolicyAction",
    "PolicyConfig",
    "PolicyDecision",
    "PolicyEngine",
    "RetryingGateway",
    "RiskLevel",
    "ScriptedGateway",
    "SpendCapError",
    "SpendCapGateway",
    "build_gateway",
    "detect_injection",
    "extract_intent",
]


def __getattr__(name: str) -> object:
    # Lazy to avoid importing application settings (and requiring env vars) on
    # a bare ``import packages.agent`` — e.g. in the offline eval harness.
    if name == "build_default_gateway":
        from packages.agent.factory import build_default_gateway

        return build_default_gateway
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
