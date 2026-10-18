"""
CRM memory API routes (Phase 8a).

Thin, tenant-scoped HTTP layer over :class:`packages.core.crm.CRMService`:

Reads (staff):
- ``GET  /api/v1/crm/customers/{id}/memory``   latest traits + consent + recent events
- ``GET  /api/v1/crm/customers/{id}/events``   the append-only event log
- ``GET  /api/v1/crm/customers/{id}/traits``   all trait versions (with evidence)

Writes (staff):
- ``POST /api/v1/crm/customers/{id}/consent``  record a consent decision (sourced)
- ``POST /api/v1/crm/customers/{id}/opt-out``  record a marketing denial
- ``POST /api/v1/crm/customers/{id}/derive``   recompute evidence-backed traits

Data-subject rights (owner):
- ``GET    /api/v1/crm/customers/{id}/export``  full record for a DSR request
- ``DELETE /api/v1/crm/customers/{id}``         human-approved erasure (audited)

All arithmetic is deterministic and lives in the service; the API only shapes
I/O. No endpoint accepts a caller-supplied trait name (allow-list lives in the
service), so an API caller cannot inject a sensitive attribute.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.dependencies import (
    get_current_tenant_id,
    get_current_user_id,
    get_db,
    require_owner,
    require_staff,
)
from packages.core.crm import CRMService, CustomerNotFoundError
from packages.core.domain import TenantId

router = APIRouter(prefix="/api/v1/crm", tags=["crm"])


# =============================================================================
# Schemas
# =============================================================================


class EventView(BaseModel):
    id: str
    event_type: str
    channel: str | None
    summary: str
    occurred_at: str


class TraitView(BaseModel):
    name: str
    value: Any
    confidence: float
    version: int
    evidence: dict[str, Any]
    computed_at: str


class ConsentView(BaseModel):
    purpose: str
    granted: bool
    source: str
    recorded_at: str


class MemoryView(BaseModel):
    customer_id: str
    traits: list[TraitView]
    consents: list[ConsentView]
    recent_events: list[EventView]


class EventListView(BaseModel):
    customer_id: str
    events: list[EventView]


class TraitListView(BaseModel):
    customer_id: str
    traits: list[TraitView]


class ConsentRequest(BaseModel):
    purpose: str = Field(min_length=1, max_length=30)
    granted: bool
    source: str = Field(min_length=1, max_length=50)
    evidence: str | None = Field(default=None, max_length=300)


class EraseResponse(BaseModel):
    customer_id: str
    erased: bool


# =============================================================================
# Shaping
# =============================================================================


def _event(e: Any) -> EventView:
    return EventView(
        id=str(e.id),
        event_type=e.event_type,
        channel=e.channel,
        summary=e.summary,
        occurred_at=e.occurred_at.isoformat(),
    )


def _trait(t: Any) -> TraitView:
    return TraitView(
        name=t.name,
        value=t.value_json,
        confidence=t.confidence,
        version=t.version,
        evidence=t.evidence_json,
        computed_at=t.computed_at.isoformat(),
    )


def _consent(c: Any) -> ConsentView:
    return ConsentView(
        purpose=c.purpose,
        granted=c.granted,
        source=c.source,
        recorded_at=c.recorded_at.isoformat(),
    )


async def _customer_or_404(service: CRMService, tenant_id: TenantId, customer_id: UUID) -> None:
    customer = await service.repo.get_customer(tenant_id, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")


# =============================================================================
# Reads (staff)
# =============================================================================


@router.get("/customers/{customer_id}/memory", response_model=MemoryView)
async def get_customer_memory(
    customer_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_staff)],
    db: Annotated[AsyncSession, Depends(get_db)],
    recent_limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> MemoryView:
    """Latest trait per name + all consent records + most recent events (chronological)."""
    service = CRMService(db)
    await _customer_or_404(service, TenantId(tenant_id), customer_id)
    traits = await service.repo.latest_traits(TenantId(tenant_id), customer_id)
    consents = await service.repo.list_consents(TenantId(tenant_id), customer_id)
    events = await service.repo.recent_events(TenantId(tenant_id), customer_id, limit=recent_limit)
    return MemoryView(
        customer_id=str(customer_id),
        traits=[_trait(t) for t in traits],
        consents=[_consent(c) for c in consents],
        recent_events=[_event(e) for e in events],
    )


@router.get("/customers/{customer_id}/events", response_model=EventListView)
async def list_customer_events(
    customer_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_staff)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> EventListView:
    """The append-only event log for one customer (oldest first)."""
    service = CRMService(db)
    await _customer_or_404(service, TenantId(tenant_id), customer_id)
    events = await service.repo.list_events(TenantId(tenant_id), customer_id, limit=limit)
    return EventListView(customer_id=str(customer_id), events=[_event(e) for e in events])


@router.get("/customers/{customer_id}/traits", response_model=TraitListView)
async def list_customer_traits(
    customer_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_staff)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TraitListView:
    """All trait versions (evidence included) for one customer."""
    service = CRMService(db)
    await _customer_or_404(service, TenantId(tenant_id), customer_id)
    traits = await service.repo.list_traits(TenantId(tenant_id), customer_id)
    return TraitListView(customer_id=str(customer_id), traits=[_trait(t) for t in traits])


# =============================================================================
# Writes (staff)
# =============================================================================


@router.post("/customers/{customer_id}/consent", response_model=ConsentView)
async def record_consent(
    customer_id: UUID,
    body: ConsentRequest,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_staff)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConsentView:
    """Record one consent decision (append; latest per purpose wins)."""
    service = CRMService(db)
    await _customer_or_404(service, TenantId(tenant_id), customer_id)
    record = await service.record_consent(
        TenantId(tenant_id),
        customer_id,
        purpose=body.purpose,
        granted=body.granted,
        source=body.source,
        evidence=body.evidence,
    )
    await db.commit()
    return _consent(record)


@router.post("/customers/{customer_id}/opt-out", response_model=ConsentView)
async def opt_out(
    customer_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_staff)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ConsentView:
    """Record a marketing opt-out for this customer."""
    service = CRMService(db)
    await _customer_or_404(service, TenantId(tenant_id), customer_id)
    record = await service.opt_out(TenantId(tenant_id), customer_id)
    await db.commit()
    return _consent(record)


@router.post("/customers/{customer_id}/derive", response_model=TraitListView)
async def derive_traits(
    customer_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_staff)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TraitListView:
    """Recompute the allow-listed, evidence-backed traits for one customer."""
    service = CRMService(db)
    await _customer_or_404(service, TenantId(tenant_id), customer_id)
    await service.derive_traits(TenantId(tenant_id), customer_id)
    await db.commit()
    traits = await service.repo.latest_traits(TenantId(tenant_id), customer_id)
    return TraitListView(customer_id=str(customer_id), traits=[_trait(t) for t in traits])


# =============================================================================
# Data-subject rights (owner)
# =============================================================================


@router.get("/customers/{customer_id}/export")
async def export_customer(
    customer_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Full customer record for a data-subject access request."""
    service = CRMService(db)
    try:
        return await service.export_customer(TenantId(tenant_id), customer_id)
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer not found") from exc


@router.delete("/customers/{customer_id}", response_model=EraseResponse)
async def erase_customer(
    customer_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    _: Annotated[Any, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> EraseResponse:
    """Human-approved erasure of a customer's CRM memory (audited).

    The owner is the approver; the action is written to the audit log with the
    acting user id.
    """
    service = CRMService(db)
    try:
        await service.delete_customer(TenantId(tenant_id), customer_id, approved_by=user_id)
    except CustomerNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer not found") from exc
    await db.commit()
    return EraseResponse(customer_id=str(customer_id), erased=True)


__all__: list[str] = []
