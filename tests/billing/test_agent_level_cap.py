"""The plan's agent-level ceiling binds the agent (M1.6, U80).

The matrix says Starter grants L1 (answer) and Trial grants L2 (transact).
Before M1.6 the ceiling was published and never consulted, so a Starter
tenant that asked for L2 created orders anyway - the plan was decoration.

Two things must hold at once:

* a tenant whose plan grants less than it asked for runs at the ceiling
  (it still answers; it is not silenced), and
* a tenant whose plan grants what it asked for is untouched (the pilot's
  trial must keep taking orders).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from packages.agent.intent import HeuristicIntentExtractor
from packages.conversation.agent_service import ConversationAgentService
from packages.core.domain.stock import StockLedger, StockMovementRepository
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.core import AuditLog, Tenant
from packages.core.models.order import Order

pytestmark = pytest.mark.integration

PHONE = "254712345678"


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, tenant_id, to, body, conversation_id=None) -> str | None:
        self.sent.append((str(to), body))
        return "wamid.outbound.test"


async def _set_plan(session, tenant_id, plan: str) -> None:
    await session.execute(
        text(
            "UPDATE subscriptions SET plan = :plan, status = 'active', "
            "period_start = now(), period_end = now() + interval '30 days' "
            "WHERE tenant_id = :tid"
        ),
        {"plan": plan, "tid": str(tenant_id)},
    )
    await session.flush()


async def _set_autonomy_setting(session, tenant_id, level: str) -> None:
    tenant = await session.get(Tenant, tenant_id)
    tenant.settings = {**(tenant.settings or {}), "agent_autonomy": level}
    await session.flush()


async def _seed(session, tenant_id):
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

    from packages.core.models.whatsapp import (
        WhatsAppContact,
        WhatsAppConversation,
        WhatsAppConversationState,
    )

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


def _service(session, tenant_id, sender) -> ConversationAgentService:
    return ConversationAgentService(
        session, tenant_id, extractor=HeuristicIntentExtractor(), sender=sender
    )


async def _orders(session, tenant_id) -> list[Order]:
    rows = await session.execute(select(Order).where(Order.tenant_id == tenant_id))
    return list(rows.scalars().all())


async def _audit_actions(session, tenant_id) -> list[str]:
    rows = await session.execute(
        select(AuditLog.action).where(AuditLog.tenant_id == tenant_id)
    )
    return list(rows.scalars().all())


async def test_starter_plan_caps_an_l2_tenant_to_l1(tenant_session, tenant) -> None:
    """A Starter tenant that asks for L2 must not create orders."""
    await _set_plan(tenant_session, tenant.id, "starter")
    await _set_autonomy_setting(tenant_session, tenant.id, "L2")
    contact, conversation = await _seed(tenant_session, tenant.id)
    sender = RecordingSender()

    result = await _service(tenant_session, tenant.id, sender).respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="nataka 2 black hoodie",
        provider_message_id="wamid.cap.1",
    )

    # L1 cannot execute an order: the message is drafted for the owner.
    assert result.order_id == ""
    assert result.action != "execute_order"
    assert await _orders(tenant_session, tenant.id) == []
    # The cap is visible in the audit log: the plan limited the agent.
    assert "billing.agent_level_capped" in await _audit_actions(tenant_session, tenant.id)


async def test_the_trial_keeps_transacting(tenant_session, tenant) -> None:
    """The pilot must not break: a trial (L2) still takes orders."""
    await _set_autonomy_setting(tenant_session, tenant.id, "L2")
    contact, conversation = await _seed(tenant_session, tenant.id)
    sender = RecordingSender()

    result = await _service(tenant_session, tenant.id, sender).respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="nataka 2 black hoodie",
        provider_message_id="wamid.cap.2",
    )

    assert result.action == "execute_order"
    assert result.order_id
    assert len(await _orders(tenant_session, tenant.id)) == 1
    # No cap: the plan granted what the tenant asked for.
    assert "billing.agent_level_capped" not in await _audit_actions(tenant_session, tenant.id)
