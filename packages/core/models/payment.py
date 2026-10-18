"""
Payment SQLAlchemy Models.

Persists STK Push attempts and their outcomes. The payments table is
tenant-scoped and protected by Row-Level Security (see ADR 0010).

The order itself remains the source of truth for fulfilment state; this table
is the payment attempt ledger (one order may have several attempts).
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
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.models.base import Base


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    """Persist enum *values* (lowercase) to match the Alembic String columns."""
    return [member.value for member in enum_cls]


class PaymentRecordStatus(str, enum.Enum):
    """Lifecycle of a single payment attempt."""

    INITIATED = "initiated"  # request sent to provider, awaiting callback
    PENDING = "pending"  # provider says still processing
    COMPLETED = "completed"  # confirmed paid and amount matched
    FAILED = "failed"  # terminal failure (funds, wrong PIN, timeout)
    CANCELLED = "cancelled"  # customer cancelled
    PARTIAL = "partial"  # less than expected amount received
    REFUNDED = "refunded"  # reversed (human-approved)


class PaymentMethodType(str, enum.Enum):
    """How the customer is paying."""

    MPESA_STK = "mpesa_stk"
    MPESA_PAY_ON_DELIVERY = "mpesa_pod"
    CASH_ON_DELIVERY = "cash_on_delivery"
    MANUAL = "manual"


class Payment(Base):
    """A single M-Pesa (or other) payment attempt against an order."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="daraja")
    method: Mapped[str] = mapped_column(
        Enum(PaymentMethodType, native_enum=False, values_callable=_enum_values),
        nullable=False,
        default=PaymentMethodType.MPESA_STK,
        server_default="mpesa_stk",
    )
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        Enum(PaymentRecordStatus, native_enum=False, values_callable=_enum_values),
        nullable=False,
        default=PaymentRecordStatus.INITIATED,
        server_default="initiated",
        index=True,
    )

    # Provider correlation identifiers.
    checkout_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    merchant_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mpesa_receipt_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    result_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}",
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
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Postgres treats NULLs as distinct in unique indexes, so rows whose
        # checkout_request_id is not yet known never collide.
        Index(
            "uq_payments_tenant_checkout_request_id",
            "tenant_id",
            "checkout_request_id",
            unique=True,
        ),
        Index("ix_payments_tenant_status", "tenant_id", "status"),
        Index("ix_payments_tenant_order", "tenant_id", "order_id"),
        Index("ix_payments_tenant_created", "tenant_id", "created_at"),
    )


__all__ = [
    "Payment",
    "PaymentMethodType",
    "PaymentRecordStatus",
]
