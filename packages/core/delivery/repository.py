"""
Delivery repositories (tenant-scoped).

All lookups carry ``tenant_id`` explicitly; every table is also covered by
Postgres RLS (migration 0010) as the second line of defense.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain.delivery import DeliveryStatus
from packages.core.models.delivery import Delivery, DeliveryZone, Rider


class DeliveryRepository:
    """Tenant-scoped data access for zones, riders and deliveries."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # -- zones ------------------------------------------------------------

    async def add_zone(self, zone: DeliveryZone) -> DeliveryZone:
        self.session.add(zone)
        await self.session.flush()
        return zone

    async def get_zone(self, tenant_id: UUID, zone_id: UUID) -> DeliveryZone | None:
        stmt = select(DeliveryZone).where(
            DeliveryZone.tenant_id == tenant_id,
            DeliveryZone.id == zone_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_zones(self, tenant_id: UUID, *, active_only: bool = False) -> list[DeliveryZone]:
        stmt = select(DeliveryZone).where(DeliveryZone.tenant_id == tenant_id)
        if active_only:
            stmt = stmt.where(DeliveryZone.active.is_(True))
        stmt = stmt.order_by(DeliveryZone.name)
        return list((await self.session.execute(stmt)).scalars().all())

    async def update_zone(
        self,
        zone: DeliveryZone,
        *,
        name: str | None = None,
        fee_cents: int | None = None,
        active: bool | None = None,
    ) -> DeliveryZone:
        if name is not None:
            zone.name = name
        if fee_cents is not None:
            zone.fee_cents = fee_cents
        if active is not None:
            zone.active = active
        await self.session.flush()
        return zone

    # -- riders -------------------------------------------------------------

    async def add_rider(self, rider: Rider) -> Rider:
        self.session.add(rider)
        await self.session.flush()
        return rider

    async def get_rider(self, tenant_id: UUID, rider_id: UUID) -> Rider | None:
        stmt = select(Rider).where(
            Rider.tenant_id == tenant_id,
            Rider.id == rider_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_rider_by_phone(self, tenant_id: UUID, phone: str) -> Rider | None:
        stmt = select(Rider).where(
            Rider.tenant_id == tenant_id,
            Rider.phone == phone,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_riders(self, tenant_id: UUID, *, active_only: bool = False) -> list[Rider]:
        stmt = select(Rider).where(Rider.tenant_id == tenant_id)
        if active_only:
            stmt = stmt.where(Rider.active.is_(True))
        stmt = stmt.order_by(Rider.name)
        return list((await self.session.execute(stmt)).scalars().all())

    async def deactivate_rider(self, rider: Rider) -> Rider:
        rider.active = False
        await self.session.flush()
        return rider

    # -- deliveries ----------------------------------------------------------

    async def add_delivery(self, delivery: Delivery) -> Delivery:
        self.session.add(delivery)
        await self.session.flush()
        return delivery

    async def get_delivery(self, tenant_id: UUID, delivery_id: UUID) -> Delivery | None:
        stmt = select(Delivery).where(
            Delivery.tenant_id == tenant_id,
            Delivery.id == delivery_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_delivery_by_order(self, tenant_id: UUID, order_id: UUID) -> Delivery | None:
        stmt = select(Delivery).where(
            Delivery.tenant_id == tenant_id,
            Delivery.order_id == order_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def active_delivery_for_order(self, tenant_id: UUID, order_id: UUID) -> Delivery | None:
        """The in-flight delivery for an order (assigned/picked_up/arrived/failed)."""
        stmt = select(Delivery).where(
            Delivery.tenant_id == tenant_id,
            Delivery.order_id == order_id,
            Delivery.status.in_([
                DeliveryStatus.ASSIGNED.value,
                DeliveryStatus.PICKED_UP.value,
                DeliveryStatus.ARRIVED.value,
                DeliveryStatus.FAILED.value,
            ]),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_deliveries(
        self, tenant_id: UUID, *, status: str | None = None, rider_id: UUID | None = None
    ) -> list[Delivery]:
        stmt = select(Delivery).where(Delivery.tenant_id == tenant_id)
        if status is not None:
            stmt = stmt.where(Delivery.status == status)
        if rider_id is not None:
            stmt = stmt.where(Delivery.rider_id == rider_id)
        stmt = stmt.order_by(Delivery.created_at.desc())
        return list((await self.session.execute(stmt)).scalars().all())
