"""
Plan x feature entitlement matrix (plan M1.4, skeptic G2).

This is a *matrix*, not an entitled/denied boolean: Starter ⊂ Professional ⊂
Enterprise componentwise, and trial is Professional-tier agent on WhatsApp only.
Every cell is explicit, so a missing cell is a test failure, not a silent deny.

Invariant I3: the gate that consumes this matrix may only deny **agent LLM
spend** and **premium API** features. It must never gate data export, safety or
fraud flags, rider flow, audit logging, inbound persistence or the deterministic
holding reply — those are always available.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from packages.core.models.billing import PlanTier


class AgentLevel(IntEnum):
    """Autonomy ceiling granted by a plan (AGENTS.md §6)."""

    NONE = 0
    L0 = 1  # shadow
    L1 = 2  # answer
    L2 = 3  # transact
    L3 = 4  # optimize


class ApiAccess(IntEnum):
    NONE = 0
    API = 1
    API_AND_WEBHOOKS = 2


@dataclass(frozen=True)
class Entitlements:
    """The features a plan grants. ``seats=None`` means unlimited."""

    plan: PlanTier
    channels: frozenset[str]
    agent_level: AgentLevel
    api_access: ApiAccess
    insights: bool
    seats: int | None

    def allows_channel(self, channel: str) -> bool:
        return channel in self.channels

    def allows_agent_level(self, level: AgentLevel) -> bool:
        return self.agent_level >= level


# WhatsApp is the primary channel; Instagram is Professional+; TikTok is
# feature-flagged and not sold on any plan yet.
_MATRIX: dict[PlanTier, Entitlements] = {
    PlanTier.TRIAL: Entitlements(
        plan=PlanTier.TRIAL,
        channels=frozenset({"whatsapp"}),
        agent_level=AgentLevel.L2,
        api_access=ApiAccess.NONE,
        insights=True,
        seats=1,
    ),
    PlanTier.STARTER: Entitlements(
        plan=PlanTier.STARTER,
        channels=frozenset({"whatsapp"}),
        agent_level=AgentLevel.L1,
        api_access=ApiAccess.NONE,
        insights=True,
        seats=1,
    ),
    PlanTier.PROFESSIONAL: Entitlements(
        plan=PlanTier.PROFESSIONAL,
        channels=frozenset({"whatsapp", "instagram"}),
        agent_level=AgentLevel.L2,
        api_access=ApiAccess.API,
        insights=True,
        seats=3,
    ),
    PlanTier.ENTERPRISE: Entitlements(
        plan=PlanTier.ENTERPRISE,
        channels=frozenset({"whatsapp", "instagram", "tiktok"}),
        agent_level=AgentLevel.L3,
        api_access=ApiAccess.API_AND_WEBHOOKS,
        insights=True,
        seats=None,
    ),
}


def entitlements_for(plan: str | PlanTier) -> Entitlements:
    """Return the entitlement row for a plan.

    Unknown plan strings fail closed to the most restrictive row (Starter),
    never to an exception that could take down a request path.
    """
    if isinstance(plan, PlanTier):
        tier = plan
    else:
        try:
            tier = PlanTier(plan)
        except ValueError:
            tier = PlanTier.STARTER
    return _MATRIX[tier]


def all_plans() -> list[PlanTier]:
    return list(PlanTier)


__all__ = [
    "AgentLevel",
    "ApiAccess",
    "Entitlements",
    "all_plans",
    "entitlements_for",
]
