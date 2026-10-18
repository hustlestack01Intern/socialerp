"""
CRM memory models (Phase 8).

Three tables backing "a customer database that remembers history, preferences
and behaviour" (AGENTS.md §10), with the project's privacy constraints baked in:

- :class:`CustomerEvent` — append-only event log. The service exposes no
  update/delete path; the migration adds a DB trigger that rejects UPDATE/DELETE
  as a production backstop. Events summarise; they do not store raw message
  bodies wholesale (data minimisation).
- :class:`ConsentRecord` — explicit, sourced consent per purpose; the latest
  record per (customer, purpose) wins. Marketing requires a granted record.
- :class:`CustomerTrait` — versioned, evidence-backed derived traits. The
  service has an allow-list of trait names and refuses anything else, so no
  inferred sensitive attribute can be stored (health/religion/ethnicity/…).

Every table carries ``tenant_id``; RLS policy + grants live in migration 0012.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    UUID,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.models.base import Base


class CustomerEvent(Base):
    """Append-only customer event log (immutable)."""

    __tablename__ = "customer_events"

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
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    summary: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    data_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"),
    )
    # At-least-once producers (outbox handlers) must not double-append: a
    # non-null dedupe_key is unique per tenant.
    dedupe_key: Mapped[str | None] = mapped_column(String(150), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_customer_events_tenant_dedupe"),
        Index("ix_customer_events_tenant_customer_time", "tenant_id", "customer_id", "occurred_at"),
        Index("ix_customer_events_tenant_type", "tenant_id", "event_type"),
    )


class ConsentRecord(Base):
    """One consent decision for a (customer, purpose). Latest wins."""

    __tablename__ = "consent_records"

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
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    evidence: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_consent_records_tenant_customer_purpose", "tenant_id", "customer_id", "purpose"),
    )


class CustomerTrait(Base):
    """A versioned, evidence-backed derived trait for a customer."""

    __tablename__ = "customer_traits"

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
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default="1",
    )
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "customer_id", "name", "version", name="uq_customer_traits_version",
        ),
        Index("ix_customer_traits_tenant_customer_name", "tenant_id", "customer_id", "name"),
    )


# =============================================================================
# Customer Identity Model (Cross-Channel Linking)
# =============================================================================


class CustomerIdentity(Base):
    """
    Links a unified CRM customer to their identities across channels.

    Enables cross-channel customer view while respecting Kenya DPA consent requirements.
    Each link carries explicit consent evidence per AGENTS.md §10.
    """

    __tablename__ = "customer_identities"

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
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # "whatsapp" | "instagram" | "tiktok"
    channel_identity: Mapped[str] = mapped_column(
        String(255), nullable=False,
    )  # phone / ig_handle / tiktok_user_id
    channel_identity_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
    )  # "phone" | "handle" | "user_id"

    # Consent evidence (per Kenya DPA explicit consent requirement)
    consent_given_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    consent_source: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
    )  # "owner_linked" | "customer_provided" | "oauth_flow"
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Primary identity flag (one per channel per customer)
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
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

    # Indexes & Constraints
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "customer_id", "channel", "channel_identity",
            name="uq_customer_identities_tenant_customer_channel",
        ),
        Index("ix_customer_identities_tenant_customer", "tenant_id", "customer_id"),
        Index("ix_customer_identities_tenant_channel_identity", "tenant_id", "channel", "channel_identity"),
    )


__all__ = [
    "ConsentRecord",
    "CustomerEvent",
    "CustomerIdentity",
    "CustomerTrait",
]
