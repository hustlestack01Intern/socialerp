"""
Onboarding Wizard Data Model

DB-backed progress tracking for the 30-minute onboarding wizard.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.models.base import Base


class OnboardingState(str, enum.Enum):
    """State of the onboarding wizard."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class FallbackType(str, enum.Enum):
    """Types of fallback tracks during onboarding."""

    MANUAL_PAYMENT_MODE = "MANUAL_PAYMENT_MODE"
    MANUAL_CATALOG = "MANUAL_CATALOG"
    NO_META_ASSETS = "NO_META_ASSETS"
    NO_DARAJA_TILL = "NO_DARAJA_TILL"


class OnboardingWizardProgress(Base):
    """
    Tracks a tenant's progress through the onboarding wizard.

    One row per tenant. Stores per-step timings, fallback flags,
    and completion state.
    """

    __tablename__ = "onboarding_wizard_progress"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Wizard state
    state: Mapped[str] = mapped_column(
        Enum(OnboardingState, native_enum=False),
        nullable=False,
        default=OnboardingState.NOT_STARTED,
        server_default="NOT_STARTED",
    )

    current_step: Mapped[int] = mapped_column(
        default=0, nullable=False,
    )  # 0 = not started, 1-6 = step number

    # Per-step timing data (JSONB)
    # {
    #   "1": {
    #     "started_at": "2026-10-18T10:00:00Z",
    #     "completed_at": "2026-10-18T10:03:15Z",
    #     "duration_ms": 195000,
    #     "status": "COMPLETED",
    #     "attempts": 1,
    #     "paused_at": null,
    #     "resumed_at": null,
    #     "fallback": null
    #   },
    #   "2": {
    #     "started_at": "2026-10-18T10:03:15Z",
    #     "completed_at": null,
    #     "duration_ms": null,
    #     "status": "IN_PROGRESS",
    #     "attempts": 1,
    #     "paused_at": null,
    #     "resumed_at": null,
    #     "fallback": {
    #       "type": "MANUAL_PAYMENT_MODE",
    #       "triggered_at": "2026-10-18T10:10:00Z",
    #       "reason": "no_daraja_credentials"
    #     }
    #   }
    # }
    step_timings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Aggregated fallback flags (for quick queries)
    fallback_flags: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Launch approval (zero-interference gate)
    launch_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Completion timestamp
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Standard timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default="now()",
    )

    # RLS
    __table_args__ = (
        Index("ix_onboarding_wizard_progress_state", "state"),
    )


# =============================================================================
# Onboarding Events (for audit/outbox)
# =============================================================================

# These are defined in events.py but listed here for reference
# OnboardingStepStarted
# OnboardingStepCompleted
# OnboardingWizardCompleted
# OnboardingFallbackTriggered
