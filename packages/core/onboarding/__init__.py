"""
Onboarding Wizard Module

Instrumented 30-minute onboarding wizard for SocialERP tenants.
"""

from packages.core.onboarding.metrics import (
    ONBOARDING_ACTIVE_SESSIONS,
    ONBOARDING_FALLBACK,
    ONBOARDING_LAUNCH_APPROVED,
    ONBOARDING_SLO_BREACH,
    ONBOARDING_STEP_COMPLETED,
    ONBOARDING_STEP_DURATION,
    ONBOARDING_STEP_STARTED,
    ONBOARDING_TOTAL_DURATION,
    ONBOARDING_WIZARD_ABORTED,
    STEP_TARGETS,
    TOTAL_TARGET_SECONDS,
)
from packages.core.onboarding.model import OnboardingWizardProgress
from packages.core.onboarding.service import OnboardingWizardService
from packages.core.onboarding.steps import (
    FALLBACK_TYPES,
    STEP_CONFIG,
    OnboardingStep,
)

__all__ = [
    "FALLBACK_TYPES",
    "ONBOARDING_ACTIVE_SESSIONS",
    "ONBOARDING_FALLBACK",
    "ONBOARDING_LAUNCH_APPROVED",
    "ONBOARDING_SLO_BREACH",
    "ONBOARDING_STEP_COMPLETED",
    "ONBOARDING_STEP_DURATION",
    "ONBOARDING_STEP_STARTED",
    "ONBOARDING_TOTAL_DURATION",
    "ONBOARDING_WIZARD_ABORTED",
    "STEP_CONFIG",
    "STEP_TARGETS",
    "TOTAL_TARGET_SECONDS",
    "OnboardingStep",
    "OnboardingWizardProgress",
    "OnboardingWizardService",
]
