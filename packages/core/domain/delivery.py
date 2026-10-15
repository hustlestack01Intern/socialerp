"""
Delivery Domain Model

Value objects and the delivery state machine. The delivery FSM mirrors the
order FSM (ADR 0006) for the physical lifecycle of an order, and maps 1:1 to
the rider's WhatsApp quick replies (picked up / arrived / delivered):

    assigned -> picked_up -> arrived -> delivered
                                    \\-> failed -> picked_up (retry)

The order remains the authority for money/stock; a delivery row carries the
rider, zone, fee and proof for one physical attempt at delivering one order.
For pay-on-delivery orders the order is already ``out_for_delivery`` when the
STK Push fires at arrival (goods moving, payment due) — the payment service's
payable-order check admits that case explicitly.
"""

from __future__ import annotations

from enum import Enum


class DeliveryStatus(str, Enum):
    """Delivery lifecycle statuses - strict transitions enforced."""

    ASSIGNED = "assigned"
    PICKED_UP = "picked_up"
    ARRIVED = "arrived"
    DELIVERED = "delivered"
    FAILED = "failed"


LEGAL_DELIVERY_TRANSITIONS: dict[DeliveryStatus, set[DeliveryStatus]] = {
    DeliveryStatus.ASSIGNED: {DeliveryStatus.PICKED_UP, DeliveryStatus.FAILED},
    DeliveryStatus.PICKED_UP: {DeliveryStatus.ARRIVED, DeliveryStatus.FAILED},
    DeliveryStatus.ARRIVED: {DeliveryStatus.DELIVERED, DeliveryStatus.FAILED},
    # A failed delivery goes back on the road (same-rider retry); repeated
    # failures land here again for owner triage (refund path is human-approved).
    DeliveryStatus.FAILED: {DeliveryStatus.PICKED_UP},
    DeliveryStatus.DELIVERED: set(),
}


def is_legal_delivery_transition(from_status: DeliveryStatus, to_status: DeliveryStatus) -> bool:
    """Check if a delivery state transition is legal."""
    return to_status in LEGAL_DELIVERY_TRANSITIONS.get(from_status, set())


class IllegalDeliveryTransitionError(ValueError):
    """Raised when a delivery attempts an illegal state transition."""


class DeliveryFailureReason(str, Enum):
    """Reasons a delivery attempt can fail (owner-facing categories)."""

    CUSTOMER_UNREACHABLE = "customer_unreachable"
    WRONG_ADDRESS = "wrong_address"
    CUSTOMER_REFUSED = "customer_refused"
    RIDER_UNAVAILABLE = "rider_unavailable"
    OTHER = "other"
