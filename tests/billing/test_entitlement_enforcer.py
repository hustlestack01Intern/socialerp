"""The enforcer: one plan -> one decision (M1.6, U80).

The matrix was published but not enforced. These tests pin every cell of
``packages/core/billing/entitlements.py`` to a decision, so a plan that gains
or loses a feature cannot be published without the code following.

Invariant I3 is checked here too: the gate may deny premium features and agent
spend, never the owner's own data (insights).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from packages.core.billing.enforcer import (
    ENFORCED_FEATURES,
    Denial,
    EntitlementEnforcer,
    Feature,
)
from packages.core.billing.entitlements import AgentLevel, ApiAccess, entitlements_for
from packages.core.models.billing import PlanTier
from packages.core.models.core import User, UserRole

pytestmark = pytest.mark.integration


async def _set_plan(session, tenant_id, plan: str, status: str = "active") -> None:
    """Move the tenant's subscription to a plan/status."""
    await session.execute(
        text(
            "UPDATE subscriptions SET plan = :plan, status = :status, "
            "period_start = now(), period_end = now() + interval '30 days' "
            "WHERE tenant_id = :tid"
        ),
        {"plan": plan, "status": status, "tid": str(tenant_id)},
    )
    await session.flush()


async def _add_user(session, tenant_id, email: str) -> User:
    user = User(
        tenant_id=tenant_id,
        email=email,
        password_hash="x",
        full_name=email,
        role=UserRole.STAFF,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


# ---------------------------------------------------------------- the matrix


@pytest.mark.parametrize("plan", list(PlanTier))
async def test_every_cell_matches_the_matrix(tenant_session, tenant, plan) -> None:
    """Each plan's decisions are exactly the matrix's cells."""
    await _set_plan(tenant_session, tenant.id, plan.value)
    enforcer = EntitlementEnforcer(tenant_session)
    ents = entitlements_for(plan)

    for channel in ("whatsapp", "instagram", "tiktok"):
        decision = await enforcer.check_channel(tenant.id, channel)
        assert decision.allowed is (channel in ents.channels), (plan, channel)

    api = await enforcer.check_api_access(tenant.id)
    assert api.allowed is (ents.api_access >= ApiAccess.API), plan

    level = await enforcer.check_agent_level(tenant.id, "L3")
    assert level.allowed is (ents.agent_level >= AgentLevel.L3), plan

    insights = await enforcer.check_insights(tenant.id)
    assert insights.allowed is ents.insights, plan


async def test_unknown_plan_fails_closed(tenant_session, tenant) -> None:
    """A plan the code does not know must not open a door."""
    await _set_plan(tenant_session, tenant.id, "platinum-imagination")
    enforcer = EntitlementEnforcer(tenant_session)
    decision = await enforcer.check_channel(tenant.id, "instagram")
    assert decision.allowed is False
    assert decision.reason == Denial.CHANNEL_NOT_INCLUDED
    assert decision.entitlements == entitlements_for(PlanTier.STARTER)


# ------------------------------------------------------------------ channels


async def test_trial_cannot_use_instagram(tenant_session, tenant) -> None:
    enforcer = EntitlementEnforcer(tenant_session)
    decision = await enforcer.check_channel(tenant.id, "instagram")
    assert decision.allowed is False
    assert decision.reason == Denial.CHANNEL_NOT_INCLUDED
    assert decision.feature == Feature.CHANNELS
    assert "instagram" in decision.message


async def test_trial_can_use_whatsapp(tenant_session, tenant) -> None:
    enforcer = EntitlementEnforcer(tenant_session)
    decision = await enforcer.check_channel(tenant.id, "whatsapp")
    assert decision.allowed is True
    assert decision.reason == "ok"


async def test_professional_can_use_instagram(tenant_session, tenant) -> None:
    await _set_plan(tenant_session, tenant.id, "professional")
    enforcer = EntitlementEnforcer(tenant_session)
    assert (await enforcer.check_channel(tenant.id, "instagram")).allowed is True
    assert (await enforcer.check_channel(tenant.id, "tiktok")).allowed is False


# ----------------------------------------------------------------- api access


async def test_trial_has_no_api_access(tenant_session, tenant) -> None:
    enforcer = EntitlementEnforcer(tenant_session)
    decision = await enforcer.check_api_access(tenant.id)
    assert decision.allowed is False
    assert decision.reason == Denial.API_ACCESS_NOT_INCLUDED


async def test_professional_has_api_access(tenant_session, tenant) -> None:
    await _set_plan(tenant_session, tenant.id, "professional")
    enforcer = EntitlementEnforcer(tenant_session)
    assert (await enforcer.check_api_access(tenant.id)).allowed is True


async def test_webhooks_need_the_enterprise_level(tenant_session, tenant) -> None:
    await _set_plan(tenant_session, tenant.id, "professional")
    enforcer = EntitlementEnforcer(tenant_session)
    decision = await enforcer.check_api_access(
        tenant.id, minimum=ApiAccess.API_AND_WEBHOOKS
    )
    assert decision.allowed is False
    assert decision.reason == Denial.API_ACCESS_NOT_INCLUDED


# --------------------------------------------------------------------- seats


async def test_seat_limit_is_counted(tenant_session, tenant) -> None:
    """A trial has one seat; the owner already uses it."""
    await _add_user(tenant_session, tenant.id, "one@example.com")
    enforcer = EntitlementEnforcer(tenant_session)
    decision = await enforcer.check_seats(tenant.id)
    assert decision.allowed is False
    assert decision.reason == Denial.SEAT_LIMIT_REACHED
    assert decision.limit == "1"


async def test_a_free_seat_is_allowed(tenant_session, tenant) -> None:
    enforcer = EntitlementEnforcer(tenant_session)
    assert (await enforcer.check_seats(tenant.id)).allowed is True


async def test_professional_has_three_seats(tenant_session, tenant) -> None:
    await _set_plan(tenant_session, tenant.id, "professional")
    for index in range(3):
        await _add_user(tenant_session, tenant.id, f"seat{index}@example.com")
    enforcer = EntitlementEnforcer(tenant_session)
    assert (await enforcer.check_seats(tenant.id)).allowed is False
    assert (await enforcer.check_seats(tenant.id, adding=0)).allowed is True


async def test_enterprise_seats_are_unlimited(tenant_session, tenant) -> None:
    await _set_plan(tenant_session, tenant.id, "enterprise")
    for index in range(5):
        await _add_user(tenant_session, tenant.id, f"ent{index}@example.com")
    enforcer = EntitlementEnforcer(tenant_session)
    assert (await enforcer.check_seats(tenant.id, adding=10)).allowed is True


# --------------------------------------------------------------- agent level


async def test_the_plan_caps_the_agent_level(tenant_session, tenant) -> None:
    """A trial grants L2; asking for L3 is refused (the caller caps it)."""
    enforcer = EntitlementEnforcer(tenant_session)
    allowed = await enforcer.check_agent_level(tenant.id, "L2")
    denied = await enforcer.check_agent_level(tenant.id, "L3")
    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason == Denial.AGENT_LEVEL_ABOVE_CEILING
    assert denied.limit == "L2"


async def test_starter_caps_at_l1(tenant_session, tenant) -> None:
    await _set_plan(tenant_session, tenant.id, "starter")
    enforcer = EntitlementEnforcer(tenant_session)
    assert (await enforcer.check_agent_level(tenant.id, "L1")).allowed is True
    decision = await enforcer.check_agent_level(tenant.id, "L2")
    assert decision.allowed is False
    assert decision.limit == "L1"


# ---------------------------------------------------------------- plan state


async def test_a_stopped_plan_blocks_the_premium_features(tenant_session, tenant) -> None:
    """A professional plan that is not running must not hand out API access."""
    await _set_plan(tenant_session, tenant.id, "professional", status="expired")
    enforcer = EntitlementEnforcer(tenant_session)
    decision = await enforcer.check_api_access(tenant.id)
    assert decision.allowed is False
    assert decision.reason == Denial.PLAN_NOT_ENTITLED


async def test_insights_are_not_state_gated(tenant_session, tenant) -> None:
    """I3: the gate may not gate the owner's own data."""
    await _set_plan(tenant_session, tenant.id, "professional", status="cancelled")
    enforcer = EntitlementEnforcer(tenant_session)
    assert (await enforcer.check_insights(tenant.id)).allowed is True


# ------------------------------------------------------------ the published set


def test_the_enforced_set_covers_every_cell() -> None:
    """Every cell the matrix has is a cell the code decides."""
    assert {
        "channels",
        "agent_level",
        "api_access",
        "insights",
        "seats",
    } == ENFORCED_FEATURES
