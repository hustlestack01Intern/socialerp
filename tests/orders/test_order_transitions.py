"""
Unit tests for the single order-transition helper (ADR 0015).

These are the regression tests for the split-enum identity bug (B7): the helper
must use the canonical ``domain.order.OrderStatus`` and set the status-specific
timestamp, and must reject edges not in the ADR 0006 table.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from packages.core.domain.order import IllegalTransitionError, OrderStatus
from packages.core.models.order import Order
from packages.core.orders.transitions import apply_order_transition


def _order(status: OrderStatus) -> Order:
    return Order(
        tenant_id=uuid4(),
        customer_id=uuid4(),
        customer_phone="254712345678",
        total_cents=1000,
        status=status.value,
    )


def test_sets_status_and_confirmed_at_on_awaiting_payment() -> None:
    order = _order(OrderStatus.DRAFT)
    apply_order_transition(order, OrderStatus.AWAITING_PAYMENT)
    assert order.status == OrderStatus.AWAITING_PAYMENT.value
    assert order.confirmed_at is not None
    assert order.updated_at is not None


def test_sets_paid_at_and_packed_at_across_the_money_path() -> None:
    order = _order(OrderStatus.AWAITING_PAYMENT)
    apply_order_transition(order, OrderStatus.PAID)
    assert order.status == OrderStatus.PAID.value
    assert order.paid_at is not None

    apply_order_transition(order, OrderStatus.PACKED)
    assert order.status == OrderStatus.PACKED.value
    assert order.packed_at is not None


def test_sets_shipped_and_delivered_timestamps() -> None:
    order = _order(OrderStatus.PACKED)
    apply_order_transition(order, OrderStatus.OUT_FOR_DELIVERY)
    assert order.shipped_at is not None
    apply_order_transition(order, OrderStatus.DELIVERED)
    assert order.delivered_at is not None


def test_illegal_transition_raises() -> None:
    order = _order(OrderStatus.DRAFT)
    with pytest.raises(IllegalTransitionError):
        apply_order_transition(order, OrderStatus.PAID)


def test_pod_pack_edge_is_legal_in_the_table() -> None:
    """ADR 0015 added awaiting_payment -> packed (service-guarded for POD)."""
    order = _order(OrderStatus.AWAITING_PAYMENT)
    apply_order_transition(order, OrderStatus.PACKED)
    assert order.status == OrderStatus.PACKED.value
