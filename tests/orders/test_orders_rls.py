"""
Phase 6 tenant-isolation tests for orders.

Data is created as the table owner (RLS bypassed). Reads then go through the
non-owner ``socialerp_app`` role, which is subject to the RLS policies installed
by the test schema, proving one tenant cannot see another's orders/customers.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from socialerp.tenancy import Tenant
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models.order import Customer, Order, PaymentMethod


async def _make_tenant(session: AsyncSession) -> UUID:
    tenant = Tenant(
        id=uuid4(),
        name="RLS Biz",
        slug=f"rls-biz-{uuid4().hex[:8]}",
        owner_phone="254700000001",
        owner_email="rls@example.com",
        is_active=True,
        settings={},
    )
    session.add(tenant)
    await session.flush()
    return tenant.id


async def _make_order(session: AsyncSession, tenant_id: UUID) -> UUID:
    customer = Customer(tenant_id=tenant_id, phone="254711111111", name="Buyer")
    session.add(customer)
    await session.flush()
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone="254711111111",
        total_cents=1000,
        status="awaiting_payment",
        payment_method=PaymentMethod.MPESA_STK,
    )
    session.add(order)
    await session.flush()
    return order.id


async def _count_as_tenant(session: AsyncSession, tenant_id: UUID, model) -> int:
    await session.execute(select(func.set_config("app.current_tenant_id", str(tenant_id), True)))
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_orders_are_isolated_between_tenants(session_factory, rls_session_factory) -> None:
    async with session_factory() as session, session.begin():
        tenant_a = await _make_tenant(session)
        tenant_b = await _make_tenant(session)
        order_a = await _make_order(session, tenant_a)

    async with rls_session_factory() as session:
        assert await _count_as_tenant(session, tenant_a, Order) == 1
        assert await _count_as_tenant(session, tenant_b, Order) == 0
        assert await _count_as_tenant(session, tenant_a, Customer) == 1
        assert await _count_as_tenant(session, tenant_b, Customer) == 0

        # The other tenant's order is invisible and unreadable by id.
        await session.execute(select(func.set_config("app.current_tenant_id", str(tenant_b), True)))
        row = (await session.execute(select(Order).where(Order.id == order_a))).scalar_one_or_none()
        assert row is None
