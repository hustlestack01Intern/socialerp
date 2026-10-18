"""
Onboarding Wizard Domain Events

Events emitted by the onboarding wizard for audit and outbox processing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from packages.core.domain import TenantId
from packages.core.events.base import DomainEvent


@dataclass
class OnboardingStepStarted(DomainEvent):
    """Onboarding step started."""

    tenant_id: TenantId
    step: int
    started_at: datetime
    attempt: int = 1

    @property
    def event_type(self) -> str:
        return "onboarding.step_started"


@dataclass
class OnboardingStepCompleted(DomainEvent):
    """Onboarding step completed (successfully or with fallback)."""

    tenant_id: TenantId
    step: int
    completed_at: datetime
    duration_ms: int
    status: str  # COMPLETED, FALLBACK
    fallback_type: str | None = None
    fallback_reason: str | None = None

    @property
    def event_type(self) -> str:
        return "onboarding.step_completed"


@dataclass
class OnboardingWizardCompleted(DomainEvent):
    """Onboarding wizard fully completed."""

    tenant_id: TenantId
    completed_at: datetime
    total_duration_ms: int
    slo_met: bool
    fallback_count: int
    step_durations: dict[str, int]  # step -> ms

    @property
    def event_type(self) -> str:
        return "onboarding.wizard_completed"


@dataclass
class OnboardingFallbackTriggered(DomainEvent):
    """Fallback triggered during onboarding step."""

    tenant_id: TenantId
    step: int
    fallback_type: str  # e.g., "MANUAL_PAYMENT_MODE"
    triggered_at: datetime
    reason: str

    @property
    def event_type(self) -> str:
        return "onboarding.fallback_triggered"


@dataclass
class OnboardingLaunchApproved(DomainEvent):
    """Owner approved launch (zero-interference gate opened)."""

    tenant_id: TenantId
    approved_at: datetime
    approved_by: UUID | None = None  # Owner user ID

    @property
    def event_type(self) -> str:
        return "onboarding.launch_approved"


@dataclass
class OnboardingWizardAborted(DomainEvent):
    """Onboarding wizard aborted by owner."""

    tenant_id: TenantId
    aborted_at: datetime
    reason: str | None = None
    step_at_abort: int | None = None

    @property
    def event_type(self) -> str:
        return "onboarding.wizard_aborted"