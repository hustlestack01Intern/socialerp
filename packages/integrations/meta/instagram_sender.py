"""
Instagram Outbound Sender

Sends messages via Instagram Graph API with template enforcement,
24h window tracking, comment-to-DM eligibility, and rate limiting.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.config import get_settings
from packages.core.domain import TenantId
from packages.core.models.instagram import (
    InstagramContact,
    InstagramConversation,
    InstagramConversationState,
    InstagramDmEligibility,
    InstagramMessage,
    InstagramMessageStatus,
    InstagramMessageType,
)
from packages.integrations.meta.window_tracker import InstagramLimitsManager

logger = logging.getLogger(__name__)

settings = get_settings()


class InstagramSender:
    """
    Sends messages via Instagram Graph API.

    Enforces:
    - 24h customer service window (free-form only within window)
    - Template-only outside window (Marketing + Utility only, no Authentication)
    - Rate limiting (750 calls/hour for private replies)
    - Comment-to-DM eligibility (time-limited, single-use)
    - Tenant isolation
    """

    def __init__(
        self, session: AsyncSession, limits_manager: InstagramLimitsManager | None = None,
    ) -> None:
        self.session = session
        self.limits = limits_manager or InstagramLimitsManager(session)
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
                    "Authorization": f"Bearer {settings.meta_app_secret}",
                    "Content-Type": "application/json",
                },
            )
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    # =============================================================================
    # Core Send Methods
    # =============================================================================

    async def send_text(
        self,
        tenant_id: TenantId,
        thread_id: str,
        body: str,
        ig_handle: str | None = None,
        conversation_id: UUID | None = None,
    ) -> InstagramMessage:
        """
        Send a free-form text message.

        Only allowed within 24h customer service window.
        """
        # Check limits
        allowed, meta = await self.limits.can_send_message(
            tenant_id, thread_id, ig_handle, is_template=False,
        )
        if not allowed:
            raise ValueError(f"Cannot send free-form message: {meta.get('error', 'unknown')}")

        # Get or create contact
        contact = await self._get_or_create_contact(tenant_id, thread_id, ig_handle)

        # Get or create conversation
        conversation = await self._get_or_create_conversation(
            tenant_id, contact, conversation_id, thread_id,
        )

        # Send via Meta API
        payload = {
            "recipient": {"thread_key": thread_id},
            "message": {"text": body},
        }

        response = await self._send_request(tenant_id, payload)

        # Create message record
        message = InstagramMessage(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            direction="outbound",
            message_type=InstagramMessageType.TEXT,
            status=InstagramMessageStatus.SENT,
            text_body=body,
            meta_message_id=response.get("message_id"),
            metadata_json={"meta_response": response},
        )

        # Update limits
        await self.limits.send_message(tenant_id, thread_id, ig_handle, is_template=False)

        return message

    async def send_media(
        self,
        tenant_id: TenantId,
        thread_id: str,
        media_type: str,
        media_url: str,
        ig_handle: str | None = None,
        caption: str | None = None,
        filename: str | None = None,
        conversation_id: UUID | None = None,
    ) -> InstagramMessage:
        """
        Send a media message (image, video, audio, file).

        Only allowed within 24h window (free-form).
        """
        # Check limits
        allowed, meta = await self.limits.can_send_message(
            tenant_id, thread_id, ig_handle, is_template=False,
        )
        if not allowed:
            raise ValueError(f"Cannot send media message: {meta.get('error', 'unknown')}")

        # Validate media type
        valid_types = ["image", "video", "audio", "file"]
        if media_type not in valid_types:
            raise ValueError(f"Invalid media type: {media_type}. Must be one of {valid_types}")

        # Get or create contact
        contact = await self._get_or_create_contact(tenant_id, thread_id, ig_handle)

        # Get or create conversation
        conversation = await self._get_or_create_conversation(
            tenant_id, contact, conversation_id, thread_id,
        )

        # Build payload
        media_obj: dict[str, Any] = {"url": media_url}
        if caption:
            media_obj["caption"] = caption
        if filename and media_type == "file":
            media_obj["filename"] = filename

        payload: dict[str, Any] = {
            "recipient": {"thread_key": thread_id},
            "message": {
                "attachment": {
                    "type": media_type,
                    "payload": media_obj,
                },
            },
        }

        response = await self._send_request(tenant_id, payload)

        # Create message record
        message = InstagramMessage(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            direction="outbound",
            message_type=InstagramMessageType(media_type.upper()),
            status=InstagramMessageStatus.SENT,
            media_url=media_url,
            media_caption=caption,
            meta_message_id=response.get("message_id"),
            metadata_json={"meta_response": response},
        )

        # Update limits
        await self.limits.send_message(tenant_id, thread_id, ig_handle, is_template=False)

        return message

    async def send_template(
        self,
        tenant_id: TenantId,
        thread_id: str,
        template_name: str,
        language_code: str = "en",
        components: list[dict[str, Any]] | None = None,
        ig_handle: str | None = None,
        conversation_id: UUID | None = None,
    ) -> InstagramMessage:
        """
        Send a template message.

        Allowed both inside and outside 24h window.
        Instagram supports Marketing + Utility templates (no Authentication).
        """
        # Verify template exists and is approved
        template = await self._get_approved_template(tenant_id, template_name, language_code)
        if not template:
            raise ValueError(f"Template '{template_name}' not found or not approved")

        # Get or create contact
        contact = await self._get_or_create_contact(tenant_id, thread_id, ig_handle)

        # Get or create conversation
        conversation = await self._get_or_create_conversation(
            tenant_id, contact, conversation_id, thread_id,
        )

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
            "recipient": {"thread_key": thread_id},
            "message": {
                "template": {
                    "name": template_name,
                    "language": {"code": language_code},
                    "components": payload_components,
                },
            },
        }

        response = await self._send_request(tenant_id, payload)

        # Create message record
        message = InstagramMessage(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            direction="outbound",
            message_type=InstagramMessageType.TEMPLATE,
            status=InstagramMessageStatus.SENT,
            template_name=template_name,
            template_language=language_code,
            template_components=payload_components,
            meta_message_id=response.get("message_id"),
            metadata_json={"meta_response": response},
        )

        # Update limits (templates don't consume window)
        await self.limits.send_message(tenant_id, thread_id, ig_handle, is_template=True)

        return message

    # =============================================================================
    # Comment-to-DM Private Reply
    # =============================================================================

    async def send_private_reply(
        self,
        tenant_id: TenantId,
        comment_id: str,
        body: str,
        ig_handle: str | None = None,
    ) -> InstagramMessage:
        """
        Send a private reply to a comment (comment-to-DM).

        Enforces:
        - Eligibility check (time-limited, rate-limited, single-use)
        - 7-day window from comment creation
        - 1 reply per comment
        """
        # Check eligibility
        eligibility = await self._check_eligibility(tenant_id, comment_id)
        if not eligibility:
            raise ValueError(
                "Private reply not allowed: eligibility expired, already used, or not found",
            )

        # Get or create contact from eligibility
        contact = await self.session.get(InstagramContact, eligibility.contact_id)
        if not contact:
            raise ValueError("Eligibility contact not found")

        # Get or create conversation
        conversation = await self._get_or_create_conversation(
            tenant_id, contact, None, eligibility.thread_id,
        )

        # Send private reply via Graph API
        payload = {
            "message": {"text": body},
        }

        response = await self._send_private_reply_request(tenant_id, comment_id, payload)

        # Create message record
        message = InstagramMessage(
            tenant_id=tenant_id,
            contact_id=contact.id,
            conversation_id=conversation.id,
            direction="outbound",
            message_type=InstagramMessageType.TEXT,
            status=InstagramMessageStatus.SENT,
            text_body=body,
            meta_message_id=response.get("message_id"),
            metadata_json={
                "meta_response": response,
                "private_reply": True,
                "source_comment_id": comment_id,
            },
        )

        # Mark eligibility as consumed
        await self._consume_eligibility(tenant_id, eligibility.id, message.id)

        # Update limits (private replies have separate rate limit)
        thread_id = eligibility.thread_id or contact.thread_id
        if not thread_id:
            raise ValueError("No thread_id available for private reply")
        await self.limits.send_message(tenant_id, thread_id, ig_handle, is_template=False)

        return message

    # =============================================================================
    # Private Helpers
    # =============================================================================

    async def _send_request(self, tenant_id: TenantId, payload: dict[str, Any]) -> dict[str, Any]:
        """Send request to Meta Graph API."""
        ig_account_id = settings.meta_instagram_account_id
        if not ig_account_id:
            raise ValueError("META_INSTAGRAM_ACCOUNT_ID not configured")

        url = f"/{ig_account_id}/messages"

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
            logger.exception(f"Instagram API error: {e.response.status_code} - {error_detail}")

            await self.audit.log_action(
                tenant_id=tenant_id,
                user_id=None,
                action="instagram_send_failed",
                resource="instagram_message",
                after={"error": error_detail, "status_code": e.response.status_code},
            )
            raise ValueError(f"Instagram API error: {error_detail}") from e
        except Exception:
            logger.exception("Failed to send Instagram message")
            await self.audit.log_action(
                tenant_id=tenant_id,
                user_id=None,
                action="instagram_send_failed",
                resource="instagram_message",
                after={"error": "Unknown error"},
            )
            raise

    async def _send_private_reply_request(
        self, tenant_id: TenantId, comment_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send private reply to comment via Graph API."""
        url = f"/{comment_id}/private_replies"

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
            logger.exception(
                f"Instagram private reply error: {e.response.status_code} - {error_detail}",
            )

            # Handle specific error codes
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_code = error_data.get("error", {}).get("code")
                if error_code == 200:
                    raise ValueError("Private reply not allowed (expired or already used)") from e
            if e.response.status_code == 429:
                retry_after = e.response.headers.get("Retry-After", "60")
                raise ValueError(f"Rate limited. Retry after {retry_after}s") from e

            await self.audit.log_action(
                tenant_id=tenant_id,
                user_id=None,
                action="instagram_private_reply_failed",
                resource="instagram_message",
                after={"error": error_detail, "status_code": e.response.status_code},
            )
            raise ValueError(f"Instagram private reply error: {error_detail}") from e
        except Exception:
            logger.exception("Failed to send Instagram private reply")
            await self.audit.log_action(
                tenant_id=tenant_id,
                user_id=None,
                action="instagram_private_reply_failed",
                resource="instagram_message",
                after={"error": "Unknown error"},
            )
            raise

    async def _get_or_create_contact(
        self, tenant_id: TenantId, thread_id: str, ig_handle: str | None = None,
    ) -> InstagramContact:
        """Get existing contact or create new one."""
        result = await self.session.execute(
            select(InstagramContact).where(
                InstagramContact.tenant_id == tenant_id,
                InstagramContact.thread_id == thread_id,
            ),
        )
        contact = result.scalar_one_or_none()

        if contact:
            if ig_handle and not contact.ig_handle:
                contact.ig_handle = ig_handle
            return contact

        contact = InstagramContact(
            tenant_id=tenant_id,
            ig_scoped_user_id=thread_id,  # Use thread_id as fallback
            ig_handle=ig_handle,
            thread_id=thread_id,
        )
        self.session.add(contact)
        await self.session.flush()
        return contact

    async def _get_or_create_conversation(
        self,
        tenant_id: TenantId,
        contact: InstagramContact,
        conversation_id: UUID | None = None,
        thread_id: str | None = None,
    ) -> InstagramConversation:
        """Get existing open conversation or create new one."""
        if conversation_id:
            result = await self.session.execute(
                select(InstagramConversation).where(
                    InstagramConversation.id == conversation_id,
                    InstagramConversation.tenant_id == tenant_id,
                ),
            )
            conversation = result.scalar_one_or_none()
            if conversation:
                return conversation

        # Check for existing open conversation
        result = await self.session.execute(
            select(InstagramConversation)
            .where(
                InstagramConversation.tenant_id == tenant_id,
                InstagramConversation.contact_id == contact.id,
                InstagramConversation.state.in_([
                    InstagramConversationState.OPEN,
                    InstagramConversationState.AWAITING_RESPONSE,
                    InstagramConversationState.HANDOFF_REQUESTED,
                    InstagramConversationState.HANDOFF_ACTIVE,
                ]),
            )
            .order_by(InstagramConversation.created_at.desc()),
        )
        conversation = result.scalar_one_or_none()

        if conversation:
            if thread_id and not conversation.thread_id:
                conversation.thread_id = thread_id
            return conversation

        # Create new conversation
        conversation = InstagramConversation(
            tenant_id=tenant_id,
            contact_id=contact.id,
            state=InstagramConversationState.AWAITING_RESPONSE,
            thread_id=thread_id,
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

    async def _check_eligibility(
        self, tenant_id: TenantId, comment_id: str,
    ) -> InstagramDmEligibility | None:
        """Check if private reply is eligible for a comment."""
        result = await self.session.execute(
            select(InstagramDmEligibility).where(
                InstagramDmEligibility.tenant_id == tenant_id,
                InstagramDmEligibility.comment_id == comment_id,
            ),
        )
        eligibility = result.scalar_one_or_none()

        if not eligibility:
            return None

        # Check if already consumed
        if eligibility.consumed_at is not None:
            return None

        # Check if expired
        if eligibility.expires_at < datetime.now(UTC):
            return None

        # Check attempts remaining
        if eligibility.attempts_remaining <= 0:
            return None

        return eligibility

    async def _consume_eligibility(
        self, tenant_id: TenantId, eligibility_id: UUID, message_id: UUID,
    ) -> None:
        """Mark eligibility as consumed."""
        from datetime import UTC, datetime

        result = await self.session.execute(
            select(InstagramDmEligibility).where(
                InstagramDmEligibility.id == eligibility_id,
                InstagramDmEligibility.tenant_id == tenant_id,
            ),
        )
        eligibility = result.scalar_one_or_none()
        if eligibility:
            eligibility.attempts_remaining -= 1
            if eligibility.attempts_remaining <= 0:
                eligibility.consumed_at = datetime.now(UTC)
                eligibility.consumed_by_message_id = message_id


class InstagramSenderAdapter:
    """Adapter exposing InstagramSender through the agent's send seam."""

    def __init__(self, session: AsyncSession) -> None:
        self._sender = InstagramSender(session)

    async def send_text(
        self,
        tenant_id: TenantId,
        to: str,  # thread_id for Instagram
        body: str,
        conversation_id: UUID | None = None,
    ) -> str | None:
        message = await self._sender.send_text(tenant_id, to, body, conversation_id=conversation_id)
        return message.meta_message_id

    async def close(self) -> None:
        await self._sender.close()


# =============================================================================
# Instagram Template Manager
# =============================================================================


class InstagramTemplateManager:
    """Manages Instagram message templates."""

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

        # Instagram only supports MARKETING and UTILITY
        if category not in ("MARKETING", "UTILITY"):
            raise ValueError(
                f"Instagram only supports MARKETING and UTILITY templates, got {category}",
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
        from packages.core.models.whatsapp import WhatsAppTemplate, WhatsAppTemplateStatus

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
        template.status = WhatsAppTemplateStatus.PENDING
        return template

    async def sync_template_status(self, tenant_id: TenantId) -> None:
        """Sync template statuses from Meta API."""
        # TODO: Call Meta API to get latest template statuses
        pass
