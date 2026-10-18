"""
Owner alert service.

Raises durable, deduplicated alerts the owner can act on, and acknowledges them.
This is the replacement for log-only alerting (see docs/UNCERTAINTY.md U15): a
log line is lost to rotation; an ``owner_alerts`` row is queryable.

Invariants
----------
1. Raising an alert is IDEMPOTENT per ``(tenant_id, dedupe_key)``: an at-least-once
   outbox handler replaying an event does not create a second alert for the same
   subject, and a genuine concurrent race (two workers) is absorbed by the unique
   constraint rather than surfacing as an error. ``dedupe_key`` is optional;
   alerts without one always insert.
2. Alerts carry no PII beyond what the owner already sees (order ids, amounts,
   receipts); the service never copies customer phone/name into the alert body.
3. Alerts are repository-scoped to the tenant on every query; Postgres RLS is the
   second line of defence and activates once the API tenancy middleware is wired
   (docs/UNCERTAINTY.md U23).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.alerts.repository import AlertRepository
from packages.core.audit.logger import AuditLogger
from packages.core.domain import TenantId
from packages.core.models.alert import AlertSeverity, AlertStatus, OwnerAlert


class AlertService:
    """Create and manage owner alerts for one tenant."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.alerts = AlertRepository(session)
        self.audit = AuditLogger(session)

    async def raise_alert(
        self,
        tenant_id: TenantId,
        *,
        kind: str,
        title: str,
        body: str = "",
        severity: AlertSeverity = AlertSeverity.WARNING,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> OwnerAlert:
        """Create an alert, or return the existing one for the same dedupe_key."""
        if dedupe_key is not None:
            existing = await self.alerts.get_by_dedupe_key(tenant_id, dedupe_key)
            if existing is not None:
                return existing

        alert = OwnerAlert(
            tenant_id=tenant_id,
            kind=kind,
            severity=severity.value,
            title=title,
            body=body,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=metadata or {},
            dedupe_key=dedupe_key,
            status=AlertStatus.OPEN.value,
        )
        try:
            # Savepoint so a lost race (unique violation) rolls back only this
            # INSERT, leaving the caller's transaction usable.
            async with self.session.begin_nested():
                await self.alerts.add(alert)
        except IntegrityError:
            if dedupe_key is None:
                raise
            existing = await self.alerts.get_by_dedupe_key(tenant_id, dedupe_key)
            if existing is None:
                raise
            return existing
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="alert.raised",
            resource="owner_alert",
            resource_id=alert.id,
            after={
                "kind": kind,
                "severity": severity.value,
                "title": title,
                "resource_type": resource_type,
                "dedupe_key": dedupe_key,
            },
        )
        return alert

    async def acknowledge(
        self,
        tenant_id: TenantId,
        alert_id: UUID,
        *,
        user_id: UUID | None,
    ) -> OwnerAlert | None:
        """Mark an alert acknowledged (idempotent). Returns None if not found."""
        alert = await self.alerts.get(tenant_id, alert_id)
        if alert is None:
            return None
        if alert.status == AlertStatus.OPEN.value:
            alert.status = AlertStatus.ACKNOWLEDGED.value
            alert.acknowledged_at = datetime.now(UTC)
            alert.acknowledged_by = user_id
            await self.session.flush()
            await self.audit.log_action(
                tenant_id=tenant_id,
                user_id=user_id,
                action="alert.acknowledged",
                resource="owner_alert",
                resource_id=alert.id,
                after={
                    "status": alert.status,
                    "acknowledged_by": str(user_id) if user_id else None,
                },
            )
        return alert

    async def list_alerts(
        self, tenant_id: TenantId, *, status: str | None = None, limit: int = 100,
    ) -> list[OwnerAlert]:
        return await self.alerts.list_alerts(tenant_id, status=status, limit=limit)


__all__ = ["AlertService"]
