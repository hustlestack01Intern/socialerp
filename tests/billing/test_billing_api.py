"""
Billing API tests — M1.5 hardening (plan 2026-10-21 F2, human-approved).

The pre-F2 hole (U68): ``POST /api/v1/billing/subscription`` let a tenant
owner self-upgrade to any paid plan with no payment check. F2 closes it:

* a non-trial plan change REQUIRES a payment reference (M-Pesa code);
* the reference is durably deduped UNIQUE across all tenants (one M-Pesa
  code can pay for at most one plan change, ever);
* the change is owner-only, authenticated via REAL JWTs (only ``get_db``
  is redirected — no stubbed role dependencies), and audited;
* trial plan changes stay reference-free.

The table is platform-scoped on purpose (no RLS): a cross-tenant uniqueness
check is exactly the point, and the row carries no customer PII.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.main import app
from packages.core.auth.dependencies import get_db
from packages.core.auth.jwt import create_access_token
from packages.core.models.billing import PlanTier, Subscription, SubscriptionStatus
from packages.core.models.core import UserRole
from tests.crm.helpers import make_user


async def _make_second_tenant(session_factory: async_sessionmaker[AsyncSession]):
    """A second, fully-persisted tenant (trial active) for cross-tenant tests."""
    from socialerp.tenancy import Tenant

    from packages.core.billing import BillingService

    tenant_id = uuid4()
    async with session_factory() as session, session.begin():
        tenant = Tenant(
            id=tenant_id,
            name="Second Business",
            slug=f"second-biz-{uuid4().hex[:8]}",
            owner_phone="254799999999",
            owner_email=f"second-{uuid4().hex[:8]}@example.com",
            is_active=True,
            settings={},
        )
        session.add(tenant)
        await session.flush()
        await BillingService(session).start_trial(tenant.id, channel="whatsapp")
        return tenant


async def _owner_token(session_factory: async_sessionmaker[AsyncSession], tenant_id) -> str:
    async with session_factory() as session, session.begin():
        user = await make_user(session, tenant_id, role=UserRole.OWNER)
        return create_access_token(str(user.id), str(tenant_id), [UserRole.OWNER.value])


async def _staff_token(session_factory: async_sessionmaker[AsyncSession], tenant_id) -> str:
    async with session_factory() as session, session.begin():
        user = await make_user(session, tenant_id, role=UserRole.STAFF)
        return create_access_token(str(user.id), str(tenant_id), [UserRole.STAFF.value])


async def _client_for(
    session_factory: async_sessionmaker[AsyncSession], token: str
) -> AsyncClient:
    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )
    client.headers["Authorization"] = f"Bearer {token}"
    return client


async def _subscription_row(
    session_factory: async_sessionmaker[AsyncSession], tenant_id
) -> Subscription:
    async with session_factory() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        sub = result.scalar_one()
        await session.refresh(sub)
        return sub


async def _audit_count(
    session_factory: async_sessionmaker[AsyncSession], tenant_id, action: str
) -> int:
    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE tenant_id = :tid AND action = :action"
            ),
            {"tid": str(tenant_id), "action": action},
        )
        return int(result.scalar_one())


class TestPaidPlanChangeRequiresPaymentReference:
    async def test_missing_reference_is_rejected_and_plan_unchanged(
        self, tenant, session_factory
    ) -> None:
        token = await _owner_token(session_factory, tenant.id)
        try:
            async with await _client_for(session_factory, token) as client:
                resp = await client.post(
                    "/api/v1/billing/subscription", json={"plan": "professional"}
                )
            assert resp.status_code == 422, resp.text
            assert "payment_reference" in resp.json()["detail"]
        finally:
            app.dependency_overrides.clear()

        sub = await _subscription_row(session_factory, tenant.id)
        assert sub.plan == PlanTier.TRIAL.value
        assert await _audit_count(session_factory, tenant.id, "billing.apply_plan_change") == 0

    async def test_malformed_reference_is_rejected(
        self, tenant, session_factory
    ) -> None:
        token = await _owner_token(session_factory, tenant.id)
        try:
            async with await _client_for(session_factory, token) as client:
                resp = await client.post(
                    "/api/v1/billing/subscription",
                    json={"plan": "starter", "payment_reference": "not a code!!"},
                )
            assert resp.status_code == 422, resp.text
            assert "payment_reference" in resp.json()["detail"]
        finally:
            app.dependency_overrides.clear()

    async def test_valid_reference_upgrades_and_audits(
        self, tenant, session_factory
    ) -> None:
        token = await _owner_token(session_factory, tenant.id)
        try:
            async with await _client_for(session_factory, token) as client:
                resp = await client.post(
                    "/api/v1/billing/subscription",
                    json={"plan": "professional", "payment_reference": "SGH4KJ8L2P"},
                )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["plan"] == "professional"
            assert body["status"] == "active"
            assert body["changed"] is True
        finally:
            app.dependency_overrides.clear()

        sub = await _subscription_row(session_factory, tenant.id)
        assert sub.plan == PlanTier.PROFESSIONAL.value
        assert sub.status == SubscriptionStatus.ACTIVE.value
        assert sub.plan_change_evidence == "SGH4KJ8L2P"

        async with session_factory() as session:
            ref_row = await session.execute(
                text(
                    "SELECT tenant_id, plan, amount_kes FROM billing_payment_references "
                    "WHERE reference = :ref"
                ),
                {"ref": "SGH4KJ8L2P"},
            )
            row = ref_row.one()
        assert str(row.tenant_id) == str(tenant.id)
        assert row.plan == "professional"
        assert row.amount_kes == 7999

        assert (
            await _audit_count(session_factory, tenant.id, "billing.apply_plan_change")
            == 1
        )

    async def test_reference_is_normalised_case(
        self, tenant, session_factory
    ) -> None:
        token = await _owner_token(session_factory, tenant.id)
        try:
            async with await _client_for(session_factory, token) as client:
                resp = await client.post(
                    "/api/v1/billing/subscription",
                    json={"plan": "starter", "payment_reference": "sgh4kj8l2q"},
                )
            assert resp.status_code == 200, resp.text
        finally:
            app.dependency_overrides.clear()

        async with session_factory() as session:
            stored = await session.execute(
                text("SELECT reference FROM billing_payment_references")
            )
            assert stored.scalar_one() == "SGH4KJ8L2Q"


class TestReferenceDedupe:
    async def test_duplicate_reference_is_rejected_and_plan_unchanged(
        self, tenant, session_factory
    ) -> None:
        token = await _owner_token(session_factory, tenant.id)
        try:
            async with await _client_for(session_factory, token) as client:
                first = await client.post(
                    "/api/v1/billing/subscription",
                    json={"plan": "starter", "payment_reference": "DUPDUPDUP1"},
                )
                assert first.status_code == 200, first.text
                second = await client.post(
                    "/api/v1/billing/subscription",
                    json={"plan": "professional", "payment_reference": "DUPDUPDUP1"},
                )
            assert second.status_code == 409, second.text
            assert "already" in second.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

        sub = await _subscription_row(session_factory, tenant.id)
        assert sub.plan == PlanTier.STARTER.value  # second upgrade did not apply
        async with session_factory() as session:
            count = await session.execute(
                text("SELECT COUNT(*) FROM billing_payment_references")
            )
            assert int(count.scalar_one()) == 1

    async def test_cross_tenant_reference_reuse_is_rejected(
        self, tenant, session_factory
    ) -> None:
        second = await _make_second_tenant(session_factory)
        token_a = await _owner_token(session_factory, tenant.id)
        token_b = await _owner_token(session_factory, second.id)
        try:
            async with await _client_for(session_factory, token_a) as client_a:
                first = await client_a.post(
                    "/api/v1/billing/subscription",
                    json={"plan": "starter", "payment_reference": "XROSSHAT1"},
                )
                assert first.status_code == 200, first.text
            app.dependency_overrides.clear()
            async with await _client_for(session_factory, token_b) as client_b:
                resp = await client_b.post(
                    "/api/v1/billing/subscription",
                    json={"plan": "professional", "payment_reference": "XROSSHAT1"},
                )
            assert resp.status_code == 409, resp.text
        finally:
            app.dependency_overrides.clear()

        sub_b = await _subscription_row(session_factory, second.id)
        assert sub_b.plan == PlanTier.TRIAL.value


class TestAuthGates:
    async def test_staff_cannot_change_plan(
        self, tenant, session_factory
    ) -> None:
        token = await _staff_token(session_factory, tenant.id)
        try:
            async with await _client_for(session_factory, token) as client:
                resp = await client.post(
                    "/api/v1/billing/subscription",
                    json={"plan": "professional", "payment_reference": "STAFFSTAFF"},
                )
            assert resp.status_code == 403, resp.text
        finally:
            app.dependency_overrides.clear()
        sub = await _subscription_row(session_factory, tenant.id)
        assert sub.plan == PlanTier.TRIAL.value

    async def test_no_token_is_401(self, tenant, session_factory) -> None:
        try:
            app.dependency_overrides[get_db] = None  # placeholder replaced below

            async def override_get_db() -> AsyncGenerator[AsyncSession]:
                async with session_factory() as session:
                    yield session

            app.dependency_overrides[get_db] = override_get_db
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/billing/subscription", json={"plan": "professional"}
                )
            assert resp.status_code == 401, resp.text
        finally:
            app.dependency_overrides.clear()


class TestTrialStillReferenceFree:
    async def test_trial_change_needs_no_reference(
        self, tenant, session_factory
    ) -> None:
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE subscriptions SET plan = 'starter', status = 'active' "
                    "WHERE tenant_id = :tid"
                ),
                {"tid": str(tenant.id)},
            )
        token = await _owner_token(session_factory, tenant.id)
        try:
            async with await _client_for(session_factory, token) as client:
                resp = await client.post(
                    "/api/v1/billing/subscription", json={"plan": "trial"}
                )
            assert resp.status_code == 200, resp.text
            assert resp.json()["plan"] == "trial"
        finally:
            app.dependency_overrides.clear()


@pytest.mark.parametrize("plan", ["starter", "professional", "enterprise"])
async def test_every_paid_plan_requires_reference(
    plan: str, tenant, session_factory
) -> None:
    token = await _owner_token(session_factory, tenant.id)
    try:
        async with await _client_for(session_factory, token) as client:
            resp = await client.post(
                "/api/v1/billing/subscription", json={"plan": plan}
            )
        assert resp.status_code == 422, resp.text
    finally:
        app.dependency_overrides.clear()
