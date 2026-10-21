"""
Meta Webhook Receiver (WhatsApp + Instagram)

FastAPI endpoints for receiving WhatsApp Cloud API and Instagram Graph API webhooks
with HMAC-SHA256 signature verification, dedupe, and normalization.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from socialerp.outbox import add_outbox_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.config import get_settings
from packages.core.database import get_db_session
from packages.core.domain import CustomerPhone, TenantId
from packages.core.events.whatsapp import (
    MessageDelivered,
    MessageFailed,
    MessageRead,
    MessageReceived,
)
from packages.core.models import (
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppDedupe,
    WhatsAppMessage,
)
from packages.core.models.instagram import (
    InstagramContact,
    InstagramConversation,
    InstagramConversationState,
    InstagramDedupe,
    InstagramDmEligibility,
    InstagramMessage,
    InstagramMessageStatus,
    InstagramMessageType,
)
from packages.core.models.whatsapp import (
    WhatsAppConversationState,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from packages.core.security import verify_hmac_sha256
from packages.integrations.meta.instagram_parser import (
    detect_meta_channel,
    normalize_instagram_comment,
    normalize_instagram_dm,
    normalize_instagram_story_reply,
    parse_instagram_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["Meta Webhooks"])

# =============================================================================
# Settings
# =============================================================================


def get_webhook_settings() -> Any:
    """Get current settings for webhook (avoids module-level caching issues)."""
    return get_settings()


# =============================================================================
# Dependencies
# =============================================================================


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Get database session."""
    async for session in get_db_session():
        yield session


# =============================================================================
# Helpers
# =============================================================================


def extract_tenant_id_from_webhook(payload: dict[str, Any]) -> str | None:
    """Extract tenant identifier from webhook payload.

    In production, this would come from the webhook URL path or a header.
    For now, we use the phone number ID from the payload to look up the tenant.
    """
    try:
        # Meta sends the phone_number_id in the entry
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if "metadata" in value:
                    meta = value.get("metadata", {}) if isinstance(value, dict) else {}
                    if isinstance(meta, dict):
                        pid = meta.get("phone_number_id")
                        if pid is not None:
                            return str(pid)
                    return None
    except Exception:
        pass
    return None


async def get_tenant_by_phone_number_id(db: AsyncSession, phone_number_id: str) -> str | None:
    """Look up tenant by WhatsApp phone number ID."""
    # In production, this would be a configured mapping
    # For now, we use a setting or derive from the phone number ID
    s = get_webhook_settings()
    return (
        s.meta_whatsapp_phone_number_id
        if s.meta_whatsapp_phone_number_id == phone_number_id
        else None
    )


async def _get_tenant_from_meta_webhook(
    db: AsyncSession, payload: dict[str, Any],
) -> TenantId | None:
    """Extract tenant ID from Meta webhook (WhatsApp or Instagram)."""
    channel = detect_meta_channel(payload)

    if channel == "whatsapp":
        phone_number_id = extract_tenant_id_from_webhook(payload)
        if not phone_number_id:
            s = get_webhook_settings()
            phone_number_id = s.meta_whatsapp_phone_number_id
        tenant_id_str = await get_tenant_by_phone_number_id(db, phone_number_id)
        if tenant_id_str:
            return TenantId(UUID(tenant_id_str))
    else:  # instagram
        # Instagram uses the IG Professional Account ID
        try:
            for entry in payload.get("entry", []):
                ig_professional_account_id = entry.get("id")
                if ig_professional_account_id:
                    # Look up tenant by Instagram account ID
                    tenant_id_str = await get_tenant_by_instagram_account_id(
                        db, ig_professional_account_id,
                    )
                    if tenant_id_str:
                        return TenantId(UUID(tenant_id_str))
        except Exception:
            pass

    return None


async def get_tenant_by_instagram_account_id(
    db: AsyncSession, ig_account_id: str,
) -> str | None:
    """Look up tenant by Instagram Professional Account ID."""
    # In production, this would be a configured mapping
    # For now, we use a setting
    s = get_webhook_settings()
    return (
        s.meta_instagram_account_id
        if s.meta_instagram_account_id == ig_account_id
        else None
    )


