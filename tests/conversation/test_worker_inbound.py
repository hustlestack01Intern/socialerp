"""Worker `inbound_message` handler: runs the agent in its own tenant session.

The handler is exercised through the real outbox handler entry point, with only
the service factory (extractor + sender) substituted so no network/model is used.
A replayed event must not create a second order.
"""

from __future__ import annotations

from uuid import UUID

from socialerp.tenancy import Tenant
from sqlalchemy import select, text

from apps.worker import main as worker
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

PHONE = "254712345678"


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, tenant_id, to, body, conversation_id=None) -> str | None:
        self.sent.append(body)
        return "wamid.outbound.worker"


async def _set_config(session, tenant_id) -> None:
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


async def _seed(session, tenant_id, *, autonomy="L2"):
    await _set_config(session, tenant_id)
    tenant = await session.get(Tenant, tenant_id)
    tenant.settings = {"agent_autonomy": autonomy}

    product = Product(tenant_id=tenant_id, name="Black Hoodie", status=ProductStatus.ACTIVE.value)
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        tenant_id=tenant_id,
        product_id=product.id,
        sku="HOOD-BLK-M",
        attributes=[],
        cost_price_cents=2500,
        sell_price_cents=5000,
        quantity=10,
        is_active=True,
    )
    session.add(variant)
    await session.flush()
    ledger = StockLedger(tenant_id=tenant_id, product_id=product.id, variant_id=variant.id)
    ledger.load([])
    movement = ledger.record_receive(
        quantity=10,
        unit_cost_cents=2500,
        reference_id=None,
        reference_type="purchase_order",
    )
    await StockMovementRepository(session).add(movement)

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
    await session.commit()
    return contact.id, conversation.id


async def test_handler_runs_agent_and_creates_order(tenant, session_factory, monkeypatch) -> None:
    async with session_factory() as session:
        contact_id, conversation_id = await _seed(session, tenant.id)

    sender = RecordingSender()

    def _factory(session, tenant_id):
        return ConversationAgentService(
            session, tenant_id, extractor=HeuristicIntentExtractor(), sender=sender
        )

    monkeypatch.setattr(worker, "build_conversation_agent_service", _factory)

    payload = {
        "message_id": "m1",
        "conversation_id": str(conversation_id),
        "contact_id": str(contact_id),
        "customer_phone": PHONE,
        "text_body": "nataka 2 black hoodie",
        "provider_message_id": "wamid.worker.1",
    }

    from socialerp.database import close_db

    try:
        await worker.handle_inbound_message(payload, tenant.id)
    finally:
        await close_db()

    assert len(sender.sent) == 1
    assert "KES 100" in sender.sent[0]

    async with session_factory() as session:
        await _set_config(session, tenant.id)
        orders = (
            (await session.execute(select(Order).where(Order.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
        assert len(orders) == 1

        messages = (
            (
                await session.execute(
                    select(WhatsAppMessage).where(
                        WhatsAppMessage.conversation_id == conversation_id,
                        WhatsAppMessage.direction == "outbound",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(messages) == 1
        assert messages[0].status == WhatsAppMessageStatus.SENT


async def test_replayed_event_does_not_create_a_second_order(
    tenant, session_factory, monkeypatch
) -> None:
    async with session_factory() as session:
        contact_id, conversation_id = await _seed(session, tenant.id)

    sender = RecordingSender()
    monkeypatch.setattr(
        worker,
        "build_conversation_agent_service",
        lambda session, tenant_id: ConversationAgentService(
            session, tenant_id, extractor=HeuristicIntentExtractor(), sender=sender
        ),
    )

    payload = {
        "message_id": "m1",
        "conversation_id": str(conversation_id),
        "contact_id": str(contact_id),
        "customer_phone": PHONE,
        "text_body": "nataka 2 black hoodie",
        "provider_message_id": "wamid.worker.dup",
    }

    from socialerp.database import close_db

    try:
        await worker.handle_inbound_message(payload, tenant.id)
        await worker.handle_inbound_message(payload, tenant.id)
    finally:
        await close_db()

    async with session_factory() as session:
        await _set_config(session, tenant.id)
        orders = (
            (await session.execute(select(Order).where(Order.tenant_id == tenant.id)))
            .scalars()
            .all()
        )
        assert len(orders) == 1  # idempotent on the provider message id


async def test_non_text_payload_is_skipped(tenant, session_factory, monkeypatch) -> None:
    calls: list[object] = []

    def _factory(_session, _tenant_id):
        calls.append(1)
        return None

    monkeypatch.setattr(worker, "build_conversation_agent_service", _factory)

    await worker.handle_inbound_message(
        {"conversation_id": str(UUID(int=0)), "contact_id": str(UUID(int=0)), "text_body": None},
        tenant.id,
    )
    assert calls == []
