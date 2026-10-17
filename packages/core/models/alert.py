"""
Owner alert SQLAlchemy model.

A durable, tenant-scoped record of something the owner must act on (money
captured on a non-collectable order, a reconciliation mismatch, a failed
delivery needing triage, …). This replaces log-only alerting: a log line can be
lost to rotation, an ``owner_alerts`` row is queryable and acknowledgeable.

Every table carries ``tenant_id`` and is covered by the standard tenant
isolation policy (see migration ``0011_owner_alerts`` and ADR 0002/ADR 0010).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    UUID,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.models.base import Base


class AlertSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class OwnerAlert(Base):
    """A durable alert for the business owner."""

    __tablename__ = "owner_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="warning", server_default="warning"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    resource_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    # At-least-once outbox handlers must not create duplicate alerts on replay:
    # a non-null dedupe_key is unique per tenant.
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open"
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_owner_alerts_tenant_dedupe"),
        Index("ix_owner_alerts_tenant_status", "tenant_id", "status"),
        Index("ix_owner_alerts_tenant_created", "tenant_id", "created_at"),
    )


__all__ = ["AlertSeverity", "AlertStatus", "OwnerAlert"]
