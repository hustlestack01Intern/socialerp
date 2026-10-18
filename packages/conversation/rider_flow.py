"""
Rider WhatsApp quick-reply flow (Phase 7b).

Riders run their whole job over WhatsApp — no app. An inbound text from a
*known rider phone* is routed to a deterministic delivery transition and
answered with a short acknowledgement. This is deliberately NOT an LLM:

* the keyword map is fixed and unit-tested (English + Swahili carry the same
  weight because that is how Nairobi delivery riders actually text);
* the delivery FSM validates the transition (an out-of-order "delivered"
  cannot jump the queue);
* money/stock authority is untouched — this module only moves the physical
  lifecycle. For a pay-on-delivery arrival it *flags* that collection is due;
  the caller triggers the STK request **after** committing the arrival, so an
  at-least-once retry cannot fire a second prompt (see ``trigger_pod_collection``).

If the sender phone does not match a rider — or matches a rider who simply has
nothing in flight — ``try_handle`` returns ``handled=False`` and the normal
customer agent runs instead.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from socialerp.observability import get_logger
from socialerp.settings import get_settings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.conversation.agent_service import MessageSender
from packages.core.audit.logger import AuditLogger
from packages.core.delivery.repository import DeliveryRepository
from packages.core.delivery.service import (
    DeliveryError,
    DeliveryService,
    PaymentNotCollectedError,
    ProofOfDeliveryRequiredError,
)
from packages.core.domain import CustomerPhone, TenantId
from packages.core.domain.delivery import DeliveryFailureReason
from packages.core.domain.order import PaymentMethod
from packages.core.domain.payment import PaymentProvider
from packages.core.models.delivery import Delivery, Rider
from packages.core.models.whatsapp import (
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from packages.core.repositories import OrderRepository
from packages.integrations.provider_resolver import resolve_tenant_payment_provider

logger = get_logger(__name__)

# =============================================================================
# Keyword router (fixed, tested, bilingual)
# =============================================================================

# (action, language, phrase). Checked in list order, so FAILED precedes
# DELIVERED ("delivery failed" must not read as delivered) and DELIVERED
# precedes PICKED_UP.
_KEYWORDS: tuple[tuple[str, str, str], ...] = (
    # -- failed -----------------------------------------------------------
    ("failed", "en", "failed"),
    ("failed", "en", "not delivered"),
    ("failed", "en", "undelivered"),
    ("failed", "en", "not done"),
    ("failed", "en", "customer refused"),
    ("failed", "en", "refused"),
    ("failed", "en", "cannot find"),
    ("failed", "en", "cant find"),
    ("failed", "en", "no answer"),
    ("failed", "en", "no one"),
    ("failed", "en", "unreachable"),
    ("failed", "en", "wrong address"),
    ("failed", "sw", "hakuna mtu"),
    ("failed", "sw", "haipo"),
    ("failed", "sw", "kataliwa"),
    ("failed", "sw", "sikufikisha"),
    ("failed", "sw", "shida"),
    ("failed", "sw", "imefeli"),
    # -- delivered --------------------------------------------------------
    ("delivered", "en", "delivered"),
    ("delivered", "en", "delivery done"),
    ("delivered", "en", "handed over"),
    ("delivered", "en", "complete"),
    ("delivered", "en", "done"),
    ("delivered", "sw", "nimekabidhi"),
    ("delivered", "sw", "kabidhi"),
    ("delivered", "sw", "nimemaliza"),
    ("delivered", "sw", "maliza"),
    # -- arrived ----------------------------------------------------------
    ("arrived", "en", "arrived"),
    ("arrived", "en", "arrive"),
    ("arrived", "en", "i am here"),
    ("arrived", "en", "im here"),
    ("arrived", "en", "reached"),
    ("arrived", "en", "outside"),
    ("arrived", "sw", "nimefika"),
    ("arrived", "sw", "nimewasili"),
    ("arrived", "sw", "niko hapa"),
    # -- picked up --------------------------------------------------------
    ("picked_up", "en", "picked up"),
    ("picked_up", "en", "pickedup"),
    ("picked_up", "en", "pick up"),
    ("picked_up", "en", "picked"),
    ("picked_up", "en", "on the way"),
    ("picked_up", "en", "leaving"),
    ("picked_up", "sw", "nimechukua"),
    ("picked_up", "sw", "nimechukuwa"),
    ("picked_up", "sw", "nimeondoka"),
    ("picked_up", "sw", "naondoka"),
    # -- status -----------------------------------------------------------
    ("status", "en", "status"),
    ("status", "en", "update"),
    ("status", "en", "tracking"),
    ("status", "en", "where is"),
    ("status", "sw", "hali"),
    ("status", "sw", "wapi"),
)

# Reason phrases → owner-facing category. Checked in order.
_REASON_KEYWORDS: tuple[tuple[DeliveryFailureReason, str], ...] = (
    (DeliveryFailureReason.CUSTOMER_UNREACHABLE, "unreachable"),
    (DeliveryFailureReason.CUSTOMER_UNREACHABLE, "no answer"),
    (DeliveryFailureReason.CUSTOMER_UNREACHABLE, "no one"),
    (DeliveryFailureReason.CUSTOMER_UNREACHABLE, "cant reach"),
    (DeliveryFailureReason.CUSTOMER_UNREACHABLE, "hakuna mtu"),
    (DeliveryFailureReason.CUSTOMER_UNREACHABLE, "haipo"),
    (DeliveryFailureReason.WRONG_ADDRESS, "wrong address"),
    (DeliveryFailureReason.WRONG_ADDRESS, "address"),
    (DeliveryFailureReason.WRONG_ADDRESS, "mahali"),
    (DeliveryFailureReason.CUSTOMER_REFUSED, "refused"),
    (DeliveryFailureReason.CUSTOMER_REFUSED, "refuse"),
    (DeliveryFailureReason.CUSTOMER_REFUSED, "kataliwa"),
    (DeliveryFailureReason.RIDER_UNAVAILABLE, "puncture"),
    (DeliveryFailureReason.RIDER_UNAVAILABLE, "breakdown"),
    (DeliveryFailureReason.RIDER_UNAVAILABLE, "no fuel"),
    (DeliveryFailureReason.RIDER_UNAVAILABLE, "petrol"),
    (DeliveryFailureReason.RIDER_UNAVAILABLE, "bike"),
)


# Negations that must SUPPRESS a positive lifecycle match. "not arrived" cannot
# mean arrived: a false `arrived` on a POD order fires an M-Pesa prompt to the
# customer. FAILED phrases are matched first, so "not delivered"/"not done"/
# "undelivered"/"cannot find" (explicit failure reports) are unaffected. A
# negated positive returns None, so try_handle answers with the quick-reply
# help menu. Apostrophe contractions are expanded by `_normalize_text`, so
# "won't arrive"/"wasn't delivered" both end up containing the "not" token.
_NEGATIONS: tuple[str, ...] = (
    "not",
    "cannot",
    "bado",  # Swahili "not yet"
)


def _normalize_text(text: str) -> str:
    """Lowercase, expand apostrophe contractions, strip to word tokens.

    Contraction expansion is what lets a single "not" token catch "won't",
    "wasn't", "haven't", "didn't", … Without it, "won't arrive" would match the
    positive "arrive" keyword and wrongly transition the delivery. Only
    apostrophe forms are expanded, so bare "cant find" keeps matching its
    explicit FAILED keyword unchanged.
    """
    lowered = text.lower().replace("can't", "cannot")
    lowered = lowered.replace("won't", "will not").replace("shan't", "shall not")
    lowered = re.sub(r"(\w+)n't\b", r"\1 not", lowered)  # haven't → have not, …
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _contains(haystack: str, phrase: str) -> bool:
    return f" {phrase} " in f" {haystack} "


@dataclass(frozen=True)
class RiderCommand:
    """A parsed rider quick reply."""

    action: str  # picked_up | arrived | delivered | failed | status
    language: str  # en | sw
    reason: DeliveryFailureReason | None = None


def parse_rider_command(text: str) -> RiderCommand | None:
    """Map rider text to a command, or ``None`` when nothing matches."""
    if not text or not text.strip():
        return None
    normalized = _normalize_text(text)
    if not normalized:
        return None

    action: str | None = None
    language = "en"
    for candidate_action, candidate_language, phrase in _KEYWORDS:
        if _contains(normalized, phrase):
            action = candidate_action
            language = candidate_language
            break

    if action is None:
        return None

    # A negated positive ("not arrived", "haven't picked up") must not
    # transition the delivery; let the rider re-read the help menu instead.
    if action != "failed" and any(_contains(normalized, neg) for neg in _NEGATIONS):
        return None

    reason: DeliveryFailureReason | None = None
    if action == "failed":
        for candidate_reason, phrase in _REASON_KEYWORDS:
            if _contains(normalized, phrase):
                reason = candidate_reason
                break
    return RiderCommand(action=action, language=language, reason=reason)


# =============================================================================
# Reply copy
# =============================================================================

_HELP = (
    "Reply with one word:\n"
    "• *picked* — umechukua\n"
    "• *arrived* — umefika\n"
    "• *delivered* — umekabidhi\n"
    "• *failed <reason>* — imeshindikana\n"
    "• *status* — hali ya delivery"
)

_MESSAGES: dict[tuple[str, str], str] = {
    ("picked_up", "en"): "Got it — order {ref} collected. Delivery is on the way.",
    ("picked_up", "sw"): "Sawa — order {ref} imechukuliwa. Endelea na delivery.",
    ("arrived", "en"): "Thanks — noted you have arrived for order {ref}. {payment}",
    ("arrived", "sw"): "Asante — umefika kwa order {ref}. {payment}",
    ("delivered", "en"): "Delivered ✅ order {ref}. Asante!",
    ("delivered", "sw"): "Imekabidhiwa ✅ order {ref}. Asante sana!",
    ("failed", "en"): "Noted — order {ref} failed: {reason}. The shop will follow up.",
    ("failed", "sw"): "Sawa — order {ref} imeshindikana: {reason}. Duka litafuatilia.",
    ("status", "en"): "Order {ref} is currently *{status}*.",
    ("status", "sw"): "Order {ref} kwa sasa ni *{status}*.",
}

_REASON_LABEL = {
    DeliveryFailureReason.CUSTOMER_UNREACHABLE: "customer unreachable",
    DeliveryFailureReason.WRONG_ADDRESS: "wrong address",
    DeliveryFailureReason.CUSTOMER_REFUSED: "customer refused",
    DeliveryFailureReason.RIDER_UNAVAILABLE: "rider unavailable",
    DeliveryFailureReason.OTHER: "other",
}

NEED_REASON = "Sorry, why did it fail? Reply e.g. *failed no answer* or *failed wrong address*."

PAYMENT_PENDING = "An M-Pesa payment request will be sent to the customer."
PAYMENT_NOT_NEEDED = "This order was already paid."

NO_ACTIVE_DELIVERY = "You have no active delivery right now. The shop will assign one to you."

RIDER_INACTIVE = "Your rider access is switched off. Please ask the shop to activate you again."

POD_NEEDS_SHOP = (
    "Thanks. This is a pay-on-delivery order, so the shop must confirm the handover "
    "(photo + payment) to close it. I have told them."
)


def _short_ref(order_id: UUID) -> str:
    return str(order_id)[:8].upper()


# =============================================================================
# Flow
# =============================================================================


@dataclass(frozen=True)
class RiderFlowResult:
    """Observable outcome of one rider-flow run (tests + logging)."""

    handled: bool
    action: str | None
    reply_text: str
    sent: bool
    delivery_id: str | None = None
    # True when the caller must trigger a pay-on-delivery STK request, in its
    # own transaction, AFTER committing the arrival this result describes.
    collection_due: bool = False
    order_id: str | None = None


class _ProviderResolver(Protocol):
    def __call__(
        self, db: AsyncSession, tenant_id: UUID,
    ) -> Coroutine[Any, Any, PaymentProvider]: ...


class RiderFlowService:
    """Handle an inbound message from a rider's WhatsApp number."""

    def __init__(
        self,
        session: AsyncSession,
        tenant_id: TenantId,
        *,
        sender: MessageSender | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self._sender = sender
        self.deliveries = DeliveryRepository(session)
        self.service = DeliveryService(session)
        self.orders = OrderRepository(session)
        self.audit = AuditLogger(session)

    # -- entry point -------------------------------------------------------

    async def aclose(self) -> None:
        """Release the sender's resources (no-op for test doubles)."""
        close = getattr(self._sender, "close", None)
        if close is not None:
            await close()

    async def try_handle(
        self,
        *,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        rider_phone: str,
        text: str,
        provider_message_id: str | None = None,
    ) -> RiderFlowResult:
        rider = await self._lookup_rider(rider_phone)
        if rider is None:
            return RiderFlowResult(handled=False, action=None, reply_text="", sent=False)

        if not rider.active:
            return await self._reply(
                to=rider.phone,
                conversation_id=conversation_id,
                contact_id=contact_id,
                action="rider_inactive",
                reply=RIDER_INACTIVE,
                delivery=None,
                provider_message_id=provider_message_id,
            )

        command = parse_rider_command(text)
        delivery = await self.deliveries.latest_active_for_rider(self.tenant_id, rider.id)

        if delivery is None:
            if command is None:
                # Nothing in flight and not a quick reply: this may just be a
                # rider who is also a customer. Let the normal agent answer.
                return RiderFlowResult(handled=False, action=None, reply_text="", sent=False)
            return await self._reply(
                to=rider.phone,
                conversation_id=conversation_id,
                contact_id=contact_id,
                action="no_active_delivery",
                reply=NO_ACTIVE_DELIVERY,
                delivery=None,
                provider_message_id=provider_message_id,
            )

        if command is None:
            return await self._reply(
                to=rider.phone,
                conversation_id=conversation_id,
                contact_id=contact_id,
                action="help",
                reply=_HELP,
                delivery=delivery,
                provider_message_id=provider_message_id,
            )

        if command.action == "failed" and command.reason is None:
            return await self._reply(
                to=rider.phone,
                conversation_id=conversation_id,
                contact_id=contact_id,
                action="failed_needs_reason",
                reply=NEED_REASON,
                delivery=delivery,
                provider_message_id=provider_message_id,
            )

        return await self._dispatch(
            command=command,
            delivery=delivery,
            to=rider.phone,
            conversation_id=conversation_id,
            contact_id=contact_id,
            provider_message_id=provider_message_id,
        )

    # -- command dispatch --------------------------------------------------

    async def _dispatch(
        self,
        *,
        command: RiderCommand,
        delivery: Delivery,
        to: str,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        provider_message_id: str | None,
    ) -> RiderFlowResult:
        ref = _short_ref(delivery.order_id)
        reply: str
        action = command.action
        collection_due = False
        try:
            if command.action == "picked_up":
                await self.service.mark_picked_up(self.tenant_id, delivery.id)
                reply = _MESSAGES["picked_up", command.language].format(ref=ref)
            elif command.action == "arrived":
                # lock=True serializes concurrent arrivals. The POD STK request
                # is NOT fired here: the caller commits this arrival first, then
                # calls trigger_pod_collection in a fresh transaction, so an
                # at-least-once retry cannot double-prompt the customer.
                await self.service.mark_arrived(self.tenant_id, delivery.id, lock=True)
                collection_due = await self._collection_due(delivery)
                payment_note = PAYMENT_PENDING if collection_due else PAYMENT_NOT_NEEDED
                reply = _MESSAGES["arrived", command.language].format(ref=ref, payment=payment_note)
            elif command.action == "delivered":
                await self.service.mark_delivered(self.tenant_id, delivery.id)
                reply = _MESSAGES["delivered", command.language].format(ref=ref)
            elif command.action == "failed":
                if command.reason is None:  # guarded by try_handle; no assert in prod
                    raise DeliveryError("missing failure reason")
                await self.service.mark_failed(self.tenant_id, delivery.id, reason=command.reason)
                reply = _MESSAGES["failed", command.language].format(
                    ref=ref, reason=_REASON_LABEL[command.reason],
                )
            else:  # status
                reply = _MESSAGES["status", command.language].format(
                    ref=ref, status=delivery.status,
                )
        except ProofOfDeliveryRequiredError:
            # A POD handover must be closed with evidence; the text-only rider
            # channel cannot attach a photo, so hand it to the shop. (No money
            # or stock side effect has run.)
            action = "delivered_needs_shop"
            reply = POD_NEEDS_SHOP
        except PaymentNotCollectedError:
            action = "delivered_unpaid"
            reply = (
                f"Payment is not confirmed yet, so I cannot close order {ref}. "
                "Ask the customer to finish the M-Pesa prompt."
            )
        except (DeliveryError, ValueError) as exc:
            # Illegal transition / unknown delivery: tell the rider what to do
            # instead of failing silently. No money or stock side effect ran.
            action = f"{command.action}_rejected"
            reply = f"I could not update order {ref}: {exc}. Reply *status* to see where it is."
            logger.info("Rider quick reply rejected", delivery_id=str(delivery.id), reason=str(exc))

        return await self._reply(
            to=to,
            conversation_id=conversation_id,
            contact_id=contact_id,
            action=action,
            reply=reply,
            delivery=delivery,
            provider_message_id=provider_message_id,
            collection_due=collection_due,
        )

    # -- pay-on-delivery decision -----------------------------------------

    async def _collection_due(self, delivery: Delivery) -> bool:
        """True when this delivery's order is pay-on-delivery and still unpaid."""
        from packages.core.orders.service import is_order_paid

        order = await self.orders.get(self.tenant_id, delivery.order_id)
        if order is None or order.payment_method != PaymentMethod.MPESA_PAY_ON_DELIVERY.value:
            return False
        return not is_order_paid(order)

    # -- rider lookup ------------------------------------------------------

    async def _lookup_rider(self, raw_phone: str) -> Rider | None:
        candidates: list[str] = []
        with contextlib.suppress(ValueError):
            candidates.append(CustomerPhone.from_raw(raw_phone).value)
        candidates.append(raw_phone)
        for phone in candidates:
            rider = await self.deliveries.get_rider_by_phone(self.tenant_id, phone)
            if rider is not None:
                return rider
        return None

    # -- reply + audit -----------------------------------------------------

    async def _reply(
        self,
        *,
        to: str,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        action: str,
        reply: str,
        delivery: Delivery | None,
        provider_message_id: str | None,
        collection_due: bool = False,
    ) -> RiderFlowResult:
        sent = False
        send_error: str | None = None
        outbound_meta_id: str | None = None
        with contextlib.suppress(ValueError):
            if self._sender is not None:
                try:
                    outbound_meta_id = await self._sender.send_text(
                        self.tenant_id,
                        CustomerPhone.from_raw(to),
                        reply,
                        conversation_id,
                    )
                    sent = True
                except Exception as exc:  # closed window / provider error — keep draft
                    send_error = str(exc)
                    logger.warning(
                        "Rider reply send failed; storing draft",
                        action=action,
                        error=send_error,
                    )

        if conversation_id is not None and contact_id is not None:
            self.session.add(
                WhatsAppMessage(
                    tenant_id=self.tenant_id,
                    contact_id=contact_id,
                    conversation_id=conversation_id,
                    direction="outbound",
                    message_type=WhatsAppMessageType.TEXT,
                    status=WhatsAppMessageStatus.SENT if sent else WhatsAppMessageStatus.DRAFT,
                    text_body=reply,
                    meta_message_id=outbound_meta_id,
                    metadata_json={
                        "rider_flow": action,
                        "delivery_id": str(delivery.id) if delivery is not None else None,
                        "send_error": send_error,
                    },
                ),
            )
            conversation = await self.session.get(WhatsAppConversation, conversation_id)
            if conversation is not None:
                conversation.last_agent_message_at = datetime.now(UTC)

        await self.audit.log_action(
            tenant_id=self.tenant_id,
            user_id=None,
            action="delivery.rider_message_handled",
            resource="delivery",
            resource_id=delivery.id if delivery is not None else None,
            after={
                "rider_action": action,
                "sent": sent,
                "provider_message_id": provider_message_id,
                "collection_due": collection_due,
            },
        )
        await self.session.flush()

        return RiderFlowResult(
            handled=True,
            action=action,
            reply_text=reply,
            sent=sent,
            delivery_id=str(delivery.id) if delivery is not None else None,
            collection_due=collection_due,
            order_id=str(delivery.order_id) if delivery is not None else None,
        )


# =============================================================================
# Post-commit pay-on-delivery collection
# =============================================================================


async def trigger_pod_collection(
    *,
    tenant_id: UUID,
    delivery_id: UUID,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    resolve_provider: _ProviderResolver | None = None,
) -> bool:
    """Request POD collection for an arrived delivery, in its own transaction.

    MUST be called after the arrival has been committed. Idempotency is the
    arrival transition itself: a redelivered outbox event re-runs the flow,
    whose ``mark_arrived`` now fails, so ``collection_due`` is False and this is
    never called twice for one arrival. The payment carries the same
    ``delivery:{id}:arrived`` key an operator-triggered arrival uses.

    Returns True iff an STK request was actually sent. A failure is logged and
    swallows to False (a missed prompt is safer than a duplicate one; the order
    stays unpaid and visible to the owner).
    """
    from sqlalchemy import text

    from packages.core.orders.service import is_order_paid
    from packages.core.payments import OrderNotPayableError, PaymentService

    if session_factory is None:
        from socialerp.database import get_session_factory

        session_factory = get_session_factory()
    resolver = resolve_provider or resolve_tenant_payment_provider

    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        delivery = await DeliveryRepository(session).get_delivery(tenant_id, delivery_id)
        if delivery is None:
            logger.warning("POD collection: delivery vanished", delivery_id=str(delivery_id))
            return False
        order = await OrderRepository(session).get(tenant_id, delivery.order_id)
        if (
            order is None
            or order.payment_method != PaymentMethod.MPESA_PAY_ON_DELIVERY.value
            or is_order_paid(order)
        ):
            return False

        callback_url = get_settings().DARAJA_CALLBACK_URL
        if not callback_url:
            logger.warning(
                "POD arrival but DARAJA_CALLBACK_URL is unset; "
                "cannot request collection automatically",
                delivery_id=str(delivery_id),
            )
            return False

        from packages.core.domain.payment import PaymentProviderError

        try:
            provider = await resolver(session, tenant_id)
            await PaymentService(session, provider).initiate_stk_payment(
                tenant_id=tenant_id,
                order_id=delivery.order_id,
                phone_number=order.customer_phone,
                callback_url=callback_url,
                idempotency_key=f"delivery:{delivery_id}:arrived",
            )
            await session.commit()
        except (ValueError, PaymentProviderError, OrderNotPayableError) as exc:
            logger.warning(
                "POD STK request failed at rider arrival",
                delivery_id=str(delivery_id),
                error=str(exc),
            )
            return False
        return True


# Re-exported for tests that need to stub provider resolution.
__all__ = [
    "RiderCommand",
    "RiderFlowResult",
    "RiderFlowService",
    "parse_rider_command",
    "trigger_pod_collection",
]
