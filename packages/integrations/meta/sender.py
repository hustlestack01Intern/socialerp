"""
WhatsApp Outbound Sender

Sends messages to WhatsApp Cloud API with template enforcement and rate limiting.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.config import get_settings
from packages.core.domain import CustomerPhone, TenantId
from packages.core.models import (
    WhatsAppContact,
    WhatsAppMessage,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
)
from packages.core.models.whatsapp import WhatsAppTemplateStatus
from packages.integrations.meta.window_tracker import WhatsAppLimitsManager

logger = logging.getLogger(__name__)

settings = get_settings()


class WhatsAppSender:
    """
    Sends messages via WhatsApp Cloud API.

    Enforces:
    - 24h customer service window (free-form only within window)
    - Template-only outside window
    - Rate limiting (Coexistence: 20 msg/s)
    - Tenant isolation
    """

    def __init__(
        self, session: AsyncSession, limits_manager: WhatsAppLimitsManager | None = None,
    ) -> None:
        self.session = session
        self.limits = limits_manager or WhatsAppLimitsManager(session)
        self.audit = AuditLogger(session)
        self._http_client: httpx.AsyncClient | None = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            base_url = "https://graph.facebook.com"
            self._http_client = httpx.AsyncClient(
                base_url=f"{base_url}/{settings.meta_graph_api_version}",
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {settings.meta_app_secret}",  # fallback token
                    "Content-Type": "application/json",
                },
            )
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def send_text(
        self,
        tenant_id: TenantId,
        to: CustomerPhone,
        body: str,
        conversation_id: UUID | None = None,
    ) -> WhatsAppMessage:
        """
        Send a free-form text message.

        Only allowed within 24h customer service window.
        """
        # Check limits
        allowed, meta = await self.limits.can_send_message(tenant_id, to, is_template=False)
        if not allowed:
            raise ValueError(f"Cannot send free-form message: {meta.get('error', 'unknown')}")

        # Get or create contact
        contact = await self._get_or_create_contact(tenant_id, to)

        # Get or create conversation
        conversation = await self._get_or_create_conversation(tenant_id, contact, conversation_id)

        # Send via Meta API
        payload = {
            "messaging_product": "whatsapp",
            "to": str(to),
            "type": "text",
            "text": {"body": body},
        }

        response = await self._send_request(tenant_id, payload)

        # Create message record
        message = WhatsAppMessage(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            direction="outbound",
            message_type=WhatsAppMessageType.TEXT,
            status=WhatsAppMessageStatus.SENT,
            text_body=body,
            meta_message_id=response.get("messages", [{}])[0].get("id"),
            metadata_json={"meta_response": response},
        )

        # Update limits
        await self.limits.send_message(tenant_id, to, is_template=False)

        return message

    async def send_template(
        self,
        tenant_id: TenantId,
        to: CustomerPhone,
        template_name: str,
        language_code: str = "en",
        components: list[dict[str, Any]] | None = None,
        conversation_id: UUID | None = None,
    ) -> WhatsAppMessage:
        """
        Send a template message.

        Allowed both inside and outside 24h window.
        Template must be approved.
        """
        # Verify template exists and is approved
        template = await self._get_approved_template(tenant_id, template_name, language_code)
        if not template:
            raise ValueError(f"Template '{template_name}' not found or not approved")

        # Get or create contact
        contact = await self._get_or_create_contact(tenant_id, to)

        # Get or create conversation
        conversation = await self._get_or_create_conversation(tenant_id, contact, conversation_id)

        # Build components
        payload_components: list[dict[str, Any]] = list(
            components or template.body.get("components", []),
        )
        if template.header:
            payload_components.insert(0, dict(template.header))
        if template.footer:
            payload_components.append(dict(template.footer))
        if template.buttons:
            payload_components.extend([dict(b) for b in template.buttons])

        # Send via Meta API
        payload = {
            "messaging_product": "whatsapp",
            "to": str(to),
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": payload_components,
            },
        }

        response = await self._send_request(tenant_id, payload)

        # Create message record
        message = WhatsAppMessage(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            direction="outbound",
            message_type=WhatsAppMessageType.TEMPLATE,
            status=WhatsAppMessageStatus.SENT,
            template_name=template_name,
            template_language=language_code,
            template_components=payload_components,
            meta_message_id=response.get("messages", [{}])[0].get("id"),
            metadata_json={"meta_response": response},
        )

        # Update limits
        await self.limits.send_message(tenant_id, to, is_template=True)

        return message

    async def send_media(
        self,
        tenant_id: TenantId,
        to: CustomerPhone,
        media_type: str,
        media_url: str,
        caption: str | None = None,
        filename: str | None = None,
        conversation_id: UUID | None = None,
    ) -> WhatsAppMessage:
        """
        Send a media message (image, document, audio, video, sticker).

        Only allowed within 24h window (free-form).
        """
        # Check limits
        allowed, meta = await self.limits.can_send_message(tenant_id, to, is_template=False)
        if not allowed:
            raise ValueError(f"Cannot send media message: {meta.get('error', 'unknown')}")

        # Validate media type
        valid_types = ["image", "document", "audio", "video", "sticker"]
        if media_type not in valid_types:
            raise ValueError(f"Invalid media type: {media_type}. Must be one of {valid_types}")

        # Get or create contact
        contact = await self._get_or_create_contact(tenant_id, to)

        # Get or create conversation
        conversation = await self._get_or_create_conversation(tenant_id, contact)

        # Build payload
        media_obj: dict[str, Any] = {"link": media_url}
        if caption:
            media_obj["caption"] = caption
        if filename and media_type == "document":
            media_obj["filename"] = filename
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": str(to),
            "type": media_type,
            media_type: media_obj,
        }

        response = await self._send_request(tenant_id, payload)

        # Create message record
        message = WhatsAppMessage(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            direction="outbound",
            message_type=WhatsAppMessageType(media_type.upper()),
            status=WhatsAppMessageStatus.SENT,
            media_url=media_url,
            media_caption=caption,
            meta_message_id=response.get("messages", [{}])[0].get("id"),
            metadata_json={"meta_response": response},
        )

        # Update limits
        await self.limits.send_message(tenant_id, to, is_template=False)

        return message

    async def send_interactive(
        self,
        tenant_id: TenantId,
        to: CustomerPhone,
        interactive_type: str,  # "button", "list", "product", "product_list"
        header: dict[str, Any] | None,
        body: dict[str, Any],
        footer: dict[str, Any] | None,
        action: dict[str, Any],
        conversation_id: UUID | None = None,
    ) -> WhatsAppMessage:
        """
        Send an interactive message (buttons, list, products).

        Only allowed within 24h window (free-form).
        """
        # Check limits
        allowed, meta = await self.limits.can_send_message(tenant_id, to, is_template=False)
        if not allowed:
            raise ValueError(f"Cannot send interactive message: {meta.get('error', 'unknown')}")

        # Get or create contact
        contact = await self._get_or_create_contact(tenant_id, to)

        # Get or create conversation
        conversation = await self._get_or_create_conversation(tenant_id, contact, conversation_id)

        # Build payload
        payload = {
            "messaging_product": "whatsapp",
            "to": str(to),
            "type": "interactive",
            "interactive": {
                "type": interactive_type,
            },
        }

        if header:
            payload["interactive"]["header"] = header  # type: ignore[index]
        payload["interactive"]["body"] = body  # type: ignore[index]
        if footer:
            payload["interactive"]["footer"] = footer  # type: ignore[index]
        payload["interactive"]["action"] = action  # type: ignore[index]

        response = await self._send_request(tenant_id, payload)

        # Create message record
        message = WhatsAppMessage(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            direction="outbound",
            message_type=WhatsAppMessageType.INTERACTIVE,
            status=WhatsAppMessageStatus.SENT,
            interactive_data=payload["interactive"],
            meta_message_id=response.get("messages", [{}])[0].get("id"),
            metadata_json={"meta_response": response},
        )

        # Update limits
        await self.limits.send_message(tenant_id, to, is_template=False)

        return message

    # =============================================================================
    # Private Helpers
    # =============================================================================

    async def _send_request(self, tenant_id: TenantId, payload: dict[str, Any]) -> dict[str, Any]:
        """Send request to Meta Graph API."""
        phone_number_id = settings.meta_whatsapp_phone_number_id
        if not phone_number_id:
            raise ValueError("META_WHATSAPP_PHONE_NUMBER_ID not configured")

        url = f"/{phone_number_id}/messages"

        try:
            response = await self.http_client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {}
        except httpx.HTTPStatusError as e:
            try:
                error_detail = e.response.json() if e.response.content else {"message": str(e)}
            except Exception:
                error_detail = {"message": str(e)}
            logger.exception(f"Meta API error: {e.response.status_code} - {error_detail}")

            # Log failed message
            await self.audit.log_action(
                tenant_id=tenant_id,
                user_id=None,
                action="whatsapp_send_failed",
                resource="whatsapp_message",
                after={"error": error_detail, "status_code": e.response.status_code},
            )
            raise ValueError(f"Meta API error: {error_detail}") from e
        except Exception:
            logger.exception("Failed to send WhatsApp message")
            await self.audit.log_action(
                tenant_id=tenant_id,
                user_id=None,
                action="whatsapp_send_failed",
                resource="whatsapp_message",
                after={"error": "Unknown error"},
            )
            raise

    async def _get_or_create_contact(
        self, tenant_id: TenantId, phone: CustomerPhone,
    ) -> WhatsAppContact:
        """Get existing contact or create new one."""
        from sqlalchemy import select

        result = await self.session.execute(
            select(WhatsAppContact).where(
                WhatsAppContact.tenant_id == tenant_id,
                WhatsAppContact.phone == str(phone),
            ),
        )
        contact = result.scalar_one_or_none()

        if contact:
            return contact

        contact = WhatsAppContact(
            tenant_id=tenant_id,
            phone=phone,
            wa_id=str(phone),
        )
        self.session.add(contact)
        await self.session.flush()
        return contact

    async def _get_or_create_conversation(
        self,
        tenant_id: TenantId,
        contact: WhatsAppContact,
        conversation_id: UUID | None = None,
    ) -> Any:
        """Get existing open conversation or create new one."""
        from sqlalchemy import select

        from packages.core.models.whatsapp import WhatsAppConversation, WhatsAppConversationState

        if conversation_id:
            result = await self.session.execute(
                select(WhatsAppConversation).where(
                    WhatsAppConversation.id == conversation_id,
                    WhatsAppConversation.tenant_id == tenant_id,
                ),
            )
            conversation = result.scalar_one_or_none()
            if conversation:
                return conversation

        # Check for existing open conversation
        result = await self.session.execute(
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
        self.session.add(conversation)
        await self.session.flush()
        return conversation

    async def _get_approved_template(
        self,
        tenant_id: TenantId,
        template_name: str,
        language_code: str,
    ) -> Any:
        """Get approved template by name and language."""
        from sqlalchemy import select

        from packages.core.models.whatsapp import WhatsAppTemplate, WhatsAppTemplateStatus

        result = await self.session.execute(
            select(WhatsAppTemplate).where(
                WhatsAppTemplate.tenant_id == tenant_id,
                WhatsAppTemplate.name == template_name,
                WhatsAppTemplate.language == language_code,
                WhatsAppTemplate.status == WhatsAppTemplateStatus.APPROVED,
            ),
        )
        return result.scalar_one_or_none()


class WhatsAppSenderAdapter:
    """Adapter exposing :class:`WhatsAppSender` through the agent's send seam.

    The conversation agent depends on a tiny protocol (return the provider
    message id, raise on failure); this keeps the agent package free of the
    Meta HTTP client. The caller (worker) owns the lifecycle and closes it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._sender = WhatsAppSender(session)

    async def send_text(
        self,
        tenant_id: TenantId,
        to: CustomerPhone,
        body: str,
        conversation_id: UUID | None = None,
    ) -> str | None:
        message = await self._sender.send_text(tenant_id, to, body, conversation_id)
        return message.meta_message_id

    async def close(self) -> None:
        await self._sender.close()


