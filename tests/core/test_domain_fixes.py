"""
Regression tests for verified domain bugs (2026-10-13).

Each test in this file failed against the M3 codebase before the fixes:
1. Money accepted floats (1.5 cents) instead of rejecting non-integers
2. OrderItem could not be instantiated (property/field collision on total_price)
3. Order.transition_to never applied status timestamps (paid_at stayed None)
4. FSM allowed paid->cancelled, packed->cancelled, failed_delivery->cancelled,
   delivered->refunded and lacked failed_delivery->refunded (ADR 0006)
5. StockLedger.release() conjured stock: received 10, reserved 4, released 100
   -> available 106 (no reference validation, over-release allowed)
6. StockLedger.get_reservations() returned released reservations as active
7. ReservationManager compared status against string "active" instead of the
   ReservationStatus enum, so get_active_for_product always returned []
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from packages.core.domain import SKU, Money, ProductId, Quantity, TenantId, VariantId
from packages.core.domain.order import (
    LEGAL_TRANSITIONS,
    Order,
    OrderItem,
    OrderStatus,
)
from packages.core.domain.reservation import ReservationManager
from packages.core.domain.stock import StockLedger, StockMovementType


def make_ledger() -> StockLedger:
    ledger = StockLedger(
        tenant_id=TenantId(uuid4()),
        product_id=ProductId(uuid4()),
        variant_id=VariantId(uuid4()),
    )
    ledger.load([])
    return ledger


def make_order(status: OrderStatus = OrderStatus.DRAFT) -> Order:
    ts = datetime.now(UTC)
    return Order(
        tenant_id=uuid4(),
        customer_id=uuid4(),
        customer_phone="254712345678",
        status=status,
        created_at=ts,
        updated_at=ts,
    )


# =============================================================================
# 1. Money
# =============================================================================


class TestMoneyIntegrity:
    def test_money_rejects_float_cents(self) -> None:
        """Money(cents=1.5) must be rejected: cents are integers only."""
        with pytest.raises(TypeError):
            Money(1.5)

    def test_money_rejects_str_cents(self) -> None:
        with pytest.raises(TypeError):
            Money("100")

    def test_money_accepts_int_cents(self) -> None:
        m = Money(100)
        assert m.cents == 100

    def test_money_still_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            Money(-1)


# =============================================================================
# 2. OrderItem
# =============================================================================


class TestOrderItem:
    def test_order_item_is_constructible(self) -> None:
        """OrderItem must be instantiable with valid inputs."""
        item = OrderItem(
            product_id=uuid4(),
            variant_id=None,
            sku=SKU("HOODIE-M"),
            name="Black Hoodie M",
            unit_price=Money.from_kes(1500),
            quantity=Quantity(2),
        )
        assert item.total_price == Money.from_kes(3000)
        assert item.total_price.cents == 300000

    def test_order_item_rejects_zero_quantity(self) -> None:
        with pytest.raises(ValueError):
            OrderItem(
                product_id=uuid4(),
                variant_id=None,
                sku=SKU("HOODIE-M"),
                name="Black Hoodie M",
                unit_price=Money.from_kes(1500),
                quantity=Quantity(0),
            )

    def test_order_item_total_is_computed(self) -> None:
        """total_price is derived (unit_price * quantity), never stale."""
        item = OrderItem(
            product_id=uuid4(),
            variant_id=None,
            sku=SKU("HOODIE-M"),
            name="Black Hoodie M",
            unit_price=Money.from_kes(1500),
            quantity=Quantity(3),
        )
        assert item.total_price.cents == 450000


# =============================================================================
# 3. Transition timestamps
# =============================================================================


class TestTransitionTimestamps:
    """transition_to must stamp the status-specific timestamp."""

    def test_confirmed_at_set_on_awaiting_payment(self) -> None:
        order = make_order().transition_to(OrderStatus.AWAITING_PAYMENT)
        assert order.confirmed_at is not None
        assert order.updated_at == order.confirmed_at

    def test_paid_at_set_on_paid(self) -> None:
        order = make_order(OrderStatus.AWAITING_PAYMENT).transition_to(OrderStatus.PAID)
        assert order.paid_at is not None

    def test_packed_at_set_on_packed(self) -> None:
        order = make_order(OrderStatus.PAID).transition_to(OrderStatus.PACKED)
        assert order.packed_at is not None

    def test_shipped_at_set_on_out_for_delivery(self) -> None:
        order = make_order(OrderStatus.PACKED).transition_to(OrderStatus.OUT_FOR_DELIVERY)
        assert order.shipped_at is not None

    def test_delivered_at_set_on_delivered(self) -> None:
        order = make_order(OrderStatus.OUT_FOR_DELIVERY).transition_to(OrderStatus.DELIVERED)
        assert order.delivered_at is not None

    def test_cancelled_at_set_on_cancelled(self) -> None:
        order = make_order().transition_to(OrderStatus.CANCELLED)
        assert order.cancelled_at is not None

    def test_paid_at_not_set_before_payment(self) -> None:
        order = make_order(OrderStatus.AWAITING_PAYMENT)
        assert order.paid_at is None


# =============================================================================
# 4. FSM per ADR 0006
# =============================================================================


class TestFsmMatchesAdr0006:
    """The transition table must match ADR 0006 exactly."""

    def test_exact_transition_table(self) -> None:
        expected = {
            OrderStatus.DRAFT: {OrderStatus.AWAITING_PAYMENT, OrderStatus.CANCELLED},
            OrderStatus.AWAITING_PAYMENT: {
                OrderStatus.PAID,
                OrderStatus.PACKED,
                OrderStatus.CANCELLED,
            },
            OrderStatus.PAID: {OrderStatus.PACKED, OrderStatus.REFUNDED},
            OrderStatus.PACKED: {OrderStatus.OUT_FOR_DELIVERY},
            OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED, OrderStatus.FAILED_DELIVERY},
            OrderStatus.FAILED_DELIVERY: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.REFUNDED},
            OrderStatus.DELIVERED: set(),
            OrderStatus.CANCELLED: set(),
            OrderStatus.REFUNDED: set(),
        }
        assert expected == LEGAL_TRANSITIONS

    def test_paid_cannot_be_cancelled(self) -> None:
        order = make_order(OrderStatus.PAID)
        with pytest.raises(ValueError):
            order.transition_to(OrderStatus.CANCELLED)

    def test_packed_cannot_be_cancelled(self) -> None:
        order = make_order(OrderStatus.PACKED)
        with pytest.raises(ValueError):
            order.transition_to(OrderStatus.CANCELLED)

    def test_failed_delivery_cannot_be_cancelled(self) -> None:
        order = make_order(OrderStatus.FAILED_DELIVERY)
        with pytest.raises(ValueError):
            order.transition_to(OrderStatus.CANCELLED)

    def test_failed_delivery_can_be_refunded(self) -> None:
        order = make_order(OrderStatus.FAILED_DELIVERY).transition_to(OrderStatus.REFUNDED)
        assert order.status == OrderStatus.REFUNDED

    def test_paid_can_be_refunded(self) -> None:
        order = make_order(OrderStatus.PAID).transition_to(OrderStatus.REFUNDED)
        assert order.status == OrderStatus.REFUNDED

    def test_delivered_is_terminal(self) -> None:
        order = make_order(OrderStatus.DELIVERED)
        with pytest.raises(ValueError):
            order.transition_to(OrderStatus.REFUNDED)


# =============================================================================
# 5 & 6. StockLedger release / get_reservations
# =============================================================================


class TestStockLedgerReleaseIntegrity:
    def test_release_cannot_conjure_stock(self) -> None:
        """receive 10, reserve 4, release 100 -> must raise (was: available 106)."""
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        ref = uuid4()
        ledger.reserve(4, ref, "order")

        with pytest.raises(ValueError):
            ledger.release(100, ref, "order")

        # Nothing may have been conjured.
        assert ledger.available == 6
        assert ledger.reserved == 4
        assert ledger.on_hand == 10

    def test_release_with_unknown_reference_rejected(self) -> None:
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        with pytest.raises(ValueError):
            ledger.release(3, uuid4(), "order")

    def test_release_restores_exactly_the_released_quantity(self) -> None:
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        ref = uuid4()
        ledger.reserve(4, ref, "order")
        ledger.release(4, ref, "order")
        assert ledger.available == 10
        assert ledger.reserved == 0
        assert ledger.on_hand == 10

    def test_double_release_rejected(self) -> None:
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        ref = uuid4()
        ledger.reserve(4, ref, "order")
        ledger.release(4, ref, "order")
        with pytest.raises(ValueError):
            ledger.release(4, ref, "order")

    def test_sell_requires_on_hand(self) -> None:
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        with pytest.raises(ValueError):
            ledger.sell(11, uuid4(), "order")

    def test_reservation_quantities_are_integers(self) -> None:
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        ref = uuid4()
        with pytest.raises(TypeError):
            ledger.reserve(1.5, ref, "order")
        with pytest.raises(TypeError):
            ledger.release(1.5, ref, "order")


class TestGetReservations:
    def test_active_reservation_returned(self) -> None:
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        ledger.reserve(4, uuid4(), "order")
        reservations = ledger.get_reservations()
        assert len(reservations) == 1
        assert reservations[0].movement_type == StockMovementType.RESERVE

    def test_released_reservation_not_returned(self) -> None:
        """get_reservations must exclude released reservations (was: included them)."""
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        ref = uuid4()
        ledger.reserve(4, ref, "order")
        ledger.release(4, ref, "order")
        assert ledger.get_reservations() == []

    def test_sold_reservation_not_returned(self) -> None:
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        ref = uuid4()
        ledger.reserve(4, ref, "order")
        ledger.sell(4, ref, "order")
        assert ledger.get_reservations() == []
        assert ledger.reserved == 0

    def test_partially_released_reservation_still_returned(self) -> None:
        ledger = make_ledger()
        ledger.record_receive(10, 1000, uuid4(), "purchase_order")
        ref = uuid4()
        ledger.reserve(6, ref, "order")
        ledger.release(2, ref, "order")
        reservations = ledger.get_reservations()
        assert len(reservations) == 1
        assert ledger.reserved == 4


# =============================================================================
# 7. ReservationManager status handling
# =============================================================================


class TestReservationManagerStatus:
    def test_get_active_for_product_uses_enum(self) -> None:
        """get_active_for_product must match ACTIVE reservations.

        Regression: a str comparison previously yielded always-empty results.
        """
        manager = ReservationManager(tenant_id=TenantId(uuid4()))
        product_id = uuid4()
        r = manager.create(product_id=product_id, variant_id=None, quantity=2, reference_id=uuid4())

        active = manager.get_active_for_product(product_id)
        assert len(active) == 1
        assert active[0].id == r.id
        assert manager.get_total_reserved(product_id) == 2

    def test_released_not_counted_as_active(self) -> None:
        manager = ReservationManager(tenant_id=TenantId(uuid4()))
        product_id = uuid4()
        r = manager.create(product_id=product_id, variant_id=None, quantity=2, reference_id=uuid4())
        assert manager.release(r.id) is True

        assert manager.get_active_for_product(product_id) == []
        assert manager.get_total_reserved(product_id) == 0

    def test_fulfilled_not_counted_as_active(self) -> None:
        manager = ReservationManager(tenant_id=TenantId(uuid4()))
        product_id = uuid4()
        r = manager.create(product_id=product_id, variant_id=None, quantity=2, reference_id=uuid4())
        assert manager.fulfill(r.id) is True

        assert manager.get_active_for_product(product_id) == []
        assert manager.get_total_reserved(product_id) == 0

    def test_create_honours_reference_type(self) -> None:
        manager = ReservationManager(tenant_id=TenantId(uuid4()))
        r = manager.create(
            product_id=uuid4(),
            variant_id=None,
            quantity=1,
            reference_id=uuid4(),
            reference_type="manual",
        )
        assert r.reference_type == "manual"

    def test_expiry_status(self) -> None:
        manager = ReservationManager(tenant_id=TenantId(uuid4()))
        product_id = uuid4()
        r = manager.create(product_id=product_id, variant_id=None, quantity=1, reference_id=uuid4())
        assert manager.expire(r.id) is True
        # Expired reservations no longer count as active stock claims.
        assert manager.get_active_for_product(product_id) == []
        assert manager.get_total_reserved(product_id) == 0
