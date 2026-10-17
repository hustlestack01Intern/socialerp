"""
Owner alert API routes (Phase 7 remainder).

Thin, tenant-scoped HTTP layer over :class:`packages.core.alerts.AlertService`:

- ``GET  /api/v1/alerts``                  (authenticated): list alerts (filter by status).
- ``POST /api/v1/alerts/{alert_id}/ack``   (authenticated): acknowledge one.

Replaces log-only alerting (UNCERTAINTY U15): an owner can now *see* "money was
captured on a non-collectable order" and acknowledge it, instead of hoping the
worker warning survived log rotation. Alerts carry no customer PII.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.alerts import AlertService
from packages.core.auth.dependencies import get_current_tenant_id, get_current_user_id, get_db
from packages.core.domain import TenantId
from packages.core.models.alert import OwnerAlert

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


# =============================================================================
# Schemas
# =============================================================================


class AlertView(BaseModel):
    id: str
    kind: str
    severity: str
    title: str
    body: str
    resource_type: str | None
    resource_id: str | None
    status: str
    created_at: str
    acknowledged_at: str | None


def _alert(alert: OwnerAlert) -> AlertView:
    return AlertView(
        id=str(alert.id),
        kind=alert.kind,
        severity=str(alert.severity),
        title=alert.title,
        body=alert.body,
        resource_type=alert.resource_type,
        resource_id=str(alert.resource_id) if alert.resource_id is not None else None,
        status=str(alert.status),
        created_at=alert.created_at.isoformat() if alert.created_at else "",
        acknowledged_at=(
            alert.acknowledged_at.isoformat() if alert.acknowledged_at is not None else None
        ),
    )


# =============================================================================
# Routes
# =============================================================================


@router.get("", response_model=list[AlertView])
async def list_alerts(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AlertView]:
    """List this tenant's alerts, newest first."""
    alerts = await AlertService(db).list_alerts(TenantId(tenant_id), status=status, limit=limit)
    return [_alert(a) for a in alerts]


@router.post("/{alert_id}/ack", response_model=AlertView)
async def acknowledge_alert(
    alert_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AlertView:
    """Acknowledge one of this tenant's alerts."""
    alert = await AlertService(db).acknowledge(TenantId(tenant_id), alert_id, user_id=user_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    await db.commit()
    return _alert(alert)


__all__: list[str] = []
