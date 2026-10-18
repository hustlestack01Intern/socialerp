"""
Conversation → agent bridge (Phase 6).

Runs the grounded agent for one inbound WhatsApp message and persists the
outcome:

* loads tenant business facts (from ``tenant.settings``) and a read-only catalog
  built from the tenant's active products;
* constructs the :class:`~packages.agent.agent.Agent` with an injected intent
  extractor and a deterministic order tool (``ServiceOrderCreator``);
* audits every action (``agent.message_handled``) with the full decision trace;
* on ``requires_human`` moves the conversation to ``handoff_requested``;
* persists the reply as an outbound message: ``sent`` when policy allows
  auto-send (L1 answer / L2 order) and the message was actually delivered,
  otherwise ``draft`` for owner approval (the L0 default). A failed or
  closed-window send degrades to a draft — a reply is never lost.

The agent never does money/stock arithmetic: prices come from the catalog and
orders go through the deterministic tool (AGENTS.md §5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent.agent import (
    Agent,
    AgentOutcome,
    BusinessFacts,
    CatalogItem,
    InMemoryCatalog,
    IntentExtractor,
)
from packages.agent.policy import AutonomyLevel, PolicyEngine
from packages.agent.service_tools import ServiceOrderCreator
from packages.conversation.manager import ConversationManager
from packages.core.audit.logger import AuditLogger
from packages.core.domain import CustomerPhone, TenantId
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.core import Tenant
from packages.core.models.whatsapp import (
    WhatsAppMessage,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)

logger = logging.getLogger(__name__)

DEFAULT_AUTONOMY = AutonomyLevel.L0_SHADOW

# Facts keys read from ``tenant.settings`` (all optional, never invented).
_FACT_HOURS = "hours"
_FACT_DELIVERY_AREAS = "delivery_areas"
_FACT_PAYMENT_METHODS = "payment_methods"
_FACT_DELIVERY_FEE_NOTE = "delivery_fee_note"
_AUTONOMY_KEY = "agent_autonomy"


class MessageSender(Protocol):
    """Minimal outbound seam so the service can be tested without the network.

    Returns the provider message id on success, or ``None``. Raises on failure.
    """

    async def send_text(
        self,
        tenant_id: TenantId,
        to: CustomerPhone,
        body: str,
        conversation_id: UUID | None = None,
    ) -> str | None: ...


@dataclass(frozen=True)
class ConversationAgentResult:
    """Observable outcome of one agent run (returned for tests and logging)."""

    intent: str
    action: str
    reply_text: str
    sent: bool
    grounded: bool
    requires_human: bool
    order_id: str
    tools_used: tuple[str, ...]


def autonomy_from_settings(settings: dict[str, object] | None) -> AutonomyLevel:
    """Read the tenant's autonomy level; default L0 (shadow)."""
    raw = str((settings or {}).get(_AUTONOMY_KEY, "")).strip().upper()
    if not raw:
        return DEFAULT_AUTONOMY
    try:
        return AutonomyLevel(raw)
    except ValueError:
        logger.warning("Unknown agent_autonomy %r; defaulting to L0", raw)
        return DEFAULT_AUTONOMY


def _business_facts(settings: dict[str, object] | None) -> BusinessFacts:
    s = settings or {}
    areas = s.get(_FACT_DELIVERY_AREAS)
    payments = s.get(_FACT_PAYMENT_METHODS)
    return BusinessFacts(
        hours=str(s.get(_FACT_HOURS, "")),
        delivery_areas=[str(a) for a in areas] if isinstance(areas, list) else [],
        payment_methods=[str(p) for p in payments] if isinstance(payments, list) else [],
        delivery_fee_note=str(s.get(_FACT_DELIVERY_FEE_NOTE, "")),
    )


def _variant_display_name(product_name: str, attributes: list[dict[str, object]]) -> str:
    """Human name for a variant, e.g. "Hoodie Black M"."""
    parts = [product_name]
    for attr in attributes or []:
        value = attr.get("value") if isinstance(attr, dict) else None
        if value:
            parts.append(str(value))
    return " ".join(parts)