def parse_whatsapp_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse WhatsApp webhook payload into normalized message events."""
    messages = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue

            value = change.get("value", {})
            metadata = value.get("metadata", {})
            phone_number_id = metadata.get("phone_number_id")
            display_phone_number = metadata.get("display_phone_number")

            # Process messages
            for msg in value.get("messages", []):
                messages.append({
                    "phone_number_id": phone_number_id,
                    "display_phone_number": display_phone_number,
                    "message": msg,
                    "contacts": value.get("contacts", []),
                })

            # Process statuses
            for status_update in value.get("statuses", []):
                messages.append({
                    "phone_number_id": phone_number_id,
                    "display_phone_number": display_phone_number,
                    "status": status_update,
                })

    return messages


def normalize_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Normalize WhatsApp message to internal format."""
    msg_type = msg.get("type", "unknown")

    normalized = {
        "provider_message_id": msg.get("id"),
        "meta_timestamp": datetime.fromtimestamp(int(msg.get("timestamp", 0)), tz=UTC)
        if msg.get("timestamp")
        else None,
        "direction": "inbound",
        "message_type": msg_type,  # Keep original case to match enum values
        "status": "sent",
        "text_body": None,
        "media_url": None,
        "media_mime_type": None,
        "media_sha256": None,
        "media_caption": None,
        "interactive_data": None,
        "template_name": None,
        "template_language": None,
        "template_components": None,
        "context_message_id": None,
        "referenced_message_id": None,
    }

    # Extract content based on type
    if msg_type == "text":
        normalized["text_body"] = msg.get("text", {}).get("body")
    elif msg_type in ("image", "document", "audio", "video", "sticker"):
        media = msg.get(msg_type, {})
        normalized["media_url"] = media.get("link") or media.get("url")
        normalized["media_mime_type"] = media.get("mime_type")
        normalized["media_sha256"] = media.get("sha256")
        normalized["media_caption"] = media.get("caption")
    elif msg_type == "interactive":
        normalized["interactive_data"] = msg.get("interactive")
    elif msg_type == "template":
        template = msg.get("template", {})
        normalized["template_name"] = template.get("name")
        normalized["template_language"] = template.get("language", {}).get("code")
        normalized["template_components"] = template.get("components")
    elif msg_type == "location":
        normalized["interactive_data"] = {"location": msg.get("location")}
    elif msg_type == "contacts":
        normalized["interactive_data"] = {"contacts": msg.get("contacts")}
    elif msg_type == "reaction":
        normalized["interactive_data"] = {"reaction": msg.get("reaction")}

    # Context (reply/forward)
    if "context" in msg:
        normalized["context_message_id"] = msg["context"].get("id")
        normalized["referenced_message_id"] = msg["context"].get("id")

    return normalized


def normalize_status(status: dict[str, Any]) -> dict[str, Any]:
    """Normalize WhatsApp status update."""
    return {
        "provider_message_id": status.get("id"),
        "meta_timestamp": datetime.fromtimestamp(int(status.get("timestamp", 0)), tz=UTC)
        if status.get("timestamp")
        else None,
        "status": status.get("status"),
        "recipient_id": status.get("recipient_id"),
        "conversation_id": status.get("conversation", {}).get("id"),
        "pricing": status.get("pricing"),
        "errors": status.get("errors"),
    }


# =============================================================================
# Webhook Endpoints
# =============================================================================


