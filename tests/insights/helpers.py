"""Synthetic seeding helpers for the Phase 8b insights tests.

Mirrors ``tests/segments/helpers.py`` (which mirrors ``tests/crm/helpers.py``);
``tests/crm/helpers.py`` is shared and deliberately NOT edited for 8b. All data
is synthetic; helpers flush (but do not commit) so they can be used inside a
caller-owned transaction.

Orders are inserted DIRECTLY as :class:`Order` rows (not via OrderService):
insights tests need exact control over ``created_at`` windows, payment state
and totals — things the state machine would fight us on — and must stay
independent of the orders module's own evolution.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models.catalog import Product, ProductVariant
from packages.core.models.order import Customer, Order, OrderItem, OrderStatus, PaymentStatus
from tests.segments.helpers import make_customer, make_tenant  # re-export for convenience

__all__ = ["add_item", "add_order", "add_product", "add_variant", "make_customer", "make_tenant"]


async def add_product(session: AsyncSession, tenant_id: UUID, *, name: str = "Hoodie") -> Product:
    product = Product(
        tenant_id=tenant_id,
        name=name,
        status="active",
    )
    session.add(product)
    await session.flush()
    return product


async def add_variant(
    session: AsyncSession,
    tenant_id: UUID,
    product: Product,
    *,
    sku: str | None = None,
    quantity: int = 10,
    reorder_level: int = 5,
    is_active: bool = True,
    sell_price_cents: int = 250_00,
) -> ProductVariant:
    variant = ProductVariant(
        product_id=product.id,
        tenant_id=tenant_id,
        sku=sku or f"SKU-{uuid4().hex[:8]}",
        cost_price_cents=150_00,
        sell_price_cents=sell_price_cents,
        quantity=quantity,
        reorder_level=reorder_level,
        is_active=is_active,
    )
    session.add(variant)
    await session.flush()
    return variant


async def add_order(
    session: AsyncSession,
    tenant_id: UUID,
    customer: Customer,
    *,
    created_at: datetime,
    total_cents: int = 100_00,
    paid: bool = False,
    status: OrderStatus = OrderStatus.DRAFT,
) -> Order:
    """One order stamped at an exact instant; ``paid=True`` marks it completed."""
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone=customer.phone,
        customer_name=customer.name,
        subtotal_cents=total_cents,
        total_cents=total_cents,
        status=status,
        payment_status=PaymentStatus.COMPLETED if paid else PaymentStatus.PENDING,
        paid_at=created_at if paid else None,
        created_at=created_at,
    )
    session.add(order)
    await session.flush()
    return order


async def add_item(
    session: AsyncSession,
    tenant_id: UUID,
    order: Order,
    product: Product,
    *,
    quantity: int,
    name: str | None = None,
    sku: str | None = None,
    unit_price_cents: int = 100_00,
) -> OrderItem:
    item = OrderItem(
        order_id=order.id,
        tenant_id=tenant_id,
        product_id=product.id,
        sku=sku or f"SKU-{product.id.hex[:8]}",
        name=name or product.name,
        unit_price_cents=unit_price_cents,
        quantity=quantity,
        total_price_cents=unit_price_cents * quantity,
    )
    session.add(item)
    await session.flush()
    return item


def utc_day(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """A timezone-aware UTC instant for window-boundary seeding."""
    return datetime(year, month, day, hour, tzinfo=UTC)