class ConversationAgentService:
    """Runs the agent for one inbound message and persists the outcome."""

    def __init__(
        self,
        session: AsyncSession,
        tenant_id: TenantId,
        *,
        extractor: IntentExtractor | None = None,
        sender: MessageSender | None = None,
        policy: PolicyEngine | None = None,
        autonomy: AutonomyLevel | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self._extractor = extractor
        self._sender = sender
        self.policy = policy or PolicyEngine()
        self._autonomy = autonomy

    # -- default collaborators --------------------------------------------

    def _build_extractor(self) -> IntentExtractor:
        if self._extractor is not None:
            return self._extractor
        # Lazy import + lazy gateway construction: tests inject a deterministic
        # extractor and never touch the model.
        from socialerp.settings import get_settings

        from packages.agent.factory import build_default_gateway
        from packages.agent.intent import LLMIntentExtractor

        settings = get_settings()
        gateway = build_default_gateway(settings)
        return LLMIntentExtractor(gateway, model=settings.LLM_MODEL)

    async def aclose(self) -> None:
        """Release the sender's resources (no-op for test doubles)."""
        close = getattr(self._sender, "close", None)
        if close is not None:
            await close()

    # -- data loading ------------------------------------------------------

    async def _load_settings(self) -> dict[str, object]:
        result = await self.session.execute(
            select(Tenant.settings).where(Tenant.id == self.tenant_id),
        )
        settings = result.scalar_one_or_none()
        return dict(settings or {})

    async def _build_catalog(self) -> InMemoryCatalog:
        result = await self.session.execute(
            select(ProductVariant, Product.name)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(
                ProductVariant.tenant_id == self.tenant_id,
                ProductVariant.is_active.is_(True),
                Product.status == ProductStatus.ACTIVE.value,
            ),
        )
        items = [
            CatalogItem(
                sku=variant.sku,
                name=_variant_display_name(product_name, variant.attributes),
                price_cents=variant.sell_price_cents,
                # Display-level availability only; the order tool re-validates
                # against the event-sourced ledger before reserving (ADR 0013).
                in_stock=variant.quantity > 0,
            )
            for variant, product_name in result.all()
        ]
        return InMemoryCatalog(items)

    # -- main entry point --------------------------------------------------

    async def respond_to_message(
        self,
        *,
        conversation_id: UUID,
        contact_id: UUID,
        customer_phone: str,
        text: str,
        provider_message_id: str | None = None,
    ) -> ConversationAgentResult:
        # Phase 8c: the owner console intercepts messages from the owner's phone
        # BEFORE the customer agent. It returns None for every non-owner sender,
        # so the customer path below is unchanged (ADR 0019).
        owner_result = await self._try_owner_console(
            conversation_id=conversation_id,
            contact_id=contact_id,
            sender_phone=customer_phone,
            text=text,
            provider_message_id=provider_message_id,
        )
        if owner_result is not None:
            return owner_result

        settings = await self._load_settings()
        autonomy = self._autonomy or autonomy_from_settings(settings)
        catalog = await self._build_catalog()

        agent = Agent(
            extractor=self._build_extractor(),
            policy=self.policy,
            catalog=catalog,
            facts=_business_facts(settings),
            autonomy=autonomy,
            # Tool is present even at L0/L1; policy gates whether it runs.
            order_creator=ServiceOrderCreator(self.session, self.tenant_id),
        )

        outcome = await agent.handle_message(
            text,
            customer_phone=customer_phone,
            # Replaying the same provider message cannot create a second order
            # (the tool dedupes on this key).
            idempotency_key=provider_message_id,
        )

        await self._audit(conversation_id, outcome, autonomy, provider_message_id)

        if outcome.decision.requires_human:
            manager = ConversationManager(self.session)
            await manager.set_handoff(
                tenant_id=self.tenant_id,
                conversation_id=conversation_id,
                reason=outcome.decision.reason or outcome.intent.intent.value,
            )

        sent = await self._persist_reply(
            conversation_id=conversation_id,
            contact_id=contact_id,
            customer_phone=customer_phone,
            outcome=outcome,
            autonomy=autonomy,
        )

        return ConversationAgentResult(
            intent=outcome.intent.intent.value,
            action=outcome.decision.action.value,
            reply_text=outcome.reply_text,
            sent=sent,
            grounded=outcome.grounded,
            requires_human=outcome.decision.requires_human,
            order_id=outcome.order_id,
            tools_used=tuple(outcome.tools_used),
        )

    # -- owner console hook (Phase 8c) -------------------------------------

    async def _try_owner_console(
        self,
        *,
        conversation_id: UUID,
        contact_id: UUID,
        sender_phone: str,
        text: str,
        provider_message_id: str | None,
    ) -> ConversationAgentResult | None:
        """Run the owner console for owner senders; None for everyone else.

        Lazy import avoids a circular import (owner_console imports
        MessageSender/autonomy_from_settings from this module). The console
        shares this service's sender but never closes it — the caller owns the
        sender's lifecycle (the service ``aclose`` closes it).
        """
        from packages.conversation.owner_console import OwnerConsoleService

        console = OwnerConsoleService(
            self.session,
            self.tenant_id,
            sender=self._sender,
            policy=self.policy,
            autonomy=self._autonomy,
        )
        outcome = await console.try_handle(
            conversation_id=conversation_id,
            contact_id=contact_id,
            sender_phone=sender_phone,
            text=text,
            provider_message_id=provider_message_id,
        )
        if not outcome.handled:
            return None
        return ConversationAgentResult(
            intent=f"owner_console:{outcome.command or 'help'}",
            action=outcome.decision or "",
            reply_text=outcome.reply_text,
            sent=outcome.sent,
            grounded=True,
            requires_human=outcome.requires_human,
            order_id=outcome.order_id,
            tools_used=(),
        )

    # -- persistence -------------------------------------------------------

    async def _persist_reply(
        self,
        *,
        conversation_id: UUID,
        contact_id: UUID,
        customer_phone: str,
        outcome: AgentOutcome,
        autonomy: AutonomyLevel,
    ) -> bool:
        """Store the reply; send it when policy allows and the window is open.

        Returns True iff an outbound message was actually sent.
        """
        wants_send = (
            outcome.sent and not outcome.decision.requires_human and self._sender is not None
        )

        send_error: str | None = None
        provider_message_id: str | None = None
        sent = False
        if wants_send:
            try:
                provider_message_id = await self._sender.send_text(  # type: ignore[union-attr]
                    self.tenant_id,
                    CustomerPhone(customer_phone),
                    outcome.reply_text,
                    conversation_id,
                )
                sent = True
            except Exception as exc:  # closed window, provider error — keep the draft
                send_error = str(exc)
                logger.warning(
                    "Outbound send failed; storing draft",
                    extra={"conversation_id": str(conversation_id), "error": send_error},
                )

        message = WhatsAppMessage(
            tenant_id=self.tenant_id,
            contact_id=contact_id,
            conversation_id=conversation_id,
            direction="outbound",
            message_type=WhatsAppMessageType.TEXT,
            status=WhatsAppMessageStatus.SENT if sent else WhatsAppMessageStatus.DRAFT,
            text_body=outcome.reply_text,
            meta_message_id=provider_message_id,
            metadata_json={
                "autonomy": autonomy.value,
                "action": outcome.decision.action.value,
                "order_id": outcome.order_id or None,
                "send_error": send_error,
            },
        )
        self.session.add(message)
        await self.session.flush()

        from packages.core.models.whatsapp import WhatsAppConversation

        conversation = await self.session.get(WhatsAppConversation, conversation_id)
        if conversation is not None:
            conversation.last_agent_message_at = datetime.now(UTC)
        return sent

    async def _audit(
        self,
        conversation_id: UUID,
        outcome: AgentOutcome,
        autonomy: AutonomyLevel,
        provider_message_id: str | None,
    ) -> None:
        audit = AuditLogger(self.session)
        await audit.log_action(
            tenant_id=self.tenant_id,
            user_id=None,
            action="agent.message_handled",
            resource="conversation",
            resource_id=str(conversation_id),
            after={
                "intent": outcome.intent.intent.value,
                "confidence": outcome.intent.confidence,
                "language": outcome.intent.language,
                "injection_detected": outcome.intent.injection_detected,
                "autonomy": autonomy.value,
                "decision": outcome.decision.action.value,
                "risk": outcome.decision.risk.value,
                "reason": outcome.decision.reason,
                "grounded": outcome.grounded,
                "sent": outcome.sent,
                "requires_human": outcome.decision.requires_human,
                "tools_used": outcome.tools_used,
                "order_id": outcome.order_id or None,
                "provider_message_id": provider_message_id,
            },
        )
        await self.session.flush()


__all__ = [
    "DEFAULT_AUTONOMY",
    "ConversationAgentResult",
    "ConversationAgentService",
    "MessageSender",
    "autonomy_from_settings",
]
