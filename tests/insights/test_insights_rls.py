"""
Phase 8b: tenant-isolation (RLS) tests for insights (orders + product_variants).

Insights read orders (reports) and product_variants (stock alerts) and must
fail closed between tenants under the non-owner ``socialerp_app`` role, with
the same setup as tests/alerts/test_alerts_rls.py: rows are created as the
table owner (RLS bypassed); reads then go through the RLS-subscribed role.
The service must never fold one tenant's orders or stock into another tenant's
numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain import TenantId
from packages.core.insights import InsightsService
from packages.core.models.catalog import Product, ProductVariant
from packages.core.models.order import Customer, Order, OrderStatus, PaymentStatus
from tests.segments.helpers import make_customer, make_tenant

DAY_START = datetime(2026, 10, 10, 0, tzinfo=UTC)


async def _make_order(session: AsyncSession, tenant_id: UUID, customer: Customer) -> UUID:
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone=customer.phone,
        customer_name=customer.name,
        subtotal_cents=100_00,
        total_cents=100_00,
        status=OrderStatus.DRAFT,
        payment_status=PaymentStatus.PENDING,
        created_at=DAY_START,
    )
    session.add(order)
    await session.flush()
    return order.id


async def _make_variant(session: AsyncSession, tenant_id: UUID, product: Product) -> UUID:
    variant = ProductVariant(
        product_id=product.id,
        tenant_id=tenant_id,
        sku=f"RLS-{tenant_id.hex[:8]}",
        cost_price_cents=1,
        sell_price_cents=2,
        quantity=0,
        reorder_level=5,
        is_active=True,
    )
    session.add(variant)
    await session.flush()
    return variant.id


async def test_insights_reports_are_isolated_between_tenants(
    session_factory, rls_session_factory
) -> None:
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session, slug_prefix="rls-ins-a")
        tenant_b = await make_tenant(session, slug_prefix="rls-ins-b")
        customer_a = await make_customer(session, tenant_a.id)
        customer_b = await make_customer(session, tenant_b.id)
        order_a = await _make_order(session, tenant_a.id, customer_a)
        order_b = await _make_order(session, tenant_b.id, customer_b)

    async with rls_session_factory() as session:
        service = InsightsService(session)

        await session.execute(
            select(func.set_config("app.current_tenant_id", str(tenant_a.id), True))
        )
        report_a = await service.daily_report(TenantId(tenant_a.id), DAY_START.date())
        assert report_a.period.orders_count == 1

        # Tenant A's session asking for tenant B's report: no rows visible.
        report_b = await service.daily_report(TenantId(tenant_b.id), DAY_START.date())
        assert report_b.period.orders_count == 0
        assert report_b.period.revenue_cents == 0

        # Tenant B's own context sees its own order, never A's.
        await session.execute(
            select(func.set_config("app.current_tenant_id", str(tenant_b.id), True))
        )
        report_b2 = await service.daily_report(TenantId(tenant_b.id), DAY_START.date())
        assert report_b2.period.orders_count == 1
        assert order_a != order_b


async def test_stock_alerts_are_isolated_between_tenants(
    session_factory, rls_session_factory
) -> None:
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session, slug_prefix="rls-stk-a")
        tenant_b = await make_tenant(session, slug_prefix="rls-stk-b")
        product_a = Product(tenant_id=tenant_a.id, name="A product", status="active")
        product_b = Product(tenant_id=tenant_b.id, name="B product", status="active")
        session.add_all([product_a, product_b])
        await session.flush()
        variant_a = await _make_variant(session, tenant_a.id, product_a)
        variant_b = await _make_variant(session, tenant_b.id, product_b)

    async with rls_session_factory() as session:
        service = InsightsService(session)

        await session.execute(
            select(func.set_config("app.current_tenant_id", str(tenant_a.id), True))
        )
        alerts_a = await service.stock_alerts(TenantId(tenant_a.id))
        assert [a.variant_id for a in alerts_a] == [variant_a]

        alerts_b = await service.stock_alerts(TenantId(tenant_b.id))
        assert alerts_b == [], "tenant A sees none of tenant B's stock"

        await session.execute(
            select(func.set_config("app.current_tenant_id", str(tenant_b.id), True))
        )
        alerts_b2 = await service.stock_alerts(TenantId(tenant_b.id))
        assert [a.variant_id for a in alerts_b2] == [variant_b]
        assert variant_a not in {a.variant_id for a in alerts_b2}
