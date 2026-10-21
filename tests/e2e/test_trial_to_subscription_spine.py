"""M1.6 (E1) vertical experiment: the trial -> subscription spine, end to end.

Plan: ``docs/plans/2026-10-19-trial-spine-first-clients.md`` step 12 (M1.6).

This is the vertical slice that proves a prospect can actually be *sold* a trial
and then *converted*:

    register (public route)
      -> channel connect starts the trial clock
      -> the trial expires (clock-forward)
      -> inbound is DENIED: 0 LLM calls, holding reply, human handoff, audit
      -> the owner pays (unique M-Pesa reference) and the plan changes
      -> inbound is ANSWERED again (the agent spends again)

Everything here runs against the REAL services and the REAL HTTP routes. The
only doubles are the two external edges — the LLM gateway and the Meta sender —
which are faked at the boundary on purpose (no network, no real money).

Why it matters: each step was unit-tested on its own, but a spine is only as
strong as its weakest joint. This file is the joint test: it fails if any link
in the chain stops being wired to the next one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from packages.agent.gateway import ScriptedGateway
from packages.agent.intent import LLMIntentExtractor
from packages.conversation.agent_service import HOLDING_REPLY, ConversationAgentService
from packages.core.models.core import AuditLog
from packages.core.models.whatsapp import (
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppConversationState,
    WhatsAppMessage,
)

pytestmark = pytest.mark.integration

CUSTOMER_PHONE = "254712345678"

# A valid intent payload for the scripted gateway (see tests/agent/test_intent.py).
_FAQ_PAYLOAD = json.dumps({"intent": "faq", "confidence": 0.9, "language": "en"})


class RecordingSender:
    """Stands in for the Meta sender; records what would have been sent."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_text(self, tenant_id, to, body, conversation_id=None) -> str | None:
        self.sent.append((str(to), body))
        return "wamid.outbound.spine"


async def _subscription(session, tenant_id: UUID) -> dict[str, object]:
    row = (
        await session.execute(
            text(
                "SELECT plan, status, period_end, extension_count "
                "FROM subscriptions WHERE tenant_id = :tid"
            ),
            {"tid": str(tenant_id)},
        )
    ).one_or_none()
    assert row is not None, "subscription row missing"
    return {
        "plan": row.plan,
        "status": row.status,
        "period_end": row.period_end,
        "extension_count": row.extension_count,
    }


