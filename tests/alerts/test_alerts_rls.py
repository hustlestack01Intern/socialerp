"""
Phase 7 remainder: tenant-isolation (RLS) tests for owner_alerts.

Alerts contain money/operational context and must fail closed between tenants.
Data is created as the table owner (RLS bypassed); reads and writes then go
through the non-owner ``socialerp_app`` role, which is subject to the RLS policy
installed by the test schema (mirrors tests/orders/test_orders_rls.py).
"""

from __future__ import annotations

from uuid import UUID, uuid4

from socialerp.tenancy import Tenant
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models.alert import OwnerAlert


async def _make_tenant(session: AsyncSession) -> UUID:
    tenant = Tenant(
        id=uuid4(),
        name="RLS Alerts Biz",
        slug=f"rls-alerts-{uuid4().hex[:8]}",
        owner_phone="254700000001",
        owner_email="rls-alerts@example.com",
        is_active=True,
        settings={},
    )
    session.add(tenant)
    await session.flush()
    return tenant.id


async def _make_alert(session: AsyncSession, tenant_id: UUID, *, title: str) -> UUID:
    alert = OwnerAlert(
        tenant_id=tenant_id,
        kind="payment.collected_after_terminal",
        severity="critical",
        title=title,
        body="body",
        metadata_json={},
    )
    session.add(alert)
    await session.flush()
    return alert.id


async def _count_as_tenant(session: AsyncSession, tenant_id: UUID) -> int:
    await session.execute(select(func.set_config("app.current_tenant_id", str(tenant_id), True)))
    return int(await session.scalar(select(func.count()).select_from(OwnerAlert)) or 0)


async def test_owner_alerts_are_isolated_between_tenants(
    session_factory, rls_session_factory,
) -> None:
    async with session_factory() as session, session.begin():
        tenant_a = await _make_tenant(session)
        tenant_b = await _make_tenant(session)
        alert_a = await _make_alert(session, tenant_a, title="A alert")

    async with rls_session_factory() as session:
        assert await _count_as_tenant(session, tenant_a) == 1
        assert await _count_as_tenant(session, tenant_b) == 0

        # Tenant B cannot read tenant A's alert by id.
        await session.execute(select(func.set_config("app.current_tenant_id", str(tenant_b), True)))
        row = (
            await session.execute(select(OwnerAlert).where(OwnerAlert.id == alert_a))
        ).scalar_one_or_none()
        assert row is None


async def test_owner_alerts_rls_blocks_cross_tenant_insert(
    session_factory, rls_session_factory,
) -> None:
    """A tenant cannot INSERT an alert stamped with another tenant's id (WITH CHECK)."""
    async with session_factory() as session, session.begin():
        tenant_a = await _make_tenant(session)
        tenant_b = await _make_tenant(session)

    async with rls_session_factory() as session:
        await session.execute(select(func.set_config("app.current_tenant_id", str(tenant_a), True)))
        session.add(
            OwnerAlert(
                tenant_id=tenant_b,  # foreign tenant
                kind="forged",
                severity="warning",
                title="forged",
                body="",
                metadata_json={},
            ),
        )
        try:
            await session.flush()
        except DBAPIError:
            await session.rollback()
        else:
            raise AssertionError("RLS allowed a cross-tenant insert into owner_alerts")
