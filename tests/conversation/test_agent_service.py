"""Conversation→agent bridge: one inbound message → audited, persisted outcome.

Uses a deterministic extractor and a recording sender so the test exercises the
real service logic, the real order tool and the real DB without the network.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from socialerp.tenancy import Tenant
from sqlalchemy import select

from packages.agent.intent import HeuristicIntentExtractor
from packages.conversation.agent_service import ConversationAgentService
from packages.core.domain.stock import StockLedger, StockMovementRepository
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.order import Order
from packages.core.models.whatsapp import (
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppConversationState,
    WhatsAppMessage,
    WhatsAppMessageStatus,
)
from packages.core.orders import OrderService

PHONE = "254712345678"


class RecordingSender:
    """Records sends; optionally raises to simulate a closed window/provider error."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail = fail

    async def send_text(self, tenant_id, to, body, conversation_id=None) -> str | None:
        if self.fail:
            raise ValueError("customer service window closed")
        self.sent.append((str(to), body))
        return "wamid.outbound.test"


async def _set_settings(session, tenant_id, settings: dict) -> None:
    tenant = await session.get(Tenant, tenant_id)
    tenant.settings = settings
    await session.flush()


async def _seed_conversation(session, tenant_id):
    contact = WhatsAppContact(tenant_id=tenant_id, phone=PHONE, wa_id=PHONE)
    session.add(contact)
    await session.flush()
    conversation = WhatsAppConversation(
        tenant_id=tenant_id,
        contact_id=contact.id,
        state=WhatsAppConversationState.OPEN,
    )
    session.add(conversation)
    await session.flush()
    return contact, conversation


async def _seed_variant(
    session, tenant_id, *, name="Black Hoodie", price_cents=5000, qty=10, attributes=None,
):
    product = Product(tenant_id=tenant_id, name=name, status=ProductStatus.ACTIVE.value)
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        tenant_id=tenant_id,
        product_id=product.id,
        sku="HOOD-BLK-M",
        attributes=attributes or [],
        cost_price_cents=price_cents // 2,
        sell_price_cents=price_cents,
        quantity=qty,
        is_active=True,
    )
    session.add(variant)
    await session.flush()

    ledger = StockLedger(tenant_id=tenant_id, product_id=product.id, variant_id=variant.id)
    ledger.load([])
    movement = ledger.record_receive(
        quantity=qty,
        unit_cost_cents=price_cents // 2,
        reference_id=None,
        reference_type="purchase_order",
    )
    await StockMovementRepository(session).add(movement)
    return variant


def _service(session, tenant_id, sender) -> ConversationAgentService:
    return ConversationAgentService(
        session, tenant_id, extractor=HeuristicIntentExtractor(), sender=sender,
    )


async def _outbound(session, conversation_id) -> list[WhatsAppMessage]:
    rows = await session.execute(
        select(WhatsAppMessage).where(
            WhatsAppMessage.conversation_id == conversation_id,
            WhatsAppMessage.direction == "outbound",
        ),
    )
    return list(rows.scalars().all())


async def test_l2_sends_reply_and_creates_order(tenant_session, tenant) -> None:
    await _set_settings(tenant_session, tenant.id, {"agent_autonomy": "L2"})
    await _seed_variant(tenant_session, tenant.id, price_cents=5000, qty=10)
    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    sender = RecordingSender()

    result = await _service(tenant_session, tenant.id, sender).respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="nataka 2 black hoodie deliver to Kilimani",
        provider_message_id="wamid.in.1",
    )

    assert result.sent is True
    assert result.order_id
    assert result.action == "execute_order"
    assert len(sender.sent) == 1
    assert "KES 100" in sender.sent[0][1]

    order = await OrderService(tenant_session).get_order(tenant.id, UUID(result.order_id))
    assert order.status == "awaiting_payment"
    assert order.total_cents == 10_000

    messages = await _outbound(tenant_session, conversation.id)
    assert len(messages) == 1
    assert messages[0].status == WhatsAppMessageStatus.SENT
    assert messages[0].meta_message_id == "wamid.outbound.test"

    refreshed = await tenant_session.get(WhatsAppConversation, conversation.id)
    assert refreshed.last_agent_message_at is not None