async def _seed_contact(session, tenant_id: UUID) -> WhatsAppContact:
    """Get-or-create the customer contact (one identity per tenant + wa_id)."""
    existing = (
        await session.execute(
            select(WhatsAppContact).where(
                WhatsAppContact.tenant_id == tenant_id,
                WhatsAppContact.wa_id == CUSTOMER_PHONE,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    contact = WhatsAppContact(tenant_id=tenant_id, phone=CUSTOMER_PHONE, wa_id=CUSTOMER_PHONE)
    session.add(contact)
    await session.flush()
    return contact


async def _new_conversation(session, tenant_id: UUID, contact: WhatsAppContact):
    conversation = WhatsAppConversation(
        tenant_id=tenant_id,
        contact_id=contact.id,
        state=WhatsAppConversationState.OPEN,
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def _audit_actions(session, tenant_id: UUID) -> list[str]:
    rows = await session.execute(
        select(AuditLog.action).where(AuditLog.tenant_id == tenant_id)
    )
    return list(rows.scalars().all())


async def _outbound(session, conversation_id: UUID) -> list[WhatsAppMessage]:
    rows = await session.execute(
        select(WhatsAppMessage).where(
            WhatsAppMessage.conversation_id == conversation_id,
            WhatsAppMessage.direction == "outbound",
        )
    )
    return list(rows.scalars().all())


async def _spine_up_to_payment(api_client, session_factory) -> tuple[UUID, dict[str, str]]:
    """Steps 1-5 of the spine: signup -> trial -> expiry -> denial -> payment.

    Every invariant of those steps is asserted here, so both the green test and
    the xfail test below share one honest narrative.
    """
    email = f"spine-{uuid4().hex[:6]}@example.com"

    # ---------------------------------------------------------------- step 1
    # Signup is public and creates the tenant + owner + a PENDING subscription.
    resp = await api_client.post(
        "/api/v1/auth/register",
        json={
            "business_name": "Spine Traders",
            "slug": f"spine-{uuid4().hex[:8]}",
            "email": email,
            "owner_email": email,
            "owner_password": "S3cret-pass!",
            "owner_name": "Amina",
            "phone": "254712000111",
        },
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    async with session_factory() as session:
        tenant_id = (
            await session.execute(
                text("SELECT id FROM tenants WHERE owner_email = :email"),
                {"email": email},
            )
        ).scalar_one()

    async with session_factory() as session:
        sub = await _subscription(session, tenant_id)
    assert sub["plan"] == "trial"
    assert sub["status"] == "pending"
    # A signup that never connects a channel must NOT burn the trial clock.
    assert sub["period_end"] is None

    # ---------------------------------------------------------------- step 2
    # Connecting a channel (onboarding step 2) starts the 14-day trial.
    for step in (1, 2):
        started = await api_client.post(f"/api/v1/onboarding/steps/{step}/start", headers=headers)
        assert started.status_code == 200, started.text
        completed = await api_client.post(
            f"/api/v1/onboarding/steps/{step}/complete", headers=headers
        )
        assert completed.status_code == 200, completed.text

    async with session_factory() as session:
        sub = await _subscription(session, tenant_id)
    assert sub["status"] == "active"
    period_end = sub["period_end"]
    assert isinstance(period_end, datetime)
    days = (period_end - datetime.now(UTC)).days
    assert 13 <= days <= 14, f"trial should run 14 days, got {days}"

    # ---------------------------------------------------------------- step 3
    # Clock-forward: the trial expires 14 days later.
    async with session_factory() as session:
        await session.execute(
            text(
                "UPDATE subscriptions SET period_end = now() - interval '1 day' "
                "WHERE tenant_id = :tid"
            ),
            {"tid": str(tenant_id)},
        )
        await session.commit()

    # ---------------------------------------------------------------- step 4
    # An inbound customer message for an expired tenant is DENIED: no LLM spend,
    # a deterministic holding reply, a human handoff and an audit row.
    async with session_factory() as session:
        contact = await _seed_contact(session, tenant_id)
        conversation = await _new_conversation(session, tenant_id, contact)
        gateway = ScriptedGateway([_FAQ_PAYLOAD])
        sender = RecordingSender()
        service = ConversationAgentService(
            session,
            tenant_id,
            extractor=LLMIntentExtractor(gateway, model="scripted", tenant_id=str(tenant_id)),
            sender=sender,
        )
        result = await service.respond_to_message(
            conversation_id=conversation.id,
            contact_id=contact.id,
            customer_phone=CUSTOMER_PHONE,
            text="habari, do you have the hoodie?",
            provider_message_id="wamid.spine.denied",
        )
        await session.commit()

        assert result.intent == "entitlement_denied"
        assert result.action == "expired"
        assert result.reply_text == HOLDING_REPLY
        # Zero model spend on the denied path.
        assert gateway.calls == []
        # Nothing was auto-sent to the customer; the owner gets a draft.
        assert sender.sent == []
        drafts = await _outbound(session, conversation.id)
        assert len(drafts) == 1
        assert drafts[0].status == "draft"
        # The conversation is handed to a human, never dropped.
        conv = await session.get(WhatsAppConversation, conversation.id)
        assert conv is not None
        assert conv.state == WhatsAppConversationState.HANDOFF_REQUESTED

    async with session_factory() as session:
        actions = await _audit_actions(session, tenant_id)
    assert "billing.agent_denied" in actions

    # ---------------------------------------------------------------- step 5
    # The owner pays for a plan (manual M-Pesa reference) and the plan changes.
    resp = await api_client.post(
        "/api/v1/billing/subscription",
        headers=headers,
        json={"plan": "professional", "payment_reference": "QJG7XK2M9P"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan"] == "professional"

    async with session_factory() as session:
        sub = await _subscription(session, tenant_id)
        actions = await _audit_actions(session, tenant_id)
    assert sub["plan"] == "professional"
    assert sub["status"] == "active"
    assert "billing.apply_plan_change" in actions

    # The same M-Pesa code cannot pay for a second upgrade (F2/U68).
    resp = await api_client.post(
        "/api/v1/billing/subscription",
        headers=headers,
        json={"plan": "enterprise", "payment_reference": "QJG7XK2M9P"},
    )
    assert resp.status_code == 409, resp.text

    return tenant_id, headers


async def test_trial_to_subscription_spine(api_client, session_factory) -> None:
    """Signup -> trial -> expiry -> denial -> the sale is recorded."""
    tenant_id, _headers = await _spine_up_to_payment(api_client, session_factory)
    assert tenant_id is not None


async def test_paid_plan_reopens_the_agent(api_client, session_factory) -> None:
    """After payment the customer is answered again (the joint the spine needs).

    This was the broken joint found by the experiment: a paid plan change did
    not open a paid period, so the expired trial's date survived the upgrade
    and the gate kept denying a tenant that had paid. Fixed by F3 (approved
    2026-10-21); the strict ``xfail`` marker was removed when it flipped.
    """
    tenant_id, _headers = await _spine_up_to_payment(api_client, session_factory)

    async with session_factory() as session:
        contact = await _seed_contact(session, tenant_id)
        conversation = await _new_conversation(session, tenant_id, contact)
        gateway = ScriptedGateway([_FAQ_PAYLOAD])
        sender = RecordingSender()
        service = ConversationAgentService(
            session,
            tenant_id,
            extractor=LLMIntentExtractor(gateway, model="scripted", tenant_id=str(tenant_id)),
            sender=sender,
        )
        result = await service.respond_to_message(
            conversation_id=conversation.id,
            contact_id=contact.id,
            customer_phone=CUSTOMER_PHONE,
            text="habari, do you have the hoodie?",
            provider_message_id="wamid.spine.resumed",
        )
        await session.commit()

        assert result.intent != "entitlement_denied"
        # The agent spends again — the gate only denied while the plan was unpaid.
        assert len(gateway.calls) == 1
        replies = await _outbound(session, conversation.id)
        assert len(replies) == 1