@router.post("/meta")
async def meta_webhook_receive(
    request: Request,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Receive Meta webhook (WhatsApp or Instagram) with signature verification."""

    # Read raw body for signature verification
    body = await request.body()

    # Verify signature (same for both WhatsApp and Instagram)
    s = get_webhook_settings()
    if not x_hub_signature_256 or not verify_hmac_sha256(
        body, x_hub_signature_256, s.meta_app_secret,
    ):
        client_host = request.client.host if request.client else "unknown"
        logger.warning(f"Invalid Meta webhook signature from {client_host}")

        await _log_invalid_signature(
            db, body, x_hub_signature_256, request.client.host if request.client else None,
        )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature",
        )

    # Parse JSON
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON") from e

    # Detect channel
    channel = detect_meta_channel(payload)

    # Log webhook received
    webhook_id = str(uuid4())
    await _log_webhook_received(db, webhook_id, payload, True)

    # Extract tenant
    tenant_id = await _get_tenant_from_meta_webhook(db, payload)
    if not tenant_id:
        logger.warning(f"No tenant found for {channel} webhook")
        return {"status": "received", "tenant_found": False, "channel": channel}

    # Check if Instagram is enabled for this tenant
    if channel == "instagram":
        from packages.core.models.core import Tenant
        tenant = await db.get(Tenant, tenant_id)
        if tenant and not (
            tenant.settings.get("instagram_enabled", False)
            and tenant.settings.get("launch_approved", False)
        ):
            logger.info(
                "Instagram webhook received but channel not enabled/approved "
                f"for tenant {tenant_id}",
            )
            return {
                "status": "received",
                "tenant_found": True,
                "channel": "instagram",
                "enabled": False,
            }

        # The plan decides which channels are processed (M1.6, U80). A
        # tenant whose plan does not include Instagram gets no Instagram
        # conversations: the raw event is still recorded (the webhook log
        # above), so nothing is lost, but no customer is answered on a
        # channel the tenant did not buy.
        from packages.core.billing.enforcer import EntitlementEnforcer

        decision = await EntitlementEnforcer(db).check_channel(tenant_id, "instagram")
        if not decision.allowed:
            logger.info(
                "Instagram webhook refused: the plan does not include it",
                extra={"tenant_id": tenant_id, "reason": decision.reason},
            )
            await AuditLogger(db).log_action(
                tenant_id=tenant_id,
                user_id=None,
                action="billing.channel_not_included",
                resource="tenant",
                resource_id=str(tenant_id),
                after={"channel": "instagram", "reason": decision.reason},
            )
            await db.flush()
            return {
                "status": "received",
                "tenant_found": True,
                "channel": "instagram",
                "enabled": False,
            }

    # Parse and process based on channel
    if channel == "whatsapp":
        normalized_messages = parse_whatsapp_webhook(payload)
        for norm_msg in normalized_messages:
            if "message" in norm_msg:
                await _process_inbound_message(db, tenant_id, norm_msg, webhook_id)
            elif "status" in norm_msg:
                await _process_status_update(db, tenant_id, norm_msg)
    else:  # instagram
        normalized_messages = parse_instagram_webhook(payload)
        for norm_msg in normalized_messages:
            if norm_msg["type"] == "dm":
                norm_dm = normalize_instagram_dm(
                    norm_msg["message"], norm_msg["ig_professional_account_id"],
                )
                await _process_instagram_inbound(db, tenant_id, norm_dm, webhook_id)
            elif norm_msg["type"] == "comment":
                norm_comment = normalize_instagram_comment(
                    norm_msg["comment"], norm_msg["ig_professional_account_id"],
                )
                await _process_instagram_comment(db, tenant_id, norm_comment, webhook_id)
            elif norm_msg["type"] == "story_reply":
                norm_story = normalize_instagram_story_reply(
                    norm_msg["story_reply"], norm_msg["ig_professional_account_id"],
                )
                await _process_instagram_story_reply(db, tenant_id, norm_story, webhook_id)

    return {"status": "received", "channel": channel}


# Keep the old WhatsApp endpoint for backward compatibility (deprecated)
@router.post("/whatsapp")
async def whatsapp_webhook_receive_deprecated(
    request: Request,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Deprecated: Use /meta endpoint instead. Kept for backward compatibility."""
    return await meta_webhook_receive(request, x_hub_signature_256, db)


@router.get("/whatsapp")
async def whatsapp_webhook_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
) -> Response:
    """Meta webhook verification endpoint (works for both WhatsApp and Instagram)."""
    s = get_webhook_settings()
    if hub_mode == "subscribe" and hub_verify_token == s.meta_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")

    raise HTTPException(status_code=403, detail="Invalid verify token")


@router.get("/instagram")
async def instagram_webhook_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
) -> Response:
    """Instagram webhook verification endpoint (alias to shared verify)."""
    return await whatsapp_webhook_verify(hub_mode, hub_challenge, hub_verify_token)