async def test_l0_stores_draft_and_does_not_send(tenant_session, tenant) -> None:
    await _seed_variant(tenant_session, tenant.id)
    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    sender = RecordingSender()

    result = await _service(tenant_session, tenant.id, sender).respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="nataka 2 black hoodie",
    )

    assert result.sent is False
    assert result.action == "draft_for_approval"
    assert result.order_id == ""
    assert sender.sent == []

    messages = await _outbound(tenant_session, conversation.id)
    assert len(messages) == 1
    assert messages[0].status == WhatsAppMessageStatus.DRAFT

    orders = (
        (await tenant_session.execute(select(Order).where(Order.tenant_id == tenant.id)))
        .scalars()
        .all()
    )
    assert orders == []


async def test_l1_faq_auto_sends_grounded_reply(tenant_session, tenant) -> None:
    await _set_settings(
        tenant_session,
        tenant.id,
        {
            "agent_autonomy": "L1",
            "hours": "9am-6pm",
            "delivery_areas": ["Kilimani"],
            "payment_methods": ["M-Pesa"],
        },
    )
    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    sender = RecordingSender()

    result = await _service(tenant_session, tenant.id, sender).respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="what are your hours?",
    )

    assert result.action == "auto_reply"
    assert result.sent is True
    assert result.grounded is True
    assert "9am-6pm" in sender.sent[0][1]


async def test_handoff_moves_conversation_and_keeps_draft(tenant_session, tenant) -> None:
    await _set_settings(tenant_session, tenant.id, {"agent_autonomy": "L2"})
    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    sender = RecordingSender()

    result = await _service(tenant_session, tenant.id, sender).respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="i want to talk to a human",
    )

    assert result.requires_human is True
    assert result.sent is False
    assert sender.sent == []

    refreshed = await tenant_session.get(WhatsAppConversation, conversation.id)
    assert refreshed.state == WhatsAppConversationState.HANDOFF_REQUESTED

    messages = await _outbound(tenant_session, conversation.id)
    assert len(messages) == 1
    assert messages[0].status == WhatsAppMessageStatus.DRAFT


async def test_send_failure_degrades_to_draft(tenant_session, tenant) -> None:
    await _set_settings(tenant_session, tenant.id, {"agent_autonomy": "L2"})
    await _seed_variant(tenant_session, tenant.id)
    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    sender = RecordingSender(fail=True)

    result = await _service(tenant_session, tenant.id, sender).respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="nataka 2 black hoodie",
    )

    assert result.sent is False
    assert result.order_id  # order still created before the send attempt

    messages = await _outbound(tenant_session, conversation.id)
    assert len(messages) == 1
    assert messages[0].status == WhatsAppMessageStatus.DRAFT
    assert messages[0].metadata_json["send_error"]


async def test_catalog_is_tenant_scoped(tenant_session, tenant) -> None:
    # The only product belongs to another tenant; this tenant's catalog is empty.
    await _set_settings(tenant_session, tenant.id, {"agent_autonomy": "L1"})
    other_tenant_id = uuid4()
    tenant_session.add(
        Tenant(
            id=other_tenant_id,
            name="Other Business",
            slug=f"other-{other_tenant_id.hex[:8]}",
            owner_phone="254700000000",
            owner_email="other@example.com",
            is_active=True,
            settings={},
        ),
    )
    await tenant_session.flush()
    await _seed_variant(tenant_session, other_tenant_id, name="Black Hoodie")

    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    sender = RecordingSender()

    result = await _service(tenant_session, tenant.id, sender).respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="how much is the black hoodie",
    )

    assert result.grounded is False
    assert "not sure" in result.reply_text.lower()
