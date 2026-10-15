"""
Order SQLAlchemy Models

Database models for orders with RLS support and strict state machine.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    UUID,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.models.base import Base

# =============================================================================
# Enums
# =============================================================================


class OrderStatus(str, enum.Enum):
    DRAFT = "draft"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    PACKED = "packed"
    OUT_FOR_DELIVERY = "out_for_delivery"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    FAILED_DELIVERY = "failed_delivery"


class OrderType(str, enum.Enum):
    STANDARD = "standard"
    PREORDER = "preorder"
    SUBSCRIPTION = "subscription"


class PaymentMethod(str, enum.Enum):
    MPESA_STK = "mpesa_stk"
    MPESA_PAY_ON_DELIVERY = "mpesa_pod"
    CASH_ON_DELIVERY = "cash_on_delivery"
    MANUAL = "manual"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


# =============================================================================
# Order Model
# =============================================================================


class Order(Base):
    """Order with strict state machine."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Prices in cents (KES)
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivery_fee_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(
        Enum(OrderStatus, native_enum=False),
        nullable=False,
        default=OrderStatus.DRAFT,
        server_default="draft",
    )
    order_type: Mapped[str] = mapped_column(
        Enum(OrderType, native_enum=False),
        nullable=False,
        default=OrderType.STANDARD,
        server_default="standard",
    )

    # Delivery info (JSON)
    delivery_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_area: Mapped[str | None] = mapped_column(String(100), nullable=True)
    delivery_lat: Mapped[float | None] = mapped_column(nullable=True)
    delivery_lng: Mapped[float | None] = mapped_column(nullable=True)
    delivery_instructions: Mapped[str] = mapped_column(Text, nullable=True)
    estimated_delivery_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Payment info
    payment_method: Mapped[str] = mapped_column(
        Enum(PaymentMethod, native_enum=False),
        nullable=False,
        default=PaymentMethod.MPESA_STK,
        server_default="mpesa_stk",
    )
    payment_status: Mapped[str] = mapped_column(
        Enum(PaymentStatus, native_enum=False),
        nullable=False,
        default=PaymentStatus.PENDING,
        server_default="pending",
    )
    payment_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_amount_cents: Mapped[int] = mapped_column(Integer, nullable=True)
    payment_paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Status timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    packed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata
    notes: Mapped[str] = mapped_column(Text, nullable=True, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Relationships
    items: Mapped[list[OrderItem]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    # Indexes & Constraints
    __table_args__ = (
        Index("ix_orders_tenant_status", "tenant_id", "status"),
        Index("ix_orders_tenant_customer", "tenant_id", "customer_id"),
        Index("ix_orders_tenant_created", "tenant_id", "created_at"),
        Index("ix_orders_tenant_status_created", "tenant_id", "status", "created_at"),
    )


# =============================================================================
# Order Item Model
# =============================================================================


class OrderItem(Base):
    """Single line item in an order."""

    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="SET NULL"),
        nullable=True,
    )
    sku: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    # Relationships
    order: Mapped[Order] = relationship(
        "Order",
        back_populates="items",
        lazy="joined",
    )

    # Indexes
    # NOTE: order_id and tenant_id carry index=True on the columns, which
    # already creates ix_order_items_order_id / ix_order_items_tenant_id.
    # Declaring them again here produced duplicate index names.
    __table_args__ = ()


# =============================================================================
# Customer Model (minimal - for orders)
# =============================================================================


class Customer(Base):
    """Customer for orders."""

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_area: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    # Relationships
    orders: Mapped[list[Order]] = relationship(
        "Order",
        back_populates="customer",
        lazy="dynamic",
    )

    __table_args__ = (UniqueConstraint("tenant_id", "phone", name="uq_customers_tenant_phone"),)


# =============================================================================
# Add customer relationship to Order
# =============================================================================

# Add customer relationship to Order class
Order.customer = relationship(
    "Customer",
    back_populates="orders",
    lazy="joined",
)


# =============================================================================
# Export
# =============================================================================

__all__ = [
    "Customer",
    "Order",
    "OrderItem",
    "OrderStatus",
    "OrderType",
    "PaymentMethod",
    "PaymentStatus",
]