async def _process_inbound_message(
    db: AsyncSession,
    tenant_id: TenantId,
    norm_msg: dict[str, Any],
    webhook_id: str,
) -> None:
    """Process a single inbound message."""

    msg = norm_msg["message"]
    provider_msg_id = msg.get("id")

    if not provider_msg_id:
        logger.warning("Message missing ID, skipping")
        return

    # Check dedupe
    result = await db.execute(
        select(WhatsAppDedupe).where(
            WhatsAppDedupe.tenant_id == tenant_id,
            WhatsAppDedupe.provider_message_id == provider_msg_id,
        ),
    )
    existing = result.scalar_one_or_none()

    if existing:
        # Log dedupe
        logger.info(f"Duplicate message detected: {provider_msg_id}")

        audit = AuditLogger(db)
        await audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="webhook_deduplicated",
            resource="whatsapp_message",
            resource_id=existing.message_id,
            after={
                "provider_message_id": provider_msg_id,
                "existing_message_id": str(existing.message_id),
            },
        )
        return

    # Get or create contact
    contact = await _get_or_create_contact(db, tenant_id, msg, norm_msg.get("contacts", []))

    # Get or create conversation
    conversation = await _get_or_create_conversation(db, tenant_id, contact)

    # Normalize message content
    normalized = normalize_message(msg)

    # Create message record
    message = WhatsAppMessage(
        tenant_id=tenant_id,
        contact_id=contact.id,
        conversation_id=conversation.id,
        direction="inbound",
        message_type=WhatsAppMessageType(normalized["message_type"]),
        status=WhatsAppMessageStatus(normalized["status"]),
        text_body=normalized["text_body"],
        media_url=normalized["media_url"],
        media_mime_type=normalized["media_mime_type"],
        media_sha256=normalized["media_sha256"],
        media_caption=normalized["media_caption"],
        interactive_data=normalized["interactive_data"],
        template_name=normalized["template_name"],
        template_language=normalized["template_language"],
        template_components=normalized["template_components"],
        meta_message_id=provider_msg_id,
        meta_timestamp=normalized["meta_timestamp"],
        metadata_json={"webhook_id": webhook_id},
    )

    db.add(message)
    await db.flush()

    # Update contact window
    contact.update_window()

    # Create dedupe record
    dedupe = WhatsAppDedupe(
        tenant_id=tenant_id,
        provider_message_id=provider_msg_id,
        message_id=message.id,
    )
    db.add(dedupe)

    # Update conversation state
    conversation.last_customer_message_at = datetime.now(UTC)
    if conversation.state == WhatsAppConversationState.OPEN:
        conversation.state = WhatsAppConversationState.AWAITING_RESPONSE

    # Log domain event
    MessageReceived(
        tenant_id=tenant_id,
        contact_id=contact.id,
        conversation_id=conversation.id,
        message_id=message.id,
        provider_message_id=provider_msg_id,
        direction="inbound",
        message_type=normalized["message_type"],
        text_body=normalized["text_body"],
        media_url=normalized["media_url"],
        meta_timestamp=normalized["meta_timestamp"],
    )
    audit = AuditLogger(db)
    await audit.log_action(
        tenant_id=tenant_id,
        user_id=None,
        action="message_received",
        resource="whatsapp_message",
        resource_id=message.id,
        after={
            "contact_id": str(contact.id),
            "conversation_id": str(conversation.id),
            "provider_message_id": provider_msg_id,
            "message_type": normalized["message_type"],
        },
    )

    logger.info(f"Processed inbound message: {provider_msg_id} for tenant {tenant_id}")

    # Hand the inbound message to the agent via the outbox (ADR 0014). The
    # webhook stays fast; the worker relay runs the agent, retryably. The
    # idempotency key makes a replayed event a no-op.
    await add_outbox_event(
        event_type="inbound_message",
        payload={
            "message_id": str(message.id),
            "conversation_id": str(conversation.id),
            "contact_id": str(contact.id),
            "customer_phone": contact.phone,
            "text_body": normalized["text_body"],
            "provider_message_id": provider_msg_id,
        },
        idempotency_key=f"inbound_message:{provider_msg_id}",
        tenant_id=tenant_id,
        session=db,
    )


