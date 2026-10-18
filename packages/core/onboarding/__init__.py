"""
Onboarding Wizard Module

Instrumented 30-minute onboarding wizard for SocialERP tenants.
"""

from packages.core.onboarding.model import OnboardingWizardProgress
from packages.core.onboarding.service import OnboardingWizardService
from packages.core.onboarding.steps import (
    OnboardingStep,
    STEP_CONFIG,
    FALLBACK_TYPES,
)
from packages.core.onboarding.metrics import (
    ONBOARDING_STEP_DURATION,
    ONBOARDING_TOTAL_DURATION,
    ONBOARDING_STEP_STARTED,
    ONBOARDING_STEP_COMPLETED,
    ONBOARDING_ACTIVE_SESSIONS,
    ONBOARDING_FALLBACK,
    ONBOARDING_SLO_BREACH,
    ONBOARDING_LAUNCH_APPROVED,
    ONBOARDING_WIZARD_ABORTED,
    STEP_TARGETS,
    TOTAL_TARGET_SECONDS,
)

__all__ = [
    "OnboardingWizardProgress",
    "OnboardingWizardService",
    "OnboardingStep",
    "STEP_CONFIG",
    "FALLBACK_TYPES",
    "ONBOARDING_STEP_DURATION",
    "ONBOARDING_TOTAL_DURATION",
    "ONBOARDING_STEP_STARTED",
    "ONBOARDING_STEP_COMPLETED",
    "ONBOARDING_ACTIVE_SESSIONS",
    "ONBOARDING_FALLBACK",
    "ONBOARDING_SLO_BREACH",
    "ONBOARDING_LAUNCH_APPROVED",
    "ONBOARDING_WIZARD_ABORTED",
    "STEP_TARGETS",
    "TOTAL_TARGET_SECONDS",
]