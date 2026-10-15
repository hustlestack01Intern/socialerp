"""
Conversation Manager

Unified inbox and conversation state management for WhatsApp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.domain import TenantId
from packages.core.events.whatsapp import ConversationStateChanged
from packages.core.models import (
    WhatsAppConversation,
    WhatsAppConversationState,
    WhatsAppMessage,
)


class ConversationManager:
    """
    Manages WhatsApp conversations and unified inbox.

    Features:
    - Conversation threading by contact
    - State machine for conversation lifecycle
    - Human handoff workflow
    - SLA tracking
    - Tenant isolation via RLS
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit = AuditLogger(session)

    # =========================================================================
    # Conversation Queries
    # =========================================================================

    async def get_conversations(
        self,
        tenant_id: TenantId,
        state: WhatsAppConversationState | None = None,
        contact_id: UUID | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[WhatsAppConversation], int]:
        """Get paginated conversations for a tenant."""
        query = select(WhatsAppConversation).where(
            WhatsAppConversation.tenant_id == tenant_id,
        )

        if state:
            query = query.where(WhatsAppConversation.state == state)

        if contact_id:
            query = query.where(WhatsAppConversation.contact_id == contact_id)

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = await self.session.scalar(count_query) or 0

        # Apply pagination
        query = query.order_by(desc(WhatsAppConversation.updated_at))
        query = query.offset((page - 1) * page_size).limit(page_size)

        result = await self.session.execute(query)
        conversations = list(result.scalars().all())

        return conversations, total

    async def get_conversation(
        self, tenant_id: TenantId, conversation_id: UUID
    ) -> WhatsAppConversation | None:
        """Get a single conversation by ID."""
        result = await self.session.execute(
            select(WhatsAppConversation).where(
                WhatsAppConversation.id == conversation_id,
                WhatsAppConversation.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_conversation_with_messages(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        message_limit: int = 100,
    ) -> tuple[WhatsAppConversation, list[WhatsAppMessage]] | tuple[None, None]:
        """Get conversation with its messages."""
        conversation = await self.get_conversation(tenant_id, conversation_id)
        if not conversation:
            return None, None

        result = await self.session.execute(
            select(WhatsAppMessage)
            .where(
                WhatsAppMessage.conversation_id == conversation_id,
            )
            .order_by(WhatsAppMessage.created_at)
            .limit(message_limit)
        )
        messages = list(result.scalars().all())

        return conversation, messages

    # =========================================================================
    # Conversation State Management
    # =========================================================================

    async def update_state(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        new_state: WhatsAppConversationState,
        reason: str | None = None,
        triggered_by: UUID | None = None,
    ) -> WhatsAppConversation | None:
        """Update conversation state with audit logging."""
        conversation = await self.get_conversation(tenant_id, conversation_id)
        if not conversation:
            return None

        old_state = conversation.state
        if old_state == new_state:
            return conversation

        conversation.state = new_state
        conversation.updated_at = datetime.now(UTC)

        if new_state == WhatsAppConversationState.HANDOFF_REQUESTED:
            conversation.handoff_requested_at = datetime.now(UTC)
        elif new_state == WhatsAppConversationState.HANDOFF_ACTIVE:
            conversation.handoff_accepted_at = datetime.now(UTC)
        elif new_state == WhatsAppConversationState.CLOSED:
            conversation.closed_at = datetime.now(UTC)

        # Log state change event
        event = ConversationStateChanged(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            old_state=old_state,
            new_state=new_state.value,
            reason=reason,
            triggered_by=triggered_by,
        )
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=triggered_by,
            action="conversation_state_changed",
            resource="conversation",
            resource_id=str(conversation_id),
            metadata={"event": event.event_type, "payload": event._payload()},
        )

        return conversation

    async def set_handoff(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        reason: str,
        assigned_to: UUID | None = None,
    ) -> WhatsAppConversation | None:
        """Request human handoff for a conversation."""
        conversation = await self.update_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            new_state=WhatsAppConversationState.HANDOFF_REQUESTED,
            reason=reason,
        )

        if conversation:
            conversation.handoff_reason = reason
            conversation.handoff_assigned_to = assigned_to
            conversation.first_response_due_at = datetime.now(UTC) + timedelta(minutes=5)

        return conversation

    async def accept_handoff(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        accepted_by: UUID,
    ) -> WhatsAppConversation | None:
        """Accept a handoff request."""
        conversation = await self.update_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            new_state=WhatsAppConversationState.HANDOFF_ACTIVE,
            triggered_by=accepted_by,
        )

        if conversation:
            conversation.handoff_assigned_to = accepted_by
            conversation.handoff_accepted_at = datetime.now(UTC)

        return conversation

    async def close_conversation(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        reason: str | None = None,
    ) -> WhatsAppConversation | None:
        """Close a conversation."""
        return await self.update_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            new_state=WhatsAppConversationState.CLOSED,
            reason=reason,
        )

    # =========================================================================
    # Message Management
    # =========================================================================

    async def add_message(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        direction: str,
        message_type: str,
        text_body: str | None = None,
        media_url: str | None = None,
        media_caption: str | None = None,
        template_name: str | None = None,
        meta_message_id: str | None = None,
    ) -> WhatsAppMessage | None:
        """Add a message to a conversation."""
        conversation = await self.get_conversation(tenant_id, conversation_id)
        if not conversation:
            return None

        message = WhatsAppMessage(
            tenant_id=tenant_id,
            contact_id=conversation.contact_id,
            conversation_id=conversation_id,
            direction=direction,
            message_type=message_type,
            text_body=text_body,
            media_url=media_url,
            media_caption=media_caption,
            template_name=template_name,
            meta_message_id=meta_message_id,
        )

        self.session.add(message)

        # Update conversation timestamps
        conversation.updated_at = datetime.now(UTC)
        if direction == "inbound":
            conversation.last_customer_message_at = datetime.now(UTC)
        else:
            conversation.last_agent_message_at = datetime.now(UTC)

        await self.session.flush()
        return message

    # =========================================================================
    # SLA & Metrics
    # =========================================================================

    async def get_overdue_conversations(self, tenant_id: TenantId) -> list[WhatsAppConversation]:
        """Get conversations past their SLA due time."""
        now = datetime.now(UTC)
        result = await self.session.execute(
            select(WhatsAppConversation)
            .where(
                WhatsAppConversation.tenant_id == tenant_id,
                WhatsAppConversation.first_response_due_at.is_not(None),
                WhatsAppConversation.first_response_due_at < now,
                WhatsAppConversation.state.in_([
                    WhatsAppConversationState.AWAITING_RESPONSE,
                    WhatsAppConversationState.HANDOFF_REQUESTED,
                ]),
            )
            .order_by(WhatsAppConversation.first_response_due_at)
        )
        return list(result.scalars().all())

    async def get_conversation_stats(self, tenant_id: TenantId) -> dict[str, Any]:
        """Get conversation statistics for a tenant."""
        # Total conversations
        total = (
            await self.session.scalar(
                select(func.count(WhatsAppConversation.id)).where(
                    WhatsAppConversation.tenant_id == tenant_id,
                )
            )
            or 0
        )

        # By state
        state_counts = {}
        for state in WhatsAppConversationState:
            count = (
                await self.session.scalar(
                    select(func.count(WhatsAppConversation.id)).where(
                        WhatsAppConversation.tenant_id == tenant_id,
                        WhatsAppConversation.state == state,
                    )
                )
                or 0
            )
            state_counts[state.value] = count

        # Active (non-closed) conversations
        active = sum(v for k, v in state_counts.items() if k != "closed")

        # Overdue
        overdue = len(await self.get_overdue_conversations(tenant_id))

        return {
            "total": total,
            "active": active,
            "by_state": state_counts,
            "overdue": overdue,
        }


# Need to import timedelta
