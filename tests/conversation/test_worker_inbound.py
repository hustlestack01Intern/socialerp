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
from packages.core.models.delivery import Delivery
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


# =============================================================================
# Rider quick-reply interception (Phase 7b)
# =============================================================================


async def _seed_rider_delivery(session, tenant_id, *, payment_method="mpesa_stk"):
    """Seed a dispatched delivery whose rider phone == PHONE (the inbound id)."""
    from packages.core.delivery.service import DeliveryService
    from packages.core.models.delivery import DeliveryZone, Rider
    from packages.core.models.order import Customer, Order

    await _set_config(session, tenant_id)
    zone = DeliveryZone(tenant_id=tenant_id, name="Zone", fee_cents=2000)
    session.add(zone)
    await session.flush()
    rider = Rider(tenant_id=tenant_id, phone=PHONE, name="Rider", active=True)
    session.add(rider)
    await session.flush()
    customer = Customer(tenant_id=tenant_id, phone=PHONE)
    session.add(customer)
    await session.flush()
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone=PHONE,
        total_cents=5000,
        status="packed",
        payment_method=payment_method,
    )
    session.add(order)
    await session.flush()
    delivery = await DeliveryService(session).dispatch(
        tenant_id, order_id=order.id, rider_id=rider.id, zone_id=zone.id
    )
    await session.commit()
    return delivery.id


async def test_rider_quick_reply_bypasses_agent(tenant, session_factory, monkeypatch) -> None:
    sender = RecordingSender()
    async with session_factory() as session:
        contact_id, conversation_id = await _seed(session, tenant.id)
    async with session_factory() as session:
        delivery_id = await _seed_rider_delivery(session, tenant.id)

    agent_calls: list[object] = []
    monkeypatch.setattr(
        worker,
        "build_conversation_agent_service",
        lambda _s, _t: agent_calls.append(1) or None,
    )

    from packages.conversation.rider_flow import RiderFlowService

    monkeypatch.setattr(
        worker,
        "build_rider_flow_service",
        lambda session, tenant_id: RiderFlowService(session, tenant_id, sender=sender),
    )

    payload = {
        "message_id": "r1",
        "conversation_id": str(conversation_id),
        "contact_id": str(contact_id),
        "customer_phone": PHONE,
        "text_body": "picked up",
        "provider_message_id": "wamid.rider.worker.1",
    }

    from socialerp.database import close_db

    try:
        await worker.handle_inbound_message(payload, tenant.id)
    finally:
        await close_db()

    # The agent never ran, and the rider got a delivery acknowledgement.
    assert agent_calls == []
    assert sender.sent and "order" in sender.sent[0].lower()

    async with session_factory() as session:
        await _set_config(session, tenant.id)
        delivery = await session.get(Delivery, delivery_id)
        assert delivery.status == "picked_up"


async def test_pod_arrival_triggers_collection_after_commit(
    tenant, session_factory, monkeypatch
) -> None:
    """The STK trigger fires only after the arrival is committed and visible."""
    sender = RecordingSender()
    async with session_factory() as session:
        contact_id, conversation_id = await _seed(session, tenant.id)
    async with session_factory() as session:
        _delivery_id = await _seed_rider_delivery(session, tenant.id, payment_method="mpesa_pod")

    from packages.conversation.rider_flow import RiderFlowService

    monkeypatch.setattr(
        worker,
        "build_rider_flow_service",
        lambda session, tenant_id: RiderFlowService(session, tenant_id, sender=sender),
    )
    monkeypatch.setattr(
        worker,
        "build_conversation_agent_service",
        lambda _s, _t: (_ for _ in ()).throw(AssertionError("agent must not run")),
    )

    observed: list[str] = []

    async def _spy_trigger(*, tenant_id, delivery_id):
        # A fresh session must already see `arrived` — proves the worker
        # committed before calling us (B2: no prompt inside the transaction).
        async with session_factory() as session:
            await _set_config(session, tenant_id)
            delivery = await session.get(Delivery, delivery_id)
            observed.append(delivery.status)
        return True

    monkeypatch.setattr("packages.conversation.rider_flow.trigger_pod_collection", _spy_trigger)

    async def _run(text: str) -> None:
        payload = {
            "message_id": f"r-{text}",
            "conversation_id": str(conversation_id),
            "contact_id": str(contact_id),
            "customer_phone": PHONE,
            "text_body": text,
            "provider_message_id": f"wamid.rider.worker.{text}",
        }
        from socialerp.database import close_db

        try:
            await worker.handle_inbound_message(payload, tenant.id)
        finally:
            await close_db()

    await _run("picked up")
    await _run("arrived")

    assert observed == ["arrived"]  # committed, and only for the POD arrival


async def test_replayed_rider_arrival_does_not_prompt_twice(
    tenant, session_factory, monkeypatch
) -> None:
    """A redelivered arrived event re-runs the flow, is rejected, and never prompts."""
    sender = RecordingSender()
    async with session_factory() as session:
        contact_id, conversation_id = await _seed(session, tenant.id)
    async with session_factory() as session:
        delivery_id = await _seed_rider_delivery(session, tenant.id, payment_method="mpesa_pod")

    from packages.conversation.rider_flow import RiderFlowService

    monkeypatch.setattr(
        worker,
        "build_rider_flow_service",
        lambda session, tenant_id: RiderFlowService(session, tenant_id, sender=sender),
    )

    prompts: list[str] = []

    async def _spy_trigger(*, tenant_id, delivery_id):
        prompts.append(str(delivery_id))
        return True

    monkeypatch.setattr("packages.conversation.rider_flow.trigger_pod_collection", _spy_trigger)

    async def _run(text: str) -> None:
        payload = {
            "message_id": f"r-{text}",
            "conversation_id": str(conversation_id),
            "contact_id": str(contact_id),
            "customer_phone": PHONE,
            "text_body": text,
            "provider_message_id": f"wamid.rider.worker.{text}",
        }
        from socialerp.database import close_db

        try:
            await worker.handle_inbound_message(payload, tenant.id)
        finally:
            await close_db()

    await _run("picked up")
    await _run("arrived")
    await _run("arrived")  # replay: rejected transition -> collection_due stays False

    assert prompts == [str(delivery_id)]  # exactly one prompt
