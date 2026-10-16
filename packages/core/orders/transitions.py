"""
Order transition helper (single source of truth).

The order state machine's legal table lives in
``packages.core.domain.order`` (ADR 0006, amended by ADR 0015). This module owns
the *mutation*: validate a transition and set the status together with its
timestamp. Every service that moves an order (orders, payments, delivery) calls
:func:`apply_order_transition` so timestamps can never silently disagree with the
status, and the canonical ``domain.order.OrderStatus`` is the only enum involved
(fixing the former split-enum identity bug, ADR 0015).
"""

from __future__ import annotations

from datetime import UTC, datetime

from packages.core.domain.order import (
    IllegalTransitionError,
    OrderStatus,
    is_legal_transition,
)
from packages.core.models.order import Order


def apply_order_transition(
    order: Order,
    new_status: OrderStatus,
    *,
    now: datetime | None = None,
) -> None:
    """Validate and apply an order status transition in place.

    Raises :class:`IllegalTransitionError` when the edge is not in the ADR 0006
    legal table. Sets ``status``, ``updated_at`` and the status-specific
    timestamp. ``new_status`` must be the canonical ``domain.order.OrderStatus``.
    """
    current = OrderStatus(order.status)
    if not is_legal_transition(current, new_status):
        raise IllegalTransitionError(f"Illegal transition: {current.value} -> {new_status.value}")

    ts = now or datetime.now(UTC)
    order.status = new_status.value
    order.updated_at = ts
    if new_status is OrderStatus.AWAITING_PAYMENT:
        order.confirmed_at = ts
    elif new_status is OrderStatus.PAID:
        order.paid_at = ts
    elif new_status is OrderStatus.PACKED:
        order.packed_at = ts
    elif new_status is OrderStatus.OUT_FOR_DELIVERY:
        order.shipped_at = ts
    elif new_status is OrderStatus.DELIVERED:
        order.delivered_at = ts
    elif new_status is OrderStatus.CANCELLED:
        order.cancelled_at = ts
