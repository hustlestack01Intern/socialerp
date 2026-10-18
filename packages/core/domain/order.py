"""
Order Domain Models

Order state machine with strict transitions and event sourcing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from packages.core.domain import (
    SKU,
    Money,
    Quantity,
)

# =============================================================================
# Enums
# =============================================================================


class OrderStatus(str, Enum):
    """Order state machine statuses - strict transitions enforced."""

    DRAFT = "draft"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    PACKED = "packed"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    FAILED_DELIVERY = "failed_delivery"


class OrderType(str, Enum):
    """Order type."""

    STANDARD = "standard"
    PREORDER = "preorder"
    SUBSCRIPTION = "subscription"


class PaymentMethod(str, Enum):
    """Payment method."""

    MPESA_STK = "mpesa_stk"
    MPESA_PAY_ON_DELIVERY = "mpesa_pod"
    CASH_ON_DELIVERY = "cash_on_delivery"
    MANUAL = "manual"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


# =============================================================================
# Legal Transitions
# =============================================================================

# Legal state transitions for order state machine.
# Canonical table from ADR 0006: delivered, cancelled and refunded are terminal;
# paid orders can only be refunded (never cancelled); failed deliveries can be
# retried (out_for_delivery) or refunded.
LEGAL_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.AWAITING_PAYMENT, OrderStatus.CANCELLED},
    OrderStatus.AWAITING_PAYMENT: {OrderStatus.PAID, OrderStatus.PACKED, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.PACKED, OrderStatus.REFUNDED},
    OrderStatus.PACKED: {OrderStatus.OUT_FOR_DELIVERY},
    OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED, OrderStatus.FAILED_DELIVERY},
    OrderStatus.FAILED_DELIVERY: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.REFUNDED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}


def is_legal_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    """Check if a state transition is legal."""
    return to_status in LEGAL_TRANSITIONS.get(from_status, set())


class IllegalTransitionError(ValueError):
    """Raised when an order attempts a transition that is not in the legal table."""


# =============================================================================
# Value Objects
# =============================================================================


@dataclass(frozen=True)
class OrderItem:
    """Single line item in an order."""

    product_id: UUID
    variant_id: UUID | None
    sku: SKU
    name: str
    unit_price: Money
    quantity: Quantity
    attributes: dict[str, str] = field(default_factory=dict)  # e.g., {"color": "red", "size": "M"}
    id: UUID = field(default_factory=uuid4)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quantity.value <= 0:
            raise ValueError("Order item quantity must be positive")
        # total_price is derived by construction: unit_price * quantity.
        # No stored total field, so it can never go stale or disagree.

    @property
    def total_price(self) -> Money:
        return self.unit_price * self.quantity.value


@dataclass(frozen=True)
class DeliveryInfo:
    """Delivery information for an order."""

    address: str
    area: str  # Nairobi neighborhood
    lat: float | None = None
    lng: float | None = None
    instructions: str = ""
    fee_cents: int = 0
    estimated_date: str | None = None


@dataclass(frozen=True)
class PaymentInfo:
    """Payment information for an order."""

    method: PaymentMethod
    amount_cents: int
    status: str = "pending"  # pending, completed, failed, refunded
    reference_id: str | None = None  # M-Pesa receipt number
    paid_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Order Aggregate
# =============================================================================


@dataclass(frozen=True)
class Order:
    """Order aggregate root with strict state machine."""

    tenant_id: UUID
    customer_id: UUID
    customer_phone: str  # Normalized 254XXXXXXXXX
    items: tuple[OrderItem, ...] = field(default_factory=tuple)
    subtotal: Money = field(default_factory=lambda: Money.from_cents(0))
    delivery_fee: Money = field(default_factory=lambda: Money.from_cents(0))
    discount: Money = field(default_factory=lambda: Money.from_cents(0))
    total: Money = field(default_factory=lambda: Money.from_cents(0))
    customer_name: str | None = None
    customer_email: str | None = None

    status: OrderStatus = OrderStatus.DRAFT
    order_type: OrderType = OrderType.STANDARD

    delivery_info: DeliveryInfo | None = None
    payment_info: PaymentInfo | None = None

    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    confirmed_at: datetime | None = None
    paid_at: datetime | None = None
    packed_at: datetime | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_reason: str | None = None

    # Metadata
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate totals
        calculated_subtotal = sum((item.total_price for item in self.items), Money.from_cents(0))
        expected_total = calculated_subtotal + self.delivery_fee - self.discount
        if self.total != expected_total:
            raise ValueError(
                f"Total {self.total} != subtotal {calculated_subtotal} "
                f"+ delivery {self.delivery_fee} - discount {self.discount}",
            )

        if self.items:
            # Validate all items belong to same tenant (checked at service layer)
            pass

    def can_transition_to(self, new_status: OrderStatus) -> bool:
        """Check if transition to new status is legal."""
        return is_legal_transition(self.status, new_status)

    def transition_to(self, new_status: OrderStatus, timestamp: datetime | None = None) -> Order:
        """Transition to new status (returns new Order instance)."""
        if not self.can_transition_to(new_status):
            raise IllegalTransitionError(
                f"Illegal transition: {self.status.value} -> {new_status.value}",
            )

        ts = timestamp or datetime.now(UTC)

        confirmed_at = self.confirmed_at
        paid_at = self.paid_at
        packed_at = self.packed_at
        shipped_at = self.shipped_at
        delivered_at = self.delivered_at
        cancelled_at = self.cancelled_at

        # Set status-specific timestamps
        if new_status == OrderStatus.AWAITING_PAYMENT:
            confirmed_at = ts
        elif new_status == OrderStatus.PAID:
            paid_at = ts
        elif new_status == OrderStatus.PACKED:
            packed_at = ts
        elif new_status == OrderStatus.OUT_FOR_DELIVERY:
            shipped_at = ts
        elif new_status == OrderStatus.DELIVERED:
            delivered_at = ts
        elif new_status == OrderStatus.CANCELLED:
            cancelled_at = ts

        return Order(
            id=self.id,
            tenant_id=self.tenant_id,
            customer_id=self.customer_id,
            customer_phone=self.customer_phone,
            customer_name=self.customer_name,
            customer_email=self.customer_email,
            items=self.items,
            subtotal=self.subtotal,
            delivery_fee=self.delivery_fee,
            discount=self.discount,
            total=self.total,
            status=new_status,
            order_type=self.order_type,
            delivery_info=self.delivery_info,
            payment_info=self.payment_info,
            created_at=self.created_at,
            updated_at=ts,
            confirmed_at=confirmed_at,
            paid_at=paid_at,
            packed_at=packed_at,
            shipped_at=shipped_at,
            delivered_at=delivered_at,
            cancelled_at=cancelled_at,
            cancelled_reason=self.cancelled_reason,
            notes=self.notes,
            metadata=self.metadata,
        )

    def add_item(self, item: OrderItem) -> Order:
        """Add item to order (returns new Order)."""
        new_items = (*self.items, item)
        new_subtotal = self.subtotal + item.total_price
        new_total = self.total + item.total_price

        return Order(
            id=self.id,
            tenant_id=self.tenant_id,
            customer_id=self.customer_id,
            customer_phone=self.customer_phone,
            customer_name=self.customer_name,
            customer_email=self.customer_email,
            items=new_items,
            subtotal=new_subtotal,
            delivery_fee=self.delivery_fee,
            discount=self.discount,
            total=new_total,
            status=self.status,
            order_type=self.order_type,
            delivery_info=self.delivery_info,
            payment_info=self.payment_info,
            created_at=self.created_at,
            updated_at=datetime.now(UTC),
            confirmed_at=self.confirmed_at,
            paid_at=self.paid_at,
            packed_at=self.packed_at,
            shipped_at=self.shipped_at,
            delivered_at=self.delivered_at,
            cancelled_at=self.cancelled_at,
            cancelled_reason=self.cancelled_reason,
            notes=self.notes,
            metadata=self.metadata,
        )

    def with_payment(self, payment_info: PaymentInfo) -> Order:
        """Update payment info."""
        new_total = self.subtotal + self.delivery_fee - self.discount
        return Order(
            id=self.id,
            tenant_id=self.tenant_id,
            customer_id=self.customer_id,
            customer_phone=self.customer_phone,
            customer_name=self.customer_name,
            customer_email=self.customer_email,
            items=self.items,
            subtotal=self.subtotal,
            delivery_fee=self.delivery_fee,
            discount=self.discount,
            total=new_total,
            status=self.status,
            order_type=self.order_type,
            delivery_info=self.delivery_info,
            payment_info=payment_info,
            created_at=self.created_at,
            updated_at=datetime.now(UTC),
            confirmed_at=self.confirmed_at,
            paid_at=self.paid_at,
            packed_at=self.packed_at,
            shipped_at=self.shipped_at,
            delivered_at=self.delivered_at,
            cancelled_at=self.cancelled_at,
            cancelled_reason=self.cancelled_reason,
            notes=self.notes,
            metadata=self.metadata,
        )

    def cancel(self, reason: str, timestamp: datetime | None = None) -> Order:
        """Cancel the order."""
        if not self.can_transition_to(OrderStatus.CANCELLED):
            raise ValueError(f"Cannot cancel order in status {self.status.value}")

        return self.transition_to(OrderStatus.CANCELLED, timestamp)

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity.value for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "customer_id": str(self.customer_id),
            "customer_phone": self.customer_phone,
            "customer_name": self.customer_name,
            "customer_email": self.customer_email,
            "items": [item.to_dict() if hasattr(item, "to_dict") else {} for item in self.items]
            if self.items
            else [],
            "subtotal_cents": self.subtotal.cents,
            "delivery_fee_cents": self.delivery_fee.cents,
            "discount_cents": self.discount.cents,
            "total_cents": self.total.cents,
            "status": self.status.value,
            "order_type": self.order_type.value,
        }


# =============================================================================
# Input Models
# =============================================================================


@dataclass(frozen=True)
class CreateOrderRequest:
    """Input for creating an order."""

    customer_id: UUID
    customer_phone: str
    items: tuple[dict[str, Any], ...]  # {product_id, variant_id, quantity}
    customer_name: str | None = None
    customer_email: str | None = None
    delivery_info: dict[str, Any] | None = None
    payment_method: str = "mpesa_stk"
    notes: str = ""


@dataclass(frozen=True)
class UpdateOrderRequest:
    """Input for updating an order."""

    status: str | None = None
    delivery_info: dict[str, Any] | None = None
    payment_method: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class CancelOrderRequest:
    """Input for cancelling an order."""

    reason: str


# =============================================================================
# Customer (minimal - for orders)
# =============================================================================


@dataclass(frozen=True)
class Customer:
    """Customer for orders."""

    id: UUID
    tenant_id: UUID
    phone: str
    created_at: datetime
    updated_at: datetime
    name: str | None = None
    email: str | None = None
    default_address: str | None = None
    default_area: str | None = None
    is_active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
