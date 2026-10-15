"""
Policy engine (Phase 5).

Decides how much autonomy the agent may exercise for a given (autonomy level,
intent, confidence). The **always-human** set is fixed and transcends every
level: refunds, discounts beyond the configured limit, price changes, bulk
marketing sends, customer-data deletion, anything high-risk or low-confidence, and
any message that looks like a prompt-injection attempt.

Default autonomy is **L0 (shadow)**: the agent drafts, a human approves; nothing is
auto-sent (AGENTS.md §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from packages.agent.intent import Intent, IntentResult


class AutonomyLevel(str, Enum):
    L0_SHADOW = "L0"  # draft; human approves every reply
    L1_ANSWER = "L1"  # auto-reply to FAQs from catalog/rules only
    L2_TRANSACT = "L2"  # create orders, reserve stock, send STK (Phase 6)
    L3_OPTIMIZE = "L3"  # propose/apply low-risk inventory actions (later)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PolicyAction(str, Enum):
    AUTO_REPLY = "auto_reply"  # send automatically (L1+)
    DRAFT_FOR_APPROVAL = "draft_for_approval"  # L0 shadow
    HANDOFF = "handoff"  # route to a human, agent stops
    ESCALATE_HUMAN = "escalate_human"  # high-risk/low-confidence; human must decide


# Intents that must never be handled without a human, at any autonomy level.
ALWAYS_HUMAN_INTENTS = frozenset({
    Intent.COMPLAINT,
    Intent.TALK_TO_HUMAN,
})

# Sensitive operations the agent may never perform autonomously (modelled as
# action names for future L2/L3 tool calls).
ALWAYS_HUMAN_ACTIONS = frozenset({
    "refund",
    "price_change",
    "discount_beyond_limit",
    "bulk_marketing_send",
    "delete_customer_data",
})


@dataclass(frozen=True)
class PolicyConfig:
    min_confidence: float = 0.5
    max_discount_percent: float = 10.0


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    risk: RiskLevel
    reason: str
    autonomy: AutonomyLevel

    @property
    def requires_human(self) -> bool:
        return self.action in (PolicyAction.HANDOFF, PolicyAction.ESCALATE_HUMAN)

    @property
    def auto_send(self) -> bool:
        return self.action is PolicyAction.AUTO_REPLY


class PolicyEngine:
    """Maps (autonomy, intent, confidence) to a decision. Pure and testable."""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()

    def decide(
        self,
        result: IntentResult,
        *,
        autonomy: AutonomyLevel,
        sensitive_action: str | None = None,
    ) -> PolicyDecision:
        # 1. Prompt-injection: escalate immediately, never auto-handle.
        if result.injection_detected:
            return PolicyDecision(
                PolicyAction.ESCALATE_HUMAN,
                RiskLevel.HIGH,
                "prompt-injection attempt detected",
                autonomy,
            )

        # 2. Risk flags (e.g. suspected fraud): always human, never auto-handled.
        if result.risk_flags:
            return PolicyDecision(
                PolicyAction.ESCALATE_HUMAN,
                RiskLevel.HIGH,
                "risk flags: " + ", ".join(result.risk_flags),
                autonomy,
            )

        # 3. Sensitive operations are always human, at every level.
        if sensitive_action is not None and sensitive_action in ALWAYS_HUMAN_ACTIONS:
            return PolicyDecision(
                PolicyAction.ESCALATE_HUMAN,
                RiskLevel.HIGH,
                f"sensitive action requires human: {sensitive_action}",
                autonomy,
            )

        # 4. Low confidence or unknown intent: ask a human, do not guess.
        if result.intent is Intent.UNKNOWN or result.confidence < self.config.min_confidence:
            return PolicyDecision(
                PolicyAction.ESCALATE_HUMAN,
                RiskLevel.MEDIUM,
                "low confidence or unknown intent",
                autonomy,
            )

        # 5. Always-human intents.
        if result.intent in ALWAYS_HUMAN_INTENTS:
            return PolicyDecision(
                PolicyAction.HANDOFF,
                RiskLevel.MEDIUM,
                f"intent requires a human: {result.intent.value}",
                autonomy,
            )

        # 6. State-changing intents: draft for approval until L2 exists.
        if result.intent is Intent.CREATE_ORDER:
            return PolicyDecision(
                PolicyAction.DRAFT_FOR_APPROVAL,
                RiskLevel.MEDIUM,
                "order capture is human-approved",
                autonomy,
            )

        # 7. Informational intents.
        if autonomy is AutonomyLevel.L0_SHADOW:
            return PolicyDecision(
                PolicyAction.DRAFT_FOR_APPROVAL,
                RiskLevel.LOW,
                "shadow mode: draft for approval",
                autonomy,
            )

        # L1+ may auto-answer grounded FAQs.
        return PolicyDecision(
            PolicyAction.AUTO_REPLY,
            RiskLevel.LOW,
            "grounded informational answer",
            autonomy,
        )


__all__ = [
    "ALWAYS_HUMAN_ACTIONS",
    "ALWAYS_HUMAN_INTENTS",
    "AutonomyLevel",
    "PolicyAction",
    "PolicyConfig",
    "PolicyDecision",
    "PolicyEngine",
    "RiskLevel",
]
