"""The webhook enqueues exactly one agent job per inbound WhatsApp message."""

from __future__ import annotations

from datetime import UTC, datetime

from socialerp.outbox import Outbox
from sqlalchemy import select

from packages.core.models.whatsapp import WhatsAppMessage
from packages.integrations.meta.webhook import (
    _process_inbound_message,
    parse_whatsapp_webhook,
)

PHONE = "254712345678"


def _payload(message_id: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551234567",
                                "phone_number_id": "pni-1",
                            },
                            "contacts": [{"profile": {"name": "Test User"}, "wa_id": PHONE}],
                            "messages": [
                                {
                                    "from": PHONE,
                                    "id": message_id,
                                    "timestamp": str(int(datetime.now(UTC).timestamp())),
                                    "text": {"body": "nataka 2 black hoodie"},
                                    "type": "text",
                                },
                            ],
                        },
                        "field": "messages",
                    },
                ],
            },
        ],
    }


async def test_inbound_message_enqueues_agent_outbox_event(tenant_session, tenant) -> None:
    norm_msgs = parse_whatsapp_webhook(_payload("wamid.webhook.1"))
    assert len(norm_msgs) == 1

    await _process_inbound_message(tenant_session, tenant.id, norm_msgs[0], "wh-1")

    events = (
        (
            await tenant_session.execute(
                select(Outbox).where(
                    Outbox.tenant_id == tenant.id,
                    Outbox.event_type == "inbound_message",
                ),
            )
        )
        .scalars()
        .all()
    )

    assert len(events) == 1
    event = events[0]
    assert event.idempotency_key == "inbound_message:wamid.webhook.1"
    assert event.payload["provider_message_id"] == "wamid.webhook.1"
    assert event.payload["customer_phone"] == PHONE
    assert event.payload["text_body"] == "nataka 2 black hoodie"
    assert event.payload["conversation_id"]
    assert event.payload["contact_id"]


async def test_duplicate_message_is_deduped_and_not_re_enqueued(tenant_session, tenant) -> None:
    norm_msgs = parse_whatsapp_webhook(_payload("wamid.webhook.dup"))

    await _process_inbound_message(tenant_session, tenant.id, norm_msgs[0], "wh-1")
    await _process_inbound_message(tenant_session, tenant.id, norm_msgs[0], "wh-2")

    # Only one inbound message row and one outbox event (webhook dedupe).
    messages = (
        (
            await tenant_session.execute(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.tenant_id == tenant.id,
                    WhatsAppMessage.direction == "inbound",
                ),
            )
        )
        .scalars()
        .all()
    )
    assert len(messages) == 1

    events = (
        (
            await tenant_session.execute(
                select(Outbox).where(
                    Outbox.tenant_id == tenant.id,
                    Outbox.event_type == "inbound_message",
                ),
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
