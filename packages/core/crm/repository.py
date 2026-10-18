"""
CRM repositories (tenant-scoped).

Every method takes ``tenant_id`` and constrains on it; the tables also carry
RLS (migration 0012) as the second line of defence. There is deliberately NO
update/delete method for :class:`CustomerEvent` (the log is append-only);
erasure goes through a narrowly-scoped path in the service that sets the
``app.allow_event_erasure`` session flag.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models.crm import ConsentRecord, CustomerEvent, CustomerTrait
from packages.core.models.order import Customer, Order, OrderItem


class CRMRepository:
    """Tenant-scoped data access for CRM memory."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------------------------------------------------------------- events
    async def add_event(self, event: CustomerEvent) -> CustomerEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_event_by_dedupe(self, tenant_id: UUID, dedupe_key: str) -> CustomerEvent | None:
        stmt = select(CustomerEvent).where(
            CustomerEvent.tenant_id == tenant_id,
            CustomerEvent.dedupe_key == dedupe_key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_events(
        self, tenant_id: UUID, customer_id: UUID, *, limit: int = 200
    ) -> list[CustomerEvent]:
        stmt = (
            select(CustomerEvent)
            .where(
                CustomerEvent.tenant_id == tenant_id,
                CustomerEvent.customer_id == customer_id,
            )
            .order_by(CustomerEvent.occurred_at.asc(), CustomerEvent.id.asc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def recent_events(
        self, tenant_id: UUID, customer_id: UUID, *, limit: int = 20
    ) -> list[CustomerEvent]:
        """The most recent events, returned oldest-first (chronological).

        Unlike :meth:`list_events` (which caps at the OLDEST ``limit`` rows),
        this takes the NEWEST ``limit`` rows and reverses them into
        chronological order, so a customer with more events than ``limit``
        still sees their real recent history in the /memory view.
        """
        stmt = (
            select(CustomerEvent)
            .where(
                CustomerEvent.tenant_id == tenant_id,
                CustomerEvent.customer_id == customer_id,
            )
            .order_by(CustomerEvent.occurred_at.desc(), CustomerEvent.id.desc())
            .limit(limit)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return list(reversed(rows))

    # --------------------------------------------------------------- consent
    async def add_consent(self, record: ConsentRecord) -> ConsentRecord:
        self.session.add(record)
        await self.session.flush()
        return record

    async def latest_consent(
        self, tenant_id: UUID, customer_id: UUID, purpose: str
    ) -> ConsentRecord | None:
        stmt = (
            select(ConsentRecord)
            .where(
                ConsentRecord.tenant_id == tenant_id,
                ConsentRecord.customer_id == customer_id,
                ConsentRecord.purpose == purpose,
            )
            .order_by(ConsentRecord.recorded_at.desc(), ConsentRecord.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def list_consents(self, tenant_id: UUID, customer_id: UUID) -> list[ConsentRecord]:
        stmt = (
            select(ConsentRecord)
            .where(
                ConsentRecord.tenant_id == tenant_id,
                ConsentRecord.customer_id == customer_id,
            )
            .order_by(ConsentRecord.recorded_at.asc(), ConsentRecord.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    # ---------------------------------------------------------------- traits
    async def add_trait(self, trait: CustomerTrait) -> CustomerTrait:
        self.session.add(trait)
        await self.session.flush()
        return trait

    async def max_trait_version(self, tenant_id: UUID, customer_id: UUID, name: str) -> int:
        stmt = select(func.max(CustomerTrait.version)).where(
            CustomerTrait.tenant_id == tenant_id,
            CustomerTrait.customer_id == customer_id,
            CustomerTrait.name == name,
        )
        return int(await self.session.scalar(stmt) or 0)

    async def list_traits(self, tenant_id: UUID, customer_id: UUID) -> list[CustomerTrait]:
        stmt = (
            select(CustomerTrait)
            .where(
                CustomerTrait.tenant_id == tenant_id,
                CustomerTrait.customer_id == customer_id,
            )
            .order_by(CustomerTrait.name.asc(), CustomerTrait.version.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def latest_traits(self, tenant_id: UUID, customer_id: UUID) -> list[CustomerTrait]:
        """The highest-version row per trait name."""
        traits = await self.list_traits(tenant_id, customer_id)
        latest: dict[str, CustomerTrait] = {}
        for trait in traits:
            current = latest.get(trait.name)
            if current is None or trait.version > current.version:
                latest[trait.name] = trait
        return list(latest.values())

    # ------------------------------------------------------------------- data
    async def get_customer(self, tenant_id: UUID, customer_id: UUID) -> Customer | None:
        stmt = select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def orders_for_customer(self, tenant_id: UUID, customer_id: UUID) -> list[Order]:
        stmt = (
            select(Order)
            .where(Order.tenant_id == tenant_id, Order.customer_id == customer_id)
            .order_by(Order.created_at.asc(), Order.id.asc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def items_for_orders(self, tenant_id: UUID, order_ids: Sequence[UUID]) -> list[OrderItem]:
        if not order_ids:
            return []
        stmt = select(OrderItem).where(
            OrderItem.tenant_id == tenant_id, OrderItem.order_id.in_(list(order_ids))
        )
        return list((await self.session.execute(stmt)).scalars().all())


__all__ = ["CRMRepository"]
