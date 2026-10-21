"""
Billing SQLAlchemy models (trial spine, plan M1.2 / ADR 0023).

Two concerns, deliberately separate:

* :class:`Subscription` — one row per tenant. It records the plan
  (``trial|starter|professional|enterprise``), status, and the trial/period
  boundary. It is the single authority for *entitlement*.
* :class:`UsageCounter` — a monotonic integer counter per
  ``(tenant, metric, period)``. It is the single authority for *caps*.
* :class:`UsageEvent` — an append-only ledger of the events that produced the
  counters. The counter is ``sum(events)`` per key; the unique
  ``(tenant_id, metric, source_event_id)`` row makes ``record_usage`` idempotent
  under webhook replay (U66 / INV11). The counter is derived, never a source of
  truth that can double-credit.

Period keys are UTC (``DATE`` for daily, ``YYYY-MM`` for monthly) and the trial
boundary is an absolute ``timestamptz`` instant; Nairobi is display-only (U66).
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
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.models.base import Base


class PlanTier(str, enum.Enum):
    TRIAL = "trial"
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, enum.Enum):
    # Created at signup, before a channel is connected: no trial clock yet.
    PENDING = "pending"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class Subscription(Base):
    """One row per tenant: the entitlement authority."""

    __tablename__ = "subscriptions"

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
    plan: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PlanTier.TRIAL.value,
        server_default=PlanTier.TRIAL.value,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SubscriptionStatus.PENDING.value,
        server_default=SubscriptionStatus.PENDING.value,
    )
    # When the current period (trial) ends. NULL while `pending` (never
    # connected): the trial clock has not started (U67 / INV2).
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    # Human-approved extension bookkeeping (U60/U68): at most one 7-day
    # extension, audit-logged.
    extension_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    last_extension_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Recorded evidence for a manual plan change (M-Pesa transaction code, etc.).
    plan_change_evidence: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_subscriptions_tenant"),
        Index("ix_subscriptions_status", "status"),
    )


class UsageCounter(Base):
    """Monotonic integer counter per ``(tenant, metric, period)`` (INV11/INV15)."""

    __tablename__ = "usage_counters"

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
    # e.g. "messages_inbound", "llm_calls", "llm_spend_usd_micros".
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    # UTC period key: a DATE rendered "YYYY-MM-DD" (daily) or "YYYY-MM" (monthly).
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "metric", "period", name="uq_usage_counters_key"),
    )


class BillingPaymentReference(Base):
    """Platform-scoped ledger of subscription payment references (F2 / U68).

    One row per accepted M-Pesa code used to pay for a NON-trial plan change.
    Deliberately **without RLS**: the whole point is cross-tenant uniqueness —
    one code can pay for at most one plan change, ever. No customer PII: an
    M-Pesa confirmation code is a merchant-facing transaction id.
    """

    __tablename__ = "billing_payment_references"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    reference: Mapped[str] = mapped_column(String(30), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_kes: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("reference", name="uq_billing_payment_references_ref"),
    )


class UsageEvent(Base):
    """Append-only ledger: counters are the sum of these (INV11, U66).

    ``source_event_id`` is a permanent, provider-supplied id
    (e.g. ``provider_message_id``). The unique constraint makes
    ``record_usage`` idempotent under replay — unlike ``IdempotencyRecord``,
    whose 24h TTL would let a late replay double-credit.
    """

    __tablename__ = "usage_events"

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
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    # The increment this event contributes (usually 1, or micro-USD for spend).
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    source_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "metric",
            "source_event_id",
            name="uq_usage_events_source",
        ),
        Index("ix_usage_events_tenant_metric_period", "tenant_id", "metric", "period"),
    )


__all__ = [
    "BillingPaymentReference",
    "PlanTier",
    "Subscription",
    "SubscriptionStatus",
    "UsageCounter",
    "UsageEvent",
]
