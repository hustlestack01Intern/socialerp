"""F3: a paid plan change must open a paid period.

Found by the M1.6 vertical spine experiment (2026-10-21): a tenant that paid
for a plan after an expired trial kept the trial's stale ``period_end``, so
the entitlement gate kept denying them — the sale did not unlock the
product. The mirror defect: an upgrade from ``pending`` left
``period_end = NULL``, i.e. an eternal paid subscription.

Rules (approved 2026-10-21, ``docs/plans/2026-10-21-money-path-f3-paid-period.md``):

* a paid plan always has a period: ``now + BILLING_PAID_PERIOD_DAYS`` (30);
* a live period is never shortened (INV18);
* a change to the trial plan keeps the trial semantics (the clock starts at
  channel connect, U67).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from socialerp.settings import get_settings
from sqlalchemy import text

from packages.core.billing.service import BillingService
from packages.core.models.billing import PlanTier, SubscriptionStatus

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


async def _make_user(session, tenant_id) -> str:
    """Create an owner user (``audit_log.user_id`` is a FK to users)."""
    user_id = str(uuid4())
    await session.execute(
        text(
            "INSERT INTO users "
            "(id, tenant_id, email, password_hash, full_name, role, is_active) "
            "VALUES (:id, :tid, :email, 'x', 'Owner', 'owner', true)"
        ),
        {
            "id": user_id,
            "tid": str(tenant_id),
            "email": f"owner-{uuid4().hex[:8]}@example.com",
        },
    )
    await session.flush()
    return user_id


async def _set_subscription(session, tenant_id, *, status: str, plan: str, period_end):
    await session.execute(
        text(
            "UPDATE subscriptions SET status = :st, plan = :pl, period_end = :pe "
            "WHERE tenant_id = :tid"
        ),
        {
            "st": status,
            "pl": plan,
            "pe": period_end,
            "tid": str(tenant_id),
        },
    )
    await session.flush()


async def test_paid_plan_change_opens_a_paid_period(tenant_session, tenant) -> None:
    """An expired trial that is upgraded to a paid plan becomes entitled."""
    await _set_subscription(
        tenant_session,
        tenant.id,
        status=SubscriptionStatus.EXPIRED.value,
        plan=PlanTier.TRIAL.value,
        period_end=NOW - timedelta(days=1),
    )
    approver = await _make_user(tenant_session, tenant.id)
    service = BillingService(tenant_session, now=NOW)
    sub = await service.apply_plan_change(
        tenant.id,
        plan=PlanTier.PROFESSIONAL,
        approver_user_id=UUID(approver),
        evidence="QJG7XK2M9P",
    )
    await tenant_session.flush()

    days = get_settings().BILLING_PAID_PERIOD_DAYS
    assert sub.status == SubscriptionStatus.ACTIVE.value
    assert sub.plan == PlanTier.PROFESSIONAL.value
    assert sub.period_end == NOW + timedelta(days=days)
    # The customer can use the product again: this is the joint that was broken.
    decision = await service.check_entitlement(tenant.id)
    assert decision.entitled is True
    assert decision.reason == "ok"


async def test_paid_plan_change_never_shortens_a_live_period(tenant_session, tenant) -> None:
    """INV18: a period that is already paid for is not shortened by an upgrade."""
    live_end = NOW + timedelta(days=45)
    await _set_subscription(
        tenant_session,
        tenant.id,
        status=SubscriptionStatus.ACTIVE.value,
        plan=PlanTier.STARTER.value,
        period_end=live_end,
    )
    approver = await _make_user(tenant_session, tenant.id)
    service = BillingService(tenant_session, now=NOW)
    sub = await service.apply_plan_change(
        tenant.id,
        plan=PlanTier.ENTERPRISE,
        approver_user_id=UUID(approver),
        evidence="QJG7XK2M9P",
    )
    await tenant_session.flush()
    assert sub.period_end == live_end


async def test_paid_plan_change_inside_a_live_period_gives_a_full_period(
    tenant_session, tenant
) -> None:
    """A payment inside a live period opens a full period from the payment date.

    A renewal on day 10 of a 30-day period must buy 30 days from today (the
    old period is not thrown away, it is superseded), not the 20 days that
    were left — that would be a silent short payment.
    """
    await _set_subscription(
        tenant_session,
        tenant.id,
        status=SubscriptionStatus.ACTIVE.value,
        plan=PlanTier.STARTER.value,
        period_end=NOW + timedelta(days=20),
    )
    approver = await _make_user(tenant_session, tenant.id)
    service = BillingService(tenant_session, now=NOW)
    sub = await service.apply_plan_change(
        tenant.id,
        plan=PlanTier.PROFESSIONAL,
        approver_user_id=UUID(approver),
        evidence="QJG7XK2M9P",
    )
    await tenant_session.flush()
    days = get_settings().BILLING_PAID_PERIOD_DAYS
    assert sub.period_end == NOW + timedelta(days=days)
    # Never shortened: the old date is still inside the new period.
    assert sub.period_end > NOW + timedelta(days=20)


async def test_paid_plan_from_pending_gets_a_period(tenant_session, tenant) -> None:
    """A tenant that never trialled gets a real period (no eternal subscription)."""
    await _set_subscription(
        tenant_session,
        tenant.id,
        status=SubscriptionStatus.PENDING.value,
        plan=PlanTier.TRIAL.value,
        period_end=None,
    )
    approver = await _make_user(tenant_session, tenant.id)
    service = BillingService(tenant_session, now=NOW)
    sub = await service.apply_plan_change(
        tenant.id,
        plan=PlanTier.STARTER,
        approver_user_id=UUID(approver),
        evidence="QJG7XK2M9P",
    )
    await tenant_session.flush()
    days = get_settings().BILLING_PAID_PERIOD_DAYS
    assert sub.period_end == NOW + timedelta(days=days)
    assert sub.status == SubscriptionStatus.ACTIVE.value


async def test_plan_change_to_trial_does_not_open_a_paid_period(tenant_session, tenant) -> None:
    """The trial clock still starts at channel connect (U67), not at the change."""
    await _set_subscription(
        tenant_session,
        tenant.id,
        status=SubscriptionStatus.ACTIVE.value,
        plan=PlanTier.PROFESSIONAL.value,
        period_end=NOW + timedelta(days=10),
    )
    approver = await _make_user(tenant_session, tenant.id)
    service = BillingService(tenant_session, now=NOW)
    sub = await service.apply_plan_change(
        tenant.id,
        plan=PlanTier.TRIAL,
        approver_user_id=UUID(approver),
        evidence=None,
    )
    await tenant_session.flush()
    # Trial semantics: the clock is untouched by a plan change to trial.
    assert sub.period_end == NOW + timedelta(days=10)
    assert sub.plan == PlanTier.TRIAL.value


async def test_paid_period_is_audited(tenant_session, tenant) -> None:
    """The money trail shows what was bought and for how long."""
    await _set_subscription(
        tenant_session,
        tenant.id,
        status=SubscriptionStatus.EXPIRED.value,
        plan=PlanTier.TRIAL.value,
        period_end=NOW - timedelta(days=1),
    )
    approver = await _make_user(tenant_session, tenant.id)
    service = BillingService(tenant_session, now=NOW)
    await service.apply_plan_change(
        tenant.id,
        plan=PlanTier.STARTER,
        approver_user_id=UUID(approver),
        evidence="QJG7XK2M9P",
    )
    await tenant_session.flush()

    rows = await tenant_session.execute(
        text(
            "SELECT after_json FROM audit_log WHERE tenant_id = :tid "
            "AND action = 'billing.apply_plan_change'"
        ),
        {"tid": str(tenant.id)},
    )
    after = rows.scalar_one()
    assert after["plan"] == PlanTier.STARTER.value
    assert after["status"] == SubscriptionStatus.ACTIVE.value
    assert after["period_end"] is not None
