"""
Segment API routes (Phase 8b).

Thin, owner-only, tenant-scoped HTTP layer over
:class:`packages.core.crm.segment_service.SegmentService`:

- ``GET /api/v1/segments``                    list the fixed segment definitions
- ``GET /api/v1/segments/{key}/members``      paginated member customer ids

AuthZ: ``require_owner`` on every route (plan 8b API section: segment
membership is owner-facing insight; non-owner denied — AC "API authZ:
owner-only"). Read-only: no writes, no audit rows, no side effects.

No new PII: members are customer IDs only — the routes never read or shape
phone/name/email beyond what the existing CRM read paths expose (plan
invariant 4). The ``predicate`` field is exposed for explainability: it is
static, allow-listed data (no customer values).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.dependencies import get_current_tenant_id, get_db, require_owner
from packages.core.crm.segment_service import SegmentService
from packages.core.crm.segments import SEGMENT_RULES, SegmentKey
from packages.core.domain import TenantId

router = APIRouter(prefix="/api/v1/segments", tags=["segments"])


# =============================================================================
# Schemas
# =============================================================================


class SegmentView(BaseModel):
    key: str
    title: str
    description: str
    predicate: dict[str, Any]


class SegmentMembersView(BaseModel):
    segment: str
    total: int
    limit: int
    offset: int
    customer_ids: list[str]


# =============================================================================
# Routes (owner-only)
# =============================================================================


@router.get("", response_model=list[SegmentView])
async def list_segments(
    _: Annotated[Any, Depends(require_owner)],
) -> list[SegmentView]:
    """The fixed segment catalogue (static definitions; no DB read needed)."""
    return [
        SegmentView(
            key=rule.key.value,
            title=rule.title,
            description=rule.description,
            predicate=dict(rule.predicate),
        )
        for rule in SEGMENT_RULES.values()
    ]


@router.get("/{segment_key}/members", response_model=SegmentMembersView)
async def segment_members(
    segment_key: str,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    _: Annotated[Any, Depends(require_owner)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SegmentMembersView:
    """Customer ids belonging to a segment for THIS tenant (sorted, paginated)."""
    try:
        key = SegmentKey(segment_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Unknown segment") from exc
    page = await SegmentService(db).list_members(
        TenantId(tenant_id), key, limit=limit, offset=offset,
    )
    return SegmentMembersView(
        segment=page.segment.value,
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        customer_ids=[str(cid) for cid in page.customer_ids],
    )


__all__: list[str] = []