async def _process_status_update(
    db: AsyncSession,
    tenant_id: TenantId,
    norm_status: dict[str, Any],
) -> None:
    """Process message status update (delivered, read, failed)."""

    status = norm_status.get("status")
    provider_msg_id = norm_status.get("provider_message_id")

    if not provider_msg_id:
        return

    # Find message by provider ID
    result = await db.execute(
        select(WhatsAppMessage).where(
            WhatsAppMessage.tenant_id == tenant_id,
            WhatsAppMessage.meta_message_id == provider_msg_id,
        ),
    )
    message = result.scalar_one_or_none()

    if not message:
        logger.warning(f"Status update for unknown message: {provider_msg_id}")
        return

    # Update message status
    new_status = WhatsAppMessageStatus(status)
    message.status = new_status

    if status == "delivered":
        message.delivered_at = datetime.now(UTC)
    elif status == "read":
        message.read_at = datetime.now(UTC)
    elif status == "failed":
        message.failed_at = datetime.now(UTC)
        message.processing_error = str(norm_status.get("errors"))

    # Log domain event
    if status == "delivered":
        MessageDelivered(
            tenant_id=tenant_id,
            message_id=message.id,
            provider_message_id=provider_msg_id,
            delivered_at=datetime.now(UTC),
        )
    elif status == "read":
        MessageRead(
            tenant_id=tenant_id,
            message_id=message.id,
            provider_message_id=provider_msg_id,
            read_at=datetime.now(UTC),
        )
    elif status == "failed":
        MessageFailed(
            tenant_id=tenant_id,
            message_id=message.id,
            provider_message_id=provider_msg_id,
            error_code="delivery_failed",
            error_message=str(norm_status.get("errors")),
        )
    else:
        return

    from packages.core.audit.logger import AuditLogger

    audit = AuditLogger(db)
    action_map = {
        "delivered": "message_delivered",
        "read": "message_read",
        "failed": "message_failed",
    }
    await audit.log_action(
        tenant_id=tenant_id,
        user_id=None,
        action=action_map.get(status, "message_status_update"),
        resource="whatsapp_message",
        resource_id=message.id,
        after={"status": status, "provider_message_id": provider_msg_id},
    )


async def _get_or_create_contact(
    db: AsyncSession,
    tenant_id: TenantId,
    msg: dict[str, Any],
    contacts: list[dict[str, Any]],
) -> WhatsAppContact:
    """Get or create WhatsApp contact from message."""

    from_phone = msg.get("from")
    if not from_phone:
        raise ValueError("Message missing 'from' field")

    phone = CustomerPhone.from_raw(from_phone)

    # Check existing contact by phone
    result = await db.execute(
        select(WhatsAppContact).where(
            WhatsAppContact.tenant_id == tenant_id,
            WhatsAppContact.phone == str(phone),
        ),
    )
    contact = result.scalar_one_or_none()

    if contact:
        # Update profile if provided in contacts
        for c in contacts:
            if c.get("wa_id") == contact.wa_id:
                if c.get("profile", {}).get("name"):
                    contact.name = c["profile"]["name"]
                if not contact.profile_picture_url and c.get("profile", {}).get("picture"):
                    contact.profile_picture_url = c["profile"]["picture"]
                break
        return contact

    # Find wa_id from contacts
    wa_id = from_phone
    for c in contacts:
        if c.get("wa_id"):
            wa_id = c["wa_id"]
            break

    # Create new contact
    contact = WhatsAppContact(
        tenant_id=tenant_id,
        phone=str(phone),
        wa_id=wa_id,
        name=next(
            (c.get("profile", {}).get("name") for c in contacts if c.get("wa_id") == wa_id), None,
        ),
        profile_picture_url=next(
            (
                c.get("profile", {}).get("picture")
                for c in contacts
                if c.get("wa_id") == wa_id
            ),
            None,
        ),
    )

    from packages.core.audit.logger import AuditLogger

    audit = AuditLogger(db)
    await audit.log_action(
        tenant_id=tenant_id,
        user_id=None,
        action="create",
        resource="whatsapp_contact",
        resource_id=contact.id,
        after={"phone": str(phone), "wa_id": wa_id},
    )

    db.add(contact)
    await db.flush()  # Flush to generate ID
    return contact


