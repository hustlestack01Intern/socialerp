"""Phase 8b: stock alerts — below-reorder snapshot, read-only, tenant-scoped.

Covers plan acceptance criteria and failure modes:
- Active variants with quantity < reorder_level are surfaced (the owner's own
  threshold); quantity == reorder_level is NOT an alert (strict inequality).
- Inactive variants are skipped (unsellable stock is not a restock signal).
- Read-only: no durable owner_alerts row is written (plan invariant 8).
- Tenant scoping; deterministic sku-sorted ordering.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain import TenantId
from packages.core.insights import InsightsService
from packages.core.models.alert import OwnerAlert
from tests.insights.helpers import add_product, add_variant, make_tenant


async def test_stock_alerts_list_variants_below_reorder_level(
    db_session: AsyncSession,
) -> None:
    tenant = await make_tenant(db_session)
    product = await add_product(db_session, tenant.id, name="Hoodie")

    below = await add_variant(
        db_session, tenant.id, product, sku="A-LOW", quantity=2, reorder_level=5
    )
    at_level = await add_variant(
        db_session, tenant.id, product, sku="B-EQUAL", quantity=5, reorder_level=5
    )
    healthy = await add_variant(
        db_session, tenant.id, product, sku="C-OK", quantity=50, reorder_level=5
    )
    inactive = await add_variant(
        db_session,
        tenant.id,
        product,
        sku="D-OFF",
        quantity=0,
        reorder_level=5,
        is_active=False,
    )

    alerts = await InsightsService(db_session).stock_alerts(TenantId(tenant.id))

    assert [a.sku for a in alerts] == ["A-LOW"], "strict < and active-only"
    assert alerts[0].variant_id == below.id
    assert alerts[0].product_name == "Hoodie"
    assert alerts[0].quantity == 2
    assert alerts[0].reorder_level == 5
    assert at_level.id != below.id and healthy.id != below.id and inactive.id != below.id


async def test_stock_alerts_are_sorted_by_sku(db_session: AsyncSession) -> None:
    tenant = await make_tenant(db_session)
    product = await add_product(db_session, tenant.id)

    for sku in ("ZETA", "alpha", "Midway"):
        await add_variant(db_session, tenant.id, product, sku=sku, quantity=0, reorder_level=1)

    alerts = await InsightsService(db_session).stock_alerts(TenantId(tenant.id))
    assert [a.sku for a in alerts] == ["Midway", "ZETA", "alpha"], "ORDER BY sku ASC"


async def test_stock_alerts_are_tenant_scoped(db_session: AsyncSession) -> None:
    tenant_a = await make_tenant(db_session, slug_prefix="stock-a")
    tenant_b = await make_tenant(db_session, slug_prefix="stock-b")
    product_a = await add_product(db_session, tenant_a.id)
    product_b = await add_product(db_session, tenant_b.id)
    await add_variant(db_session, tenant_a.id, product_a, sku="A-1", quantity=0, reorder_level=3)
    await add_variant(db_session, tenant_b.id, product_b, sku="B-1", quantity=0, reorder_level=3)

    service = InsightsService(db_session)
    alerts_a = await service.stock_alerts(TenantId(tenant_a.id))
    alerts_b = await service.stock_alerts(TenantId(tenant_b.id))

    assert [a.sku for a in alerts_a] == ["A-1"]
    assert [a.sku for a in alerts_b] == ["B-1"]


async def test_stock_alerts_raise_no_durable_alerts(db_session: AsyncSession) -> None:
    """Read-only invariant: computing alerts must not insert owner_alerts rows."""
    tenant = await make_tenant(db_session)
    product = await add_product(db_session, tenant.id)
    await add_variant(db_session, tenant.id, product, quantity=0, reorder_level=5)

    before = int(
        await db_session.scalar(
            select(func.count()).select_from(OwnerAlert).where(OwnerAlert.tenant_id == tenant.id)
        )
        or 0
    )
    alerts = await InsightsService(db_session).stock_alerts(TenantId(tenant.id))
    after = int(
        await db_session.scalar(
            select(func.count()).select_from(OwnerAlert).where(OwnerAlert.tenant_id == tenant.id)
        )
        or 0
    )

    assert len(alerts) == 1
    assert before == after == 0, "plan invariant 8: insights emit no durable alerts"
