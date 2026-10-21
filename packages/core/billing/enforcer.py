"""
The one place a plan becomes a decision (M1.6, U80).

Why this module exists
----------------------
The plan x feature matrix (:mod:`packages.core.billing.entitlements`) was
published by the API but *not enforced*: the only gate was the plan **state**
(expired / cancelled / not started -> the agent stops). A pricing page that
lists a feature the code never checks is a promise the platform cannot keep, so
the human decided (2026-10-21): enforce it in code.

The derivation is short:

1. A promise is kept when the *same* function decides and every caller asks
   that function. So there is exactly one mapping from (subscription, plan
   row) to a decision, and it lives here.
2. Each matrix cell is a *property of the plan row* ("does this plan include
   Instagram?"), and the plan *state* is a separate property of the
   subscription ("is this plan paid and running?"). Both must hold for a
   premium feature; the cell is checked first because it is the more specific
   reason ("your plan has no Instagram" beats "your plan has not started").
3. Invariant I3 bounds what may be denied: agent LLM spend and premium API
   features. Inbound persistence, safety/fraud flags, the rider flow, the
   audit log, data export and the deterministic holding reply are never gated
   here. The insights cell is the owner's own data, so it is gated by the cell
   only - a cancelled tenant keeps reading their own history.

Example
-------
>>> enforcer.check_agent_level(tenant_id, "L2")   # doctest: +SKIP
FeatureDecision(allowed=False, feature='agent_level',
                reason='agent_level_above_ceiling', plan='starter', ...)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.billing.entitlements import AgentLevel, ApiAccess, Entitlements
from packages.core.billing.service import BillingService, EntitlementDecision

# The cells this module can decide. The pricing page's "enforced today" list
# is generated from this set (checked by tests/architecture), so a cell that is
# published but not gated cannot be claimed as enforced.
ENFORCED_FEATURES: frozenset[str] = frozenset(
    {"channels", "agent_level", "api_access", "insights", "seats"}
)

# Every switch the platform actually throws today, in the words the pricing
# page uses. This is the full list - the matrix cells above plus the gates
# that are not matrix cells (the plan state, the trial clock, the one-business
# -per-number rule). The pricing page's enforced list is compared with this
# set by tests/architecture/test_pricing_page_matches_code.py, so the page can
# neither drop a switch nor invent one.
ENFORCED_SWITCHES: frozenset[str] = frozenset(
    {
        "plan_state",
        "plan_published",
        "trial_clock",
        "channels",
        "agent_level",
        "api_access",
        "insights",
        "seats",
        "one_business_per_number",
    }
)

# The channels the software actually implements. A plan may not sell a channel
# that does not exist yet: TikTok is behind a feature flag and is not sold
# (AGENTS.md §3, Phase 10). The check below refuses an unimplemented channel
# even if a future matrix row grants it, so the gap cannot be sold by accident.
IMPLEMENTED_CHANNELS: frozenset[str] = frozenset({"whatsapp", "instagram"})


class Feature(StrEnum):
    """The matrix cells that can be checked."""

    CHANNELS = "channels"
    AGENT_LEVEL = "agent_level"
    API_ACCESS = "api_access"
    INSIGHTS = "insights"
    SEATS = "seats"


class Denial(StrEnum):
    """Machine-readable reasons. A denial always carries one of these."""

    CHANNEL_NOT_INCLUDED = "channel_not_included"
    CHANNEL_NOT_IMPLEMENTED = "channel_not_implemented"
    AGENT_LEVEL_ABOVE_CEILING = "agent_level_above_ceiling"
    API_ACCESS_NOT_INCLUDED = "api_access_not_included"
    INSIGHTS_NOT_INCLUDED = "insights_not_included"
    SEAT_LIMIT_REACHED = "seat_limit_reached"
    PLAN_NOT_ENTITLED = "plan_not_entitled"


# The cells that only work while the subscription is actually running.
#
# The channel and agent-level cells are deliberately NOT state-gated: a
# stopped tenant's inbound message must still be persisted (invariant I3: the
# gate may never gate inbound persistence), and the agent's own state gate
# already stops the agent for a stopped plan. Insights are the owner's own
# data, not a premium service (I3: no gating of data export or the owner's
# view of their own business).
_STATE_GATED_FEATURES: frozenset[str] = frozenset({"api_access", "seats"})


@dataclass(frozen=True)
class FeatureDecision:
    """One decision about one feature for one tenant."""

    allowed: bool
    feature: str
    reason: str
    plan: str
    entitlements: Entitlements
    # Human-readable facts for the message shown to the owner/buyer.
    limit: str = ""
    detail: str = ""
    message: str = ""

    @property
    def denied(self) -> bool:
        return not self.allowed


def _plan_label(plan: str) -> str:
    return plan.replace("_", " ").title()


def _level_index(level: str) -> int:
    """'L0' -> 0, 'L2' -> 2. The agent's levels are ordered by that digit."""
    text = str(level).strip().upper()
    if not text.startswith("L") or not text[1:].isdigit():
        return -1
    return int(text[1:])


