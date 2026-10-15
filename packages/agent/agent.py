"""
Agent loop (Phase 5, L0 shadow + L1 answer).

Deterministic and grounded: replies are composed from tenant facts and catalog
data supplied by tools, so the agent cannot invent a price, stock level, opening
hours or delivery area. The LLM's only role is structured intent extraction.

Every decision carries the policy decision that allowed it. Fail safe: if the
intent extractor or gateway errors, the agent sends a safe fallback and hands off
to a human.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from packages.agent.errors import AgentError
from packages.agent.gateway import LLMError, LLMMessage
from packages.agent.intent import Intent, IntentResult, ItemSlot
from packages.agent.policy import (
    AutonomyLevel,
    PolicyAction,
    PolicyDecision,
    PolicyEngine,
    RiskLevel,
)
from packages.agent.tools import (
    OrderCreateLine,
    OrderCreateRequest,
    OrderCreator,
)

HANDOFF_TEXT = "Let me connect you with a team member who can help with this."
SAFE_FALLBACK = (
    "Sorry, I couldn't handle that automatically. A team member will assist you shortly."
)


@dataclass(frozen=True)
class CatalogItem:
    """A catalog entry exposed to the agent (price in KES cents)."""

    sku: str
    name: str
    price_cents: int
    in_stock: bool


class Catalog(Protocol):
    """Read-only catalog lookup (tool boundary; wired to repositories later)."""

    def find(self, query: str) -> list[CatalogItem]: ...


class InMemoryCatalog:
    """Simple substring-matching catalog for tests and evals."""

    def __init__(self, items: list[CatalogItem]) -> None:
        self.items = items

    def find(self, query: str) -> list[CatalogItem]:
        needle = query.lower()
        # Score by whole-word name tokens present in the query (word boundaries
        # avoid a size token like "m" matching inside "much").
        scored: list[tuple[int, CatalogItem]] = []
        for item in self.items:
            tokens = [t for t in item.name.lower().split() if t]
            score = sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}\b", needle))
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [item for _, item in scored]


@dataclass
class BusinessFacts:
    """Tenant-supplied facts the agent may quote verbatim."""

    hours: str = ""
    delivery_areas: list[str] = field(default_factory=list)
    payment_methods: list[str] = field(default_factory=list)
    delivery_fee_note: str = ""


class IntentExtractor(Protocol):
    async def extract(self, message: str) -> IntentResult: ...


@dataclass(frozen=True)
class AgentOutcome:
    intent: IntentResult
    decision: PolicyDecision
    reply_text: str
    sent: bool
    grounded: bool
    tools_used: list[str] = field(default_factory=list)
    order_id: str = ""
    model_version: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Agent:
    """Grounded L0/L1 agent over a single inbound customer message."""

    def __init__(
        self,
        *,
        extractor: IntentExtractor,
        policy: PolicyEngine,
        catalog: Catalog,
        facts: BusinessFacts,
        autonomy: AutonomyLevel = AutonomyLevel.L0_SHADOW,
        order_creator: OrderCreator | None = None,
    ) -> None:
        self.extractor = extractor
        self.policy = policy
        self.catalog = catalog
        self.facts = facts
        self.autonomy = autonomy
        self.order_creator = order_creator

    async def handle_message(
        self,
        message: str,
        *,
        history: list[LLMMessage] | None = None,
        customer_phone: str | None = None,
    ) -> AgentOutcome:
        try:
            intent = await self.extractor.extract(message)
        except (AgentError, LLMError):
            decision = PolicyDecision(
                PolicyAction.ESCALATE_HUMAN,
                RiskLevel.MEDIUM,
                "intent extraction failed",
                self.autonomy,
            )
            return AgentOutcome(
                intent=IntentResult(intent=Intent.UNKNOWN, confidence=0.0),
                decision=decision,
                reply_text=SAFE_FALLBACK,
                sent=False,
                grounded=False,
            )

        decision = self.policy.decide(intent, autonomy=self.autonomy)

        if decision.requires_human:
            reply = (
                f"{SAFE_FALLBACK} {HANDOFF_TEXT}"
                if decision.action is PolicyAction.ESCALATE_HUMAN
                else HANDOFF_TEXT
            )
            return AgentOutcome(
                intent=intent,
                decision=decision,
                reply_text=reply,
                sent=False,
                grounded=False,
            )

        if decision.may_execute:
            return await self._execute_order(intent, decision, customer_phone)

        reply, grounded, tools_used = self._compose(intent)
        return AgentOutcome(
            intent=intent,
            decision=decision,
            reply_text=reply,
            sent=decision.auto_send,
            grounded=grounded,
            tools_used=tools_used,
        )

    # -- L2 transaction execution -----------------------------------------

    async def _execute_order(
        self,
        intent: IntentResult,
        decision: PolicyDecision,
        customer_phone: str | None,
    ) -> AgentOutcome:
        """Create the order through the deterministic tool (L2+).

        The agent resolves each requested item to a catalog SKU and hands the
        tool a *price guard* (the catalog price). The tool re-validates against
        the catalog, computes the total and reserves stock — the agent never
        does arithmetic. Any failure is reported truthfully from the tool (or
        handed to a human); nothing is improvised.
        """
        if self.order_creator is None or not customer_phone:
            return AgentOutcome(
                intent=intent,
                decision=decision,
                reply_text="I can place that order — what phone number should I use for payment?",
                sent=False,
                grounded=False,
            )

        resolved: list[OrderCreateLine] = []
        unresolved: list[ItemSlot] = []
        for slot in intent.items:
            matches = self.catalog.find(slot.description)
            if not matches:
                unresolved.append(slot)
                continue
            item = matches[0]
            resolved.append(
                OrderCreateLine(
                    sku=item.sku,
                    quantity=slot.quantity,
                    expected_unit_price_cents=item.price_cents,
                )
            )

        if unresolved or not resolved:
            missing = ", ".join(s.description for s in unresolved) if unresolved else ""
            reply = (
                f"I couldn't match {missing} to an item in our catalogue. "
                "Could you confirm the product name?"
                if missing
                else "Sure — what would you like to order, and how many?"
            )
            return AgentOutcome(
                intent=intent,
                decision=decision,
                reply_text=reply,
                sent=False,
                grounded=False,
                tools_used=["catalog.find"],
            )

        request = OrderCreateRequest(
            customer_phone=customer_phone,
            lines=tuple(resolved),
            delivery_area=intent.delivery_area,
        )
        try:
            result = await self.order_creator.create_order(request)
        except Exception:
            return AgentOutcome(
                intent=intent,
                decision=decision,
                reply_text=f"{SAFE_FALLBACK} {HANDOFF_TEXT}",
                sent=False,
                grounded=False,
                tools_used=["catalog.find", "orders.create"],
            )

        if not result.ok:
            # A business rejection (out of stock, unknown item): tell the truth
            # from the tool and let a human take over. Never claim success.
            return AgentOutcome(
                intent=intent,
                decision=decision,
                reply_text=result.customer_message,
                sent=False,
                grounded=False,
                tools_used=["catalog.find", "orders.create"],
            )

        total = f"KES {result.total_cents // 100:,}"
        return AgentOutcome(
            intent=intent,
            decision=decision,
            reply_text=(
                f"Done! Order {result.order_id} is confirmed at {total}. "
                f"I'll send an M-Pesa payment request to {customer_phone} shortly."
            ),
            sent=True,
            grounded=True,
            tools_used=["catalog.find", "orders.create"],
            order_id=result.order_id,
        )

    # -- grounded composition ---------------------------------------------

    def _compose(self, intent: IntentResult) -> tuple[str, bool, list[str]]:
        kind = intent.intent
        if kind is Intent.GREET:
            return self._greet(), True, []
        if kind is Intent.FAQ:
            return self._faq(intent)
        if kind is Intent.PRODUCT_INQUIRY:
            return self._product(intent)
        if kind is Intent.CREATE_ORDER:
            return self._order_draft(intent)
        if kind is Intent.TRACK_ORDER:
            # No order lookup wired in Phase 5: do not guess status.
            return HANDOFF_TEXT, False, []
        return HANDOFF_TEXT, False, []

    def _greet(self) -> str:
        return "Habari! How can I help you today?"

    def _faq(self, intent: IntentResult) -> tuple[str, bool, list[str]]:
        parts: list[str] = []
        if self.facts.hours:
            parts.append(f"We're open {self.facts.hours}.")
        if self.facts.delivery_areas:
            parts.append("We deliver to: " + ", ".join(self.facts.delivery_areas) + ".")
        if self.facts.payment_methods:
            parts.append("We accept " + ", ".join(self.facts.payment_methods) + ".")
        if not parts:
            return HANDOFF_TEXT, False, []
        return " ".join(parts), True, ["business_facts"]

    def _product(self, intent: IntentResult) -> tuple[str, bool, list[str]]:
        query = intent.product_query or ""
        matches = self.catalog.find(query)
        if not matches:
            return (
                "I'm not sure which item you mean. Could you tell me the product name?",
                False,
                ["catalog.find"],
            )
        item = matches[0]
        price = f"KES {item.price_cents // 100:,}"
        if item.in_stock:
            return (
                f"{item.name} is available at {price}. Would you like to order?",
                True,
                ["catalog.find"],
            )
        return (
            f"{item.name} is currently out of stock ({price} when back). "
            "Want me to note your interest?",
            True,
            ["catalog.find"],
        )

    def _order_draft(self, intent: IntentResult) -> tuple[str, bool, list[str]]:
        if not intent.items:
            return (
                "Sure — what would you like to order, and how many?",
                False,
                [],
            )
        lines = []
        grounded = True
        tools: list[str] = []
        for slot in intent.items:
            matches = self.catalog.find(slot.description)
            if not matches:
                grounded = False
                lines.append(f"{slot.quantity} x {slot.description} (please confirm)")
                continue
            item = matches[0]
            tools.append("catalog.find")
            lines.append(f"{slot.quantity} x {item.name} @ KES {item.price_cents // 100:,}")
        area = f" Delivery to {intent.delivery_area}." if intent.delivery_area else ""
        return (
            "Here's your draft order: "
            + "; ".join(lines)
            + "."
            + area
            + " A team member will confirm and send payment details.",
            grounded,
            tools,
        )


__all__ = [
    "HANDOFF_TEXT",
    "SAFE_FALLBACK",
    "Agent",
    "AgentOutcome",
    "BusinessFacts",
    "Catalog",
    "CatalogItem",
    "InMemoryCatalog",
    "IntentExtractor",
]
