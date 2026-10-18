"""Phase 8b: insights reports — counts, revenue, AOV, new/returning, top items.

Covers plan acceptance criteria and failure modes:
- Counts span ALL statuses in the half-open window; revenue counts PAID only.
- New vs returning is decided by the customer's earliest order ever (all-time),
  not by anything inside the window.
- Top items rank PAID orders only (an unpaid reservation is not a sale).
- Determinism: same DB state => identical reports; stable tie-breaks.
- Tenant scoping: one tenant's orders never leak into another's numbers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain import TenantId
from packages.core.insights import InsightsService
from packages.core.models.order import OrderStatus
from tests.insights.helpers import (
    add_item,
    add_order,
    add_product,
    make_customer,
    make_tenant,
    utc_day,
)

DAY = date(2026, 10, 10)
PREV_DAY = DAY - timedelta(days=1)
TWO_DAYS_AGO = DAY - timedelta(days=2)


async def test_daily_report_counts_all_statuses_but_revenue_paid_only(
    db_session: AsyncSession,
) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)

    paid = await add_order(
        db_session,
        tenant.id,
        customer,
        created_at=utc_day(2026, 10, 10, 9),
        paid=True,
        total_cents=500_00,
    )
    pending = await add_order(
        db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 11), total_cents=300_00
    )
    cancelled = await add_order(
        db_session,
        tenant.id,
        customer,
        created_at=utc_day(2026, 10, 10, 13),
        total_cents=700_00,
        status=OrderStatus.CANCELLED,
    )
    assert paid.id and pending.id and cancelled.id

    report = await InsightsService(db_session).daily_report(TenantId(tenant.id), DAY)

    assert report.period.orders_count == 3, "all statuses are demand attempts"
    assert report.period.paid_orders_count == 1
    assert report.period.revenue_cents == 500_00, "unpaid/cancelled money is never revenue"
    assert report.period.aov_cents == 500_00


async def test_aov_is_floor_division_and_zero_without_paid_orders(
    db_session: AsyncSession,
) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)

    # 100 + 250 cents paid => aov = 350 // 2 = 175 (floor, not float).
    await add_order(
        db_session,
        tenant.id,
        customer,
        created_at=utc_day(2026, 10, 10, 9),
        paid=True,
        total_cents=100,
    )
    await add_order(
        db_session,
        tenant.id,
        customer,
        created_at=utc_day(2026, 10, 10, 10),
        paid=True,
        total_cents=250,
    )
    service = InsightsService(db_session)
    report = await service.daily_report(TenantId(tenant.id), DAY)
    assert report.period.aov_cents == 175

    empty = await service.daily_report(TenantId(tenant.id), date(2026, 1, 1))
    assert empty.period.orders_count == 0
    assert empty.period.aov_cents == 0, "division-by-zero guard: no paid orders => 0"


async def test_new_vs_returning_uses_all_time_first_order(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    brand_new = await make_customer(db_session, tenant.id)
    returning = await make_customer(db_session, tenant.id)

    # Returning customer's first order was BEFORE the window...
    await add_order(db_session, tenant.id, returning, created_at=utc_day(2026, 9, 1, 9), paid=True)
    # ...and they ordered again INSIDE it.
    await add_order(
        db_session, tenant.id, returning, created_at=utc_day(2026, 10, 10, 9), paid=True
    )
    # The other customer's first-ever order falls inside the window.
    await add_order(
        db_session, tenant.id, brand_new, created_at=utc_day(2026, 10, 10, 12), paid=True
    )

    report = await InsightsService(db_session).daily_report(TenantId(tenant.id), DAY)

    assert report.period.new_customers == 1
    assert report.period.returning_customers == 1


async def test_half_open_window_excludes_end_and_includes_start(
    db_session: AsyncSession,
) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)

    inside_start = await add_order(
        db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 0)
    )
    next_day = await add_order(db_session, tenant.id, customer, created_at=utc_day(2026, 10, 11, 0))
    prev_second = await add_order(
        db_session, tenant.id, customer, created_at=datetime(2026, 10, 9, 23, 59, 59, tzinfo=UTC)
    )
    assert inside_start.id and next_day.id and prev_second.id

    report = await InsightsService(db_session).daily_report(TenantId(tenant.id), DAY)
    assert report.period.orders_count == 1, "[start, end): 00:00 in, next-day 00:00 out"
    assert report.period.period_start == DAY
    assert report.period.period_end == DAY + timedelta(days=1)


async def test_weekly_report_spans_seven_days(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)

    await add_order(
        db_session,
        tenant.id,
        customer,
        created_at=utc_day(2026, 10, 5, 9),
        paid=True,
        total_cents=100_00,
    )
    await add_order(
        db_session,
        tenant.id,
        customer,
        created_at=utc_day(2026, 10, 11, 9),
        paid=True,
        total_cents=200_00,
    )
    # Day 8 falls outside the [Mon 5th, Mon 12th) week.
    await add_order(db_session, tenant.id, customer, created_at=utc_day(2026, 10, 12, 9), paid=True)

    report = await InsightsService(db_session).weekly_report(TenantId(tenant.id), date(2026, 10, 5))

    assert report.period_start == date(2026, 10, 5)
    assert report.period_end == date(2026, 10, 12)
    assert report.orders_count == 2
    assert report.revenue_cents == 300_00


async def test_top_items_paid_only_ranked_and_tie_broken(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)
    product = await add_product(db_session, tenant.id, name="Hoodie")

    paid = await add_order(
        db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 9), paid=True
    )
    unpaid = await add_order(db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 10))
    await add_item(
        db_session, tenant.id, paid, product, quantity=3, name="Hoodie", sku="HOODIE-BLK"
    )
    # Unpaid order's items must NOT be counted, no matter the quantity.
    await add_item(
        db_session, tenant.id, unpaid, product, quantity=99, name="Hoodie", sku="HOODIE-BLK"
    )

    report = await InsightsService(db_session).daily_report(TenantId(tenant.id), DAY)

    assert len(report.period.top_items) == 1
    assert report.period.top_items[0].quantity == 3, "unpaid reservation is not a sale"
    assert report.period.top_items[0].name == "Hoodie"
    assert report.period.top_items[0].sku == "HOODIE-BLK"


async def test_top_items_ties_break_by_name_then_sku(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)
    product = await add_product(db_session, tenant.id)

    order = await add_order(
        db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 9), paid=True
    )
    await add_item(db_session, tenant.id, order, product, quantity=2, name="Zebra", sku="Z-1")
    await add_item(db_session, tenant.id, order, product, quantity=2, name="Alpha", sku="A-1")

    report = await InsightsService(db_session).daily_report(TenantId(tenant.id), DAY)

    names = [item.name for item in report.period.top_items]
    assert names == ["Alpha", "Zebra"], "equal quantity => alphabetical tie-break, deterministic"


async def test_top_items_respects_top_k(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)
    product = await add_product(db_session, tenant.id)
    order = await add_order(
        db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 9), paid=True
    )

    for i in range(4):
        await add_item(
            db_session, tenant.id, order, product, quantity=10 - i, name=f"Item-{i}", sku=f"SKU-{i}"
        )

    report = await InsightsService(db_session).daily_report(TenantId(tenant.id), DAY, top_k=2)
    assert [i.name for i in report.period.top_items] == ["Item-0", "Item-1"]


async def test_reports_are_deterministic(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    customer = await make_customer(db_session, tenant.id)
    product = await add_product(db_session, tenant.id)
    order = await add_order(
        db_session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 9), paid=True
    )
    await add_item(db_session, tenant.id, order, product, quantity=1)

    service = InsightsService(db_session)
    first = await service.daily_report(TenantId(tenant.id), DAY)
    second = await service.daily_report(TenantId(tenant.id), DAY)

    assert first == second, "frozen dataclasses: same DB state => identical reports"


async def test_reports_are_tenant_scoped(db_session: AsyncSession) -> None:
    tenant_a = await make_tenant(db_session, slug_prefix="ins-a")
    tenant_b = await make_tenant(db_session, slug_prefix="ins-b")
    customer_a = await make_customer(db_session, tenant_a.id)
    customer_b = await make_customer(db_session, tenant_b.id)

    await add_order(
        db_session,
        tenant_a.id,
        customer_a,
        created_at=utc_day(2026, 10, 10, 9),
        paid=True,
        total_cents=100_00,
    )
    await add_order(
        db_session,
        tenant_b.id,
        customer_b,
        created_at=utc_day(2026, 10, 10, 9),
        paid=True,
        total_cents=900_00,
    )

    service = InsightsService(db_session)
    report_a = await service.daily_report(TenantId(tenant_a.id), DAY)
    report_b = await service.daily_report(TenantId(tenant_b.id), DAY)

    assert report_a.period.revenue_cents == 100_00
    assert report_b.period.revenue_cents == 900_00
    assert report_a.period.orders_count == 1
    assert report_b.period.orders_count == 1
