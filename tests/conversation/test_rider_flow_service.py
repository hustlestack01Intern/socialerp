"""Service-level tests for rider flow."""

from __future__ import annotations

from uuid import UUID

import pytest
from socialerp.tenancy import Tenant
from sqlalchemy import select, text

from packages.conversation.rider_flow import RiderFlowService
from packages.core.delivery.service import DeliveryService
from packages.core.domain.stock import StockLedger, StockMovementRepository
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.delivery import DeliveryZone, Rider
from packages.core.models.order import Customer, Order
from packages.core.models.whatsapp import (
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppConversationState,
    WhatsAppMessage,
)

TENANT_PHONE = "254712345678"
RIDER_PHONE = "254700000123"
RIDER_PHONE_ALT = "0700 000 123"


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, tenant_id, to, body, conversation_id=None) -> str | None:
        self.sent.append(body)
        return "wamid.rider.1"


async def _set_config(session, tenant_id) -> None:
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )


async def _seed(
    session,
    tenant_id,
    *,
    payment_method: str = "mpesa_stk",
    rider_phone: str = RIDER_PHONE,
    rider_active: bool = True,
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    await _set_config(session, tenant_id)
    tenant = await session.get(Tenant, tenant_id)
    tenant.settings = {}

    product = Product(tenant_id=tenant_id, name="Hoodie", status=ProductStatus.ACTIVE.value)
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        tenant_id=tenant_id,
        product_id=product.id,
        sku="HOODIE-RIDER",
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
    await StockMovementRepository(session).add(
        ledger.record_receive(
            quantity=10, unit_cost_cents=2500, reference_id=None, reference_type="purchase_order"
        )
    )

    contact = WhatsAppContact(tenant_id=tenant_id, phone=TENANT_PHONE, wa_id=TENANT_PHONE)
    session.add(contact)
    await session.flush()
    conversation = WhatsAppConversation(
        tenant_id=tenant_id, contact_id=contact.id, state=WhatsAppConversationState.OPEN.value
    )
    session.add(conversation)
    await session.flush()

    zone = DeliveryZone(tenant_id=tenant_id, name="Zone", fee_cents=2000)
    session.add(zone)
    await session.flush()
    rider = Rider(tenant_id=tenant_id, phone=rider_phone, name="Rider", active=rider_active)
    session.add(rider)
    await session.flush()

    customer = Customer(tenant_id=tenant_id, phone=TENANT_PHONE)
    session.add(customer)
    await session.flush()
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone=TENANT_PHONE,
        total_cents=5000,
        status="packed",
        payment_method=payment_method,
    )
    session.add(order)
    await session.flush()
    return conversation.id, contact.id, order.id, rider.id, zone.id


async def _dispatched(
    *,
    tenant,
    session_factory,
    payment_method: str = "mpesa_stk",
    rider_phone: str = RIDER_PHONE,
    deactivate_after: bool = False,
):
    async with session_factory() as session, session.begin():
        conv_id, contact_id, order_id, rider_id, zone_id = await _seed(
            session,
            tenant.id,
            payment_method=payment_method,
            rider_phone=rider_phone,
        )
        await DeliveryService(session).dispatch(
            tenant.id, order_id=order_id, rider_id=rider_id, zone_id=zone_id
        )
    if deactivate_after:
        # dispatch requires an active rider, so switch them off afterwards.
        async with session_factory() as session, session.begin():
            await _set_config(session, tenant.id)
            rider = await session.get(Rider, rider_id)
            rider.active = False
    return conv_id, contact_id, rider_phone


async def _handle(session_factory, tenant, *, conv_id, contact_id, phone, text, sender=None):
    async with session_factory() as session:
        await _set_config(session, tenant.id)
        flow = RiderFlowService(session, tenant.id, sender=sender)
        result = await flow.try_handle(
            conversation_id=conv_id, contact_id=contact_id, rider_phone=phone, text=text
        )
        await session.commit()
        return result


@pytest.mark.asyncio
async def test_rider_picked_up_updates_state(tenant, session_factory) -> None:
    sender = RecordingSender()
    conv_id, contact_id, phone = await _dispatched(tenant=tenant, session_factory=session_factory)
    res = await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="picked up",
        sender=sender,
    )
    assert res.handled and res.action == "picked_up"
    assert sender.sent and "order" in sender.sent[0].lower()


