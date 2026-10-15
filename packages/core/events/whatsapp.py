"""
WhatsApp Domain Events

Domain events for WhatsApp integration.
All events inherit from DomainEvent and include tenant_id for RLS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from packages.core.domain import TenantId
from packages.core.events import DomainEvent

# =============================================================================
# Message Events
# =============================================================================


class MessageReceived(DomainEvent):
    """Published when inbound WhatsApp message is received and processed."""

    def __init__(
        self,
        tenant_id: TenantId,
        contact_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        provider_message_id: str,  # WhatsApp's wamid.xxx
        direction: str,  # "inbound"
        message_type: str,
        text_body: str | None,
        media_url: str | None,
        meta_timestamp: datetime | None,
        raw_payload: dict[str, Any] | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.contact_id = contact_id
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.provider_message_id = provider_message_id
        self.direction = direction
        self.message_type = message_type
        self.text_body = text_body
        self.media_url = media_url
        self.meta_timestamp = meta_timestamp
        self.raw_payload = raw_payload

    def _payload(self) -> dict[str, Any]:
        return {
            "contact_id": str(self.contact_id),
            "conversation_id": str(self.conversation_id),
            "message_id": str(self.message_id),
            "provider_message_id": self.provider_message_id,
            "direction": self.direction,
            "message_type": self.message_type,
            "text_body": self.text_body,
            "media_url": self.media_url,
            "meta_timestamp": self.meta_timestamp.isoformat() if self.meta_timestamp else None,
            "raw_payload": self.raw_payload,
        }


class MessageSent(DomainEvent):
    """Published when outbound WhatsApp message is sent successfully."""

    def __init__(
        self,
        tenant_id: TenantId,
        contact_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        provider_message_id: str | None,  # WhatsApp's wamid.xxx
        direction: str,  # "outbound"
        message_type: str,
        template_name: str | None,
        text_body: str | None,
        media_url: str | None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.contact_id = contact_id
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.provider_message_id = provider_message_id
        self.direction = direction
        self.message_type = message_type
        self.template_name = template_name
        self.text_body = text_body
        self.media_url = media_url

    def _payload(self) -> dict[str, Any]:
        return {
            "contact_id": str(self.contact_id),
            "conversation_id": str(self.conversation_id),
            "message_id": str(self.message_id),
            "provider_message_id": self.provider_message_id,
            "direction": self.direction,
            "message_type": self.message_type,
            "template_name": self.template_name,
            "text_body": self.text_body,
            "media_url": self.media_url,
        }


class MessageDelivered(DomainEvent):
    """Published when WhatsApp confirms message delivery (webhook status)."""

    def __init__(
        self,
        tenant_id: TenantId,
        message_id: UUID,
        provider_message_id: str,
        delivered_at: datetime,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.message_id = message_id
        self.provider_message_id = provider_message_id
        self.delivered_at = delivered_at

    def _payload(self) -> dict[str, Any]:
        return {
            "message_id": str(self.message_id),
            "provider_message_id": self.provider_message_id,
            "delivered_at": self.delivered_at.isoformat(),
        }


class MessageRead(DomainEvent):
    """Published when WhatsApp confirms message read (webhook status)."""

    def __init__(
        self,
        tenant_id: TenantId,
        message_id: UUID,
        provider_message_id: str,
        read_at: datetime,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.message_id = message_id
        self.provider_message_id = provider_message_id
        self.read_at = read_at

    def _payload(self) -> dict[str, Any]:
        return {
            "message_id": str(self.message_id),
            "provider_message_id": self.provider_message_id,
            "read_at": self.read_at.isoformat(),
        }


class MessageFailed(DomainEvent):
    """Published when WhatsApp message fails to send/deliver."""

    def __init__(
        self,
        tenant_id: TenantId,
        message_id: UUID,
        provider_message_id: str | None,
        error_code: str,
        error_message: str,
        error_details: dict[str, Any] | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.message_id = message_id
        self.provider_message_id = provider_message_id
        self.error_code = error_code
        self.error_message = error_message
        self.error_details = error_details

    def _payload(self) -> dict[str, Any]:
        return {
            "message_id": str(self.message_id),
            "provider_message_id": self.provider_message_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "error_details": self.error_details,
        }


# =============================================================================
# Conversation Events
# =============================================================================


class ConversationStarted(DomainEvent):
    """Published when a new WhatsApp conversation is started."""

    def __init__(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        contact_id: UUID,
        initiated_by: str,  # "customer" or "agent"
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.conversation_id = conversation_id
        self.contact_id = contact_id
        self.initiated_by = initiated_by

    def _payload(self) -> dict[str, Any]:
        return {
            "conversation_id": str(self.conversation_id),
            "contact_id": str(self.contact_id),
            "initiated_by": self.initiated_by,
        }


class ConversationStateChanged(DomainEvent):
    """Published when conversation state changes."""

    def __init__(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        old_state: str,
        new_state: str,
        reason: str | None = None,
        triggered_by: UUID | None = None,  # User ID who triggered
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.conversation_id = conversation_id
        self.old_state = old_state
        self.new_state = new_state
        self.reason = reason
        self.triggered_by = triggered_by

    def _payload(self) -> dict[str, Any]:
        return {
            "conversation_id": str(self.conversation_id),
            "old_state": self.old_state,
            "new_state": self.new_state,
            "reason": self.reason,
            "triggered_by": str(self.triggered_by) if self.triggered_by else None,
        }


class HandoffRequested(DomainEvent):
    """Published when human handoff is requested."""

    def __init__(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        contact_id: UUID,
        reason: str,
        triggered_by: str,  # "agent", "customer", "system"
        assigned_to: UUID | None = None,
        priority: str = "normal",  # "low", "normal", "high", "urgent"
        sla_due_at: datetime | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.conversation_id = conversation_id
        self.contact_id = contact_id
        self.reason = reason
        self.triggered_by = triggered_by
        self.assigned_to = assigned_to
        self.priority = priority
        self.sla_due_at = sla_due_at

    def _payload(self) -> dict[str, Any]:
        return {
            "conversation_id": str(self.conversation_id),
            "contact_id": str(self.contact_id),
            "reason": self.reason,
            "triggered_by": self.triggered_by,
            "assigned_to": str(self.assigned_to) if self.assigned_to else None,
            "priority": self.priority,
            "sla_due_at": self.sla_due_at.isoformat() if self.sla_due_at else None,
        }


class HandoffAccepted(DomainEvent):
    """Published when owner/staff accepts handoff."""

    def __init__(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        accepted_by: UUID,  # User ID
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.conversation_id = conversation_id
        self.accepted_by = accepted_by

    def _payload(self) -> dict[str, Any]:
        return {
            "conversation_id": str(self.conversation_id),
            "accepted_by": str(self.accepted_by),
        }


class HandoffCompleted(DomainEvent):
    """Published when handoff is completed (conversation returned to agent or closed)."""

    def __init__(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        completed_by: UUID,  # User ID
        outcome: str,  # "resolved", "escalated", "closed", "returned_to_agent"
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.conversation_id = conversation_id
        self.completed_by = completed_by
        self.outcome = outcome

    def _payload(self) -> dict[str, Any]:
        return {
            "conversation_id": str(self.conversation_id),
            "completed_by": str(self.completed_by),
            "outcome": self.outcome,
        }


# =============================================================================
# Contact Events
# =============================================================================


class ContactCreated(DomainEvent):
    """Published when a new WhatsApp contact is created."""

    def __init__(
        self,
        tenant_id: TenantId,
        contact_id: UUID,
        phone: str,
        wa_id: str,
        name: str | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.contact_id = contact_id
        self.phone = phone
        self.wa_id = wa_id
        self.name = name

    def _payload(self) -> dict[str, Any]:
        return {
            "contact_id": str(self.contact_id),
            "phone": self.phone,
            "wa_id": self.wa_id,
            "name": self.name,
        }


class ContactWindowUpdated(DomainEvent):
    """Published when 24h customer service window is updated."""

    def __init__(
        self,
        tenant_id: TenantId,
        contact_id: UUID,
        window_opened: bool,
        last_customer_message_at: datetime,
        window_expires_at: datetime,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.contact_id = contact_id
        self.window_opened = window_opened
        self.last_customer_message_at = last_customer_message_at
        self.window_expires_at = window_expires_at

    def _payload(self) -> dict[str, Any]:
        return {
            "contact_id": str(self.contact_id),
            "window_opened": self.window_opened,
            "last_customer_message_at": self.last_customer_message_at.isoformat(),
            "window_expires_at": self.window_expires_at.isoformat(),
        }


class ContactBlocked(DomainEvent):
    """Published when a contact is blocked."""

    def __init__(
        self,
        tenant_id: TenantId,
        contact_id: UUID,
        reason: str,
        blocked_by: UUID,  # User ID
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.contact_id = contact_id
        self.reason = reason
        self.blocked_by = blocked_by

    def _payload(self) -> dict[str, Any]:
        return {
            "contact_id": str(self.contact_id),
            "reason": self.reason,
            "blocked_by": str(self.blocked_by),
        }


# =============================================================================
# Template Events
# =============================================================================


class TemplateSubmitted(DomainEvent):
    """Published when a template is submitted for approval."""

    def __init__(
        self,
        tenant_id: TenantId,
        template_id: UUID,
        template_name: str,
        language: str,
        category: str,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.template_id = template_id
        self.template_name = template_name
        self.language = language
        self.category = category

    def _payload(self) -> dict[str, Any]:
        return {
            "template_id": str(self.template_id),
            "template_name": self.template_name,
            "language": self.language,
            "category": self.category,
        }


class TemplateStatusChanged(DomainEvent):
    """Published when template approval status changes."""

    def __init__(
        self,
        tenant_id: TenantId,
        template_id: UUID,
        old_status: str,
        new_status: str,
        rejection_reason: str | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.template_id = template_id
        self.old_status = old_status
        self.new_status = new_status
        self.rejection_reason = rejection_reason

    def _payload(self) -> dict[str, Any]:
        return {
            "template_id": str(self.template_id),
            "old_status": self.old_status,
            "new_status": self.new_status,
            "rejection_reason": self.rejection_reason,
        }


# =============================================================================
# Webhook Processing Events
# =============================================================================


class WebhookReceived(DomainEvent):
    """Published when a WhatsApp webhook is received (before processing)."""

    def __init__(
        self,
        tenant_id: TenantId,
        webhook_id: str,  # Internal tracking ID
        provider_payload: dict[str, Any],
        signature_valid: bool,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.webhook_id = webhook_id
        self.provider_payload = provider_payload
        self.signature_valid = signature_valid

    def _payload(self) -> dict[str, Any]:
        return {
            "webhook_id": self.webhook_id,
            "provider_payload": self.provider_payload,
            "signature_valid": self.signature_valid,
        }


class WebhookDeduplicated(DomainEvent):
    """Published when a duplicate webhook is detected and ignored."""

    def __init__(
        self,
        tenant_id: TenantId,
        provider_message_id: str,
        existing_message_id: UUID,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.provider_message_id = provider_message_id
        self.existing_message_id = existing_message_id

    def _payload(self) -> dict[str, Any]:
        return {
            "provider_message_id": self.provider_message_id,
            "existing_message_id": str(self.existing_message_id),
        }


class WebhookSignatureInvalid(DomainEvent):
    """Published when webhook signature verification fails."""

    def __init__(
        self,
        tenant_id: TenantId,
        webhook_id: str,
        provided_signature: str,
        expected_signature: str,
        client_ip: str | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.webhook_id = webhook_id
        self.provided_signature = provided_signature
        self.expected_signature = expected_signature
        self.client_ip = client_ip

    def _payload(self) -> dict[str, Any]:
        return {
            "webhook_id": self.webhook_id,
            "provided_signature": self.provided_signature,
            "expected_signature": self.expected_signature,
            "client_ip": self.client_ip,
        }


# =============================================================================
# Auto-register all events
# =============================================================================

# The EventRegistry automatically registers all DomainEvent subclasses
# defined in this module when imported.