def agent_ceiling(entitlements: Entitlements) -> str:
    """The highest agent level the plan grants, as 'L0'..'L3' ('' = none)."""
    if entitlements.agent_level == AgentLevel.NONE:
        return ""
    return entitlements.agent_level.name


class EntitlementEnforcer:
    """Decides whether a tenant may use a feature, and says why not.

    The caller owns the transaction. Every method is read-only.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        billing: BillingService | None = None,
    ) -> None:
        self.session = session
        self.billing = billing or BillingService(session)

    # -- the plan state ----------------------------------------------------

    async def plan_state(self, tenant_id: UUID) -> EntitlementDecision:
        """The plan state gate (entitled / expired / pending / ...)."""
        return await self.billing.check_entitlement(tenant_id)

    # -- cells -------------------------------------------------------------

    async def check_channel(self, tenant_id: UUID, channel: str) -> FeatureDecision:
        """May this tenant receive and process messages on ``channel``?"""
        state = await self.plan_state(tenant_id)
        ents = state.entitlements
        if channel not in IMPLEMENTED_CHANNELS:
            # The software cannot do this channel yet, whatever the plan says.
            return FeatureDecision(
                allowed=False,
                feature=Feature.CHANNELS,
                reason=Denial.CHANNEL_NOT_IMPLEMENTED,
                plan=state.plan,
                entitlements=ents,
                limit=", ".join(sorted(IMPLEMENTED_CHANNELS)),
                detail=channel,
                message=(
                    f"{channel} is not available yet. We will not sell it "
                    "before it works."
                ),
            )
        if channel not in ents.channels:
            included = ", ".join(sorted(ents.channels)) or "no channel"
            return FeatureDecision(
                allowed=False,
                feature=Feature.CHANNELS,
                reason=Denial.CHANNEL_NOT_INCLUDED,
                plan=state.plan,
                entitlements=ents,
                limit=included,
                detail=channel,
                message=(
                    f"Your {_plan_label(state.plan)} plan does not include "
                    f"{channel} (it includes {included}). Upgrade to use it."
                ),
            )
        return self._finish(state, Feature.CHANNELS, allowed=True, detail=channel)

    async def check_api_access(
        self,
        tenant_id: UUID,
        *,
        minimum: ApiAccess = ApiAccess.API,
    ) -> FeatureDecision:
        """May this tenant use the API (``minimum`` = the access it needs)?"""
        state = await self.plan_state(tenant_id)
        ents = state.entitlements
        if ents.api_access < minimum:
            return FeatureDecision(
                allowed=False,
                feature=Feature.API_ACCESS,
                reason=Denial.API_ACCESS_NOT_INCLUDED,
                plan=state.plan,
                entitlements=ents,
                limit=ents.api_access.name.lower(),
                detail=minimum.name.lower(),
                message=(
                    f"Your {_plan_label(state.plan)} plan does not include API "
                    f"access. Upgrade to use it."
                ),
            )
        return self._finish(state, Feature.API_ACCESS, allowed=True)

    async def check_seats(
        self,
        tenant_id: UUID,
        *,
        adding: int = 1,
    ) -> FeatureDecision:
        """Is there a free seat for ``adding`` more users in this tenant?"""
        state = await self.plan_state(tenant_id)
        ents = state.entitlements
        limit = ents.seats
        if limit is not None:
            used = await self.count_active_users(tenant_id)
            if used + adding > limit:
                return FeatureDecision(
                    allowed=False,
                    feature=Feature.SEATS,
                    reason=Denial.SEAT_LIMIT_REACHED,
                    plan=state.plan,
                    entitlements=ents,
                    limit=str(limit),
                    detail=f"{used} of {limit} seats used",
                    message=(
                        f"Your {_plan_label(state.plan)} plan includes {limit} "
                        f"seat(s) and {used} are in use. Upgrade for more seats."
                    ),
                )
        return self._finish(state, Feature.SEATS, allowed=True)

    async def check_insights(self, tenant_id: UUID) -> FeatureDecision:
        """May this tenant read the insights? (the owner's own data)"""
        state = await self.plan_state(tenant_id)
        ents = state.entitlements
        if not ents.insights:
            return FeatureDecision(
                allowed=False,
                feature=Feature.INSIGHTS,
                reason=Denial.INSIGHTS_NOT_INCLUDED,
                plan=state.plan,
                entitlements=ents,
                limit="no insights",
                message=(
                    f"Your {_plan_label(state.plan)} plan does not include insights."
                ),
            )
        # Not state-gated: a cancelled tenant keeps reading their own history.
        return FeatureDecision(
            allowed=True,
            feature=Feature.INSIGHTS,
            reason="ok",
            plan=state.plan,
            entitlements=ents,
        )

    async def check_agent_level(
        self,
        tenant_id: UUID,
        requested: str,
    ) -> FeatureDecision:
        """May the agent run at ``requested`` level ('L0'..'L3')?

        A denial here is a *cap*, not a silence: the caller runs the agent at
        the ceiling instead. The reason is audited so the owner can see that
        the plan, not a bug, limited the agent.
        """
        state = await self.plan_state(tenant_id)
        ents = state.entitlements
        ceiling = agent_ceiling(ents)
        requested_index = _level_index(requested)
        ceiling_index = _level_index(ceiling)
        if ceiling_index < 0 or requested_index > ceiling_index:
            return FeatureDecision(
                allowed=False,
                feature=Feature.AGENT_LEVEL,
                reason=Denial.AGENT_LEVEL_ABOVE_CEILING,
                plan=state.plan,
                entitlements=ents,
                limit=ceiling or "no agent",
                detail=requested,
                message=(
                    f"Your {_plan_label(state.plan)} plan allows the agent up to "
                    f"{ceiling or 'no agent level'}; it is running at that level."
                ),
            )
        return self._finish(state, Feature.AGENT_LEVEL, allowed=True, detail=requested)

    # -- helpers -----------------------------------------------------------

    async def count_active_users(self, tenant_id: UUID) -> int:
        """How many active users (seats) the tenant has."""
        from packages.core.models.core import User

        result = await self.session.execute(
            select(func.count())
            .select_from(User)
            .where(User.tenant_id == tenant_id, User.is_active.is_(True))
        )
        return int(result.scalar_one())

    def _finish(
        self,
        state: EntitlementDecision,
        feature: Feature,
        *,
        allowed: bool,
        detail: str = "",
    ) -> FeatureDecision:
        """Apply the plan *state* on top of a passed cell check.

        The cell is the specific reason; the state is the general one. A
        premium feature needs both.
        """
        if allowed and feature.value in _STATE_GATED_FEATURES and not state.entitled:
            return FeatureDecision(
                allowed=False,
                feature=feature.value,
                reason=Denial.PLAN_NOT_ENTITLED,
                plan=state.plan,
                entitlements=state.entitlements,
                limit=state.reason,
                detail=detail,
                message=(
                    f"Your plan ({_plan_label(state.plan)}) is not running "
                    f"({state.reason}). Start or renew it to use this."
                ),
            )
        return FeatureDecision(
            allowed=allowed,
            feature=feature.value,
            reason="ok",
            plan=state.plan,
            entitlements=state.entitlements,
            detail=detail,
        )


__all__ = [
    "ENFORCED_FEATURES",
    "IMPLEMENTED_CHANNELS",
    "Denial",
    "EntitlementEnforcer",
    "Feature",
    "FeatureDecision",
    "agent_ceiling",
]
