"""Policy engine: always-human set, confidence gating and autonomy levels."""

from __future__ import annotations

import pytest

from packages.agent.intent import Intent, IntentResult
from packages.agent.policy import (
    AutonomyLevel,
    PolicyAction,
    PolicyEngine,
    RiskLevel,
)


def _result(intent: Intent, confidence: float = 0.9, **kw: object) -> IntentResult:
    return IntentResult(intent=intent, confidence=confidence, **kw)


def test_injection_escalates_even_at_l3() -> None:
    engine = PolicyEngine()
    decision = engine.decide(
        _result(Intent.FAQ, injection_detected=True), autonomy=AutonomyLevel.L3_OPTIMIZE
    )
    assert decision.action is PolicyAction.ESCALATE_HUMAN
    assert decision.risk is RiskLevel.HIGH
    assert decision.requires_human


def test_sensitive_action_always_human() -> None:
    engine = PolicyEngine()
    decision = engine.decide(
        _result(Intent.FAQ), autonomy=AutonomyLevel.L3_OPTIMIZE, sensitive_action="refund"
    )
    assert decision.action is PolicyAction.ESCALATE_HUMAN
    assert decision.requires_human


def test_risk_flags_escalate_at_any_level() -> None:
    decision = PolicyEngine().decide(
        _result(Intent.TALK_TO_HUMAN, risk_flags=["personal_number_payment"]),
        autonomy=AutonomyLevel.L1_ANSWER,
    )
    assert decision.action is PolicyAction.ESCALATE_HUMAN
    assert decision.risk is RiskLevel.HIGH


def test_low_confidence_escalates() -> None:
    decision = PolicyEngine().decide(
        _result(Intent.FAQ, confidence=0.2), autonomy=AutonomyLevel.L1_ANSWER
    )
    assert decision.action is PolicyAction.ESCALATE_HUMAN


def test_unknown_intent_escalates() -> None:
    decision = PolicyEngine().decide(
        _result(Intent.UNKNOWN, confidence=0.9), autonomy=AutonomyLevel.L1_ANSWER
    )
    assert decision.action is PolicyAction.ESCALATE_HUMAN


@pytest.mark.parametrize("intent", [Intent.COMPLAINT, Intent.TALK_TO_HUMAN])
def test_always_human_intents_handoff(intent: Intent) -> None:
    decision = PolicyEngine().decide(_result(intent), autonomy=AutonomyLevel.L1_ANSWER)
    assert decision.action is PolicyAction.HANDOFF
    assert decision.requires_human


def test_create_order_drafts_for_approval() -> None:
    decision = PolicyEngine().decide(_result(Intent.CREATE_ORDER), autonomy=AutonomyLevel.L1_ANSWER)
    assert decision.action is PolicyAction.DRAFT_FOR_APPROVAL
    assert not decision.auto_send
    assert not decision.may_execute


@pytest.mark.parametrize("autonomy", [AutonomyLevel.L2_TRANSACT, AutonomyLevel.L3_OPTIMIZE])
def test_create_order_executes_at_l2_and_above(autonomy: AutonomyLevel) -> None:
    decision = PolicyEngine().decide(_result(Intent.CREATE_ORDER), autonomy=autonomy)
    assert decision.action is PolicyAction.EXECUTE_ORDER
    assert decision.may_execute
    assert decision.auto_send
    assert not decision.requires_human


def test_order_capture_still_drafts_at_l0() -> None:
    decision = PolicyEngine().decide(_result(Intent.CREATE_ORDER), autonomy=AutonomyLevel.L0_SHADOW)
    assert decision.action is PolicyAction.DRAFT_FOR_APPROVAL
    assert not decision.may_execute


def test_injection_blocks_l2_order_execution() -> None:
    decision = PolicyEngine().decide(
        _result(Intent.CREATE_ORDER, injection_detected=True),
        autonomy=AutonomyLevel.L2_TRANSACT,
    )
    assert decision.action is PolicyAction.ESCALATE_HUMAN
    assert not decision.may_execute


def test_low_confidence_blocks_l2_order_execution() -> None:
    decision = PolicyEngine().decide(
        _result(Intent.CREATE_ORDER, confidence=0.2), autonomy=AutonomyLevel.L2_TRANSACT
    )
    assert decision.action is PolicyAction.ESCALATE_HUMAN
    assert not decision.may_execute


def test_faq_shadow_mode_drafts() -> None:
    decision = PolicyEngine().decide(_result(Intent.FAQ), autonomy=AutonomyLevel.L0_SHADOW)
    assert decision.action is PolicyAction.DRAFT_FOR_APPROVAL
    assert not decision.auto_send


def test_faq_at_l1_auto_replies() -> None:
    decision = PolicyEngine().decide(_result(Intent.FAQ), autonomy=AutonomyLevel.L1_ANSWER)
    assert decision.action is PolicyAction.AUTO_REPLY
    assert decision.auto_send
    assert not decision.requires_human


def test_configurable_confidence_threshold() -> None:
    from packages.agent.policy import PolicyConfig

    engine = PolicyEngine(PolicyConfig(min_confidence=0.95))
    decision = engine.decide(_result(Intent.FAQ, confidence=0.9), autonomy=AutonomyLevel.L1_ANSWER)
    assert decision.action is PolicyAction.ESCALATE_HUMAN
