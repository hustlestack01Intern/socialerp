"""
Handoff Manager

Human handoff workflow for WhatsApp conversations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.domain import TenantId, UserId
from packages.core.events import DomainEvent
from packages.core.events.whatsapp import HandoffAccepted, HandoffCompleted, HandoffRequested
from packages.core.models import (
    User,
    WhatsAppConversation,
    WhatsAppConversationState,
)


class HandoffManager:
    """
    Manages human handoff workflow for WhatsApp conversations.

    Flow:
    1. Agent triggers handoff (low confidence, high-risk, customer request)
    2. Owner/staff notified via WhatsApp template + in-app
    3. SLA: 5 minutes to accept
    4. Owner accepts → conversation moves to HANDOFF_ACTIVE
    5. Owner resolves → conversation returns to agent or closes
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit = AuditLogger(session)

    async def _audit_handoff(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        event: DomainEvent,
        user_id: UserId | None = None,
    ) -> None:
        """Write one append-only audit entry for a handoff event."""
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=user_id,
            action=event.event_type,
            resource="whatsapp_conversation",
            resource_id=str(conversation_id),
            metadata={"event": event.event_type, "payload": event._payload()},
        )

    # =========================================================================
    # Handoff Triggers
    # =========================================================================

    async def request_handoff(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        reason: str,
        triggered_by: str,  # "agent", "customer", "system"
        priority: str = "normal",  # "low", "normal", "high", "urgent"
    ) -> dict[str, Any]:
        """
        Request human handoff for a conversation.

        Returns:
            Dict with handoff details and notification info
        """
        from packages.conversation.manager import ConversationManager

        conv_manager = ConversationManager(self.session)
        conversation = await conv_manager.set_handoff(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            reason=reason,
        )

        if not conversation:
            raise ValueError("Conversation not found")

        # Set SLA due time based on priority
        sla_minutes = {"low": 30, "normal": 5, "high": 2, "urgent": 1}.get(priority, 5)
        conversation.first_response_due_at = datetime.now(UTC) + timedelta(minutes=sla_minutes)

        # Log event
        event = HandoffRequested(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            contact_id=conversation.contact_id,
            reason=reason,
            triggered_by=triggered_by,
            priority=priority,
            sla_due_at=conversation.first_response_due_at,
        )
        await self._audit_handoff(tenant_id, conversation_id, event)

        # Determine owners to notify
        owners = await self._get_owners_to_notify(tenant_id, conversation)

        return {
            "handoff_id": str(conversation.id),
            "conversation_id": str(conversation.id),
            "reason": reason,
            "priority": priority,
            "sla_due_at": conversation.first_response_due_at.isoformat(),
            "owners_to_notify": [
                {"id": str(o.id), "phone": o.phone, "name": o.name} for o in owners
            ],
        }

    async def accept_handoff(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        accepted_by: UserId,
    ) -> dict[str, Any]:
        """
        Owner/staff accepts a handoff request.
        """
        from packages.conversation.manager import ConversationManager

        conv_manager = ConversationManager(self.session)
        conversation = await conv_manager.accept_handoff(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            accepted_by=accepted_by,
        )

        if not conversation:
            raise ValueError("Conversation not found or not in handoff state")

        # Log event
        event = HandoffAccepted(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            accepted_by=accepted_by,
        )
        await self._audit_handoff(tenant_id, conversation_id, event, user_id=accepted_by)

        return {
            "handoff_id": str(conversation.id),
            "status": "active",
            "accepted_by": str(accepted_by),
            "accepted_at": datetime.now(UTC).isoformat(),
        }

    async def complete_handoff(
        self,
        tenant_id: TenantId,
        conversation_id: UUID,
        completed_by: UserId,
        outcome: str,  # "resolved", "escalated", "closed", "returned_to_agent"
    ) -> dict[str, Any]:
        """
        Complete a handoff (resolve, escalate, close, or return to agent).
        """
        from packages.conversation.manager import ConversationManager

        conv_manager = ConversationManager(self.session)

        if outcome == "returned_to_agent":
            new_state = WhatsAppConversationState.AWAITING_RESPONSE
        elif outcome == "closed":
            new_state = WhatsAppConversationState.CLOSED
        else:
            new_state = WhatsAppConversationState.HANDOFF_ACTIVE

        conversation = await conv_manager.update_state(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            new_state=new_state,
            reason=f"Handoff completed: {outcome}",
        )

        if not conversation:
            raise ValueError("Conversation not found")

        # Log event
        event = HandoffCompleted(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            completed_by=completed_by,
            outcome=outcome,
        )
        await self._audit_handoff(tenant_id, conversation_id, event, user_id=completed_by)

        return {
            "handoff_id": str(conversation.id),
            "outcome": outcome,
            "new_state": new_state.value,
            "completed_at": datetime.now(UTC).isoformat(),
        }

    # =========================================================================
    # Notifications
    # =========================================================================

    async def _get_owners_to_notify(self, tenant_id: TenantId, conversation: Any) -> list[Any]:
        """Get list of owners/staff to notify for handoff."""
        # Get owner (role=owner) and active staff
        result = await self.session.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.role.in_(["owner", "staff"]),
                User.is_active,
            )
        )
        return list(result.scalars().all())

    async def notify_owners(
        self,
        tenant_id: TenantId,
        owners: list[Any],
        conversation_id: UUID,
        reason: str,
        priority: str,
    ) -> list[dict[str, Any]]:
        """
        Notify owners via WhatsApp template + in-app notification.

        Returns list of notification results.
        """
        # This would integrate with WhatsApp sender to send template messages
        # For now, return placeholder
        return [
            {
                "owner_id": str(owner.id),
                "notified": True,
                "method": "whatsapp_template",
            }
            for owner in owners
        ]

    # =========================================================================
    # SLA Monitoring
    # =========================================================================

    async def get_overdue_handoffs(self, tenant_id: TenantId) -> list[dict[str, Any]]:
        """Get handoffs past their SLA due time."""
        now = datetime.now(UTC)
        result = await self.session.execute(
            select(WhatsAppConversation)
            .where(
                WhatsAppConversation.tenant_id == tenant_id,
                WhatsAppConversation.state == WhatsAppConversationState.HANDOFF_REQUESTED,
                WhatsAppConversation.first_response_due_at.is_not(None),
                WhatsAppConversation.first_response_due_at < now,
            )
            .order_by(WhatsAppConversation.first_response_due_at)
        )

        overdue: list[dict[str, Any]] = []
        for c in result.scalars().all():
            due_at = c.first_response_due_at
            if due_at is None:
                continue
            overdue.append({
                "conversation_id": str(c.id),
                "contact_id": str(c.contact_id),
                "due_at": due_at.isoformat(),
                "overdue_minutes": int((now - due_at).total_seconds() / 60),
                "reason": c.handoff_reason,
            })
        return overdue

    async def escalate_overdue(self, tenant_id: TenantId) -> list[dict[str, Any]]:
        """Escalate overdue handoffs (notify higher priority)."""
        overdue = await self.get_overdue_handoffs(tenant_id)

        escalated = []
        for handoff in overdue:
            # Find senior owners to escalate to
            result = await self.session.execute(
                select(User).where(
                    User.tenant_id == tenant_id,
                    User.role == "owner",
                    User.is_active,
                )
            )
            senior_owners = list(result.scalars().all())

            # Notify senior owners
            await self.notify_owners(
                tenant_id=tenant_id,
                owners=senior_owners,
                conversation_id=UUID(handoff["conversation_id"]),
                reason=f"ESCALATED: {handoff['reason']}",
                priority="urgent",
            )

            escalated.append({
                "conversation_id": handoff["conversation_id"],
                "escalated_to": [str(o.id) for o in senior_owners],
            })

        return escalated

    # =========================================================================
    # Statistics
    # =========================================================================

    async def get_handoff_stats(self, tenant_id: TenantId) -> dict[str, Any]:
        """Get handoff statistics for a tenant."""
        # Total handoffs requested
        total_requested = (
            await self.session.execute(
                select(func.count(WhatsAppConversation.id)).where(
                    WhatsAppConversation.tenant_id == tenant_id,
                    WhatsAppConversation.handoff_requested_at.is_not(None),
                )
            )
        ).scalar() or 0

        # Currently active handoffs
        active = (
            await self.session.execute(
                select(func.count(WhatsAppConversation.id)).where(
                    WhatsAppConversation.tenant_id == tenant_id,
                    WhatsAppConversation.state.in_([
                        WhatsAppConversationState.HANDOFF_REQUESTED,
                        WhatsAppConversationState.HANDOFF_ACTIVE,
                    ]),
                )
            )
        ).scalar() or 0

        # Completed handoffs
        completed = (
            await self.session.execute(
                select(func.count(WhatsAppConversation.id)).where(
                    WhatsAppConversation.tenant_id == tenant_id,
                    WhatsAppConversation.state.in_([
                        WhatsAppConversationState.CLOSED,
                        WhatsAppConversationState.AWAITING_RESPONSE,
                    ]),
                    WhatsAppConversation.handoff_requested_at.is_not(None),
                )
            )
        ).scalar() or 0

        # Overdue
        overdue = len(await self.get_overdue_handoffs(tenant_id))

        return {
            "total_requested": total_requested,
            "active": active,
            "completed": completed,
            "overdue": overdue,
        }


# Need to import func
