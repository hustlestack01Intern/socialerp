"""
Stock SQLAlchemy Models

Database models for event-sourced stock ledger with RLS support.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    UUID,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.models.base import Base

# =============================================================================
# Enums
# =============================================================================


class StockMovementType(str, enum.Enum):
    RECEIVE = "receive"
    RESERVE = "reserve"
    RELEASE = "release"
    SELL = "sell"
    ADJUST = "adjust"
    RETURN = "return"


class StockMovementReason(str, enum.Enum):
    PURCHASE_ORDER = "purchase_order"
    ORDER_CREATED = "order_created"
    PAYMENT_CONFIRMED = "payment_confirmed"
    ORDER_CANCELLED = "order_cancelled"
    RESERVATION_EXPIRED = "reservation_expired"
    MANUAL_COUNT = "manual_count"
    DAMAGED = "damaged"
    RETURNED = "returned"
    OTHER = "other"


# =============================================================================
# Stock Movement Model
# =============================================================================


class StockMovement(Base):
    """Immutable stock movement record - append-only ledger entry."""

    __tablename__ = "stock_movements"

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
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("product_variants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    movement_type: Mapped[str] = mapped_column(
        Enum(StockMovementType, native_enum=False),
        nullable=False,
    )
    quantity_change: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(
        Enum(StockMovementReason, native_enum=False),
        nullable=False,
    )
    reference_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    reference_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    previous_available: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    new_available: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    unit_cost_cents: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    notes: Mapped[str] = mapped_column(
        Text,
        nullable=True,
        default="",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Indexes
    __table_args__ = (
        Index("ix_stock_movements_tenant_product", "tenant_id", "product_id"),
        Index("ix_stock_movements_tenant_variant", "tenant_id", "variant_id"),
        Index("ix_stock_movements_tenant_type", "tenant_id", "movement_type"),
        Index("ix_stock_movements_tenant_created", "tenant_id", "created_at"),
        Index("ix_stock_movements_reference", "reference_id", "reference_type"),
    )


__all__ = [
    "StockMovement",
    "StockMovementReason",
    "StockMovementType",
]
