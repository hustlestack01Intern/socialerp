"""The HTTP face of the entitlement enforcer (M1.6, U80).

One place decides whether a tenant may use a feature
(:class:`packages.core.billing.enforcer.EntitlementEnforcer`); this module
only turns a denial into an HTTP answer, so every route refuses the same way.

The status code is ``402 Payment Required``: the tenant is not *forbidden*,
their plan simply does not include the thing they asked for, and the message
says which plan and what is missing. ``403`` would say "you are not allowed",
which is a different problem (and would be wrong for the owner of a paid
account asking for a feature their plan lacks).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.billing.enforcer import EntitlementEnforcer, FeatureDecision


async def require_api_access(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    minimum: str = "API",
) -> FeatureDecision:
    """Refuse (402) when the tenant's plan does not include API access."""
    from packages.core.billing.entitlements import ApiAccess

    enforcer = EntitlementEnforcer(session)
    decision = await enforcer.check_api_access(
        tenant_id, minimum=ApiAccess[minimum]
    )
    return _refuse_unless(decision)


async def require_seat(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    adding: int = 1,
) -> FeatureDecision:
    """Refuse (402) when the tenant has no free seat left."""
    enforcer = EntitlementEnforcer(session)
    decision = await enforcer.check_seats(tenant_id, adding=adding)
    return _refuse_unless(decision)


async def require_channel(
    session: AsyncSession,
    tenant_id: UUID,
    channel: str,
) -> FeatureDecision:
    """Refuse (402) when the tenant's plan does not include the channel."""
    enforcer = EntitlementEnforcer(session)
    decision = await enforcer.check_channel(tenant_id, channel)
    return _refuse_unless(decision)


async def require_insights(
    session: AsyncSession,
    tenant_id: UUID,
) -> FeatureDecision:
    """Refuse (402) when the tenant's plan does not include insights."""
    enforcer = EntitlementEnforcer(session)
    decision = await enforcer.check_insights(tenant_id)
    return _refuse_unless(decision)


def _refuse_unless(decision: FeatureDecision) -> FeatureDecision:
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=decision.message,
        )
    return decision


__all__ = [
    "require_api_access",
    "require_channel",
    "require_insights",
    "require_seat",
]
