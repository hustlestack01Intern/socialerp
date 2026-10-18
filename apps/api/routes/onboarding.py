"""
Onboarding Wizard API Routes

Endpoints for managing the 30-minute instrumented onboarding wizard.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.dependencies import get_current_tenant_id
from packages.core.database import get_db_session
from packages.core.domain import TenantId
from packages.core.models.core import Tenant
from packages.core.onboarding.service import OnboardingWizardService
from packages.core.onboarding.steps import (
    FALLBACK_TYPES,
    STEP_CONFIG,
    get_total_target_seconds,
    is_valid_step,
)
from packages.core.onboarding.model import FallbackType

router = APIRouter(prefix="/api/v1/onboarding", tags=["Onboarding Wizard"])


# =============================================================================
# Dependencies
# =============================================================================


from collections.abc import AsyncGenerator


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def get_tenant_id(tenant_id: str = Depends(get_current_tenant_id)) -> str:
    return tenant_id


def get_wizard_service(db: AsyncSession = Depends(get_db)) -> OnboardingWizardService:
    return OnboardingWizardService(db)


# =============================================================================
# Wizard State Endpoints
# =============================================================================


@router.get("")
async def get_onboarding_progress(
    service: OnboardingWizardService = Depends(get_wizard_service),
    tenant_id: TenantId = Depends(get_tenant_id),
) -> dict[str, Any]:
    """Get current onboarding progress for the authenticated tenant."""
    progress = await service.get_progress(tenant_id)
    if not progress:
        return {
            "state": "NOT_STARTED",
            "current_step": 0,
            "step_timings": {},
            "fallback_flags": {},
            "launch_approved_at": None,
            "completed_at": None,
            "slo_status": {
                "total_duration_ms": None,
                "slo_met": None,
                "step_details": {},
                "fallback_count": 0,
            },
        }

    return {
        "state": progress.state,
        "current_step": progress.current_step,
        "step_timings": progress.step_timings,
        "fallback_flags": progress.fallback_flags,
        "launch_approved_at": progress.launch_approved_at.isoformat()
        if progress.launch_approved_at
        else None,
        "completed_at": progress.completed_at.isoformat()
        if progress.completed_at
        else None,
        "slo_status": service.get_slo_status(progress),
    }


@router.get("/config")
async def get_onboarding_config() -> dict[str, Any]:
    """Get onboarding wizard step configuration (public)."""
    return {
        "steps": {
            str(num): {
                "name": config.name,
                "target_seconds": config.target_seconds,
                "target_minutes": config.target_seconds / 60,
                "required_assets": config.required_assets,
                "fallback_types": config.fallback_types,
            }
            for num, config in STEP_CONFIG.items()
        },
        "total_target_seconds": get_total_target_seconds(),
        "total_target_minutes": get_total_target_seconds() / 60,
        "fallback_types": FALLBACK_TYPES,
    }


# =============================================================================
# Step Management Endpoints
# =============================================================================


@router.post("/steps/{step}/start")
async def start_step(
    step: int,
    service: OnboardingWizardService = Depends(get_wizard_service),
    tenant_id: TenantId = Depends(get_tenant_id),
) -> dict[str, Any]:
    """Start an onboarding step."""
    if not is_valid_step(step):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid step: {step}. Must be 1-6.",
        )

    try:
        progress = await service.start_step(tenant_id, step)
        return {
            "state": progress.state,
            "current_step": progress.current_step,
            "step_timings": progress.step_timings,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/steps/{step}/complete")
async def complete_step(
    step: int,
    fallback_type: str | None = None,
    fallback_reason: str | None = None,
    service: OnboardingWizardService = Depends(get_wizard_service),
    tenant_id: TenantId = Depends(get_tenant_id),
) -> dict[str, Any]:
    """Complete an onboarding step."""
    if not is_valid_step(step):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid step: {step}. Must be 1-6.",
        )

    fb_type = None
    if fallback_type:
        try:
            fb_type = FallbackType(fallback_type)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid fallback type: {fallback_type}",
            )

    try:
        progress = await service.complete_step(
            tenant_id, step, fallback_type=fb_type, fallback_reason=fallback_reason
        )
        return {
            "state": progress.state,
            "current_step": progress.current_step,
            "step_timings": progress.step_timings,
            "fallback_flags": progress.fallback_flags,
            "launch_approved_at": progress.launch_approved_at.isoformat()
            if progress.launch_approved_at
            else None,
            "completed_at": progress.completed_at.isoformat()
            if progress.completed_at
            else None,
            "slo_status": service.get_slo_status(progress),
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/steps/{step}/pause")
async def pause_step(
    step: int,
    service: OnboardingWizardService = Depends(get_wizard_service),
    tenant_id: TenantId = Depends(get_tenant_id),
) -> dict[str, Any]:
    """Pause the current step (owner leaves wizard)."""
    if not is_valid_step(step):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid step: {step}. Must be 1-6.",
        )

    try:
        progress = await service.pause_step(tenant_id, step)
        return {"state": progress.state, "step_timings": progress.step_timings}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/steps/{step}/resume")
async def resume_step(
    step: int,
    service: OnboardingWizardService = Depends(get_wizard_service),
    tenant_id: TenantId = Depends(get_tenant_id),
) -> dict[str, Any]:
    """Resume a paused step."""
    if not is_valid_step(step):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid step: {step}. Must be 1-6.",
        )

    try:
        progress = await service.resume_step(tenant_id, step)
        return {"state": progress.state, "step_timings": progress.step_timings}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/steps/{step}/fallback")
async def trigger_fallback(
    step: int,
    fallback_type: str,
    fallback_reason: str,
    service: OnboardingWizardService = Depends(get_wizard_service),
    tenant_id: TenantId = Depends(get_tenant_id),
) -> dict[str, Any]:
    """Record a fallback for a step (non-blocking, per AGENTS.md §11)."""
    if not is_valid_step(step):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid step: {step}. Must be 1-6.",
        )

    try:
        fb_type = FallbackType(fallback_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid fallback type: {fallback_type}",
        )

    try:
        progress = await service.trigger_fallback(
            tenant_id, step, fb_type, fallback_reason
        )
        return {
            "state": progress.state,
            "current_step": progress.current_step,
            "step_timings": progress.step_timings,
            "fallback_flags": progress.fallback_flags,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


# =============================================================================
# Launch Approval (Zero-Interference Gate)
# =============================================================================


@router.post("/approve-launch")
async def approve_launch(
    service: OnboardingWizardService = Depends(get_wizard_service),
    tenant_id: TenantId = Depends(get_tenant_id),
) -> dict[str, Any]:
    """
    Owner approves launch (opens zero-interference gate).

    After this, customer messaging is enabled for WhatsApp/Instagram.
    """
    try:
        progress = await service.approve_launch(tenant_id)
        return {
            "state": progress.state,
            "launch_approved_at": progress.launch_approved_at.isoformat()
            if progress.launch_approved_at
            else None,
            "message": "Launch approved. Customer messaging is now enabled.",
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.get("/launch-status")
async def get_launch_status(
    service: OnboardingWizardService = Depends(get_wizard_service),
    tenant_id: TenantId = Depends(get_tenant_id),
) -> dict[str, bool]:
    """Check if launch has been approved for this tenant."""
    approved = await service.is_launch_approved(tenant_id)
    return {"launch_approved": approved}