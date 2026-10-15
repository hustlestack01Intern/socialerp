"""
Append-only audit log for all agent actions and system events.

Every agent action, payment callback, state transition, and security event
is recorded with full context for compliance, debugging, and analytics.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from socialerp.tenancy import get_current_tenant_id

from packages.core.models.core import AuditLog

__all__ = [
    "AuditLog",
    "AuditLogEntry",
    "log_agent_action",
    "log_event",
    "log_payment_event",
    "log_security_event",
    "log_webhook_event",
]


class AuditLogEntry(BaseModel):
    """Schema for audit log entries."""

    event_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    actor_type: str  # "user", "agent", "system", "webhook", "rider"
    actor_id: str | None = None  # user_id, agent_run_id, webhook_id, etc.
    action: str  # e.g., "order.created", "payment.callback", "agent.reply"
    payload: dict[str, Any] = Field(default_factory=dict)
    decision: str | None = None  # "allowed", "denied", "handed_off", "error"
    rule_ref: str | None = None  # reference to policy rule that allowed/denied
    confidence: float | None = None  # for agent decisions
    correlation_id: str | None = None  # for tracing related events
    created_at: datetime = Field(default_factory=datetime.utcnow)


# NOTE: `AuditLog` is the canonical model from `packages.core.models.core`
# (imported above and re-exported) — see ADR 0008. Do not define a second
# `audit_log` table here.


async def log_event(
    action: str,
    payload: dict[str, Any],
    actor_type: str = "system",
    actor_id: str | None = None,
    decision: str | None = None,
    rule_ref: str | None = None,
    confidence: float | None = None,
    correlation_id: str | None = None,
    tenant_id: UUID | None = None,
) -> UUID:
    """
    Log an audit event.

    Must be called within a tenant context (tenant_id from context or explicit).
    """
    from socialerp.database import get_session

    if tenant_id is None:
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise RuntimeError("No tenant context for audit log")

    entry = AuditLogEntry(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        payload=payload,
        decision=decision,
        rule_ref=rule_ref,
        confidence=confidence,
        correlation_id=correlation_id,
    )

    async with get_session() as session:
        log_entry = AuditLog(
            tenant_id=entry.tenant_id,
            actor_type=entry.actor_type,
            actor_id=entry.actor_id,
            action=entry.action,
            payload=entry.payload,
            decision=entry.decision,
            rule_ref=entry.rule_ref,
            confidence=entry.confidence,
            correlation_id=entry.correlation_id,
            created_at=entry.created_at,
        )
        session.add(log_entry)
        await session.flush()
        return log_entry.id


async def log_agent_action(
    action: str,
    payload: dict[str, Any],
    agent_run_id: str,
    decision: str,
    rule_ref: str,
    confidence: float,
    correlation_id: str | None = None,
) -> UUID:
    """Log an agent action with full context."""
    return await log_event(
        action=action,
        payload=payload,
        actor_type="agent",
        actor_id=agent_run_id,
        decision=decision,
        rule_ref=rule_ref,
        confidence=confidence,
        correlation_id=correlation_id,
    )


async def log_webhook_event(
    provider: str,
    event_type: str,
    payload: dict[str, Any],
    webhook_id: str,
    decision: str = "processed",
) -> UUID:
    """Log an incoming webhook event."""
    return await log_event(
        action=f"webhook.{provider}.{event_type}",
        payload=payload,
        actor_type="webhook",
        actor_id=webhook_id,
        decision=decision,
        correlation_id=webhook_id,
    )


async def log_payment_event(
    action: str,
    payload: dict[str, Any],
    payment_id: str,
    decision: str,
    correlation_id: str | None = None,
) -> UUID:
    """Log a payment-related event."""
    return await log_event(
        action=f"payment.{action}",
        payload=payload,
        actor_type="system",
        actor_id=payment_id,
        decision=decision,
        correlation_id=correlation_id,
    )


async def log_security_event(
    action: str,
    payload: dict[str, Any],
    actor_type: str,
    actor_id: str | None = None,
    decision: str = "flagged",
    correlation_id: str | None = None,
) -> UUID:
    """Log a security-relevant event."""
    return await log_event(
        action=f"security.{action}",
        payload=payload,
        actor_type=actor_type,
        actor_id=actor_id,
        decision=decision,
        correlation_id=correlation_id,
    )