async def _get_or_create_conversation(
    db: AsyncSession,
    tenant_id: TenantId,
    contact: WhatsAppContact,
) -> WhatsAppConversation:
    """Get or create conversation for contact."""

    # Check for existing open conversation
    from packages.core.models.whatsapp import WhatsAppConversationState

    result = await db.execute(
        select(WhatsAppConversation)
        .where(
            WhatsAppConversation.tenant_id == tenant_id,
            WhatsAppConversation.contact_id == contact.id,
            WhatsAppConversation.state.in_([
                WhatsAppConversationState.OPEN,
                WhatsAppConversationState.AWAITING_RESPONSE,
                WhatsAppConversationState.HANDOFF_REQUESTED,
                WhatsAppConversationState.HANDOFF_ACTIVE,
            ]),
        )
        .order_by(WhatsAppConversation.created_at.desc()),
    )
    conversation = result.scalar_one_or_none()

    if conversation:
        return conversation

    # Create new conversation
    conversation = WhatsAppConversation(
        tenant_id=tenant_id,
        contact_id=contact.id,
        state=WhatsAppConversationState.AWAITING_RESPONSE,
    )

    from packages.core.audit.logger import AuditLogger

    audit = AuditLogger(db)
    await audit.log_action(
        tenant_id=contact.tenant_id,
        user_id=None,
        action="create",
        resource="whatsapp_conversation",
        resource_id=conversation.id,
        after={"contact_id": str(contact.id)},
    )

    db.add(conversation)
    await db.flush()  # Flush to generate ID
    return conversation


# =============================================================================
# Instagram Processing Functions
# =============================================================================


async def _process_instagram_inbound(
    db: AsyncSession,
    tenant_id: TenantId,
    norm_msg: dict[str, Any],
    webhook_id: str,
) -> None:
    """Process a single inbound Instagram message/comment/story_reply."""

    provider_msg_id = norm_msg.get("provider_message_id")
    if not provider_msg_id:
        logger.warning("Instagram message missing ID, skipping")
        return

    # Check dedupe
    result = await db.execute(
        select(InstagramDedupe).where(
            InstagramDedupe.tenant_id == tenant_id,
            InstagramDedupe.provider_message_id == provider_msg_id,
        ),
    )
    existing = result.scalar_one_or_none()

    if existing:
        logger.info(f"Duplicate Instagram message detected: {provider_msg_id}")

        audit = AuditLogger(db)
        await audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="webhook_deduplicated",
            resource="instagram_message",
            resource_id=existing.message_id,
            after={
                "provider_message_id": provider_msg_id,
                "existing_message_id": str(existing.message_id),
            },
        )
        return

    # Get or create Instagram contact
    contact = await _get_or_create_instagram_contact(db, tenant_id, norm_msg)

    # Get or create conversation
    conversation = await _get_or_create_instagram_conversation(db, tenant_id, contact, norm_msg)

    # Create message record
    message = InstagramMessage(
        tenant_id=tenant_id,
        contact_id=contact.id,
        conversation_id=conversation.id,
        direction="inbound",
        message_type=InstagramMessageType(norm_msg.get("message_type", "unknown")),
        status=InstagramMessageStatus(norm_msg.get("status", "sent")),
        text_body=norm_msg.get("text_body"),
        media_url=norm_msg.get("media_url"),
        media_mime_type=norm_msg.get("media_mime_type"),
        media_sha256=norm_msg.get("media_sha256"),
        media_caption=norm_msg.get("media_caption"),
        interactive_data=norm_msg.get("interactive_data"),
        quick_reply_payload=norm_msg.get("quick_reply_payload"),
        referral_data=norm_msg.get("referral_data"),
        meta_message_id=provider_msg_id,
        meta_timestamp=norm_msg.get("meta_timestamp"),
        metadata_json={
            "webhook_id": webhook_id,
            "ig_scoped_user_id": norm_msg.get("ig_scoped_user_id"),
            "ig_handle": norm_msg.get("ig_handle"),
            "ig_professional_account_id": norm_msg.get("ig_professional_account_id"),
            "is_echo": norm_msg.get("is_echo", False),
        },
    )

    db.add(message)
    await db.flush()

    # Update contact window
    contact.update_window()

    # Create dedupe record
    dedupe = InstagramDedupe(
        tenant_id=tenant_id,
        provider_message_id=provider_msg_id,
        message_id=message.id,
    )
    db.add(dedupe)

    # Update conversation state
    conversation.last_customer_message_at = datetime.now(UTC)
    if conversation.state == InstagramConversationState.OPEN:
        conversation.state = InstagramConversationState.AWAITING_RESPONSE

    # Log domain event
    MessageReceived(
        tenant_id=tenant_id,
        contact_id=contact.id,
        conversation_id=conversation.id,
        message_id=message.id,
        provider_message_id=provider_msg_id,
        direction="inbound",
        message_type=norm_msg.get("message_type", "unknown"),
        text_body=norm_msg.get("text_body"),
        media_url=norm_msg.get("media_url"),
        meta_timestamp=norm_msg.get("meta_timestamp"),
    )
    audit = AuditLogger(db)
    await audit.log_action(
        tenant_id=tenant_id,
        user_id=None,
        action="message_received",
        resource="instagram_message",
        resource_id=message.id,
        after={
            "contact_id": str(contact.id),
            "conversation_id": str(conversation.id),
            "provider_message_id": provider_msg_id,
            "message_type": norm_msg.get("message_type", "unknown"),
            "channel": "instagram",
        },
    )

    logger.info(f"Processed inbound Instagram message: {provider_msg_id} for tenant {tenant_id}")

    # Hand to agent via outbox (same pipeline as WhatsApp)
    await add_outbox_event(
        event_type="inbound_message",
        payload={
            "message_id": str(message.id),
            "conversation_id": str(conversation.id),
            "contact_id": str(contact.id),
            "customer_phone": contact.ig_scoped_user_id,  # Use IG ID as channel identity
            "text_body": norm_msg.get("text_body"),
            "provider_message_id": provider_msg_id,
            "channel": "instagram",
            "ig_scoped_user_id": norm_msg.get("ig_scoped_user_id"),
            "ig_handle": norm_msg.get("ig_handle"),
        },
        idempotency_key=f"inbound_message:{provider_msg_id}",
        tenant_id=tenant_id,
        session=db,
    )


