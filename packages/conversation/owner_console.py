"""
Owner WhatsApp console (Phase 8c).

A separate agent surface keyed to the OWNER's phone number (AGENTS.md §2, §6):
the owner runs the business by chatting — "orders today?", "mark order <ref>
delivered", "restock 20 of SKU X", "alerts", "ack <alert id>".

Design (plan docs/plans/2026-10-18-phase-8c-owner-console.md, options 2+3):
* deterministic-first: fixed keyword/regex parser, no LLM on any command;
* identity: the sender must resolve to an ACTIVE ``OWNER`` user of THIS tenant
  by phone — anyone else falls through to the normal customer agent untouched;
* every command maps to an IntentResult and goes through the SAME PolicyEngine
  the customer channel uses; writes additionally face console-level gates
  (mark-delivered needs policy EXECUTE_ORDER i.e. L2+; restock needs L3);
* writes run only through existing protected services (DeliveryService,
  AlertService, the event-sourced stock ledger) — never a second code path to
  money/stock (AGENTS.md §5);
* injection markers in an OWNER message escalate to a human: the owner phone
  may be compromised (plan incentive review), so it is treated as untrusted;
* everything is audited (``console.action_executed``) with the full decision
  trace and the actor's phone (plan invariant 6);
* reads and acknowledgements are answered from the database and always sent:
  for the owner console the requester, the approver and the audience are the
  same person (U15: the console is the missing push channel), so the L0 draft
  step would only hide the answer from its sole consumer. The policy decision
  is still computed and audited. See ADR 0018.

If the sender is not an owner of this tenant, ``try_handle`` returns
``handled=False`` and the customer path runs exactly as before.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_DNS, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from socialerp.observability import get_logger
from sqlalchemy import Select, String, and_, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent.intent import Intent, IntentResult, detect_injection, detect_language
from packages.agent.policy import AutonomyLevel, PolicyDecision, PolicyEngine
from packages.conversation.agent_service import MessageSender, autonomy_from_settings
from packages.conversation.manager import ConversationManager
from packages.core.alerts.service import AlertService
from packages.core.audit.logger import AuditLogger
from packages.core.delivery.repository import DeliveryRepository
from packages.core.delivery.service import (
    DeliveryError,
    DeliveryService,
    PaymentNotCollectedError,
    ProofOfDeliveryRequiredError,
)
from packages.core.domain import CustomerPhone, TenantId
from packages.core.domain.order import OrderStatus, PaymentStatus
from packages.core.domain.stock import (
    StockLedger,
    StockMovement,
    StockMovementReason,
    StockMovementRepository,
    StockMovementType,
)
from packages.core.models.alert import AlertStatus, OwnerAlert
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.core import Tenant, User, UserRole
from packages.core.models.order import Order
from packages.core.models.stock import StockMovement as StockMovementModel
from packages.core.models.whatsapp import (
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from packages.core.repositories import OrderRepository

logger = get_logger(__name__)

_AUTONOMY_L3 = AutonomyLevel.L3_OPTIMIZE

# Order statuses that still need the owner's attention ("open").
_CLOSED_ORDER_STATUSES = (
    OrderStatus.DELIVERED.value,
    OrderStatus.CANCELLED.value,
    OrderStatus.REFUNDED.value,
    OrderStatus.FAILED_DELIVERY.value,
)

# Fixed UUIDv5 namespace for folding an inbound ``provider_message_id`` into
# the UUID-typed stock-ledger ``reference_id`` (F5: console restock idempotency
# on event replay). Derived via uuid5 of a stable project string under the
# standard DNS namespace so the key is identical across processes and
# restarts: same provider message id => same ledger reference, making a
# redelivered restock detectable.
_RESTOCK_IDEMPOTENCY_NAMESPACE = uuid5(NAMESPACE_DNS, "socialerp.owner-console.restock")

# =============================================================================
# Deterministic parser (fixed, tested, bilingual-light)
# =============================================================================


def _normalize_text(text: str) -> str:
    """Lowercase and strip to ``[0-9a-z-]`` word tokens.

    Hyphens are kept so UUIDs and SKUs ("HOODIE-RX", alert ids) survive
    normalization and can be extracted deterministically.
    """
    return re.sub(r"[^0-9a-z-]+", " ", text.lower()).strip()


def _contains(haystack: str, phrase: str) -> bool:
    return f" {phrase} " in f" {haystack} "


# Swahili vocabulary the console itself understands. ``detect_language`` does not
# know these owner-console words ("mapato", "maagizo", "ongeza", ...), so a
# Swahili owner command would otherwise be answered in English. The primary
# signal stays ``detect_language``; this only complements it for console commands.
_CONSOLE_SW_MARKERS = frozenset({
    "agizo",
    "maagizo",
    "kabidhi",
    "ongeza",
    "imethibitishwa",
    "arifa",
    "punguzo",
    "mapato",
    "mauzo",
    "msaada",
    "hali",
    "leo",
})


def _detect_owner_language(text: str) -> str:
    """Reply language for an owner message: ``en`` or ``sw`` (never ``sheng``)."""
    if detect_language(text) in ("sw", "sheng"):
        return "sw"
    words = set(re.findall(r"[a-z]+", text.lower()))
    if words & _CONSOLE_SW_MARKERS:
        return "sw"
    return "en"


class _Args(BaseModel):
    """Base for console command arguments: untrusted text, strict schema."""

    model_config = ConfigDict(extra="forbid")


class _NoArgs(_Args):
    pass


class AckAlertArgs(_Args):
    alert_id: UUID


class MarkDeliveredArgs(_Args):
    order_ref: str = Field(min_length=4, max_length=36)


class RestockArgs(_Args):
    quantity: int = Field(gt=0, le=100_000)
    target: str = Field(min_length=1, max_length=120)


class AlwaysHumanArgs(_Args):
    sensitive_action: str


@dataclass(frozen=True)
class OwnerCommand:
    """A parsed owner-console command."""

    command: str
    language: str  # "en" | "sw"
    args: _Args


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Always-human keywords, checked in order; each maps to a PolicyEngine
# ALWAYS_HUMAN_ACTIONS name so rule 3 escalates it at every autonomy level.
_ALWAYS_HUMAN: tuple[tuple[str, str, str], ...] = (
    ("refund", "refund", "refund"),
    ("delete", "delete_customer_data", "delete"),
    ("bulk", "bulk_marketing_send", "bulk"),
    ("broadcast", "bulk_marketing_send", "broadcast"),
    ("discount", "discount_beyond_limit", "discount"),
    ("punguzo", "discount_beyond_limit", "punguzo"),
    ("price", "price_change", "price"),
    ("bei", "price_change", "bei"),
)


def _parse_mark_delivered(normalized: str, language: str) -> OwnerCommand | None:
    patterns = (
        r"\bmark\s+(?:order|agizo)\s+([0-9a-f-]{4,36})\s+(?:delivered|kabidhi)\b",
        r"\b(?:order|agizo)\s+([0-9a-f-]{4,36})\s+delivered\b",
        r"\bdelivered\s+(?:order|agizo)\s+([0-9a-f-]{4,36})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match is not None:
            try:
                args = MarkDeliveredArgs(order_ref=match.group(1))
            except ValidationError:
                return None
            return OwnerCommand(command="mark_delivered", language=language, args=args)
    return None


def _parse_restock(normalized: str, language: str) -> OwnerCommand | None:
    match = re.search(
        r"\b(?:restock|ongeza)\s+(\d{1,6})\s+(?:of\s+|ya\s+)?(.+?)(?:\s+please)?$",
        normalized,
    )
    if match is None:
        return None
    try:
        args = RestockArgs(quantity=int(match.group(1)), target=match.group(2).strip())
    except ValidationError:
        return None
    if not args.target:
        return None
    return OwnerCommand(command="restock", language=language, args=args)


def _parse_ack(normalized: str, language: str) -> OwnerCommand | None:
    match = re.search(
        r"\b(?:ack|acknowledge|imethibitishwa)\s+(?:alert\s+|arifa\s+)?([0-9a-f-]{8,36})\b",
        normalized,
    )
    if match is None:
        return None
    try:
        args = AckAlertArgs(alert_id=UUID(match.group(1)))
    except (ValidationError, ValueError):
        return None
    return OwnerCommand(command="ack", language=language, args=args)


def parse_owner_command(text: str) -> OwnerCommand | None:
    """Map owner text to a command, or ``None`` when nothing matches.

    Deterministic only: no model, no network, no guessing. An unmatched or
    invalid message becomes ``None`` and the service answers with the help
    menu (never an improvised action).
    """
    if not text or not text.strip():
        return None
    normalized = _normalize_text(text)
    if not normalized:
        return None
    language = _detect_owner_language(text)

    command = _parse_mark_delivered(normalized, language)
    if command is not None:
        return command

    command = _parse_restock(normalized, language)
    if command is not None:
        return command

    command = _parse_ack(normalized, language)
    if command is not None:
        return command

    for keyword, sensitive_action, _phrase in _ALWAYS_HUMAN:
        if _contains(normalized, keyword):
            return OwnerCommand(
                command="always_human",
                language=language,
                args=AlwaysHumanArgs(sensitive_action=sensitive_action),
            )

    if any(
        _contains(normalized, phrase)
        for phrase in ("status", "summary", "hali", "open orders", "jumla")
    ):
        return OwnerCommand(command="status", language=language, args=_NoArgs())

    if any(
        _contains(normalized, phrase)
        for phrase in ("revenue", "sales today", "how much today", "mapato", "mauzo")
    ):
        return OwnerCommand(command="revenue_today", language=language, args=_NoArgs())

    if any(
        _contains(normalized, phrase)
        for phrase in (
            "orders today",
            "order today",
            "orders",
            "order count",
            "maagizo",
            "oders leo",
        )
    ):
        return OwnerCommand(command="orders_today", language=language, args=_NoArgs())

    if any(_contains(normalized, phrase) for phrase in ("alerts", "alert", "arifa")):
        return OwnerCommand(command="alerts", language=language, args=_NoArgs())

    if any(_contains(normalized, phrase) for phrase in ("help", "menu", "msaada")):
        return OwnerCommand(command="help", language=language, args=_NoArgs())

    return None


# =============================================================================
# Reply copy (short, WhatsApp-style, bilingual)
# =============================================================================

_COPY: dict[tuple[str, str], str] = {
    ("help", "en"): (
        "Owner console — reply with:\n"
        "• *orders today* — maagizo ya leo\n"
        "• *revenue today* — mapato (yaliyolipwa)\n"
        "• *status* — muhtasari\n"
        "• *alerts* — arifa wazi\n"
        "• *ack <alert id>* — pitisha arifa\n"
        "• *mark order <ref> delivered*\n"
        "• *restock <n> of <SKU>*\n"
        "Refunds/discounts/price changes always need a human on the dashboard."
    ),
    ("help", "sw"): (
        "Konso ya mwenye duka — jibu na:\n"
        "• *orders today* — maagizo ya leo\n"
        "• *revenue today* — mapato (yaliyolipwa)\n"
        "• *status* — muhtasari\n"
        "• *alerts* — arifa wazi\n"
        "• *ack <id ya arifa>* — pitisha arifa\n"
        "• *mark order <ref> delivered*\n"
        "• *restock <n> of <SKU>*\n"
        "Marejesho/punguzo/bei daima huhitaji mwanadamu kwenye dashibodi."
    ),
    ("orders_today", "en"): "Today: {n} order(s).",
    ("orders_today", "sw"): "Leo: maagizo {n}.",
    ("revenue_today", "en"): "Paid revenue today: KES {kes}.",
    ("revenue_today", "sw"): "Mapato ya leo (yaliyolipwa): KES {kes}.",
    ("status", "en"): "Today: {today} order(s). Open: {open}. Open alerts: {alerts}.",
    ("status", "sw"): "Leo: maagizo {today}. Wazi: {open}. Arifa wazi: {alerts}.",
    ("alerts_none", "en"): "No open alerts.",
    ("alerts_none", "sw"): "Hakuna arifa wazi.",
    ("alerts_head", "en"): "Open alerts:",
    ("alerts_head", "sw"): "Arifa wazi:",
    ("alerts_item", "en"): "• [{severity}] {title} — id {alert_id}",
    ("alerts_item", "sw"): "• [{severity}] {title} — id {alert_id}",
    ("ack_ok", "en"): "Alert acknowledged: {title}.",
    ("ack_ok", "sw"): "Arifa imepitishwa: {title}.",
    ("ack_already", "en"): "Alert already acknowledged: {title}.",
    ("ack_already", "sw"): "Arifa tayari imepitishwa: {title}.",
    ("ack_not_found", "en"): "No alert with that id. Reply *alerts* to list them.",
    ("ack_not_found", "sw"): "Hakuna arifa na hiyo id. Jibu *alerts* kuona orodha.",
    ("delivered_ok", "en"): "Order {ref} marked delivered ✅",
    ("delivered_ok", "sw"): "Agizo {ref} limekabidhiwa ✅",
    ("delivered_already", "en"): "Order {ref} is already delivered.",
    ("delivered_already", "sw"): "Agizo {ref} tayari limekabidhiwa.",
    ("delivered_blocked", "en"): (
        "Marking delivered needs autonomy L2 or higher (currently {level}). "
        "Nothing changed — use the dashboard."
    ),
    ("delivered_blocked", "sw"): (
        "Kukabidhi huhitaji L2 au zaidi (sasa ni {level}). Hakuna kilibadilishwa — tumia dashibodi."
    ),
    ("order_not_found", "en"): "No order matches {ref}. Nothing changed.",
    ("order_not_found", "sw"): "Hakuna agizo lenye {ref}. Hakuna kilibadilishwa.",
    ("order_ambiguous", "en"): (
        "More than one order matches {ref}; use the full id from the dashboard."
    ),
    ("order_ambiguous", "sw"): (
        "Zaidi ya agizo moja lina {ref}; tumia id kamili kutoka dashibodi."
    ),
    ("no_active_delivery", "en"): (
        "Order {ref} has no delivery in flight (status: {status}). Nothing changed."
    ),
    ("no_active_delivery", "sw"): (
        "Agizo {ref} halina delivery (hali: {status}). Hakuna kilibadilishwa."
    ),
    ("delivered_unpaid", "en"): (
        "Order {ref} is pay-on-delivery and payment is not collected yet; I cannot close it."
    ),
    ("delivered_unpaid", "sw"): ("Agizo {ref} ni lipa-ikifika na bado hajalipwa; siwezi kufunga."),
    ("delivered_needs_proof", "en"): (
        "Order {ref} is pay-on-delivery; a proof photo is required to close it (dashboard)."
    ),
    ("delivered_needs_proof", "sw"): (
        "Agizo {ref} ni lipa-ikifika; picha ya uthibitisho inahitajika (dashibodi)."
    ),
    ("delivered_rejected", "en"): "Could not update order {ref}: {error}",
    ("delivered_rejected", "sw"): "Haiwezekani kusasisha agizo {ref}: {error}",
    ("restock_ok", "en"): "Received {quantity} x {label}. Available now: {available}.",
    ("restock_ok", "sw"): "Imepokea {quantity} x {label}. Sasa zipo: {available}.",
    ("restock_already", "en"): (
        "Already received {quantity} x {label} for this message (duplicate "
        "delivery ignored). Available now: {available}."
    ),
    ("restock_already", "sw"): (
        "Tayari imepokea {quantity} x {label} kwa ujumbe huu (nakala "
        "imepuuzwa). Sasa zipo: {available}."
    ),
    ("restock_blocked", "en"): (
        "Restocking needs autonomy L3 (currently {level}). Nothing changed — use the dashboard."
    ),
    ("restock_blocked", "sw"): (
        "Kuongeza bidhaa huhitaji L3 (sasa ni {level}). Hakuna kilibadilishwa — tumia dashibodi."
    ),
    ("restock_not_found", "en"): "No product or SKU matches '{target}'. Nothing changed.",
    ("restock_not_found", "sw"): "Hakuna bidhaa au SKU kama '{target}'. Hakuna kilibadilishwa.",
    ("restock_ambiguous", "en"): ("Several items match '{target}'. Use the exact SKU."),
    ("restock_ambiguous", "sw"): "Bidhaa zingi zina '{target}'. Tumia SKU halisi.",
    ("always_human", "en"): (
        "'{keyword}' is always human-approved. Nothing was changed — handle it in the dashboard."
    ),
    ("always_human", "sw"): (
        "'{keyword}' inahitaji kibali cha mwanadamu. Hakuna kilibadilishwa — tumia dashibodi."
    ),
    ("injection", "en"): (
        "I can't run that automatically — it looks like a rule-change attempt. "
        "Nothing was changed. Reply *help* for commands, or 'talk to a human'."
    ),
    ("injection", "sw"): (
        "Siwezi kufanya hivyo kiotomatiki — inaonekana kama jaribio la kubadilisha "
        "kanuni. Hakuna kilibadilishwa. Jibu *help* kuona amri, au 'ongea na "
        "mwanadamu'."
    ),
}


def _t(key: str, language: str, **fmt: object) -> str:
    template = _COPY.get((key, language)) or _COPY[key, "en"]
    return template.format(**fmt) if fmt else template


def _kes(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _copy_lang(language: str) -> str:
    return "sw" if language in ("sw", "sheng") else "en"


# =============================================================================
# Flow
# =============================================================================


@dataclass(frozen=True)
class OwnerConsoleResult:
    """Observable outcome of one owner-console run (tests + wiring)."""

    handled: bool
    command: str | None
    decision: str | None  # PolicyAction value, None when no policy ran
    reply_text: str
    sent: bool
    requires_human: bool
    outcome: str  # audit vocabulary (see module docstring / ADR 0018)
    order_id: str = ""


_NOT_HANDLED = OwnerConsoleResult(
    handled=False,
    command=None,
    decision=None,
    reply_text="",
    sent=False,
    requires_human=False,
    outcome="not_owner",
)


@dataclass(frozen=True)
class _VariantMatch:
    variant: ProductVariant
    product_name: str


_VariantLookup = _VariantMatch | Literal["ambiguous", "not_found"]


class OwnerConsoleService:
    """Handle an inbound WhatsApp message from the owner's phone number."""

    def __init__(
        self,
        session: AsyncSession,
        tenant_id: TenantId,
        *,
        sender: MessageSender | None = None,
        policy: PolicyEngine | None = None,
        autonomy: AutonomyLevel | None = None,
    ) -> None:
        self.session = session
        self.tenant_id = tenant_id
        self._sender = sender
        self.policy = policy or PolicyEngine()
        self._autonomy = autonomy
        self.orders = OrderRepository(session)
        self.deliveries = DeliveryRepository(session)
        self.delivery_service = DeliveryService(session)
        self.alert_service = AlertService(session)
        self.movements = StockMovementRepository(session)
        self.audit = AuditLogger(session)

    # -- entry point -------------------------------------------------------

    async def try_handle(
        self,
        *,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        sender_phone: str,
        text: str,
        provider_message_id: str | None = None,
    ) -> OwnerConsoleResult:
        owner = await self._resolve_owner(sender_phone)
        if owner is None:
            # Not an owner of THIS tenant: customer path must run unchanged.
            return _NOT_HANDLED

        command = parse_owner_command(text)
        lang = _copy_lang(command.language if command is not None else _detect_owner_language(text))

        # Owner phone may be compromised; the owner channel gets the same
        # injection defense as the customer channel (plan invariant 5).
        if detect_injection(text):
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command if command is not None else None,
                args=command.args if command is not None else None,
                policy_decision=None,
                outcome="escalated",
                reply=_t("injection", lang),
                provider_message_id=provider_message_id,
                requires_human=True,
                injection=True,
            )

        if command is None or command.command == "help":
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command="help",
                args=None,
                policy_decision=None,
                outcome="help",
                reply=_t("help", lang),
                provider_message_id=provider_message_id,
            )

        autonomy = await self._load_autonomy()
        policy_decision = self._decide(command, autonomy)

        if command.command == "always_human":
            assert isinstance(command.args, AlwaysHumanArgs)
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="escalated",
                reply=_t("always_human", lang, keyword=command.args.sensitive_action),
                provider_message_id=provider_message_id,
                requires_human=True,
            )

        if command.command in ("orders_today", "revenue_today", "status"):
            return await self._run_read(
                command=command,
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                policy_decision=policy_decision,
                provider_message_id=provider_message_id,
            )

        if command.command == "alerts":
            return await self._run_alerts(
                command=command,
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                policy_decision=policy_decision,
                provider_message_id=provider_message_id,
            )

        if command.command == "ack":
            assert isinstance(command.args, AckAlertArgs)
            return await self._run_ack(
                command=command,
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                policy_decision=policy_decision,
                provider_message_id=provider_message_id,
            )

        if command.command == "mark_delivered":
            assert isinstance(command.args, MarkDeliveredArgs)
            if not policy_decision.may_execute:
                return await self._finalize(
                    conversation_id=conversation_id,
                    contact_id=contact_id,
                    sender_phone=sender_phone,
                    owner=owner,
                    command=command.command,
                    args=command.args,
                    policy_decision=policy_decision,
                    outcome="blocked_autonomy",
                    reply=_t("delivered_blocked", lang, level=autonomy.value),
                    provider_message_id=provider_message_id,
                )
            return await self._run_mark_delivered(
                command=command,
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                policy_decision=policy_decision,
                provider_message_id=provider_message_id,
            )

        # restock: policy EXECUTE_ORDER (L2+) plus the console's own L3 gate —
        # inventory optimization is an L3 action (AGENTS.md §6).
        assert isinstance(command.args, RestockArgs)
        if not policy_decision.may_execute:
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="blocked_autonomy",
                reply=_t("restock_blocked", lang, level=autonomy.value),
                provider_message_id=provider_message_id,
            )
        if autonomy is not _AUTONOMY_L3:
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="blocked_policy",
                reply=_t("restock_blocked", lang, level=autonomy.value),
                provider_message_id=provider_message_id,
                gate="console_l3",
            )
        return await self._run_restock(
            command=command,
            conversation_id=conversation_id,
            contact_id=contact_id,
            sender_phone=sender_phone,
            owner=owner,
            policy_decision=policy_decision,
            provider_message_id=provider_message_id,
        )

    async def aclose(self) -> None:
        """Release the sender's resources (no-op for test doubles).

        Only for standalone use: the agent-service hook shares the caller's
        sender and the caller keeps ownership of closing it.
        """
        close = getattr(self._sender, "close", None)
        if close is not None:
            await close()

    # -- identity ----------------------------------------------------------

    async def _resolve_owner(self, raw_phone: str) -> User | None:
        """The active OWNER user of THIS tenant whose phone matches the sender.

        Resolution is tenant-scoped: the inbound conversation already belongs
        to one tenant (the outbox event carries it), so the console operates
        strictly inside that tenant (plan invariant 7). A phone that owns a
        different tenant gets nothing here and falls through to the customer
        path.
        """
        result = await self.session.execute(
            select(User)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(
                User.tenant_id == self.tenant_id,
                User.role == UserRole.OWNER,
                User.is_active.is_(True),
                User.phone.is_not(None),
                Tenant.is_active.is_(True),
            )
            .order_by(User.created_at.asc(), User.id.asc()),
        )
        for user in result.scalars().all():
            if _phones_match(raw_phone, user.phone):
                return user
        return None

    @staticmethod
    def _normalize_phone(phone: str) -> str | None:
        with contextlib.suppress(ValueError):
            return CustomerPhone.from_raw(phone).value
        return None

    # -- autonomy / policy -------------------------------------------------

    async def _load_autonomy(self) -> AutonomyLevel:
        if self._autonomy is not None:
            return self._autonomy
        result = await self.session.execute(
            select(Tenant.settings).where(Tenant.id == self.tenant_id),
        )
        return autonomy_from_settings(result.scalar_one_or_none())

    def _decide(self, command: OwnerCommand, autonomy: AutonomyLevel) -> PolicyDecision:
        """Map a console command to the shared PolicyEngine.

        Reads and alert acknowledgements are informational (FAQ); writes reuse
        the CREATE_ORDER gate (EXECUTE_ORDER at L2+); always-human keywords are
        passed as ``sensitive_action`` so rule 3 escalates them. The console
        verbs ``order_mark_delivered``/``inventory_adjustment`` are NOT in
        ALWAYS_HUMAN_ACTIONS (the plan keeps them policy-gated, not banned) —
        extra console-level gates apply on top (L2 for delivery, L3 for restock).
        """
        if command.command in ("orders_today", "revenue_today", "status", "alerts", "ack"):
            result = IntentResult(
                intent=Intent.FAQ,
                confidence=0.95,
                language=command.language,
            )
            return self.policy.decide(result, autonomy=autonomy)

        if command.command == "always_human":
            assert isinstance(command.args, AlwaysHumanArgs)
            result = IntentResult(
                intent=Intent.FAQ,
                confidence=0.9,
                language=command.language,
            )
            return self.policy.decide(
                result, autonomy=autonomy, sensitive_action=command.args.sensitive_action,
            )

        # Writes: CREATE_ORDER is the policy's transaction gate (L2+).
        result = IntentResult(
            intent=Intent.CREATE_ORDER,
            confidence=0.95,
            language=command.language,
        )
        if command.command == "mark_delivered":
            sensitive = "order_mark_delivered"
        else:
            sensitive = "inventory_adjustment"
        return self.policy.decide(result, autonomy=autonomy, sensitive_action=sensitive)

    # -- reads -------------------------------------------------------------

    async def _run_read(
        self,
        *,
        command: OwnerCommand,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        sender_phone: str,
        owner: User,
        policy_decision: PolicyDecision,
        provider_message_id: str | None,
    ) -> OwnerConsoleResult:
        lang = command.language
        start_of_today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        if command.command == "orders_today":
            count = await self._scalar_int(
                select(func.count())
                .select_from(Order)
                .where(Order.tenant_id == self.tenant_id, Order.created_at >= start_of_today),
            )
            reply = _t("orders_today", lang, n=count)
        elif command.command == "revenue_today":
            # SQL aggregation, never model arithmetic: revenue = paid AND NOT
            # refunded — the same predicate as is_revenue_order in
            # packages/core/insights (plan 8b revenue definition; refund
            # exclusion refinement: refunds never clear paid_at — money-path
            # invariant, packages/core/payments/service.py:703-705 — so the
            # paid_at branch alone would count refunded orders at full value).
            # Pay-on-delivery paid-then-delivered (completed + paid_at) counts.
            cents = await self._scalar_int(
                select(func.coalesce(func.sum(Order.total_cents), 0)).where(
                    Order.tenant_id == self.tenant_id,
                    Order.created_at >= start_of_today,
                    and_(
                        or_(
                            Order.payment_status == PaymentStatus.COMPLETED.value,
                            Order.paid_at.is_not(None),
                        ),
                        Order.payment_status != PaymentStatus.REFUNDED.value,
                    ),
                ),
            )
            reply = _t("revenue_today", lang, kes=_kes(cents))
        else:
            today = await self._scalar_int(
                select(func.count())
                .select_from(Order)
                .where(Order.tenant_id == self.tenant_id, Order.created_at >= start_of_today),
            )
            open_orders = await self._scalar_int(
                select(func.count())
                .select_from(Order)
                .where(
                    Order.tenant_id == self.tenant_id,
                    Order.status.not_in(_CLOSED_ORDER_STATUSES),
                ),
            )
            open_alerts = await self._scalar_int(
                select(func.count())
                .select_from(OwnerAlert)
                .where(
                    OwnerAlert.tenant_id == self.tenant_id,
                    OwnerAlert.status == AlertStatus.OPEN.value,
                ),
            )
            reply = _t("status", lang, today=today, open=open_orders, alerts=open_alerts)

        return await self._finalize(
            conversation_id=conversation_id,
            contact_id=contact_id,
            sender_phone=sender_phone,
            owner=owner,
            command=command.command,
            args=command.args,
            policy_decision=policy_decision,
            outcome="answered",
            reply=reply,
            provider_message_id=provider_message_id,
        )

    async def _scalar_int(self, stmt: Select[Any]) -> int:
        result = await self.session.execute(stmt)
        value = result.scalar_one()
        return int(value or 0)

    # -- alerts ------------------------------------------------------------

    async def _run_alerts(
        self,
        *,
        command: OwnerCommand,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        sender_phone: str,
        owner: User,
        policy_decision: PolicyDecision,
        provider_message_id: str | None,
    ) -> OwnerConsoleResult:
        lang = command.language
        alerts = await self.alert_service.list_alerts(self.tenant_id, status="open", limit=5)
        if not alerts:
            reply = _t("alerts_none", lang)
        else:
            lines = [_t("alerts_head", lang)]
            for alert in alerts:
                lines.append(
                    _t(
                        "alerts_item",
                        lang,
                        severity=getattr(alert.severity, "value", alert.severity),
                        title=alert.title,
                        alert_id=str(alert.id),  # full UUID: the owner copies it into *ack*
                    ),
                )
            reply = "\n".join(lines)

        return await self._finalize(
            conversation_id=conversation_id,
            contact_id=contact_id,
            sender_phone=sender_phone,
            owner=owner,
            command="alerts",
            args=None,
            policy_decision=policy_decision,
            outcome="answered",
            reply=reply,
            provider_message_id=provider_message_id,
        )

    async def _run_ack(
        self,
        *,
        command: OwnerCommand,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        sender_phone: str,
        owner: User,
        policy_decision: PolicyDecision,
        provider_message_id: str | None,
    ) -> OwnerConsoleResult:
        assert isinstance(command.args, AckAlertArgs)
        lang = command.language
        alert = await self.alert_service.acknowledge(
            self.tenant_id, command.args.alert_id, user_id=owner.id,
        )
        if alert is None:
            reply = _t("ack_not_found", lang)
            outcome = "not_found"
        elif alert.acknowledged_by != owner.id or alert.acknowledged_at is None:
            # acknowledge() is idempotent and returns the row as-is when it was
            # already acknowledged; surface that honestly instead of re-acking.
            reply = _t("ack_already", lang, title=alert.title)
            outcome = "answered"
        else:
            reply = _t("ack_ok", lang, title=alert.title)
            outcome = "executed"

        return await self._finalize(
            conversation_id=conversation_id,
            contact_id=contact_id,
            sender_phone=sender_phone,
            owner=owner,
            command=command.command,
            args=command.args,
            policy_decision=policy_decision,
            outcome=outcome,
            reply=reply,
            provider_message_id=provider_message_id,
        )

    # -- writes ------------------------------------------------------------

    async def _run_mark_delivered(
        self,
        *,
        command: OwnerCommand,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        sender_phone: str,
        owner: User,
        policy_decision: PolicyDecision,
        provider_message_id: str | None,
    ) -> OwnerConsoleResult:
        assert isinstance(command.args, MarkDeliveredArgs)
        lang = command.language
        ref = command.args.order_ref

        order, ambiguous = await self._resolve_order_ref(ref)
        if ambiguous:
            reply = _t("order_ambiguous", lang, ref=ref)
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="ambiguous",
                reply=reply,
                provider_message_id=provider_message_id,
            )
        if order is None:
            reply = _t("order_not_found", lang, ref=ref)
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="not_found",
                reply=reply,
                provider_message_id=provider_message_id,
            )

        short = str(order.id)[:8].upper()
        if order.status == OrderStatus.DELIVERED.value:
            reply = _t("delivered_already", lang, ref=short)
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="answered",
                reply=reply,
                provider_message_id=provider_message_id,
                order_id=str(order.id),
            )

        delivery = await self.deliveries.active_delivery_for_order(self.tenant_id, order.id)
        if delivery is None:
            reply = _t("no_active_delivery", lang, ref=short, status=order.status)
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="not_found",
                reply=reply,
                provider_message_id=provider_message_id,
                order_id=str(order.id),
            )

        # The delivery FSM and its payment/proof gates stay authoritative: this
        # console never bypasses them (plan invariant 2).
        try:
            await self.delivery_service.mark_delivered(self.tenant_id, delivery.id)
        except PaymentNotCollectedError:
            reply = _t("delivered_unpaid", lang, ref=short)
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="error_relayed",
                reply=reply,
                provider_message_id=provider_message_id,
                order_id=str(order.id),
            )
        except ProofOfDeliveryRequiredError:
            reply = _t("delivered_needs_proof", lang, ref=short)
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="error_relayed",
                reply=reply,
                provider_message_id=provider_message_id,
                order_id=str(order.id),
            )
        except DeliveryError as exc:
            logger.info(
                "Owner console mark-delivered rejected",
                order_id=str(order.id),
                reason=str(exc),
            )
            reply = _t("delivered_rejected", lang, ref=short, error=str(exc))
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="error_relayed",
                reply=reply,
                provider_message_id=provider_message_id,
                order_id=str(order.id),
            )

        return await self._finalize(
            conversation_id=conversation_id,
            contact_id=contact_id,
            sender_phone=sender_phone,
            owner=owner,
            command=command.command,
            args=command.args,
            policy_decision=policy_decision,
            outcome="executed",
            reply=_t("delivered_ok", lang, ref=short),
            provider_message_id=provider_message_id,
            order_id=str(order.id),
        )

    async def _resolve_order_ref(self, order_ref: str) -> tuple[Order | None, bool]:
        """Resolve a full UUID or a 4-32 hex prefix; (order, ambiguous)."""
        ref = order_ref.strip().lower()
        if _UUID_RE.fullmatch(ref):
            order = await self.orders.get(self.tenant_id, UUID(ref))
            return order, False
        if not re.fullmatch(r"[0-9a-f]{4,36}", ref):
            return None, False
        result = await self.session.execute(
            select(Order)
            .where(
                Order.tenant_id == self.tenant_id,
                cast(Order.id, String).like(f"{ref}%"),
            )
            .order_by(Order.created_at.desc())
            .limit(2),
        )
        rows = list(result.scalars().all())
        if len(rows) > 1:
            return None, True
        return (rows[0] if rows else None), False

    async def _run_restock(
        self,
        *,
        command: OwnerCommand,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        sender_phone: str,
        owner: User,
        policy_decision: PolicyDecision,
        provider_message_id: str | None,
    ) -> OwnerConsoleResult:
        assert isinstance(command.args, RestockArgs)
        lang = command.language
        match = await self._resolve_variant(command.args.target)
        if match == "not_found":
            reply = _t("restock_not_found", lang, target=command.args.target)
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="not_found",
                reply=reply,
                provider_message_id=provider_message_id,
                gate="console_l3",
            )
        if match == "ambiguous":
            reply = _t("restock_ambiguous", lang, target=command.args.target)
            return await self._finalize(
                conversation_id=conversation_id,
                contact_id=contact_id,
                sender_phone=sender_phone,
                owner=owner,
                command=command.command,
                args=command.args,
                policy_decision=policy_decision,
                outcome="ambiguous",
                reply=reply,
                provider_message_id=provider_message_id,
                gate="console_l3",
            )

        assert isinstance(match, _VariantMatch)
        # Serialize concurrent restocks/orders for the same variant exactly the
        # way OrderService does, then go through the event-sourced ledger: stock
        # stays the sum of immutable movements (AGENTS.md §5, §8).
        await self._lock_variant(match.variant.id)

        # Idempotency (F5): the worker's outbox relay marks the event processed
        # in a SEPARATE transaction from this handler's commit
        # (packages/core/socialerp/outbox.py vs apps/worker/main.py), so a
        # crash between them redelivers the inbound_message event. Order
        # creation dedupes via idempotency_key = provider_message_id; without
        # an equivalent guard the append-only ledger would book this RECEIVE
        # twice, permanently. StockLedger.reference_id is UUID-typed
        # (packages/core/domain/stock.py), so the provider message id is folded
        # into a deterministic UUIDv5 key; the key is stored as the movement's
        # reference_id and checked BEFORE recording — mirroring
        # OrderService.get_by_idempotency_key. A replay returns the same
        # success facts (label + live availability) honestly labelled as a
        # duplicate, never a second movement.
        restock_key: UUID | None = None
        if provider_message_id is not None:
            restock_key = uuid5(_RESTOCK_IDEMPOTENCY_NAMESPACE, provider_message_id)
            already = (
                await self.session.execute(
                    select(StockMovementModel.id)
                    .where(
                        StockMovementModel.tenant_id == self.tenant_id,
                        StockMovementModel.reference_id == restock_key,
                        StockMovementModel.movement_type == StockMovementType.RECEIVE.value,
                    )
                    .limit(1),
                )
            ).first()
            if already is not None:
                ledger = await self._load_ledger(match.variant.product_id, match.variant.id)
                label = f"{match.product_name} ({match.variant.sku})"
                reply = _t(
                    "restock_already",
                    lang,
                    quantity=command.args.quantity,
                    label=label,
                    available=ledger.available,
                )
                return await self._finalize(
                    conversation_id=conversation_id,
                    contact_id=contact_id,
                    sender_phone=sender_phone,
                    owner=owner,
                    command=command.command,
                    args=command.args,
                    policy_decision=policy_decision,
                    outcome="already_applied",
                    reply=reply,
                    provider_message_id=provider_message_id,
                    gate="console_l3",
                )

        ledger = await self._load_ledger(match.variant.product_id, match.variant.id)
        movement = ledger.record_receive(
            quantity=command.args.quantity,
            unit_cost_cents=None,
            reference_id=restock_key,
            reference_type="owner_console",
            reason=StockMovementReason.OTHER,
            notes=f"owner console restock by user {owner.id}",
            created_by=owner.id,
        )
        await self.movements.add(movement)

        label = f"{match.product_name} ({match.variant.sku})"
        reply = _t(
            "restock_ok",
            lang,
            quantity=command.args.quantity,
            label=label,
            available=ledger.available,
        )
        return await self._finalize(
            conversation_id=conversation_id,
            contact_id=contact_id,
            sender_phone=sender_phone,
            owner=owner,
            command=command.command,
            args=command.args,
            policy_decision=policy_decision,
            outcome="executed",
            reply=reply,
            provider_message_id=provider_message_id,
            gate="console_l3",
        )

    async def _resolve_variant(self, target: str) -> _VariantLookup:
        """Exact SKU, else exact product name, else a unique partial match."""
        needle = target.strip()
        base = (
            select(ProductVariant, Product.name)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(
                ProductVariant.tenant_id == self.tenant_id,
                ProductVariant.is_active.is_(True),
                Product.status == ProductStatus.ACTIVE.value,
            )
        )

        for condition in (
            func.lower(ProductVariant.sku) == needle.lower(),
            func.lower(Product.name) == needle.lower(),
        ):
            result = await self.session.execute(base.where(condition).limit(2))
            rows = result.all()
            if len(rows) == 1:
                variant, product_name = rows[0]
                return _VariantMatch(variant=variant, product_name=product_name)
            if len(rows) > 1:
                return "ambiguous"

        result = await self.session.execute(
            base.where(
                or_(
                    ProductVariant.sku.ilike(f"%{needle}%"),
                    Product.name.ilike(f"%{needle}%"),
                ),
            ).limit(2),
        )
        rows = result.all()
        if len(rows) == 1:
            variant, product_name = rows[0]
            return _VariantMatch(variant=variant, product_name=product_name)
        if len(rows) > 1:
            return "ambiguous"
        return "not_found"

    async def _lock_variant(self, variant_id: UUID) -> None:
        await self.session.execute(
            text(
                "SELECT 1 FROM product_variants "
                "WHERE id = :id AND tenant_id = :tenant_id FOR UPDATE",
            ),
            {"id": str(variant_id), "tenant_id": str(self.tenant_id)},
        )

    async def _load_ledger(self, product_id: UUID, variant_id: UUID) -> StockLedger:
        rows = await self.movements.get_by_tenant_product(self.tenant_id, product_id, variant_id)
        movements = [
            StockMovement(
                id=row.id,
                tenant_id=row.tenant_id,
                product_id=row.product_id,
                variant_id=row.variant_id,
                movement_type=StockMovementType(row.movement_type),
                quantity_change=row.quantity_change,
                reason=StockMovementReason(row.reason),
                reference_id=row.reference_id,
                reference_type=row.reference_type,
                previous_available=row.previous_available,
                new_available=row.new_available,
                unit_cost_cents=row.unit_cost_cents,
                notes=row.notes or "",
                created_at=row.created_at,
                created_by=row.created_by,
            )
            for row in rows
        ]
        ledger = StockLedger(tenant_id=self.tenant_id, product_id=product_id, variant_id=variant_id)
        ledger.load(movements)
        return ledger

    # -- reply + audit -----------------------------------------------------

    async def _finalize(
        self,
        *,
        conversation_id: UUID | None,
        contact_id: UUID | None,
        sender_phone: str,
        owner: User,
        command: str | None,
        args: _Args | None,
        policy_decision: PolicyDecision | None,
        outcome: str,
        reply: str,
        provider_message_id: str | None,
        requires_human: bool = False,
        injection: bool = False,
        order_id: str = "",
        gate: str = "policy",
    ) -> OwnerConsoleResult:
        if requires_human and conversation_id is not None:
            manager = ConversationManager(self.session)
            await manager.set_handoff(
                tenant_id=self.tenant_id,
                conversation_id=conversation_id,
                reason=f"owner_console:{command or 'help'}",
            )

        sent = False
        send_error: str | None = None
        outbound_meta_id: str | None = None
        to = self._normalize_phone(sender_phone)
        if to is not None and self._sender is not None:
            try:
                outbound_meta_id = await self._sender.send_text(
                    self.tenant_id,
                    CustomerPhone(to),
                    reply,
                    conversation_id,
                )
                sent = True
            except Exception as exc:  # provider error — keep the draft
                send_error = str(exc)
                logger.warning(
                    "Owner console reply send failed; storing draft",
                    command=command,
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
                        "owner_console": command,
                        "outcome": outcome,
                        "send_error": send_error,
                    },
                ),
            )
            conversation = await self.session.get(WhatsAppConversation, conversation_id)
            if conversation is not None:
                conversation.last_agent_message_at = datetime.now(UTC)

        await self.audit.log_action(
            tenant_id=self.tenant_id,
            user_id=owner.id,
            action="console.action_executed",
            resource="owner_console",
            after={
                "command": command,
                "args": args.model_dump(mode="json") if args is not None else None,
                "decision": policy_decision.action.value if policy_decision else None,
                "risk": policy_decision.risk.value if policy_decision else None,
                "reason": policy_decision.reason if policy_decision else None,
                "autonomy": policy_decision.autonomy.value if policy_decision else None,
                "gate": gate,
                "outcome": outcome,
                "injection_detected": injection,
                "actor_phone": sender_phone,  # plan invariant 6; never logged
                "provider_message_id": provider_message_id,
                "order_id": order_id or None,
                "sent": sent,
            },
        )
        await self.session.flush()

        return OwnerConsoleResult(
            handled=True,
            command=command,
            decision=policy_decision.action.value if policy_decision else None,
            reply_text=reply,
            sent=sent,
            requires_human=requires_human,
            outcome=outcome,
            order_id=order_id,
        )


def _phones_match(raw_sender: str, stored: str | None) -> bool:
    """True when the inbound sender phone is the stored owner phone.

    Matches exact strings and normalized Kenyan forms (``07…``/``+254…``/``254…``)
    because users.phone is written by the register/profile flows in more than
    one shape.
    """
    if not stored:
        return False
    if raw_sender.strip() == stored.strip():
        return True
    sender_n = OwnerConsoleService._normalize_phone(raw_sender)
    stored_n = OwnerConsoleService._normalize_phone(stored)
    return sender_n is not None and sender_n == stored_n


__all__ = [
    "OwnerCommand",
    "OwnerConsoleResult",
    "OwnerConsoleService",
    "parse_owner_command",
]
