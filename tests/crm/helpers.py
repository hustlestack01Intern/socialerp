"""Shared seeding helpers for the Phase 8a CRM memory tests.

All data is synthetic. Helpers flush (but do not commit) so they can be used
inside a caller-owned transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models.catalog import Product, ProductStatus
from packages.core.models.core import Tenant, User, UserRole
from packages.core.models.order import Customer, Order, OrderItem

PHONE = "254712345678"


async def make_tenant(session: AsyncSession, *, slug_prefix: str = "crm") -> Tenant:
    tenant = Tenant(
        id=uuid4(),
        name="CRM Test Biz",
        slug=f"{slug_prefix}-{uuid4().hex[:8]}",
        owner_phone="254700000011",
        owner_email=f"{slug_prefix}-{uuid4().hex[:6]}@example.com",
        is_active=True,
        settings={},
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def make_user(
    session: AsyncSession, tenant_id: UUID, *, role: UserRole, user_id: UUID | None = None
) -> User:
    user = User(
        id=user_id or uuid4(),
        tenant_id=tenant_id,
        email=f"user-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        full_name="Test Actor",
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def make_customer(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    phone: str = PHONE,
    name: str | None = "Test Customer",
) -> Customer:
    customer = Customer(
        tenant_id=tenant_id,
        phone=phone,
        name=name,
        email="customer@example.com",
        default_address="1 Test Lane",
        default_area="Kilimani",
        is_active=True,
    )
    session.add(customer)
    await session.flush()
    return customer


async def make_order(
    session: AsyncSession,
    tenant_id: UUID,
    customer_id: UUID,
    *,
    total_cents: int = 5000,
    created_at: datetime | None = None,
    payment_status: str = "pending",
    paid_at: datetime | None = None,
    status: str = "awaiting_payment",
    customer_phone: str = PHONE,
    customer_name: str | None = "Test Customer",
) -> Order:
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer_id,
        customer_phone=customer_phone,
        customer_name=customer_name,
        subtotal_cents=total_cents,
        total_cents=total_cents,
        status=status,
        payment_status=payment_status,
        payment_method="mpesa_stk",
        created_at=created_at or datetime.now(UTC),
        paid_at=paid_at,
    )
    session.add(order)
    await session.flush()
    return order


async def make_product(session: AsyncSession, tenant_id: UUID) -> Product:
    product = Product(tenant_id=tenant_id, name="Hoodie", status=ProductStatus.ACTIVE.value)
    session.add(product)
    await session.flush()
    return product


async def make_order_item(
    session: AsyncSession,
    order: Order,
    tenant_id: UUID,
    *,
    product_id: UUID,
    quantity: int = 1,
    variant_attributes: list[dict[str, Any]] | None = None,
) -> OrderItem:
    item = OrderItem(
        order_id=order.id,
        tenant_id=tenant_id,
        product_id=product_id,
        sku=f"SKU-{uuid4().hex[:8].upper()}",
        name="Hoodie",
        attributes={"variant_attributes": variant_attributes or []},
        unit_price_cents=1000,
        quantity=quantity,
        total_price_cents=1000 * quantity,
    )
    session.add(item)
    await session.flush()
    return item


async def seed_paid_history(
    session: AsyncSession, tenant_id: UUID
) -> tuple[Customer, list[Order], Product]:
    """A customer with three paid orders on a deterministic 3-day cycle.

    All orders share the same hour-of-day, are spaced exactly 3 days apart, and
    the latest order carries items that make the modal size ``M`` (qty 2 vs L
    qty 1) and the modal colour ``black`` (qty 3). Totals: 5000 + 7000 + 8000.
    """
    base = datetime.now(UTC).replace(microsecond=0)
    customer = await make_customer(session, tenant_id)
    product = await make_product(session, tenant_id)

    orders: list[Order] = []
    for delta, total_cents in (
        (timedelta(days=9), 5000),
        (timedelta(days=6), 7000),
        (timedelta(days=3), 8000),
    ):
        created = base - delta
        orders.append(
            await make_order(
                session,
                tenant_id,
                customer.id,
                total_cents=total_cents,
                created_at=created,
                payment_status="completed",
                paid_at=created,
                status="paid",
            )
        )

    latest = orders[-1]
    await make_order_item(
        session,
        latest,
        tenant_id,
        product_id=product.id,
        quantity=2,
        variant_attributes=[{"name": "size", "value": "M"}],
    )
    await make_order_item(
        session,
        latest,
        tenant_id,
        product_id=product.id,
        quantity=1,
        variant_attributes=[{"name": "size", "value": "L"}],
    )
    await make_order_item(
        session,
        latest,
        tenant_id,
        product_id=product.id,
        quantity=3,
        variant_attributes=[{"name": "colour", "value": "black"}],
    )
    return customer, orders, product
