"""
Onboarding Wizard Service

Business logic for the instrumented onboarding wizard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import get_settings
from packages.core.domain import TenantId
from packages.core.models.core import Tenant
from packages.core.onboarding.model import (
    FallbackType,
    OnboardingState,
    OnboardingWizardProgress,
)
from packages.core.onboarding.steps import (
    FALLBACK_TYPES,
    STEP_CONFIG,
    get_total_target_seconds,
    is_valid_step,
)


class OnboardingWizardService:
    """
    Service for managing the onboarding wizard progress.

    Provides methods to start/complete steps, track timings,
    handle fallbacks, and enforce the zero-interference gate.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    # =============================================================================
    # Core Progress Methods
    # =============================================================================

    async def get_progress(self, tenant_id: TenantId) -> OnboardingWizardProgress | None:
        """Get onboarding progress for a tenant."""
        result = await self.session.execute(
            select(OnboardingWizardProgress).where(
                OnboardingWizardProgress.tenant_id == tenant_id,
            ),
        )
        return result.scalar_one_or_none()

    async def ensure_progress(self, tenant_id: TenantId) -> OnboardingWizardProgress:
        """Get or create progress record for a tenant."""
        progress = await self.get_progress(tenant_id)
        if progress is None:
            progress = OnboardingWizardProgress(tenant_id=tenant_id)
            self.session.add(progress)
            await self.session.flush()
        return progress

    # =============================================================================
    # Step Management
    # =============================================================================

    async def start_step(
        self, tenant_id: TenantId, step: int,
    ) -> OnboardingWizardProgress:
        """
        Mark a step as started.

        Args:
            tenant_id: Tenant ID
            step: Step number (1-6)

        Returns:
            Updated progress record

        Raises:
            ValueError: If step is invalid or wizard state doesn't allow it
        """
        if not is_valid_step(step):
            raise ValueError(f"Invalid step: {step}")

        progress = await self.ensure_progress(tenant_id)

        # Validate state transitions
        if progress.state == OnboardingState.NOT_STARTED:
            if step != 1:
                raise ValueError("Must start with step 1")
            progress.state = OnboardingState.IN_PROGRESS
        elif progress.state == OnboardingState.IN_PROGRESS:
            # Can only start the current step or next step
            if step != progress.current_step and step != progress.current_step + 1:
                raise ValueError(f"Cannot skip to step {step} from {progress.current_step}")
        elif progress.state in (OnboardingState.COMPLETED, OnboardingState.FAILED, OnboardingState.ABORTED):
            raise ValueError(f"Wizard already {progress.state.lower()}")

        now = datetime.now(UTC)

        # Initialize step timing if not present
        step_key = str(step)
        if step_key not in progress.step_timings:
            progress.step_timings[step_key] = {}

        progress.step_timings[step_key].update({
            "started_at": now.isoformat(),
            "completed_at": None,
            "duration_ms": None,
            "status": "IN_PROGRESS",
            "attempts": progress.step_timings[step_key].get("attempts", 0) + 1,
            "paused_at": None,
            "resumed_at": None,
            "fallback": None,
        })

        progress.current_step = step
        progress.updated_at = now

        await self.session.flush()

        # Emit event (via outbox in real implementation)
        # OnboardingStepStarted(tenant_id, step, started_at=now)

        return progress

    async def complete_step(
        self,
        tenant_id: TenantId,
        step: int,
        fallback_type: FallbackType | None = None,
        fallback_reason: str | None = None,
    ) -> OnboardingWizardProgress:
        """
        Mark a step as completed.

        Args:
            tenant_id: Tenant ID
            step: Step number (1-6)
            fallback_type: Optional fallback type if step used fallback
            fallback_reason: Human-readable reason for fallback

        Returns:
            Updated progress record
        """
        if not is_valid_step(step):
            raise ValueError(f"Invalid step: {step}")

        progress = await self.get_progress(tenant_id)
        if progress is None:
            raise ValueError("No onboarding progress found for tenant")

        if progress.current_step != step:
            raise ValueError(f"Step {step} is not the current step ({progress.current_step})")

        now = datetime.now(UTC)
        step_key = str(step)

        if step_key not in progress.step_timings:
            raise ValueError(f"Step {step} was not started")

        step_timing = progress.step_timings[step_key]

        # Calculate duration
        started_at = step_timing.get("started_at")
        if started_at:
            start_time = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            duration_ms = int((now - start_time).total_seconds() * 1000)
        else:
            duration_ms = 0

        # Build fallback info if applicable
        fallback_info = None
        if fallback_type:
            fallback_info = {
                "type": fallback_type.value,
                "triggered_at": now.isoformat(),
                "reason": fallback_reason,
            }

            # Update aggregated fallback flags
            fb_key = fallback_type.value
            if fb_key not in progress.fallback_flags:
                progress.fallback_flags[fb_key] = {
                    "step": step,
                    "triggered_at": now.isoformat(),
                    "reason": fallback_reason,
                }
            else:
                # Update with latest trigger
                progress.fallback_flags[fb_key]["triggered_at"] = now.isoformat()
                progress.fallback_flags[fb_key]["reason"] = fallback_reason

            # Emit fallback event
            # OnboardingFallbackTriggered(tenant_id, step, fallback_type, now)

        step_timing.update({
            "completed_at": now.isoformat(),
            "duration_ms": duration_ms,
            "status": "COMPLETED",
            "fallback": fallback_info,
        })

        progress.updated_at = now

        # If this is the last step, mark wizard as completed
        if step == 6:
            progress.state = OnboardingState.COMPLETED
            progress.completed_at = now
            progress.current_step = 6

            # Emit completion event
            # OnboardingWizardCompleted(tenant_id, total_duration_ms, now)

        await self.session.flush()
        return progress

    async def pause_step(self, tenant_id: TenantId, step: int) -> OnboardingWizardProgress:
        """Pause the current step (owner leaves wizard)."""
        progress = await self.get_progress(tenant_id)
        if progress is None or progress.current_step != step:
            raise ValueError("Invalid step to pause")

        step_key = str(step)
        if step_key in progress.step_timings:
            progress.step_timings[step_key]["paused_at"] = datetime.now(UTC).isoformat()

        await self.session.flush()
        return progress

    async def resume_step(self, tenant_id: TenantId, step: int) -> OnboardingWizardProgress:
        """Resume a paused step."""
        progress = await self.get_progress(tenant_id)
        if progress is None or progress.current_step != step:
            raise ValueError("Invalid step to resume")

        step_key = str(step)
        if step_key in progress.step_timings:
            progress.step_timings[step_key]["resumed_at"] = datetime.now(UTC).isoformat()

        await self.session.flush()
        return progress

    # =============================================================================
    # Fallback Handling
    # =============================================================================

    async def trigger_fallback(
        self,
        tenant_id: TenantId,
        step: int,
        fallback_type: FallbackType,
        reason: str,
    ) -> OnboardingWizardProgress:
        """
        Record a fallback for a step.

        Fallbacks don't block go-live; they record the deviation
        and allow the owner to continue.
        """
        if not is_valid_step(step):
            raise ValueError(f"Invalid step: {step}")

        progress = await self.get_progress(tenant_id)
        if progress is None:
            raise ValueError("No onboarding progress found")

        fb_info = FALLBACK_TYPES.get(fallback_type.value)
        if not fb_info:
            raise ValueError(f"Unknown fallback type: {fallback_type}")

        if fb_info["step"] != step:
            raise ValueError(f"Fallback {fallback_type} not applicable to step {step}")

        # Record fallback by completing step with fallback info
        return await self.complete_step(
            tenant_id,
            step,
            fallback_type=fallback_type,
            fallback_reason=reason,
        )

    # =============================================================================
    # Launch Approval (Zero-Interference Gate)
    # =============================================================================

    async def approve_launch(self, tenant_id: TenantId) -> OnboardingWizardProgress:
        """
        Owner approves launch (step 6 completion).

        Sets launch_approved_at timestamp which enables the
        zero-interference gate for customer messaging.
        """
        progress = await self.get_progress(tenant_id)
        if progress is None:
            raise ValueError("No onboarding progress found")

        if progress.state != OnboardingState.COMPLETED:
            raise ValueError("Wizard must be completed before launch approval")

        now = datetime.now(UTC)
        progress.launch_approved_at = now
        progress.updated_at = now

        await self.session.flush()

        # Update tenant settings to enable Instagram/WhatsApp channels
        tenant = await self.session.get(Tenant, tenant_id)
        if tenant:
            tenant.settings["launch_approved"] = True
            tenant.settings["instagram_enabled"] = True

        return progress

    async def is_launch_approved(self, tenant_id: TenantId) -> bool:
        """Check if launch has been approved for a tenant."""
        progress = await self.get_progress(tenant_id)
        if progress is None:
            return False
        return progress.launch_approved_at is not None

    # =============================================================================
    # Metrics & Reporting
    # =============================================================================

    def calculate_total_duration(self, progress: OnboardingWizardProgress) -> int | None:
        """
        Calculate total onboarding duration in milliseconds.

        Returns None if wizard not completed.
        """
        if progress.state != OnboardingState.COMPLETED:
            return None

        total = 0
        for _step_key, timing in progress.step_timings.items():
            if timing.get("duration_ms"):
                total += timing["duration_ms"]
        return total

    def calculate_step_duration(self, progress: OnboardingWizardProgress, step: int) -> int | None:
        """Calculate duration for a specific step in milliseconds."""
        step_key = str(step)
        timing = progress.step_timings.get(step_key)
        if not timing or not timing.get("duration_ms"):
            return None
        return int(timing["duration_ms"])

    def get_slo_status(self, progress: OnboardingWizardProgress) -> dict[str, Any]:
        """
        Get SLO compliance status for the wizard.

        Returns:
            {
                "total_duration_ms": int | None,
                "slo_met": bool | None,
                "step_details": {
                    "1": {"target_ms": 180000, "actual_ms": 195000, "met": False},
                    ...
                },
                "fallback_count": int,
            }
        """
        if progress.state != OnboardingState.COMPLETED:
            return {
                "total_duration_ms": None,
                "slo_met": None,
                "step_details": {},
                "fallback_count": 0,
            }

        total = self.calculate_total_duration(progress) or 0
        slo_met = total <= get_total_target_seconds() * 1000

        step_details = {}
        for step_num, config in STEP_CONFIG.items():
            step_key = str(step_num)
            timing = progress.step_timings.get(step_key, {})
            actual_ms = timing.get("duration_ms")
            target_ms = config.target_seconds * 1000
            step_details[str(step_num)] = {
                "target_ms": target_ms,
                "actual_ms": actual_ms,
                "met": actual_ms is not None and actual_ms <= target_ms,
                "status": timing.get("status"),
                "fallback": timing.get("fallback"),
            }

        return {
            "total_duration_ms": total,
            "slo_met": slo_met,
            "step_details": step_details,
            "fallback_count": len(progress.fallback_flags),
        }


# =============================================================================
# Helper Functions
# =============================================================================


async def get_or_create_wizard(tenant_id: TenantId, session: AsyncSession) -> OnboardingWizardProgress:
    """Get or create onboarding wizard progress for a tenant."""
    service = OnboardingWizardService(session)
    return await service.ensure_progress(tenant_id)
