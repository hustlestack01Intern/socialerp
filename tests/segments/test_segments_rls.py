"""
Phase 8b: tenant-isolation (RLS) tests for segment membership (customer_traits).

Segment membership is derived from customer_traits and must fail closed between
tenants under the non-owner ``socialerp_app`` role, mirroring
tests/alerts/test_alerts_rls.py and tests/orders/test_orders_rls.py.
Trait rows are created as the table owner (RLS bypassed); reads then go through
the RLS-subscribed role. The evaluator must never surface one tenant's member
ids to another tenant's session — not through list pages, not through
evaluate_segment.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from packages.core.crm.segment_service import SegmentService
from packages.core.crm.segments import SegmentKey
from packages.core.domain import TenantId
from packages.core.models.crm import CustomerTrait
from tests.segments.helpers import add_trait, make_customer, make_tenant


async def test_segment_membership_is_isolated_between_tenants(
    session_factory, rls_session_factory,
) -> None:
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session, slug_prefix="rls-seg-a")
        tenant_b = await make_tenant(session, slug_prefix="rls-seg-b")
        customer_a = await make_customer(session, tenant_a.id)
        customer_b = await make_customer(session, tenant_b.id)
        await add_trait(session, tenant_a.id, customer_a.id, "order_count", 1)
        await add_trait(session, tenant_b.id, customer_b.id, "order_count", 1)

    async with rls_session_factory() as session:
        service = SegmentService(session)

        # Tenant A's context sees exactly tenant A's members.
        await session.execute(
            select(func.set_config("app.current_tenant_id", str(tenant_a.id), True)),
        )
        page_a = await service.list_members(TenantId(tenant_a.id), SegmentKey.FIRST_TIME)
        assert page_a.customer_ids == (customer_a.id,)

        # Tenant B's members are invisible to tenant A's session, even when the
        # service is (mistakenly or maliciously) asked for tenant B's page.
        page_b = await service.list_members(TenantId(tenant_b.id), SegmentKey.FIRST_TIME)
        assert page_b.customer_ids == ()
        assert page_b.total == 0

        # Tenant B's context never sees tenant A's customer.
        await session.execute(
            select(func.set_config("app.current_tenant_id", str(tenant_b.id), True)),
        )
        page_b2 = await service.list_members(TenantId(tenant_b.id), SegmentKey.FIRST_TIME)
        assert page_b2.customer_ids == (customer_b.id,)
        assert customer_a.id not in page_b2.customer_ids


async def test_evaluate_segment_fails_closed_under_rls(
    session_factory, rls_session_factory,
) -> None:
    """Cross-tenant evaluate_segment returns False, never a foreign membership."""
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session, slug_prefix="rls-seg-eval-a")
        tenant_b = await make_tenant(session, slug_prefix="rls-seg-eval-b")
        customer_a = await make_customer(session, tenant_a.id)
        await add_trait(session, tenant_a.id, customer_a.id, "order_count", 1)

    async with rls_session_factory() as session:
        service = SegmentService(session)

        # Tenant B's session probing tenant A's customer: no row visible => False.
        await session.execute(
            select(func.set_config("app.current_tenant_id", str(tenant_b.id), True)),
        )
        assert (
            await service.evaluate_segment(
                TenantId(tenant_a.id), SegmentKey.FIRST_TIME, customer_a.id,
            )
            is False
        )

        # Tenant A's own session still gets the true answer.
        await session.execute(
            select(func.set_config("app.current_tenant_id", str(tenant_a.id), True)),
        )
        assert (
            await service.evaluate_segment(
                TenantId(tenant_a.id), SegmentKey.FIRST_TIME, customer_a.id,
            )
            is True
        )


async def test_rls_blocks_cross_tenant_trait_insert(session_factory, rls_session_factory) -> None:
    """A tenant cannot INSERT a trait stamped with another tenant's id (WITH CHECK)."""
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session, slug_prefix="rls-seg-ins-a")
        tenant_b = await make_tenant(session, slug_prefix="rls-seg-ins-b")
        customer_b = await make_customer(session, tenant_b.id)

    async with rls_session_factory() as session:
        await session.execute(
            select(func.set_config("app.current_tenant_id", str(tenant_a.id), True)),
        )
        session.add(
            CustomerTrait(
                tenant_id=tenant_b,  # foreign tenant
                customer_id=customer_b.id,
                name="order_count",
                value_json=1,
                version=1,
            ),
        )
        try:
            await session.flush()
        except DBAPIError:
            await session.rollback()
        else:
            raise AssertionError("RLS allowed a cross-tenant insert into customer_traits")
