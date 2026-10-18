"""
Onboarding Wizard Step Definitions

Defines the 6 steps of the 30-minute onboarding wizard with target times,
required assets, and fallback handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class OnboardingStep(int, Enum):
    """Onboarding step numbers."""

    BUSINESS_PROFILE = 1
    CONNECT_CHANNELS = 2
    CATALOG_BOOTSTRAP = 3
    PAYMENTS_SETUP = 4
    RULES_TONE = 5
    GO_LIVE_SHADOW = 6


@dataclass(frozen=True)
class StepConfig:
    """Configuration for an onboarding step."""

    number: int
    name: str
    target_seconds: int
    required_assets: list[str]  # Assets that must be present for happy path
    fallback_types: list[str]  # Fallback types that can be triggered


# Step configurations matching AGENTS.md §11
STEP_CONFIG: dict[int, StepConfig] = {
    1: StepConfig(
        number=1,
        name="business_profile",
        target_seconds=180,  # 3 min
        required_assets=[],
        fallback_types=[],
    ),
    2: StepConfig(
        number=2,
        name="connect_channels",
        target_seconds=480,  # 8 min
        required_assets=["meta_business_verification"],
        fallback_types=["NO_META_ASSETS"],
    ),
    3: StepConfig(
        number=3,
        name="catalog_bootstrap",
        target_seconds=480,  # 8 min
        required_assets=[],
        fallback_types=["MANUAL_CATALOG"],
    ),
    4: StepConfig(
        number=4,
        name="payments_setup",
        target_seconds=300,  # 5 min
        required_assets=["daraja_till"],
        fallback_types=["NO_DARAJA_TILL", "MANUAL_PAYMENT_MODE"],
    ),
    5: StepConfig(
        number=5,
        name="rules_tone",
        target_seconds=240,  # 4 min
        required_assets=[],
        fallback_types=[],
    ),
    6: StepConfig(
        number=6,
        name="go_live_shadow",
        target_seconds=120,  # 2 min
        required_assets=[],
        fallback_types=[],
    ),
}

# Total target: 30 minutes = 1800 seconds
TOTAL_TARGET_SECONDS = sum(c.target_seconds for c in STEP_CONFIG.values())

# Fallback type definitions
FALLBACK_TYPES: dict[str, dict[str, Any]] = {
    "MANUAL_PAYMENT_MODE": {
        "description": "No Daraja Till or aggregator credentials; run in manual payment confirmation mode",
        "step": 4,
        "blocks_go_live": False,
        "async_guidance": True,
    },
    "MANUAL_CATALOG": {
        "description": "No existing catalog/posts/price list; owner creates from scratch",
        "step": 3,
        "blocks_go_live": False,
        "async_guidance": False,
    },
    "NO_META_ASSETS": {
        "description": "No verified FB Page/IG Professional account; assisted mode (agent drafts, owner sends)",
        "step": 2,
        "blocks_go_live": False,
        "async_guidance": True,
    },
    "NO_DARAJA_TILL": {
        "description": "No Safaricom Till/Paybill; same as MANUAL_PAYMENT_MODE",
        "step": 4,
        "blocks_go_live": False,
        "async_guidance": True,
    },
}


def get_step_config(step: int) -> StepConfig | None:
    """Get configuration for a step."""
    return STEP_CONFIG.get(step)


def get_total_target_seconds() -> int:
    """Get total target time in seconds."""
    return TOTAL_TARGET_SECONDS


def is_valid_step(step: int) -> bool:
    """Check if step number is valid."""
    return step in STEP_CONFIG


def get_fallback_info(fallback_type: str) -> dict[str, Any] | None:
    """Get fallback type information."""
    return FALLBACK_TYPES.get(fallback_type)