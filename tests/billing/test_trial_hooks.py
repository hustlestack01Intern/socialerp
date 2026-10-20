"""M1.3: trial-start hooks (register -> pending, onboarding step 2 -> trial).

Integration-level: exercises the same code paths the API/onboarding use, against
the real schema, so a regression in the wiring is caught even though these tests
call the services directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from packages.core.billing import BillingService, SubscriptionStatus

pytestmark = pytest.mark.integration


async def _subscription_row(session_factory, tenant_id):
    async with session_factory() as session:
        return (
            await session.execute(
                text(
                    "SELECT plan, status, period_start, period_end, extension_count "
                    "FROM subscriptions WHERE tenant_id = :tid"
                ),
                {"tid": str(tenant_id)},
            )
        ).one_or_none()


class TestRegisterCreatesPending:
    async def test_register_creates_pending_subscription(self, api_client, session_factory):
        email = f"owner-{uuid4().hex[:6]}@example.com"
        # Register a fresh tenant through the real endpoint.
        resp = await api_client.post(
            "/api/v1/auth/register",
            json={
                "business_name": "Kibera Kicks",
                "slug": f"kibera-{uuid4().hex[:6]}",
                "email": email,
                "owner_email": email,
                "owner_password": "S3cret-pass!",
                "owner_name": "Amina",
                "phone": "254712000111",
            },
        )
        assert resp.status_code == 201, resp.text

        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT s.plan, s.status, s.period_end FROM subscriptions s "
                        "JOIN tenants t ON t.id = s.tenant_id "
                        "WHERE t.owner_email = :email"
                    ),
                    {"email": email},
                )
            ).one_or_none()
        assert row is not None
        assert row.plan == "trial"
        assert row.status == SubscriptionStatus.PENDING.value
        # Pending must NOT have burned the trial clock.
        assert row.period_end is None

    async def test_register_does_not_start_trial_clock(self, api_client, session_factory):
        email = f"nochan-{uuid4().hex[:6]}@example.com"
        resp = await api_client.post(
            "/api/v1/auth/register",
            json={
                "business_name": "No Channel Biz",
                "slug": f"nochan-{uuid4().hex[:6]}",
                "email": email,
                "owner_email": email,
                "owner_password": "S3cret-pass!",
                "owner_name": "Owner",
                "phone": "254712000222",
            },
        )
        assert resp.status_code == 201, resp.text
        async with session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT s.status, s.period_start FROM subscriptions s "
                        "JOIN tenants t ON t.id = s.tenant_id "
                        "WHERE t.owner_email = :email"
                    ),
                    {"email": email},
                )
            ).one_or_none()
        assert row is not None
        assert row.status == SubscriptionStatus.PENDING.value
        assert row.period_start is None


class TestOnboardingStepTwoStartsTrial:
    async def test_complete_step_two_starts_trial(self, session_factory, tenant):
        from packages.core.onboarding import OnboardingWizardService

        async with session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant.id)},
            )
            # Ensure a pending subscription exists first (as registration would).
            await BillingService(session).ensure_pending(tenant.id)
            await session.commit()

            svc = OnboardingWizardService(session)
            # Drive the wizard: step 1, then step 2 (CONNECT_CHANNELS).
            await svc.start_step(tenant.id, 1)
            await svc.complete_step(tenant.id, 1)
            await svc.start_step(tenant.id, 2)
            await svc.complete_step(tenant.id, 2)

            sub = await BillingService(session).get_subscription(tenant.id)
        assert sub is not None
        assert sub.status == SubscriptionStatus.ACTIVE.value
        assert sub.period_start is not None
        assert sub.period_end is not None

    async def test_replayed_step_two_does_not_move_period_end(self, session_factory, tenant):
        """Trial-clock gaming defense (U67): the end date is immutable."""
        from packages.core.onboarding import OnboardingWizardService

        async with session_factory() as session:
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant.id)},
            )
            await BillingService(session).ensure_pending(tenant.id)
            await session.commit()

            svc = OnboardingWizardService(session)
            await svc.start_step(tenant.id, 1)
            await svc.complete_step(tenant.id, 1)
            await svc.start_step(tenant.id, 2)
            await svc.complete_step(tenant.id, 2)
            first = await BillingService(session).get_subscription(tenant.id)
            assert first is not None and first.period_end is not None

            # Simulate a replayed trial-start (e.g. the event delivered twice):
            # calling start_trial again must not move the clock.
            await BillingService(session).start_trial(
                tenant.id, channel="whatsapp", idempotency_key="replay"
            )
            second = await BillingService(session).get_subscription(tenant.id)
        assert second is not None
        assert second.period_end == first.period_end
        assert second.period_start == first.period_start


class TestPendingExpiry:
    async def _fresh_tenant(self, session_factory, *, pending_since):
        tid = uuid4()
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO tenants "
                    "(id, name, owner_phone, owner_email, is_active, settings) "
                    "VALUES (:id, 'Never Connected', '254700000000', "
                    "'never@example.com', true, '{}'::jsonb) ON CONFLICT DO NOTHING"
                ),
                {"id": str(tid)},
            )
            await session.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(id, tenant_id, plan, status, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :tid, 'trial', 'pending', :ts, :ts)"
                ),
                {"tid": str(tid), "ts": pending_since},
            )
        return tid

    async def test_pending_past_window_is_cancelled(self, session_factory):
        """A signup that never connects for TRIAL_PENDING_EXPIRY_DAYS is cancelled."""
        from packages.core.billing import BillingService

        tid = await self._fresh_tenant(
            session_factory, pending_since=datetime.now(UTC) - timedelta(days=45)
        )
        async with session_factory() as session:
            svc = BillingService(session)
            cancelled = await svc.expire_pending_subscriptions()
            await session.commit()
        assert cancelled >= 1

        row = await _subscription_row(session_factory, tid)
        assert row is not None
        assert row.status == SubscriptionStatus.CANCELLED.value

    async def test_recent_pending_is_not_cancelled(self, session_factory):
        from packages.core.billing import BillingService

        tid = await self._fresh_tenant(session_factory, pending_since=datetime.now(UTC))
        async with session_factory() as session:
            svc = BillingService(session)
            cancelled = await svc.expire_pending_subscriptions()
            await session.commit()
        assert cancelled == 0
        row = await _subscription_row(session_factory, tid)
        assert row is not None
        assert row.status == SubscriptionStatus.PENDING.value
