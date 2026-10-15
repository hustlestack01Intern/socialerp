"""
Reservation SQLAlchemy Models

Database models for stock reservations with RLS support.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    UUID,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.models.base import Base

# =============================================================================
# Enums
# =============================================================================


class ReservationStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    RELEASED = "released"
    FULFILLED = "fulfilled"


class ReservationReason(str, enum.Enum):
    ORDER_CREATED = "order_created"
    MANUAL = "manual"


# =============================================================================
# Reservation Model
# =============================================================================


class Reservation(Base):
    """Stock reservation - ephemeral claim on stock."""

    __tablename__ = "reservations"

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
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(ReservationStatus, native_enum=False),
        nullable=False,
        default=ReservationStatus.ACTIVE,
        server_default="active",
    )
    reason: Mapped[str] = mapped_column(
        Enum(ReservationReason, native_enum=False),
        nullable=False,
        default=ReservationReason.ORDER_CREATED,
        server_default="order_created",
    )
    reference_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    reference_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="order",
        server_default="order",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Indexes & Constraints
    __table_args__ = (
        Index("ix_reservations_tenant_status", "tenant_id", "status"),
        Index("ix_reservations_tenant_expires", "tenant_id", "expires_at"),
        Index("ix_reservations_tenant_product", "tenant_id", "product_id"),
        Index("ix_reservations_reference", "reference_id", "reference_type"),
    )


__all__ = [
    "Reservation",
    "ReservationReason",
    "ReservationStatus",
]