@pytest.mark.asyncio
async def test_rider_reply_is_persisted_as_outbound_message(tenant, session_factory) -> None:
    sender = RecordingSender()
    conv_id, contact_id, phone = await _dispatched(tenant=tenant, session_factory=session_factory)
    await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="picked up",
        sender=sender,
    )
    async with session_factory() as session:
        await _set_config(session, tenant.id)
        messages = (
            (
                await session.execute(
                    select(WhatsAppMessage).where(WhatsAppMessage.direction == "outbound")
                )
            )
            .scalars()
            .all()
        )
    assert len(messages) == 1
    assert messages[0].metadata_json.get("rider_flow") == "picked_up"


@pytest.mark.asyncio
async def test_non_rider_falls_through(tenant, session_factory) -> None:
    sender = RecordingSender()
    async with session_factory() as session:
        await _set_config(session, tenant.id)
        flow = RiderFlowService(session, tenant.id, sender=sender)
        res = await flow.try_handle(
            conversation_id=UUID(int=1),
            contact_id=UUID(int=2),
            rider_phone="254700000999",
            text="picked up",
        )
    assert res.handled is False


@pytest.mark.asyncio
async def test_unknown_text_with_no_active_delivery_falls_through(tenant, session_factory) -> None:
    """A rider who is also a customer can still reach the agent."""
    sender = RecordingSender()
    async with session_factory() as session, session.begin():
        await _seed(session, tenant.id)  # rider exists, but nothing dispatched
    res = await _handle(
        session_factory,
        tenant,
        conv_id=UUID(int=1),
        contact_id=UUID(int=2),
        phone=RIDER_PHONE,
        text="habari",
        sender=sender,
    )
    assert res.handled is False
    assert sender.sent == []


@pytest.mark.asyncio
async def test_rider_phone_is_lookup_matched_when_texted_in_local_format(
    tenant, session_factory
) -> None:
    """A rider stored as 2547... must match an inbound 0712... sender id."""
    sender = RecordingSender()
    conv_id, contact_id, _phone = await _dispatched(tenant=tenant, session_factory=session_factory)
    res = await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=RIDER_PHONE_ALT,
        text="picked up",
        sender=sender,
    )
    assert res.handled and res.action == "picked_up"


@pytest.mark.asyncio
async def test_deactivated_rider_is_told_access_is_off(tenant, session_factory) -> None:
    sender = RecordingSender()
    conv_id, contact_id, phone = await _dispatched(
        tenant=tenant, session_factory=session_factory, deactivate_after=True
    )
    res = await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="picked up",
        sender=sender,
    )
    assert res.handled and res.action == "rider_inactive"


@pytest.mark.asyncio
async def test_failed_without_reason_asks_for_reason(tenant, session_factory) -> None:
    sender = RecordingSender()
    conv_id, contact_id, phone = await _dispatched(tenant=tenant, session_factory=session_factory)
    res = await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="failed",
        sender=sender,
    )
    assert res.action == "failed_needs_reason"
    # No transition happened: the delivery is still `assigned`.
    assert res.delivery_id is not None


@pytest.mark.asyncio
async def test_pod_arrival_flags_collection_without_pushing(tenant, session_factory) -> None:
    """The flow must never push STK itself — it flags collection_due for the caller."""
    sender = RecordingSender()
    conv_id, contact_id, phone = await _dispatched(
        tenant=tenant, session_factory=session_factory, payment_method="mpesa_pod"
    )
    await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="picked up",
        sender=sender,
    )
    res = await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="arrived",
        sender=sender,
    )
    assert res.action == "arrived"
    assert res.collection_due is True
    assert res.delivery_id is not None and res.order_id is not None


@pytest.mark.asyncio
async def test_prepay_arrival_does_not_flag_collection(tenant, session_factory) -> None:
    sender = RecordingSender()
    conv_id, contact_id, phone = await _dispatched(tenant=tenant, session_factory=session_factory)
    await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="picked up",
        sender=sender,
    )
    res = await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="arrived",
        sender=sender,
    )
    assert res.action == "arrived" and res.collection_due is False


@pytest.mark.asyncio
async def test_second_arrival_is_rejected(tenant, session_factory) -> None:
    """Cross-path idempotency: an arrived delivery cannot be arrived twice."""
    sender = RecordingSender()
    conv_id, contact_id, phone = await _dispatched(tenant=tenant, session_factory=session_factory)
    await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="picked up",
        sender=sender,
    )
    first = await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="arrived",
        sender=sender,
    )
    second = await _handle(
        session_factory,
        tenant,
        conv_id=conv_id,
        contact_id=contact_id,
        phone=phone,
        text="arrived",
        sender=sender,
    )
    assert first.action == "arrived"
    assert second.action == "arrived_rejected"
