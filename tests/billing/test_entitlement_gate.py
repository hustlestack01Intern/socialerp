"""M1.4: the entitlement gate denies agent LLM spend without breaking safety.

Invariant I3: the gate may only deny model spend + premium API. Inbound
persistence, the deterministic holding reply and human handoff must always work,
and a denied request must construct ZERO LLM clients (zero spend).
"""

from __future__ import annotations

from sqlalchemy import select, text

from packages.agent.intent import HeuristicIntentExtractor
from packages.conversation.agent_service import HOLDING_REPLY, ConversationAgentService
from packages.core.models.whatsapp import (
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppConversationState,
    WhatsAppMessage,
)

PHONE = "254712345678"


class RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, tenant_id, to, body, conversation_id=None) -> str | None:
        self.sent.append((str(to), body))
        return "wamid.outbound.test"


class ExplodingExtractor:
    """Any call is a test failure: the denied path must never build the LLM."""

    async def extract(self, message: str):  # pragma: no cover - asserted via flag
        raise AssertionError("LLM extractor must not be called on a denied tenant")


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


async def _set_status(session, tenant_id, status: str, *, period_end_sql: str | None = None):
    if period_end_sql:
        await session.execute(
            text(
                "UPDATE subscriptions SET status = :st, period_end = "
                + period_end_sql
                + " WHERE tenant_id = :tid"
            ),
            {"st": status, "tid": str(tenant_id)},
        )
    else:
        await session.execute(
            text("UPDATE subscriptions SET status = :st WHERE tenant_id = :tid"),
            {"st": status, "tid": str(tenant_id)},
        )
    await session.flush()


async def _outbound(session, conversation_id):
    rows = await session.execute(
        select(WhatsAppMessage).where(
            WhatsAppMessage.conversation_id == conversation_id,
            WhatsAppMessage.direction == "outbound",
        ),
    )
    return list(rows.scalars().all())


async def test_expired_tenant_is_denied_without_llm(tenant_session, tenant) -> None:
    await _set_status(
        tenant_session, tenant.id, "expired", period_end_sql="now() - interval '1 day'"
    )
    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    sender = RecordingSender()

    service = ConversationAgentService(
        tenant_session,
        tenant.id,
        extractor=ExplodingExtractor(),
        sender=sender,
    )
    result = await service.respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="nataka 2 black hoodie",
        provider_message_id="wamid.deny.1",
    )

    assert result.intent == "entitlement_denied"
    assert result.action == "expired"
    assert result.requires_human is True
    assert result.reply_text == HOLDING_REPLY
    # Nothing was auto-sent (owner can take over); a draft was persisted.
    assert sender.sent == []
    msgs = await _outbound(tenant_session, conversation.id)
    assert len(msgs) == 1
    assert msgs[0].status == "draft"
    assert msgs[0].text_body == HOLDING_REPLY


async def test_pending_tenant_is_denied(tenant_session, tenant) -> None:
    await _set_status(tenant_session, tenant.id, "pending", period_end_sql="NULL")
    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    service = ConversationAgentService(
        tenant_session, tenant.id, extractor=ExplodingExtractor(), sender=RecordingSender()
    )
    result = await service.respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="hello",
        provider_message_id="wamid.deny.2",
    )
    assert result.intent == "entitlement_denied"
    assert result.action == "pending"


async def test_past_due_tenant_is_still_entitled(tenant_session, tenant) -> None:
    """past_due keeps entitlement (never silence an in-flight customer, I4)."""
    await _set_status(tenant_session, tenant.id, "past_due")
    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    sender = RecordingSender()
    service = ConversationAgentService(
        tenant_session, tenant.id, extractor=HeuristicIntentExtractor(), sender=sender
    )
    result = await service.respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="hello there",
        provider_message_id="wamid.ok.1",
    )
    assert result.intent != "entitlement_denied"


async def test_gate_disabled_bypasses_check(tenant_session, tenant, monkeypatch) -> None:
    from socialerp.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "BILLING_ENTITLEMENT_ENABLED", False)
    await _set_status(
        tenant_session, tenant.id, "expired", period_end_sql="now() - interval '1 day'"
    )
    contact, conversation = await _seed_conversation(tenant_session, tenant.id)
    service = ConversationAgentService(
        tenant_session, tenant.id, extractor=HeuristicIntentExtractor(), sender=RecordingSender()
    )
    result = await service.respond_to_message(
        conversation_id=conversation.id,
        contact_id=contact.id,
        customer_phone=PHONE,
        text="hello",
        provider_message_id="wamid.flag.1",
    )
    assert result.intent != "entitlement_denied"
