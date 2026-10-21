"""
Billing API (plan M1.5, skeptic G4).

Owner-only, tenant-scoped HTTP over :class:`packages.core.billing.BillingService`:

- ``GET  /api/v1/billing/subscription``  current plan, status, days remaining
- ``GET  /api/v1/billing/usage``         today's counters + entitlement decision
- ``POST /api/v1/billing/subscription``  owner-only plan change, audited

Design rules:
- The subscription is always the caller's tenant (from the JWT), never a body
  field, so a forged ``tenant_id`` cannot change another tenant's plan.
- A plan change requires the owner role (``require_owner``) and writes an
  audited ``billing.apply_plan_change`` row via ``BillingService``.
- Premium features are gated by the entitlement matrix; ``402`` is reserved for
  premium API endpoints (not used here yet).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.dependencies import (
    get_current_tenant_id,
    get_current_user_id,
    get_db,
    require_owner,
)
from packages.core.billing import (
    METRIC_LLM_CALLS,
    METRIC_MESSAGES_INBOUND,
    BillingService,
    entitlements_for,
)
from packages.core.billing.provider import build_default_billing_provider
from packages.core.models.billing import PlanTier

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

# Published KES prices (docs/PRICING_STRATEGY.md). Trial is free.
_PRICE_KES: dict[PlanTier, int] = {
    PlanTier.TRIAL: 0,
    PlanTier.STARTER: 2999,
    PlanTier.PROFESSIONAL: 7999,
    PlanTier.ENTERPRISE: 19999,
}


class SubscriptionView(BaseModel):
    plan: str
    status: str
    period_start: str | None
    period_end: str | None
    days_remaining: int | None
    entitled: bool
    entitlement_reason: str
    extension_count: int


class UsageView(BaseModel):
    period: str
    messages_inbound: int
    llm_calls: int
    agent_level: int
    channels: list[str]
    api_access: int
    seats: int | None


class PlanChangeRequest(BaseModel):
    plan: PlanTier
    evidence: str | None = Field(None, max_length=500)


class PlanChangeResponse(BaseModel):
    plan: str
    status: str
    changed: bool


class CollectionInstructionsView(BaseModel):
    provider: str
    plan: str
    amount_kes: int
    paybill: str
    account_ref: str
    note: str


def _days_remaining(period_end: datetime | None, now: datetime) -> int | None:
    if period_end is None:
        return None
    return max(0, (period_end - now).days)


@router.get("/subscription", response_model=SubscriptionView)
async def get_subscription(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SubscriptionView:
    """The caller tenant's subscription + live entitlement."""
    service = BillingService(db)
    sub = await service.get_subscription(tenant_id)
    if sub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No subscription")
    decision = await service.check_entitlement(tenant_id)
    now = datetime.now(UTC)
    return SubscriptionView(
        plan=sub.plan,
        status=sub.status,
        period_start=sub.period_start.isoformat() if sub.period_start else None,
        period_end=sub.period_end.isoformat() if sub.period_end else None,
        days_remaining=_days_remaining(sub.period_end, now),
        entitled=decision.entitled,
        entitlement_reason=decision.reason,
        extension_count=getattr(sub, "extension_count", 0),
    )


@router.get("/usage", response_model=UsageView)
async def get_usage(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UsageView:
    """Today's usage counters (UTC day) plus the plan's entitlements."""
    service = BillingService(db)
    decision = await service.check_entitlement(tenant_id)
    ents = entitlements_for(decision.plan)
    period = datetime.now(UTC).date().isoformat()
    return UsageView(
        period=period,
        messages_inbound=await service.get_usage(tenant_id, metric=METRIC_MESSAGES_INBOUND),
        llm_calls=await service.get_usage(tenant_id, metric=METRIC_LLM_CALLS),
        agent_level=int(ents.agent_level),
        channels=sorted(ents.channels),
        api_access=int(ents.api_access),
        seats=ents.seats,
    )


@router.post("/subscription", response_model=PlanChangeResponse)
async def change_plan(
    body: PlanChangeRequest,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_owner)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PlanChangeResponse:
    """Owner-only, audited plan change.

    The tenant/approver come from the authenticated token, never the body. The
    manual provider is used to validate the collection path in production; here
    we apply the change directly (a human-confirmed back-office action).
    """
    service = BillingService(db)
    sub = await service.get_subscription(tenant_id)
    before_plan = sub.plan if sub is not None else None
    if sub is not None and sub.plan == body.plan.value and sub.status == "active":
        return PlanChangeResponse(plan=sub.plan, status=sub.status, changed=False)

    updated = await service.apply_plan_change(
        tenant_id,
        plan=body.plan,
        approver_user_id=user_id,
        evidence=body.evidence,
    )
    await db.commit()
    return PlanChangeResponse(
        plan=updated.plan,
        status=updated.status,
        changed=updated.plan != before_plan,
    )


@router.get("/collection-instructions", response_model=CollectionInstructionsView)
async def collection_instructions(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_owner)],
    plan: PlanTier = PlanTier.STARTER,
) -> CollectionInstructionsView:
    """How to pay us for a plan (manual M-Pesa path for the pilot)."""
    provider = build_default_billing_provider()
    instr = provider.instructions(str(tenant_id), plan, amount_kes=_PRICE_KES[plan])
    return CollectionInstructionsView(
        provider=provider.name,
        plan=instr.plan.value,
        amount_kes=instr.amount_kes,
        paybill=instr.paybill,
        account_ref=instr.account_ref,
        note=instr.note,
    )
