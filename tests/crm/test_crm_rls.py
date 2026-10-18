"""Phase 8a: tenant-isolation (RLS) tests for the CRM memory tables
(customer_events, consent_records, customer_traits).

Customer memory is privacy-sensitive: a cross-tenant leak is a release blocker
(plan invariant 6). Data is created as the table owner (RLS bypassed); reads
and writes then go through the non-owner ``socialerp_app`` role, which is
subject to the RLS policy installed by the test schema (mirrors
tests/alerts/test_alerts_rls.py).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.models.crm import ConsentRecord, CustomerEvent, CustomerTrait
from tests.crm.helpers import make_customer, make_tenant


async def _set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    await session.execute(select(func.set_config("app.current_tenant_id", str(tenant_id), True)))


CrmModel = CustomerEvent | ConsentRecord | CustomerTrait


async def _count(session: AsyncSession, model: type[CrmModel]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _seed_crm_rows(
    session: AsyncSession, tenant_id: UUID, customer_id: UUID
) -> dict[str, UUID]:
    event = CustomerEvent(
        tenant_id=tenant_id,
        customer_id=customer_id,
        event_type="message.summarised",
        summary="seeded",
        data_json={},
        dedupe_key=f"rls-{uuid4().hex}",
    )
    consent = ConsentRecord(
        tenant_id=tenant_id,
        customer_id=customer_id,
        purpose="marketing",
        granted=True,
        source="whatsapp_opt_in",
    )
    trait = CustomerTrait(
        tenant_id=tenant_id,
        customer_id=customer_id,
        name="order_count",
        value_json=3,
        confidence=1.0,
        evidence_json={"order_ids": []},
        version=1,
    )
    session.add_all([event, consent, trait])
    await session.flush()
    return {"event": event.id, "consent": consent.id, "trait": trait.id}


async def test_crm_tables_are_isolated_between_tenants(
    session_factory: async_sessionmaker[AsyncSession],
    rls_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session)
        tenant_b = await make_tenant(session)
        customer_a = await make_customer(session, tenant_a.id)
        ids = await _seed_crm_rows(session, tenant_a.id, customer_a.id)

    async with rls_session_factory() as session:
        # Tenant A sees its own rows...
        await _set_tenant(session, tenant_a.id)
        assert await _count(session, CustomerEvent) == 1
        assert await _count(session, ConsentRecord) == 1
        assert await _count(session, CustomerTrait) == 1

        # ...tenant B sees nothing.
        await _set_tenant(session, tenant_b.id)
        assert await _count(session, CustomerEvent) == 0
        assert await _count(session, ConsentRecord) == 0
        assert await _count(session, CustomerTrait) == 0

        # Direct id lookups fail closed for the foreign tenant.
        for model, key in (
            (CustomerEvent, "event"),
            (ConsentRecord, "consent"),
            (CustomerTrait, "trait"),
        ):
            row = (
                await session.execute(select(model).where(model.id == ids[key]))
            ).scalar_one_or_none()
            assert row is None, f"{model.__name__} leaked across tenants"


async def test_crm_cross_tenant_update_and_delete_affect_zero_rows(
    session_factory: async_sessionmaker[AsyncSession],
    rls_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A foreign tenant cannot rewrite or erase CRM rows even with the exact id."""
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session)
        tenant_b = await make_tenant(session)
        customer_a = await make_customer(session, tenant_a.id)
        ids = await _seed_crm_rows(session, tenant_a.id, customer_a.id)

    async with rls_session_factory() as session:
        await _set_tenant(session, tenant_b.id)
        updated = await session.execute(
            update(CustomerEvent).where(CustomerEvent.id == ids["event"]).values(summary="hacked")
        )
        assert isinstance(updated, CursorResult)
        assert updated.rowcount == 0
        deleted = await session.execute(
            delete(ConsentRecord).where(ConsentRecord.id == ids["consent"])
        )
        assert isinstance(deleted, CursorResult)
        assert deleted.rowcount == 0
        deleted_trait = await session.execute(
            delete(CustomerTrait).where(CustomerTrait.id == ids["trait"])
        )
        assert isinstance(deleted_trait, CursorResult)
        assert deleted_trait.rowcount == 0

    async with session_factory() as session:
        event = (
            await session.execute(select(CustomerEvent).where(CustomerEvent.id == ids["event"]))
        ).scalar_one()
        assert event.summary == "seeded"


async def test_rls_role_can_write_rows_for_its_own_tenant(
    session_factory: async_sessionmaker[AsyncSession],
    rls_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Positive control: the policy allows a tenant to append its own CRM rows."""
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session)
        customer_a = await make_customer(session, tenant_a.id)

    async with rls_session_factory() as session:
        await _set_tenant(session, tenant_a.id)
        session.add(
            CustomerEvent(
                tenant_id=tenant_a.id,
                customer_id=customer_a.id,
                event_type="message.summarised",
                summary="own tenant insert",
                data_json={},
            )
        )
        await session.flush()
        await session.commit()

    async with session_factory() as session:
        assert await _count(session, CustomerEvent) == 1


async def test_rls_blocks_cross_tenant_insert_into_customer_events(
    session_factory: async_sessionmaker[AsyncSession],
    rls_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session)
        tenant_b = await make_tenant(session)
        customer_a = await make_customer(session, tenant_a.id)

    async with rls_session_factory() as session:
        await _set_tenant(session, tenant_a.id)
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                session.add(
                    CustomerEvent(
                        tenant_id=tenant_b.id,  # foreign tenant
                        customer_id=customer_a.id,
                        event_type="forged",
                        summary="",
                        data_json={},
                        dedupe_key="forged-event",
                    )
                )
                await session.flush()


async def test_rls_blocks_cross_tenant_insert_into_consent_records(
    session_factory: async_sessionmaker[AsyncSession],
    rls_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session)
        tenant_b = await make_tenant(session)
        customer_a = await make_customer(session, tenant_a.id)

    async with rls_session_factory() as session:
        await _set_tenant(session, tenant_a.id)
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                session.add(
                    ConsentRecord(
                        tenant_id=tenant_b.id,  # foreign tenant
                        customer_id=customer_a.id,
                        purpose="marketing",
                        granted=True,
                        source="forged",
                    )
                )
                await session.flush()


async def test_rls_blocks_cross_tenant_insert_into_customer_traits(
    session_factory: async_sessionmaker[AsyncSession],
    rls_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        tenant_a = await make_tenant(session)
        tenant_b = await make_tenant(session)
        customer_a = await make_customer(session, tenant_a.id)

    async with rls_session_factory() as session:
        await _set_tenant(session, tenant_a.id)
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                session.add(
                    CustomerTrait(
                        tenant_id=tenant_b.id,  # foreign tenant
                        customer_id=customer_a.id,
                        name="order_count",
                        value_json=1,
                        confidence=1.0,
                        evidence_json={"x": 1},
                        version=1,
                    )
                )
                await session.flush()