# =============================================================================
# Template Manager
# =============================================================================


class TemplateManager:
    """Manages WhatsApp message templates."""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def create_template(
        self,
        tenant_id: TenantId,
        name: str,
        category: str,
        language: str,
        body: dict[str, Any],
        header: dict[str, Any] | None = None,
        footer: dict[str, Any] | None = None,
        buttons: list[dict[str, Any]] | None = None,
    ) -> Any:
        """Create a new template (PENDING approval)."""
        from packages.core.models.whatsapp import (
            WhatsAppTemplate,
            WhatsAppTemplateCategory,
            WhatsAppTemplateStatus,
        )

        template = WhatsAppTemplate(
            tenant_id=tenant_id,
            name=name,
            category=WhatsAppTemplateCategory(category),
            language=language,
            body=body,
            header=header,
            footer=footer,
            buttons=buttons or [],
            status=WhatsAppTemplateStatus.PENDING,
        )

        self.session.add(template)
        await self.session.flush()
        return template

    async def get_templates(self, tenant_id: TenantId, status: str | None = None) -> Any:
        """Get templates for a tenant."""
        from sqlalchemy import select

        from packages.core.models.whatsapp import WhatsAppTemplate, WhatsAppTemplateStatus

        query = select(WhatsAppTemplate).where(WhatsAppTemplate.tenant_id == tenant_id)
        if status:
            query = query.where(WhatsAppTemplate.status == WhatsAppTemplateStatus(status))

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def submit_for_approval(self, tenant_id: TenantId, template_id: UUID) -> Any:
        """Submit template to Meta for approval."""
        # This would call Meta API to submit template
        # For now, just update local status
        from packages.core.models.whatsapp import WhatsAppTemplate

        result = await self.session.execute(
            select(WhatsAppTemplate).where(
                WhatsAppTemplate.id == template_id,
                WhatsAppTemplate.tenant_id == tenant_id,
            ),
        )
        template = result.scalar_one_or_none()
        if not template:
            raise ValueError("Template not found")

        # TODO: Call Meta API to submit
        # For now, just mark as submitted
        template.status = WhatsAppTemplateStatus.PENDING
        return template

    async def sync_template_status(self, tenant_id: TenantId) -> None:
        """Sync template statuses from Meta API."""
        # TODO: Call Meta API to get latest template statuses
        # Update local statuses
        pass
