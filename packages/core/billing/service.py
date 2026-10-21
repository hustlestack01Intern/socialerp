"""
Billing service (trial spine, plan M1.2-M1.4).

Responsibilities:

* create the ``pending`` subscription at registration and activate the trial
  clock at channel connect (M1.3);
* answer entitlement questions (``is_entitled``) using the plan x feature matrix
  and the clock — one predicate shared by the cron expiry path and the lazy read
  path (INV4);
* record usage idempotently under replay (INV11/U66);
* apply a human-confirmed plan change, audited (M1.5).

This module MUST NOT import ``packages.core.payments``: billing is separate from
tenant customer payments (dependency test ``test_billing_no_payments_import``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from socialerp.settings import get_settings
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.billing.entitlements import AgentLevel, Entitlements, entitlements_for
from packages.core.models.billing import (
    BillingPaymentReference,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    UsageCounter,
    UsageEvent,
)

logger = logging.getLogger(__name__)

# M-Pesa confirmation codes are merchant-facing transaction ids, typically
# 10 alphanumeric characters. Shape check only — the founder still verifies
# the code against the Till/Paybill statement (billing-ops runbook).
_PAYMENT_REFERENCE_RE = re.compile(r"^[A-Z0-9]{6,20}$")


class PaymentReferenceUsedError(ValueError):
    """The M-Pesa reference was already used for another plan change (F2/U68)."""

    def __init__(self, reference: str) -> None:
        super().__init__(f"payment reference {reference!r} already used")
        self.reference = reference


def normalize_payment_reference(raw: str) -> str:
    """Strip, upper-case and shape-validate an M-Pesa reference.

    Raises ``ValueError`` with a customer-safe message on a bad shape.
    """
    reference = raw.strip().upper()
    if not _PAYMENT_REFERENCE_RE.match(reference):
        raise ValueError(
            "payment_reference must be 6-20 alphanumeric characters "
            "(the M-Pesa confirmation code)"
        )
    return reference


# Metrics and their grain. A cap check must name (metric, unit, window).
METRIC_MESSAGES_INBOUND = "messages_inbound"
METRIC_LLM_CALLS = "llm_calls"
METRIC_LLM_SPEND_USD_MICROS = "llm_spend_usd_micros"

_DAILY_METRICS = frozenset({
    METRIC_MESSAGES_INBOUND,
    METRIC_LLM_CALLS,
    METRIC_LLM_SPEND_USD_MICROS,
})


def utc_day_key(moment: datetime | None = None) -> str:
    """Return the UTC ``YYYY-MM-DD`` period key for a moment (U66)."""
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date().isoformat()


def utc_month_key(moment: datetime | None = None) -> str:
    """Return the UTC ``YYYY-MM`` period key for a moment."""
    moment = moment or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m")


@dataclass(frozen=True)
class EntitlementDecision:
    """The result of an entitlement check, with a machine-readable reason."""

    entitled: bool
    reason: str  # "ok" | "no_subscription" | "pending" | "expired" | "cancelled" | "past_due"
    plan: str
    entitlements: Entitlements


class BillingService:
    """Tenant-scoped billing operations. The caller owns the transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self._clock = now

    def _now(self) -> datetime:
        return self._clock if self._clock is not None else datetime.now(UTC)

    # -- subscription lifecycle -------------------------------------------

    async def get_subscription(self, tenant_id: UUID) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def ensure_pending(self, tenant_id: UUID) -> Subscription:
        """Create the ``pending`` subscription row at registration (idempotent).

        A tenant that signs up but never connects a channel stays ``pending``
        with no trial clock, so no trial is burned (U67 / INV2).
        """
        existing = await self.get_subscription(tenant_id)
        if existing is not None:
            return existing
        sub = Subscription(
            tenant_id=tenant_id,
            plan=PlanTier.TRIAL.value,
            status=SubscriptionStatus.PENDING.value,
        )
        self.session.add(sub)
        await self.session.flush()
        return sub

    async def start_trial(
        self,
        tenant_id: UUID,
        *,
        channel: str = "whatsapp",
        idempotency_key: str | None = None,
    ) -> Subscription:
        """Start the 14-day trial clock at channel connect (M1.3).

        Idempotent: the FIRST connect wins and ``period_end`` is fixed; a replay
        (or a second connect days later) must not move it — trial-clock gaming
        (U67). If the row does not exist yet it is created directly ``active``.
        """
        sub = await self.ensure_pending(tenant_id)
        if sub.status == SubscriptionStatus.ACTIVE and sub.period_end is not None:
            return sub  # already started; first connect wins
        if sub.status in (
            SubscriptionStatus.EXPIRED.value,
            SubscriptionStatus.CANCELLED.value,
        ):
            # An expired/cancelled subscription is NOT restarted by a reconnect:
            # only a human plan change reactivates (trial stacking defense).
            return sub

        settings = get_settings()
        now = self._now()
        sub.plan = PlanTier.TRIAL.value
        sub.status = SubscriptionStatus.ACTIVE.value
        sub.period_start = now
        sub.period_end = now + timedelta(days=settings.TRIAL_LENGTH_DAYS)
        sub.metadata_json = {**(sub.metadata_json or {}), "trial_channel": channel}
        if idempotency_key:
            sub.metadata_json = {**sub.metadata_json, "trial_start_event": idempotency_key}
        await self.session.flush()
        await AuditLogger(self.session).log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="billing.trial_started",
            resource="subscription",
            resource_id=sub.id,
            after={
                "plan": sub.plan,
                "status": sub.status,
                "period_end": sub.period_end.isoformat(),
            },
        )
        return sub

    async def extend_trial(
        self,
        tenant_id: UUID,
        *,
        approver_user_id: UUID,
        reason: str,
    ) -> Subscription:
        """Human-approved 7-day extension, at most once (U60/U68)."""
        settings = get_settings()
        sub = await self.get_subscription(tenant_id)
        if sub is None:
            raise ValueError("No subscription to extend")
        if sub.extension_count >= 1:
            raise ValueError("Trial already extended once")
        if sub.period_end is None:
            raise ValueError("Trial has not started")
        before = sub.period_end
        sub.period_end = sub.period_end + timedelta(days=settings.TRIAL_EXTENSION_DAYS)
        sub.extension_count += 1
        sub.last_extension_at = self._now()
        await self.session.flush()
        await AuditLogger(self.session).log_action(
            tenant_id=tenant_id,
            user_id=approver_user_id,
            action="billing.trial_extended",
            resource="subscription",
            resource_id=sub.id,
            before={"period_end": before.isoformat()},
            after={"period_end": sub.period_end.isoformat(), "reason": reason},
        )
        return sub

    async def expire_due_subscriptions(self) -> int:
        """Flip active trials past ``period_end`` to ``expired`` (cron + lazy).

        Returns the number of rows changed. Does NOT touch ``pending`` rows
        whose own expiry rule is handled by :meth:`expire_pending_subscriptions`.
        """
        now = self._now()
        result = await self.session.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.ACTIVE.value,
                Subscription.plan == PlanTier.TRIAL.value,
                Subscription.period_end.is_not(None),
                Subscription.period_end <= now,
            )
        )
        subs = list(result.scalars().all())
        for sub in subs:
            sub.status = SubscriptionStatus.EXPIRED.value
        if subs:
            await self.session.flush()
        return len(subs)

    async def expire_pending_subscriptions(self) -> int:
        """Cancel pending subscriptions older than the pending-expiry window.

        Keeps a verified number from being permanently blocked (M1.3).
        """
        settings = get_settings()
        cutoff = self._now() - timedelta(days=settings.TRIAL_PENDING_EXPIRY_DAYS)
        result = await self.session.execute(
            select(Subscription).where(
                Subscription.status == SubscriptionStatus.PENDING.value,
                Subscription.created_at < cutoff,
            )
        )
        subs = list(result.scalars().all())
        for sub in subs:
            sub.status = SubscriptionStatus.CANCELLED.value
        if subs:
            await self.session.flush()
        return len(subs)

    # -- entitlement -------------------------------------------------------

    async def check_entitlement(self, tenant_id: UUID) -> EntitlementDecision:
        """One predicate for entitlement, shared by cron and lazy read (INV4)."""
        sub = await self.get_subscription(tenant_id)
        if sub is None:
            return EntitlementDecision(
                entitled=False,
                reason="no_subscription",
                plan=PlanTier.STARTER.value,
                entitlements=entitlements_for(PlanTier.STARTER),
            )
        plan = sub.plan
        ents = entitlements_for(plan)
        if sub.status == SubscriptionStatus.PENDING.value:
            return EntitlementDecision(False, "pending", plan, ents)
        if sub.status in (
            SubscriptionStatus.EXPIRED.value,
            SubscriptionStatus.CANCELLED.value,
        ):
            return EntitlementDecision(False, sub.status, plan, ents)
        if sub.status == SubscriptionStatus.PAST_DUE.value:
            # past_due keeps entitlement for now (no grace removal in trial);
            # a human decides. Treated as entitled so we never silence an
            # in-flight customer (I4); flagged here for the owner console.
            return EntitlementDecision(True, "ok", plan, ents)
        # active
        if sub.period_end is not None and self._now() >= sub.period_end:
            return EntitlementDecision(False, "expired", plan, ents)
        return EntitlementDecision(True, "ok", plan, ents)

    def agent_level_ceiling(self, plan: str) -> AgentLevel:
        return entitlements_for(plan).agent_level

    # -- usage (idempotent) ------------------------------------------------

    async def record_usage(
        self,
        tenant_id: UUID,
        metric: str,
        *,
        source_event_id: str,
        amount: int = 1,
        moment: datetime | None = None,
    ) -> int:
        """Record one usage event idempotently; return the new counter value.

        The event is unique on ``(tenant, metric, source_event_id)``. A replay
        is a no-op: it neither inserts a new event nor increments the counter
        (INV11). The counter is incremented atomically (``value = value +
        amount``) so concurrent writers cannot lose an update (INV15).
        """
        if metric not in _DAILY_METRICS:
            raise ValueError(f"Unknown metric grain for {metric!r}")
        period = utc_day_key(moment)

        # 1. Insert the event; on conflict do nothing and return no row.
        stmt = (
            pg_insert(UsageEvent)
            .values(
                tenant_id=tenant_id,
                metric=metric,
                period=period,
                amount=amount,
                source_event_id=source_event_id,
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "metric", "source_event_id"],
            )
            .returning(UsageEvent.__table__.c.id)
        )
        inserted = (await self.session.execute(stmt)).scalar_one_or_none()

        if inserted is None:
            # Duplicate (replay): counter unchanged.
            current = await self._counter_value(tenant_id, metric, period)
            return current

        # 2. Atomic counter upsert.
        counter_stmt = (
            pg_insert(UsageCounter)
            .values(tenant_id=tenant_id, metric=metric, period=period, value=amount)
            .on_conflict_do_update(
                index_elements=["tenant_id", "metric", "period"],
                set_={"value": UsageCounter.__table__.c.value + amount},
            )
        )
        await self.session.execute(counter_stmt)
        return await self._counter_value(tenant_id, metric, period)

    async def _counter_value(self, tenant_id: UUID, metric: str, period: str) -> int:
        result = await self.session.execute(
            select(UsageCounter.value).where(
                UsageCounter.tenant_id == tenant_id,
                UsageCounter.metric == metric,
                UsageCounter.period == period,
            )
        )
        return int(result.scalar_one_or_none() or 0)

    async def get_usage(
        self, tenant_id: UUID, *, metric: str, moment: datetime | None = None
    ) -> int:
        """Current counter value for a metric in the UTC day of ``moment``."""
        return await self._counter_value(tenant_id, metric, utc_day_key(moment))

    async def sum_usage_events(self, tenant_id: UUID, *, metric: str) -> int:
        """Reconciliation helper: sum of ledger events for a metric (INV11)."""
        result = await self.session.execute(
            text(
                "SELECT COALESCE(SUM(amount), 0) FROM usage_events "
                "WHERE tenant_id = :tid AND metric = :metric"
            ),
            {"tid": str(tenant_id), "metric": metric},
        )
        return int(result.scalar_one() or 0)

    # -- payment references (F2 / U68) --------------------------------------

    async def record_payment_reference(
        self,
        tenant_id: UUID,
        *,
        reference: str,
        plan: PlanTier,
        amount_kes: int,
        recorded_by_user_id: UUID | None = None,
    ) -> UUID:
        """Record an M-Pesa reference for a paid plan change; dedupe UNIQUE.

        The insert races safely: ``ON CONFLICT (reference) DO NOTHING`` means
        a concurrent double-submit of the same code inserts exactly one row;
        the loser raises :class:`PaymentReferenceUsedError`. Cross-tenant
        reuse is caught by the same constraint — the table has no RLS by
        design (platform-scoped append-only ledger).

        The caller owns the transaction: on any later failure (e.g.
        ``apply_plan_change``), the reference insert rolls back with it, so a
        failed upgrade never burns a code.
        """
        normalized = normalize_payment_reference(reference)
        stmt = (
            pg_insert(BillingPaymentReference)
            .values(
                reference=normalized,
                tenant_id=tenant_id,
                plan=plan.value,
                amount_kes=amount_kes,
                recorded_by_user_id=recorded_by_user_id,
            )
            .on_conflict_do_nothing(index_elements=["reference"])
            .returning(BillingPaymentReference.__table__.c.id)
        )
        inserted = (await self.session.execute(stmt)).scalar_one_or_none()
        if inserted is None:
            raise PaymentReferenceUsedError(normalized)
        return UUID(str(inserted))

    # -- plan change (human-confirmed, audited) ----------------------------

    async def apply_plan_change(
        self,
        tenant_id: UUID,
        *,
        plan: PlanTier,
        approver_user_id: UUID,
        evidence: str | None = None,
    ) -> Subscription:
        """Apply a human-confirmed plan change (M1.5, audited).

        A plan change never by itself clears ``past_due`` (that needs an audited
        payment confirmation) and never shortens a paid period (INV18).

        F3 (approved 2026-10-21): a **paid** plan always has a period. The
        period runs from the payment date (``BILLING_PAID_PERIOD_DAYS``) and is
        never shorter than a period that is already paid for. Without this, a
        tenant that paid after an expired trial kept the trial's stale date and
        the entitlement gate kept denying them — the sale did not unlock the
        product — and a tenant upgraded from ``pending`` got ``period_end =
        NULL``, i.e. an eternal subscription. A change to the trial plan keeps
        the trial semantics (the clock starts at channel connect, U67).
        """
        sub = await self.get_subscription(tenant_id)
        if sub is None:
            sub = await self.ensure_pending(tenant_id)
        before = {
            "plan": sub.plan,
            "status": sub.status,
            "period_end": sub.period_end.isoformat() if sub.period_end else None,
        }
        sub.plan = plan.value
        # Entering a paid plan activates entitlement; entering trial keeps trial
        # semantics (clock starts at connect).
        if plan is not PlanTier.TRIAL and sub.status not in (SubscriptionStatus.PAST_DUE.value,):
            sub.status = SubscriptionStatus.ACTIVE.value
        if plan is not PlanTier.TRIAL:
            settings = get_settings()
            paid_end = self._now() + timedelta(days=settings.BILLING_PAID_PERIOD_DAYS)
            if sub.period_end is None or sub.period_end < paid_end:
                sub.period_end = paid_end
        sub.plan_change_evidence = evidence
        await self.session.flush()
        await AuditLogger(self.session).log_action(
            tenant_id=tenant_id,
            user_id=approver_user_id,
            action="billing.apply_plan_change",
            resource="subscription",
            resource_id=sub.id,
            before=before,
            after={
                "plan": sub.plan,
                "status": sub.status,
                "evidence": evidence,
                "period_end": sub.period_end.isoformat() if sub.period_end else None,
            },
        )
        return sub


__all__ = [
    "METRIC_LLM_CALLS",
    "METRIC_LLM_SPEND_USD_MICROS",
    "METRIC_MESSAGES_INBOUND",
    "BillingService",
    "EntitlementDecision",
    "PaymentReferenceUsedError",
    "normalize_payment_reference",
    "utc_day_key",
    "utc_month_key",
]
