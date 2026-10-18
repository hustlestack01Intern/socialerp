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
from packages.core.onboarding.events import (
    OnboardingStepStarted,
    OnboardingStepCompleted,
    OnboardingWizardCompleted,
    OnboardingFallbackTriggered,
)

__all__ = [
    "OnboardingWizardProgress",
    "OnboardingWizardService",
    "OnboardingStep",
    "STEP_CONFIG",
    "FALLBACK_TYPES",
    "OnboardingStepStarted",
    "OnboardingStepCompleted",
    "OnboardingWizardCompleted",
    "OnboardingFallbackTriggered",
]