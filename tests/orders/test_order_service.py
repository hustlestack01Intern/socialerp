"""
Phase 6 order service tests (real Postgres, event-sourced stock ledger).

These exercise OrderService directly: catalog-price authority, reservation on
creation, release on cancel/expiry, the sale side effect from a completed
payment, idempotency and the concurrent last-item race.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from packages.core.domain.order import IllegalTransitionError
from packages.core.domain.stock import StockLedger, StockMovementRepository
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.order import Customer, Order
from packages.core.models.reservation import Reservation, ReservationStatus
from packages.core.models.stock import StockMovement
from packages.core.orders import (
    CreateOrderCommand,
    InsufficientStockError,
    ItemNotFoundError,
    ItemUnavailableError,
    OrderLine,
    OrderService,
    PriceMismatchError,
    ReservationLostError,
)
from packages.core.orders.service import _movement_model_to_domain

PHONE = "254712345678"


async def seed_variant(
    session,
    tenant_id: UUID,
    *,
    price_cents: int = 5000,
    qty: int = 10,
    sku: str | None = None,
    is_active: bool = True,
) -> ProductVariant:
    """Create a product + variant with ledger stock (a RECEIVE movement)."""
    product = Product(
        tenant_id=tenant_id,
        name="Hoodie",
        status=ProductStatus.ACTIVE.value,
    )
    session.add(product)
    await session.flush()

    variant = ProductVariant(
        tenant_id=tenant_id,
        product_id=product.id,
        sku=sku or f"SKU-{uuid4().hex[:8].upper()}",
        attributes=[],
        cost_price_cents=price_cents // 2,
        sell_price_cents=price_cents,
        quantity=qty,
        is_active=is_active,
    )
    session.add(variant)
    await session.flush()

    ledger = StockLedger(tenant_id=tenant_id, product_id=product.id, variant_id=variant.id)
    ledger.load([])
    movement = ledger.record_receive(
        quantity=qty,
        unit_cost_cents=price_cents // 2,
        reference_id=None,
        reference_type="purchase_order",
    )
    await StockMovementRepository(session).add(movement)
    return variant


async def available(session, tenant_id: UUID, product_id: UUID, variant_id: UUID) -> int:
    rows = await StockMovementRepository(session).get_by_tenant_product(
        tenant_id, product_id, variant_id
    )
    ledger = StockLedger(tenant_id=tenant_id, product_id=product_id, variant_id=variant_id)
    ledger.load([_movement_model_to_domain(r) for r in rows])
    return ledger.available


async def reservation_for(session, order_id: UUID) -> Reservation:
    return (
        await session.execute(select(Reservation).where(Reservation.reference_id == order_id))
    ).scalar_one()


async def test_create_order_reserves_stock_and_totals(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, price_cents=5000, qty=10)
    service = OrderService(tenant_session)

    order = await service.create_order(
        tenant.id,
        CreateOrderCommand(
            customer_phone=PHONE,
            lines=(OrderLine(quantity=2, variant_id=variant.id),),
            delivery_fee_cents=200,
        ),
    )

    assert order.status == "awaiting_payment"
    assert order.subtotal_cents == 10_000
    assert order.total_cents == 10_200
    assert order.customer_phone == PHONE

    items = await service.list_items(tenant.id, order.id)
    assert len(items) == 1
    assert items[0].unit_price_cents == 5000
    assert items[0].total_price_cents == 10_000

    # Stock is reserved in the ledger (availability drops by the reserved qty).
    assert await available(tenant_session, tenant.id, variant.product_id, variant.id) == 8

    reservations = (
        (
            await tenant_session.execute(
                select(Reservation).where(Reservation.reference_id == order.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(reservations) == 1
    assert reservations[0].status == ReservationStatus.ACTIVE.value
    assert reservations[0].quantity == 2


async def test_create_order_uses_catalog_price_not_caller(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, price_cents=7500)
    service = OrderService(tenant_session)
    order = await service.create_order(
        tenant.id,
        CreateOrderCommand(customer_phone=PHONE, lines=(OrderLine(quantity=1, sku=variant.sku),)),
    )
    items = await service.list_items(tenant.id, order.id)
    assert items[0].unit_price_cents == 7500
    assert order.total_cents == 7500


async def test_create_order_expected_price_mismatch(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, price_cents=5000)
    service = OrderService(tenant_session)
    with pytest.raises(PriceMismatchError):
        await service.create_order(
            tenant.id,
            CreateOrderCommand(
                customer_phone=PHONE,
                lines=(OrderLine(quantity=1, variant_id=variant.id, expected_unit_price_cents=1),),
            ),
        )


async def test_create_order_unknown_variant(tenant_session, tenant) -> None:
    service = OrderService(tenant_session)
    with pytest.raises(ItemNotFoundError):
        await service.create_order(
            tenant.id,
            CreateOrderCommand(
                customer_phone=PHONE, lines=(OrderLine(quantity=1, variant_id=uuid4()),)
            ),
        )


async def test_create_order_inactive_variant(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, is_active=False)
    service = OrderService(tenant_session)
    with pytest.raises(ItemUnavailableError):
        await service.create_order(
            tenant.id,
            CreateOrderCommand(
                customer_phone=PHONE, lines=(OrderLine(quantity=1, variant_id=variant.id),)
            ),
        )


async def test_create_order_insufficient_stock_writes_nothing(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, qty=1)
    service = OrderService(tenant_session)
    with pytest.raises(InsufficientStockError):
        await service.create_order(
            tenant.id,
            CreateOrderCommand(
                customer_phone=PHONE, lines=(OrderLine(quantity=2, variant_id=variant.id),)
            ),
        )
    count = await tenant_session.scalar(select(func.count(Order.id)))
    assert count == 0


async def test_create_order_idempotent(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, qty=10)
    service = OrderService(tenant_session)
    cmd = CreateOrderCommand(
        customer_phone=PHONE,
        lines=(OrderLine(quantity=1, variant_id=variant.id),),
        idempotency_key="key-123",
    )
    first = await service.create_order(tenant.id, cmd)
    second = await service.create_order(tenant.id, cmd)
    assert first.id == second.id
    assert await tenant_session.scalar(select(func.count(Order.id))) == 1


async def test_create_order_merges_duplicate_lines(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, price_cents=1000, qty=10)
    service = OrderService(tenant_session)
    order = await service.create_order(
        tenant.id,
        CreateOrderCommand(
            customer_phone=PHONE,
            lines=(
                OrderLine(quantity=1, variant_id=variant.id),
                OrderLine(quantity=2, variant_id=variant.id),
            ),
        ),
    )
    items = await service.list_items(tenant.id, order.id)
    assert len(items) == 1
    assert items[0].quantity == 3
    assert order.subtotal_cents == 3000
    # Only one reservation for the merged quantity.
    assert await tenant_session.scalar(select(func.count(Reservation.id))) == 1


async def test_create_order_reuses_customer(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, qty=10)
    service = OrderService(tenant_session)
    await service.create_order(
        tenant.id,
        CreateOrderCommand(customer_phone=PHONE, lines=(OrderLine(1, variant_id=variant.id),)),
    )
    await service.create_order(
        tenant.id,
        CreateOrderCommand(customer_phone=PHONE, lines=(OrderLine(1, variant_id=variant.id),)),
    )
    assert await tenant_session.scalar(select(func.count(Customer.id))) == 1
    assert await tenant_session.scalar(select(func.count(Order.id))) == 2


async def test_cancel_order_releases_reservation(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, qty=10)
    service = OrderService(tenant_session)
    order = await service.create_order(
        tenant.id,
        CreateOrderCommand(
            customer_phone=PHONE, lines=(OrderLine(quantity=4, variant_id=variant.id),)
        ),
    )
    assert await available(tenant_session, tenant.id, variant.product_id, variant.id) == 6

    await service.cancel_order(tenant.id, order.id, "changed mind")
    assert order.status == "cancelled"
    # Availability restored.
    assert await available(tenant_session, tenant.id, variant.product_id, variant.id) == 10
    reservation = await reservation_for(tenant_session, order.id)
    assert reservation.status == ReservationStatus.RELEASED.value


async def test_cannot_cancel_paid_order(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, qty=10)
    service = OrderService(tenant_session)
    order = await service.create_order(
        tenant.id,
        CreateOrderCommand(
            customer_phone=PHONE, lines=(OrderLine(quantity=1, variant_id=variant.id),)
        ),
    )
    order.status = "paid"
    await tenant_session.flush()
    with pytest.raises(IllegalTransitionError):
        await service.cancel_order(tenant.id, order.id, "too late")


async def test_record_sale_for_paid_order_is_idempotent(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, qty=10)
    service = OrderService(tenant_session)
    order = await service.create_order(
        tenant.id,
        CreateOrderCommand(
            customer_phone=PHONE, lines=(OrderLine(quantity=3, variant_id=variant.id),)
        ),
    )
    # Simulate payment completion (the payment service moves the order to paid).
    order.status = "paid"
    await tenant_session.flush()

    await service.record_sale_for_order(tenant.id, order.id)
    sells = (
        (
            await tenant_session.execute(
                select(StockMovement).where(
                    StockMovement.movement_type == "sell",
                    StockMovement.reference_id == order.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(sells) == 1
    assert sells[0].quantity_change == 0  # SELL does not change availability

    reservation = await reservation_for(tenant_session, order.id)
    assert reservation.status == ReservationStatus.FULFILLED.value

    # Replayed event: no second SELL movement.
    await service.record_sale_for_order(tenant.id, order.id)
    sells_again = (
        (
            await tenant_session.execute(
                select(StockMovement).where(
                    StockMovement.movement_type == "sell",
                    StockMovement.reference_id == order.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(sells_again) == 1


async def test_record_sale_after_reservation_expired_re_reserves(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, qty=10)
    service = OrderService(tenant_session, reservation_ttl_minutes=0)
    order = await service.create_order(
        tenant.id,
        CreateOrderCommand(
            customer_phone=PHONE, lines=(OrderLine(quantity=3, variant_id=variant.id),)
        ),
    )
    assert await available(tenant_session, tenant.id, variant.product_id, variant.id) == 7

    # The TTL elapses and the expiry job releases the reservation.
    assert await service.expire_stale_reservations(tenant.id) == 1
    assert await available(tenant_session, tenant.id, variant.product_id, variant.id) == 10

    # The customer's (late) payment then completes. The sale must still be
    # honoured exactly once: re-reserve under the order, then sell.
    order.status = "paid"
    await tenant_session.flush()
    await service.record_sale_for_order(tenant.id, order.id)

    assert await available(tenant_session, tenant.id, variant.product_id, variant.id) == 7
    sells = (
        (
            await tenant_session.execute(
                select(StockMovement).where(
                    StockMovement.movement_type == "sell",
                    StockMovement.reference_id == order.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(sells) == 1
    reservation = await reservation_for(tenant_session, order.id)
    assert reservation.status == ReservationStatus.FULFILLED.value


async def test_record_sale_when_reservation_lost_raises(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, qty=3)
    service = OrderService(tenant_session, reservation_ttl_minutes=0)
    order_a = await service.create_order(
        tenant.id,
        CreateOrderCommand(
            customer_phone=PHONE, lines=(OrderLine(quantity=3, variant_id=variant.id),)
        ),
    )
    # A's TTL releases the stock; a second order then takes all of it.
    assert await service.expire_stale_reservations(tenant.id) == 1
    await service.create_order(
        tenant.id,
        CreateOrderCommand(
            customer_phone="254700000002",
            lines=(OrderLine(quantity=3, variant_id=variant.id),),
        ),
    )
    order_a.status = "paid"
    await tenant_session.flush()

    # A's late payment cannot be honoured without double-selling to B, so the
    # sale is refused loudly for reconciliation instead of silently overselling.
    with pytest.raises(ReservationLostError):
        await service.record_sale_for_order(tenant.id, order_a.id)


async def test_expire_stale_reservations_releases(tenant_session, tenant) -> None:
    variant = await seed_variant(tenant_session, tenant.id, qty=10)
    service = OrderService(tenant_session, reservation_ttl_minutes=0)
    order = await service.create_order(
        tenant.id,
        CreateOrderCommand(
            customer_phone=PHONE, lines=(OrderLine(quantity=5, variant_id=variant.id),)
        ),
    )
    assert await available(tenant_session, tenant.id, variant.product_id, variant.id) == 5

    released = await service.expire_stale_reservations(tenant.id)
    assert released == 1
    assert await available(tenant_session, tenant.id, variant.product_id, variant.id) == 10
    reservation = await reservation_for(tenant_session, order.id)
    assert reservation.status == ReservationStatus.EXPIRED.value


async def test_concurrent_last_item_race(session_factory, tenant) -> None:
    """Two concurrent orders for the last unit: exactly one wins."""
    async with session_factory() as session, session.begin():
        variant = await seed_variant(session, tenant.id, qty=1)
        variant_id = variant.id
        product_id = variant.product_id

    outcomes: list[str] = []

    async def attempt(session_factory_inner, key: str) -> None:
        async with session_factory_inner() as session, session.begin():
            service = OrderService(session)
            try:
                await service.create_order(
                    tenant.id,
                    CreateOrderCommand(
                        customer_phone=PHONE,
                        lines=(OrderLine(quantity=1, variant_id=variant_id),),
                        idempotency_key=key,
                    ),
                )
                outcomes.append("created")
            except InsufficientStockError:
                outcomes.append("rejected")

    await asyncio.gather(
        attempt(session_factory, "race-a"),
        attempt(session_factory, "race-b"),
    )

    assert sorted(outcomes) == ["created", "rejected"]
    async with session_factory() as session:
        assert await available(session, tenant.id, product_id, variant_id) == 0
