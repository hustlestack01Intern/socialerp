"""
Onboarding Wizard Domain Events

Events emitted by the onboarding wizard for audit and outbox processing.

They follow the same shape as the other domain events (`packages.core.events`):
an explicit ``__init__`` that calls ``super().__init__`` so the base sets
``event_id``/``occurred_at``/``event_type``, plus a ``_payload`` implementation.
``event_type`` is the class name (set by the base), matching every other event
in this codebase. Use ``__slots__`` to keep events immutable and lightweight.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from packages.core.domain import TenantId
from packages.core.events import DomainEvent


class OnboardingStepStarted(DomainEvent):
    """Onboarding step started."""

    __slots__ = ("attempt", "started_at", "step")

    def __init__(
        self,
        tenant_id: TenantId,
        step: int,
        started_at: datetime,
        attempt: int = 1,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.step = step
        self.started_at = started_at
        self.attempt = attempt

    def _payload(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "started_at": self.started_at.isoformat(),
            "attempt": self.attempt,
        }


class OnboardingStepCompleted(DomainEvent):
    """Onboarding step completed (successfully or with fallback)."""

    __slots__ = (
        "completed_at",
        "duration_ms",
        "fallback_reason",
        "fallback_type",
        "status",
        "step",
    )

    def __init__(
        self,
        tenant_id: TenantId,
        step: int,
        completed_at: datetime,
        duration_ms: int,
        status: str,
        fallback_type: str | None = None,
        fallback_reason: str | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.step = step
        self.completed_at = completed_at
        self.duration_ms = duration_ms
        self.status = status
        self.fallback_type = fallback_type
        self.fallback_reason = fallback_reason

    def _payload(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "completed_at": self.completed_at.isoformat(),
            "duration_ms": self.duration_ms,
            "status": self.status,
            "fallback_type": self.fallback_type,
            "fallback_reason": self.fallback_reason,
        }


class OnboardingWizardCompleted(DomainEvent):
    """Onboarding wizard fully completed."""

    __slots__ = (
        "completed_at",
        "fallback_count",
        "slo_met",
        "step_durations",
        "total_duration_ms",
    )

    def __init__(
        self,
        tenant_id: TenantId,
        completed_at: datetime,
        total_duration_ms: int,
        slo_met: bool,
        fallback_count: int,
        step_durations: dict[str, int],
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.completed_at = completed_at
        self.total_duration_ms = total_duration_ms
        self.slo_met = slo_met
        self.fallback_count = fallback_count
        self.step_durations = step_durations

    def _payload(self) -> dict[str, Any]:
        return {
            "completed_at": self.completed_at.isoformat(),
            "total_duration_ms": self.total_duration_ms,
            "slo_met": self.slo_met,
            "fallback_count": self.fallback_count,
            "step_durations": self.step_durations,
        }


class OnboardingFallbackTriggered(DomainEvent):
    """Fallback triggered during onboarding step."""

    __slots__ = ("fallback_type", "reason", "step", "triggered_at")

    def __init__(
        self,
        tenant_id: TenantId,
        step: int,
        fallback_type: str,
        triggered_at: datetime,
        reason: str,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.step = step
        self.fallback_type = fallback_type
        self.triggered_at = triggered_at
        self.reason = reason

    def _payload(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "fallback_type": self.fallback_type,
            "triggered_at": self.triggered_at.isoformat(),
            "reason": self.reason,
        }


class OnboardingLaunchApproved(DomainEvent):
    """Owner approved launch (zero-interference gate opened)."""

    __slots__ = ("approved_at", "approved_by")

    def __init__(
        self,
        tenant_id: TenantId,
        approved_at: datetime,
        approved_by: UUID | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.approved_at = approved_at
        self.approved_by = approved_by

    def _payload(self) -> dict[str, Any]:
        return {
            "approved_at": self.approved_at.isoformat(),
            "approved_by": str(self.approved_by) if self.approved_by else None,
        }


class OnboardingWizardAborted(DomainEvent):
    """Onboarding wizard aborted by owner."""

    __slots__ = ("aborted_at", "reason", "step_at_abort")

    def __init__(
        self,
        tenant_id: TenantId,
        aborted_at: datetime,
        reason: str | None = None,
        step_at_abort: int | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.aborted_at = aborted_at
        self.reason = reason
        self.step_at_abort = step_at_abort

    def _payload(self) -> dict[str, Any]:
        return {
            "aborted_at": self.aborted_at.isoformat(),
            "reason": self.reason,
            "step_at_abort": self.step_at_abort,
        }
