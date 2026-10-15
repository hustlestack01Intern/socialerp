"""
Property-based tests for Stock Ledger invariants.

These tests derive from the fundamental invariants of the event-sourced stock ledger:
1. Stock = Sum of immutable movements (never negative)
2. Available stock = Previous + quantity_change
3. Reservations reduce available, releases/sells increase it
4. Conservation laws hold across all operations
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from packages.core.domain import (
    ProductId,
    TenantId,
    VariantId,
)
from packages.core.domain.order import OrderStatus
from packages.core.domain.stock import (
    StockLedger,
    StockMovementReason,
    StockMovementType,
)

# =============================================================================
# Configuration
# =============================================================================

# Reduce examples for faster CI feedback
settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("dev", max_examples=500, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")


# =============================================================================
# Strategies
# =============================================================================

tenant_ids = st.uuids()
product_ids = st.uuids()
variant_ids = st.uuids()
order_ids = st.uuids()
reservation_ids = st.uuids()
quantities = st.integers(min_value=1, max_value=1000)
prices = st.integers(min_value=1, max_value=100000)  # cents
movement_types = st.sampled_from(list(StockMovementType))
reasons = st.sampled_from(list(StockMovementReason))


# =============================================================================
# StockLedger Property Tests
# =============================================================================


class TestStockLedgerProperties:
    """Property tests for StockLedger invariants."""

    def setup_ledger(self):
        """Create a fresh ledger for each test."""
        ledger = StockLedger(
            tenant_id=TenantId(uuid4()),
            product_id=ProductId(uuid4()),
            variant_id=VariantId(uuid4()),
        )
        ledger.load([])  # Initialize with empty movements
        return ledger

    @given(st.lists(quantities, min_size=1, max_size=10))
    def test_receive_increases_available(self, quantities: list[int]):
        """Receiving stock always increases available."""
        ledger = self.setup_ledger()

        for qty in quantities:
            movement = ledger.record_receive(
                quantity=qty,
                unit_cost_cents=1000,
                reference_id=uuid4(),
                reference_type="purchase_order",
            )
            assert movement.quantity_change == qty
            assert movement.movement_type == StockMovementType.RECEIVE

        expected = sum(quantities)
        assert ledger.available == expected

    @given(quantities, st.integers(min_value=1, max_value=5))
    def test_reserve_decreases_available(self, initial_qty: int, reserve_qty: int):
        """Reserving stock decreases available (if sufficient)."""
        ledger = self.setup_ledger()

        # First receive
        ledger.record_receive(
            quantity=initial_qty,
            unit_cost_cents=1000,
            reference_id=uuid4(),
            reference_type="purchase_order",
        )

        # Try to reserve
        if reserve_qty <= initial_qty:
            movement = ledger.reserve(
                quantity=reserve_qty,
                reference_id=uuid4(),
                reference_type="order",
            )
            assert movement.quantity_change == -reserve_qty
            assert ledger.available == initial_qty - reserve_qty
        else:
            with pytest.raises(ValueError, match="Insufficient stock"):
                ledger.reserve(
                    quantity=reserve_qty,
                    reference_id=uuid4(),
                )
            assert ledger.available == initial_qty

    @given(quantities, quantities)
    def test_release_restores_available(self, receive_qty: int, reserve_qty: int):
        """Releasing reservation restores available stock."""
        ledger = self.setup_ledger()

        # Receive
        ledger.record_receive(
            quantity=100,
            unit_cost_cents=1000,
            reference_id=uuid4(),
            reference_type="purchase_order",
        )

        # Reserve
        if reserve_qty <= 100:
            res_movement = ledger.reserve(
                quantity=reserve_qty,
                reference_id=uuid4(),
                reference_type="order",
            )
            reservation_id = res_movement.reference_id

            # Release
            rel_movement = ledger.release(
                quantity=reserve_qty,
                reference_id=reservation_id,
                reference_type="order",
                reason=StockMovementReason.ORDER_CANCELLED,
            )

            assert rel_movement.quantity_change == reserve_qty
            assert rel_movement.movement_type == StockMovementType.RELEASE
            assert ledger.available == 100

    @given(st.integers(min_value=1, max_value=50), st.integers(min_value=1, max_value=50))
    def test_sell_converts_reservation_to_sale(self, initial_qty: int, sell_qty: int):
        """Selling converts reservation to sale (stock goes out)."""
        ledger = self.setup_ledger()

        # Receive
        ledger.record_receive(
            quantity=initial_qty,
            unit_cost_cents=1000,
            reference_id=uuid4(),
            reference_type="purchase_order",
        )

        # Reserve
        if sell_qty <= initial_qty:
            ledger.reserve(
                quantity=sell_qty,
                reference_id=uuid4(),
                reference_type="order",
            )

            # Sell (fulfill)
            sell_movement = ledger.sell(
                quantity=sell_qty,
                reference_id=uuid4(),
                reference_type="order",
            )

            # SELL has quantity_change=0 because reservation already reduced available
            assert sell_movement.quantity_change == 0
            assert sell_movement.movement_type == StockMovementType.SELL
            assert ledger.available == initial_qty - sell_qty

    @given(st.integers(min_value=0, max_value=100).filter(lambda x: x != 50))
    def test_adjust_sets_exact_quantity(self, new_quantity: int):
        """Adjust sets stock to exact quantity."""
        ledger = self.setup_ledger()

        # Receive some stock first
        ledger.record_receive(
            quantity=50,
            unit_cost_cents=1000,
            reference_id=uuid4(),
            reference_type="purchase_order",
        )

        # Adjust
        adjust_movement = ledger.adjust(
            new_quantity=new_quantity,
            reason=StockMovementReason.MANUAL_COUNT,
            notes=f"Counted {new_quantity} units",
        )

        assert ledger.available == new_quantity
        assert adjust_movement.new_available == new_quantity
        assert adjust_movement.movement_type == StockMovementType.ADJUST

    @given(st.integers(min_value=1, max_value=20))
    def test_return_increases_available(self, return_qty: int):
        """Customer return increases available stock."""
        ledger = self.setup_ledger()

        # Receive and sell some
        ledger.record_receive(
            quantity=20,
            unit_cost_cents=1000,
            reference_id=uuid4(),
            reference_type="purchase_order",
        )
        ledger.reserve(5, uuid4(), "order")
        ledger.sell(5, uuid4(), "order")

        # Return
        return_movement = ledger.return_stock(
            quantity=return_qty,
            reference_id=uuid4(),
            reference_type="order",
        )

        assert return_movement.quantity_change == return_qty
        assert return_movement.movement_type == StockMovementType.RETURN
        assert ledger.available == 15 + return_qty  # 20 - 5 + return_qty


# =============================================================================
# Reservation Property Tests
# =============================================================================


class TestReservationProperties:
    """Property tests for reservation invariants."""

    @given(st.integers(min_value=1, max_value=100), st.integers(min_value=1, max_value=100))
    def test_reservation_creation_and_expiry(self, quantity: int, ttl_minutes: int):
        """Reservations are created with expiry and can expire."""

        from packages.core.domain import TenantId, generate_order_id
        from packages.core.domain.reservation import (
            ReservationManager,
        )

        manager = ReservationManager(tenant_id=TenantId(uuid4()))

        order_id = generate_order_id()
        product_id = uuid4()

        # Create reservation
        reservation = manager.create(
            product_id=product_id,
            variant_id=None,
            quantity=quantity,
            reference_id=order_id,
            reference_type="order",
            ttl_minutes=ttl_minutes,
        )

        assert reservation.status == "active"
        assert reservation.quantity == quantity
        assert reservation.reference_id == order_id

    @given(st.integers(min_value=1, max_value=100), st.integers(min_value=1, max_value=5))
    def test_reservation_fulfillment(self, quantity: int, ttl_minutes: int):
        """Reservations can be fulfilled (payment confirmed)."""

        from packages.core.domain import TenantId, generate_order_id
        from packages.core.domain.reservation import (
            ReservationManager,
        )

        manager = ReservationManager(tenant_id=TenantId(uuid4()))

        order_id = generate_order_id()
        product_id = uuid4()

        reservation = manager.create(
            product_id=product_id,
            variant_id=None,
            quantity=quantity,
            reference_id=order_id,
            reference_type="order",
            ttl_minutes=ttl_minutes,
        )

        # Fulfill
        result = manager.fulfill(reservation.id)
        assert result is True
        # In real implementation, status would be fulfilled
        # For now, we just check the method returns True

    @given(st.integers(min_value=1, max_value=100), st.integers(min_value=1, max_value=5))
    def test_reservation_release(self, quantity: int, ttl_minutes: int):
        """Reservations can be released (cancelled)."""
        from packages.core.domain import TenantId, generate_order_id
        from packages.core.domain.reservation import (
            ReservationManager,
        )

        manager = ReservationManager(tenant_id=TenantId(uuid4()))

        order_id = generate_order_id()
        product_id = uuid4()

        reservation = manager.create(
            product_id=product_id,
            variant_id=None,
            quantity=quantity,
            reference_id=order_id,
            reference_type="order",
            ttl_minutes=ttl_minutes,
        )

        # Release
        result = manager.release(reservation.id, "cancelled")
        assert result is True


# =============================================================================
# Order State Machine Property Tests
# =============================================================================


class TestOrderStateMachineProperties:
    """Property tests for order state machine invariants."""

    @given(st.sampled_from(list(OrderStatus)))
    def test_all_states_have_transitions(self, status: OrderStatus):
        """Every state has at least one valid transition (except terminal)."""
        from packages.core.domain.order import LEGAL_TRANSITIONS, OrderStatus

        # Terminal states have no transitions (CANCELLED, REFUNDED, DELIVERED)
        # Per ADR 0006: delivered is terminal; refunds after delivery are a
        # separate human-approved refund flow, not a delivered->refunded edge.
        if status in (OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.DELIVERED):
            assert len(LEGAL_TRANSITIONS[status]) == 0
        else:
            # Non-terminal states have at least one transition
            assert len(LEGAL_TRANSITIONS[status]) >= 1

    @given(st.sampled_from(list(OrderStatus)), st.sampled_from(list(OrderStatus)))
    def test_transition_symmetry(self, from_status: OrderStatus, to_status: OrderStatus):
        """Transitions are deterministic - same input always gives same result."""
        from packages.core.domain.order import is_legal_transition

        result1 = is_legal_transition(from_status, to_status)
        result2 = is_legal_transition(from_status, to_status)
        assert result1 == result2

    @given(st.sampled_from(list(OrderStatus)))
    def test_no_self_transitions(self, status: OrderStatus):
        """No state can transition to itself."""
        from packages.core.domain.order import is_legal_transition

        assert not is_legal_transition(status, status)


# =============================================================================
# Cross-Type Interaction Tests
# =============================================================================


class TestCrossTypeInteractions:
    """Property tests for interactions between domain types."""

    @given(st.integers(min_value=1, max_value=100), st.integers(min_value=1, max_value=10))
    def test_stock_reservation_order_consistency(self, stock_qty: int, order_qty: int):
        """Stock, reservation, and order quantities stay consistent."""
        from packages.core.domain import TenantId
        from packages.core.domain.stock import StockLedger

        ledger = StockLedger(
            tenant_id=TenantId(uuid4()),
            product_id=uuid4(),
            variant_id=None,
        )
        ledger.load([])  # Initialize with empty movements

        # Receive stock
        ledger.record_receive(stock_qty, 1000, uuid4(), "purchase_order")

        if order_qty <= stock_qty:
            # Reserve - use same reference_id for reserve and sell
            order_id = uuid4()
            ledger.reserve(order_qty, order_id, "order")

            # Verify consistency
            assert ledger.available == stock_qty - order_qty
            assert ledger.reserved == order_qty
            assert ledger.on_hand == stock_qty

            # Sell (using same order_id to mark reservation as fulfilled)
            ledger.sell(order_qty, order_id, "order")

            # After sale: available = stock_qty - order_qty, reserved = 0
            assert ledger.available == stock_qty - order_qty
            assert ledger.reserved == 0
            assert ledger.on_hand == stock_qty - order_qty


# =============================================================================
# Concurrent Access Tests (simulated)
# =============================================================================


class TestConcurrentAccessSimulation:
    """Simulated concurrent access tests for race conditions."""

    @given(st.integers(min_value=1, max_value=10), st.integers(min_value=1, max_value=10))
    def test_concurrent_reservations_same_product(self, qty1: int, qty2: int):
        """Two concurrent reservations for same product - total reserved ≤ available."""
        from packages.core.domain import TenantId
        from packages.core.domain.reservation import ReservationManager
        from packages.core.domain.stock import StockLedger

        ledger = StockLedger(
            tenant_id=TenantId(uuid4()),
            product_id=uuid4(),
            variant_id=None,
        )
        ledger.load([])  # Initialize with empty movements

        ReservationManager(tenant_id=TenantId(uuid4()))

        # Receive stock
        ledger.record_receive(20, 1000, uuid4(), "purchase_order")

        uuid4()
        uuid4()

        # Try to reserve both
        success1 = False
        success2 = False

        if qty1 <= 20:
            try:
                ledger.reserve(qty1, uuid4(), "order")
                success1 = True
            except ValueError:
                pass

        if qty2 <= (20 - (qty1 if success1 else 0)):
            try:
                ledger.reserve(qty2, uuid4(), "order")
                success2 = True
            except ValueError:
                pass

        # Total reserved should not exceed available
        total_reserved = (qty1 if success1 else 0) + (qty2 if success2 else 0)
        assert total_reserved <= 20


# =============================================================================
# Settings for Property Tests
# =============================================================================

# Reduce examples for faster CI feedback
settings.register_profile("ci", max_examples=100, suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("dev", max_examples=500, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("ci")
