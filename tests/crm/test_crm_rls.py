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

from packages.core.crm import CRMService, erasure_tombstone
from packages.core.domain import TenantId
from packages.core.models.core import UserRole
from packages.core.models.crm import ConsentRecord, CustomerEvent, CustomerTrait
from packages.core.models.order import Customer, Order
from tests.crm.helpers import make_customer, make_order, make_tenant, make_user


async def _set_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    await session.execute(select(func.set_config("app.current_tenant_id", str(tenant_id), True)))


CrmModel = CustomerEvent | ConsentRecord | CustomerTrait


async def _count(session: AsyncSession, model: type[CrmModel]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _seed_crm_rows(
    session: AsyncSession, tenant_id: UUID, customer_id: UUID,
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
            update(CustomerEvent).where(CustomerEvent.id == ids["event"]).values(summary="hacked"),
        )
        assert isinstance(updated, CursorResult)
        assert updated.rowcount == 0
        deleted = await session.execute(
            delete(ConsentRecord).where(ConsentRecord.id == ids["consent"]),
        )
        assert isinstance(deleted, CursorResult)
        assert deleted.rowcount == 0
        deleted_trait = await session.execute(
            delete(CustomerTrait).where(CustomerTrait.id == ids["trait"]),
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
            ),
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
                    ),
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
                    ),
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
                    ),
                )
                await session.flush()


async def test_erasure_works_under_rls_role_without_preexisting_tenant_guc(
    session_factory: async_sessionmaker[AsyncSession],
    rls_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """U36: ``delete_customer`` must set ``app.current_tenant_id`` itself.

    The RLS policies are keyed on ``current_tenant_id()``. If the session has
    no GUC set (the state ``TenantContextMiddleware`` will guarantee later),
    every tenant-keyed predicate evaluates NULL: the reads find nothing and
    the DELETE/UPDATEs silently match zero rows. Erasure must therefore set
    the transaction-local GUC before it reads or writes anything.
    """
    async with session_factory() as session, session.begin():
        tenant = await make_tenant(session, slug_prefix="rls-erase")
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        approver_id = approver.id
        customer = await make_customer(session, tenant.id, phone="254712345678")
        customer_id = customer.id
        service = CRMService(session)
        tid = TenantId(tenant.id)
        await service.record_event(tid, customer_id, "message.summarised", dedupe_key="rls-e1")
        await service.record_consent(
            tid, customer_id, purpose="marketing", granted=True, source="opt_in",
        )
        await service.derive_traits(tid, customer_id)
        order = await make_order(session, tenant.id, customer_id, total_cents=999)
        order_id = order.id
        tenant_id = tenant.id

    # Erase through the non-owner RLS role with NO tenant GUC pre-set.
    async with rls_session_factory() as session, session.begin():
        await CRMService(session).delete_customer(
            TenantId(tenant_id), customer_id, approved_by=approver_id,
        )

    # Verify as the table owner: the rows must actually be gone (U36 red:
    # CustomerNotFoundError, because the customer row was invisible and the
    # writes would have silently matched 0 rows).
    async with session_factory() as session:
        counts: dict[str, int] = {}
        for table, model in (
            ("customer_events", CustomerEvent),
            ("consent_records", ConsentRecord),
            ("customer_traits", CustomerTrait),
        ):
            counts[table] = int(
                await session.scalar(
                    select(func.count()).select_from(model).where(model.customer_id == customer_id),
                )
                or 0,
            )
        erased_phone = await session.scalar(
            select(Customer.phone).where(Customer.id == customer_id),
        )
        order_phone = await session.scalar(select(Order.customer_phone).where(Order.id == order_id))

    assert counts == {"customer_events": 0, "consent_records": 0, "customer_traits": 0}
    assert erased_phone == erasure_tombstone(customer_id)
    assert order_phone == erasure_tombstone(customer_id)
