"""
Billing service tests (plan M1.2-M1.4).

Covers the state-changing invariants that need a database:

* trial clock starts at connect and is immutable under replay/2nd connect (U67);
* usage counters are idempotent under replay and monotonic (U66 / INV11);
* expiry is one predicate (cron + lazy) (INV4);
* cross-tenant isolation of the new tables;
* plan change is audited and does not silently clear ``past_due`` (INV18).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from socialerp.tenancy import TenantAwareSession
from sqlalchemy import select, text

from packages.core.billing import BillingService
from packages.core.billing.service import (
    METRIC_MESSAGES_INBOUND,
    utc_day_key,
)
from packages.core.models.billing import (
    PlanTier,
    Subscription,
    SubscriptionStatus,
    UsageCounter,
    UsageEvent,
)


async def _make_tenant(session_factory, name: str = "Billing Biz"):
    tenant_id = uuid4()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenants "
                "(id, name, slug, owner_phone, owner_email, is_active, settings) "
                "VALUES (:id, :name, :slug, :phone, :email, true, '{}'::jsonb)"
            ),
            {
                "id": str(tenant_id),
                "name": name,
                "slug": f"biz-{uuid4().hex[:8]}",
                "phone": "254712345678",
                "email": f"{uuid4().hex[:8]}@example.com",
            },
        )
    return tenant_id


async def _make_user(session_factory, tenant_id) -> UUID:
    """Create an owner user (audit_log.user_id is a FK to users)."""
    user_id = uuid4()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO users "
                "(id, tenant_id, email, password_hash, full_name, role, is_active) "
                "VALUES (:id, :tid, :email, 'x', 'Owner', 'owner', true)"
            ),
            {
                "id": str(user_id),
                "tid": str(tenant_id),
                "email": f"owner-{uuid4().hex[:8]}@example.com",
            },
        )
    return user_id


class TestTrialClock:
    @pytest.mark.asyncio
    async def test_pending_before_connect(self, session_factory):
        tid = await _make_tenant(session_factory)
        async with session_factory() as session, session.begin():
            svc = BillingService(session)
            sub = await svc.ensure_pending(tid)
            assert sub.status == SubscriptionStatus.PENDING.value
            assert sub.period_end is None

    @pytest.mark.asyncio
    async def test_start_trial_sets_period_end(self, session_factory):
        tid = await _make_tenant(session_factory)
        now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        async with session_factory() as session, session.begin():
            svc = BillingService(session, now=now)
            sub = await svc.start_trial(tid)
            assert sub.status == SubscriptionStatus.ACTIVE.value
            assert sub.period_end == now + timedelta(days=14)

    @pytest.mark.asyncio
    async def test_second_connect_does_not_move_period_end(self, session_factory):
        tid = await _make_tenant(session_factory)
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        async with session_factory() as session, session.begin():
            svc = BillingService(session, now=t0)
            sub = await svc.start_trial(tid)
            first_end = sub.period_end

        # Three days later, "connect" again (replay / second event).
        t1 = t0 + timedelta(days=3)
        async with session_factory() as session, session.begin():
            svc = BillingService(session, now=t1)
            sub = await svc.start_trial(tid)
            assert sub.period_end == first_end, "trial clock must not move"

    @pytest.mark.asyncio
    async def test_expired_trial_is_not_restarted_by_reconnect(self, session_factory):
        tid = await _make_tenant(session_factory)
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        async with session_factory() as session, session.begin():
            svc = BillingService(session, now=t0)
            await svc.start_trial(tid)

        t2 = t0 + timedelta(days=20)
        async with session_factory() as session, session.begin():
            svc = BillingService(session, now=t2)
            await svc.expire_due_subscriptions()
        async with session_factory() as session, session.begin():
            svc = BillingService(session, now=t2)
            sub = await svc.start_trial(tid)
            assert sub.status == SubscriptionStatus.EXPIRED.value


class TestEntitlement:
    @pytest.mark.asyncio
    async def test_missing_subscription_denied(self, session_factory):
        tid = await _make_tenant(session_factory)
        async with session_factory() as session:
            svc = BillingService(session)
            decision = await svc.check_entitlement(tid)
            assert decision.entitled is False
            assert decision.reason == "no_subscription"

    @pytest.mark.asyncio
    async def test_pending_denied(self, session_factory):
        tid = await _make_tenant(session_factory)
        async with session_factory() as session, session.begin():
            await BillingService(session).ensure_pending(tid)
        async with session_factory() as session:
            decision = await BillingService(session).check_entitlement(tid)
            assert decision.entitled is False
            assert decision.reason == "pending"

    @pytest.mark.asyncio
    async def test_active_trial_entitled_then_expires_exactly_at_period_end(self, session_factory):
        tid = await _make_tenant(session_factory)
        t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        async with session_factory() as session, session.begin():
            await BillingService(session, now=t0).start_trial(tid)

        period_end = t0 + timedelta(days=14)
        # One second before: entitled.
        async with session_factory() as session:
            d = await BillingService(
                session, now=period_end - timedelta(seconds=1)
            ).check_entitlement(tid)
            assert d.entitled is True
        # Exactly at: denied (half-open interval).
        async with session_factory() as session:
            d = await BillingService(session, now=period_end).check_entitlement(tid)
            assert d.entitled is False
            assert d.reason == "expired"


class TestUsageConservation:
    @pytest.mark.asyncio
    async def test_replay_does_not_double_count(self, session_factory):
        tid = await _make_tenant(session_factory)
        moment = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
        async with session_factory() as session, session.begin():
            svc = BillingService(session)
            v1 = await svc.record_usage(
                tid, METRIC_MESSAGES_INBOUND, source_event_id="wamid.1", moment=moment
            )
            v2 = await svc.record_usage(
                tid, METRIC_MESSAGES_INBOUND, source_event_id="wamid.1", moment=moment
            )
            assert v1 == 1
            assert v2 == 1, "replay must not increment"
            # Ledger has exactly one event.
            total = await svc.sum_usage_events(tid, metric=METRIC_MESSAGES_INBOUND)
            assert total == 1

    @pytest.mark.asyncio
    async def test_counter_equals_events_across_many(self, session_factory):
        tid = await _make_tenant(session_factory)
        moment = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
        async with session_factory() as session, session.begin():
            svc = BillingService(session)
            for i in range(5):
                await svc.record_usage(
                    tid, METRIC_MESSAGES_INBOUND, source_event_id=f"wamid.{i}", moment=moment
                )
            # Replay two of them.
            await svc.record_usage(
                tid, METRIC_MESSAGES_INBOUND, source_event_id="wamid.0", moment=moment
            )
            await svc.record_usage(
                tid, METRIC_MESSAGES_INBOUND, source_event_id="wamid.3", moment=moment
            )
            value = await svc.get_usage(tid, metric=METRIC_MESSAGES_INBOUND, moment=moment)
            assert value == 5
            assert await svc.sum_usage_events(tid, metric=METRIC_MESSAGES_INBOUND) == 5

    @pytest.mark.asyncio
    async def test_replay_across_midnight_stays_in_original_period(self, session_factory):
        tid = await _make_tenant(session_factory)
        before_midnight = datetime(2026, 2, 1, 23, 59, tzinfo=UTC)
        after_midnight = datetime(2026, 2, 2, 0, 1, tzinfo=UTC)
        async with session_factory() as session, session.begin():
            svc = BillingService(session)
            await svc.record_usage(
                tid, METRIC_MESSAGES_INBOUND, source_event_id="wamid.x", moment=before_midnight
            )
            # Replay at 00:01 — must not land in the next day's counter.
            await svc.record_usage(
                tid, METRIC_MESSAGES_INBOUND, source_event_id="wamid.x", moment=after_midnight
            )
            day1 = utc_day_key(before_midnight)
            result = await session.execute(
                select(UsageCounter).where(
                    UsageCounter.tenant_id == tid,
                    UsageCounter.metric == METRIC_MESSAGES_INBOUND,
                )
            )
            rows = list(result.scalars().all())
            assert len(rows) == 1
            assert rows[0].period == day1
            assert rows[0].value == 1


class TestPlanChange:
    @pytest.mark.asyncio
    async def test_plan_change_is_audited_and_activates(self, session_factory):
        tid = await _make_tenant(session_factory)
        approver = await _make_user(session_factory, tid)
        async with session_factory() as session, session.begin():
            svc = BillingService(session)
            await svc.ensure_pending(tid)
            sub = await svc.apply_plan_change(
                tid,
                plan=PlanTier.PROFESSIONAL,
                approver_user_id=approver,
                evidence="MPE-TEST-123",
            )
            assert sub.plan == PlanTier.PROFESSIONAL.value
            assert sub.status == SubscriptionStatus.ACTIVE.value
            assert sub.plan_change_evidence == "MPE-TEST-123"

        async with session_factory() as session:
            result = await session.execute(
                text(
                    "SELECT count(*) FROM audit_log "
                    "WHERE tenant_id = :tid AND action = 'billing.apply_plan_change'"
                ),
                {"tid": str(tid)},
            )
            assert result.scalar_one() == 1

    @pytest.mark.asyncio
    async def test_plan_change_does_not_clear_past_due(self, session_factory):
        tid = await _make_tenant(session_factory)
        approver = await _make_user(session_factory, tid)
        async with session_factory() as session, session.begin():
            svc = BillingService(session)
            sub = await svc.ensure_pending(tid)
            sub.status = SubscriptionStatus.PAST_DUE.value
            await session.flush()
        async with session_factory() as session, session.begin():
            svc = BillingService(session)
            sub = await svc.apply_plan_change(
                tid,
                plan=PlanTier.PROFESSIONAL,
                approver_user_id=approver,
            )
            assert sub.status == SubscriptionStatus.PAST_DUE.value


class TestIsolation:
    @pytest.mark.asyncio
    async def test_subscriptions_rls_isolates_tenants(self, session_factory, rls_session_factory):
        a = await _make_tenant(session_factory, "Tenant A")
        b = await _make_tenant(session_factory, "Tenant B")
        async with session_factory() as session, session.begin():
            await BillingService(session).ensure_pending(a)
            await BillingService(session).ensure_pending(b)

        async with TenantAwareSession(a, rls_session_factory) as session:
            rows = (await session.execute(select(Subscription))).scalars().all()
            assert len(rows) == 1
            assert rows[0].tenant_id == a

        async with TenantAwareSession(b, rls_session_factory) as session:
            rows = (await session.execute(select(Subscription))).scalars().all()
            assert len(rows) == 1
            assert rows[0].tenant_id == b

    @pytest.mark.asyncio
    async def test_usage_events_rls_isolates_tenants(self, session_factory, rls_session_factory):
        a = await _make_tenant(session_factory, "Tenant A")
        b = await _make_tenant(session_factory, "Tenant B")
        moment = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
        async with session_factory() as session, session.begin():
            await BillingService(session).record_usage(
                a,
                METRIC_MESSAGES_INBOUND,
                source_event_id="wamid.a",
                moment=moment,
            )
        async with TenantAwareSession(b, rls_session_factory) as session:
            rows = (await session.execute(select(UsageEvent))).scalars().all()
            assert rows == []
