"""
Audit Logger

Append-only audit logging for all tenant actions.

The audit log is written in the same transaction as the domain change
wherever possible (outbox pattern). Every entry records who did what,
to which resource, and what changed (before/after), plus provenance
metadata. Entries are never updated or deleted.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models import AuditLog as AuditLogModel

# Actions that are allowed to be logged. Kept open (not an enum) so new
# actions can be introduced without a migration; the append-only table
# and the audit API enforce the contract instead.


class AuditLogger:
    """Append-only audit logger bound to a database session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log_action(
        self,
        *,
        tenant_id: Any,
        user_id: Any | None,
        action: str,
        resource: str | None = None,
        resource_id: Any | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLogModel:
        """Write one immutable audit entry (insert-only)."""
        entry = AuditLogModel(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource=resource or "",
            resource_id=resource_id,
            before_json=before,
            after_json=after,
            metadata_json=metadata,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.session.add(entry)
        # NOTE: the caller's transaction commits; we never commit here.
        return entry
