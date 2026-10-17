"""
Owner alert repositories (tenant-scoped).

All lookups carry ``tenant_id`` explicitly; every table is also covered by
Postgres RLS (migration 0011) as the second line of defense.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models.alert import OwnerAlert


class AlertRepository:
    """Tenant-scoped data access for owner alerts."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, alert: OwnerAlert) -> OwnerAlert:
        self.session.add(alert)
        await self.session.flush()
        return alert

    async def get(self, tenant_id: UUID, alert_id: UUID) -> OwnerAlert | None:
        stmt = select(OwnerAlert).where(
            OwnerAlert.tenant_id == tenant_id,
            OwnerAlert.id == alert_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_dedupe_key(self, tenant_id: UUID, dedupe_key: str) -> OwnerAlert | None:
        stmt = select(OwnerAlert).where(
            OwnerAlert.tenant_id == tenant_id,
            OwnerAlert.dedupe_key == dedupe_key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_alerts(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[OwnerAlert]:
        stmt = select(OwnerAlert).where(OwnerAlert.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(OwnerAlert.status == status)
        stmt = stmt.order_by(OwnerAlert.created_at.desc(), OwnerAlert.id.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())


__all__ = ["AlertRepository"]