async def _get_or_create_instagram_contact(
    db: AsyncSession,
    tenant_id: TenantId,
    norm_msg: dict[str, Any],
) -> InstagramContact:
    """Get or create Instagram contact from normalized message."""

    ig_user_id = norm_msg.get("ig_scoped_user_id")
    if not ig_user_id:
        raise ValueError("Normalized message missing ig_scoped_user_id")

    # Check existing contact by ig_scoped_user_id
    result = await db.execute(
        select(InstagramContact).where(
            InstagramContact.tenant_id == tenant_id,
            InstagramContact.ig_scoped_user_id == ig_user_id,
        ),
    )
    contact = result.scalar_one_or_none()

    if contact:
        # Update profile if provided
        if norm_msg.get("ig_handle") and not contact.ig_handle:
            contact.ig_handle = norm_msg["ig_handle"]
        if norm_msg.get("ig_professional_account_id") and not contact.thread_id:
            # Thread ID may come from conversation context
            pass
        return contact

    # Create new contact
    contact = InstagramContact(
        tenant_id=tenant_id,
        ig_scoped_user_id=ig_user_id,
        ig_handle=norm_msg.get("ig_handle"),
        name=norm_msg.get("name"),  # May come from profile
        profile_picture_url=norm_msg.get("profile_picture_url"),
    )

    audit = AuditLogger(db)
    await audit.log_action(
        tenant_id=tenant_id,
        user_id=None,
        action="create",
        resource="instagram_contact",
        resource_id=contact.id,
        after={"ig_scoped_user_id": ig_user_id, "ig_handle": norm_msg.get("ig_handle")},
    )

    db.add(contact)
    await db.flush()
    return contact


async def _get_or_create_instagram_conversation(
    db: AsyncSession,
    tenant_id: TenantId,
    contact: InstagramContact,
    norm_msg: dict[str, Any],
) -> InstagramConversation:
    """Get or create Instagram conversation for contact."""

    # Check for existing open conversation
    result = await db.execute(
        select(InstagramConversation).where(
            InstagramConversation.tenant_id == tenant_id,
            InstagramConversation.contact_id == contact.id,
            InstagramConversation.state.in_([
                InstagramConversationState.OPEN,
                InstagramConversationState.AWAITING_RESPONSE,
                InstagramConversationState.HANDOFF_REQUESTED,
                InstagramConversationState.HANDOFF_ACTIVE,
            ]),
        ).order_by(InstagramConversation.created_at.desc()),
    )
    conversation = result.scalar_one_or_none()

    if conversation:
        # Update thread_id if available
        if norm_msg.get("thread_id") and not conversation.thread_id:
            conversation.thread_id = norm_msg["thread_id"]
        return conversation

    # Create new conversation
    conversation = InstagramConversation(
        tenant_id=tenant_id,
        contact_id=contact.id,
        state=InstagramConversationState.AWAITING_RESPONSE,
        thread_id=norm_msg.get("thread_id"),
    )

    audit = AuditLogger(db)
    await audit.log_action(
        tenant_id=contact.tenant_id,
        user_id=None,
        action="create",
        resource="instagram_conversation",
        resource_id=conversation.id,
        after={"contact_id": str(contact.id), "thread_id": norm_msg.get("thread_id")},
    )

    db.add(conversation)
    await db.flush()
    return conversation


async def _process_instagram_comment(
    db: AsyncSession,
    tenant_id: TenantId,
    norm_comment: dict[str, Any],
    webhook_id: str,
) -> None:
    """Process an Instagram comment (for private reply eligibility)."""

    comment_id = norm_comment.get("provider_message_id")
    if not comment_id:
        return

    ig_scoped_user_id = norm_comment.get("ig_scoped_user_id")

    # A commenter may be a brand-new contact — create/find them first so the
    # eligibility row has a valid contact_id.
    contact_id: UUID | None = None
    if ig_scoped_user_id:
        contact = await _get_or_create_instagram_contact(db, tenant_id, norm_comment)
        contact_id = contact.id

    if contact_id is not None:
        # Comment-to-DM eligibility: single-use, 7-day window from the comment.
        existing = await db.execute(
            select(InstagramDmEligibility).where(
                InstagramDmEligibility.tenant_id == tenant_id,
                InstagramDmEligibility.comment_id == comment_id,
            ),
        )
        if existing.scalar_one_or_none() is None:
            eligibility = InstagramDmEligibility(
                tenant_id=tenant_id,
                contact_id=contact_id,
                comment_id=comment_id,
                media_id=norm_comment.get("comment_media_id"),
                thread_id=f"ig:{comment_id}",
                expires_at=datetime.now(UTC) + timedelta(days=7),
                attempts_remaining=1,
            )
            db.add(eligibility)
            await db.flush()

    audit = AuditLogger(db)
    await audit.log_action(
        tenant_id=tenant_id,
        user_id=None,
        action="comment_received",
        resource="instagram_comment",
        after={
            "comment_id": comment_id,
            "ig_scoped_user_id": ig_scoped_user_id,
            "ig_handle": norm_comment.get("ig_handle"),
            "media_id": norm_comment.get("comment_media_id"),
            "media_type": norm_comment.get("comment_media_type"),
        },
    )


async def _process_instagram_story_reply(
    db: AsyncSession,
    tenant_id: TenantId,
    norm_story: dict[str, Any],
    webhook_id: str,
) -> None:
    """Process an Instagram story reply (becomes DM after business response)."""
    # Story replies are processed as DMs
    await _process_instagram_inbound(db, tenant_id, norm_story, webhook_id)


# =============================================================================
# Webhook Logging
# =============================================================================


async def _log_webhook_received(
    db: AsyncSession,
    webhook_id: str,
    payload: dict[str, Any],
    signature_valid: bool,
) -> None:
    """Log webhook received event.

    We cannot write an outbox event here because the tenant is not known
    until the payload has been parsed; the per-message audit entries in
    ``_process_inbound_message`` are the durable record.
    """


async def _log_invalid_signature(
    db: AsyncSession,
    body: bytes,
    provided_signature: str | None,
    client_ip: str | None,
) -> None:
    """Log invalid signature attempt."""
    # This would need a tenant_id - for now just log
    logger.warning(f"Invalid signature from {client_ip}: {provided_signature}")
