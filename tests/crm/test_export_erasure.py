"""Phase 8a: data-subject rights — export completeness and erasure cascade
(plan invariants 4, 5; Slice 8a acceptance "export returns the full subject
record; erasure removes it").

``export_customer`` must return events + traits + consent + orders summary for
the data subject. ``delete_customer`` is human-approved (``approved_by`` is a
required keyword-only parameter), tenant-scoped, audited, and must remove the
customer's CRM memory while retaining de-identified order records.
"""

from __future__ import annotations

import inspect
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.crm import CRMService, CustomerNotFoundError, erasure_tombstone
from packages.core.domain import TenantId
from packages.core.models.core import Tenant, UserRole
from tests.crm.helpers import make_customer, make_order, make_tenant, make_user


async def _seed_full_record(session: AsyncSession, tenant_id: UUID) -> UUID:
    """Customer with events, derived traits, consents and two paid orders."""
    service = CRMService(session)
    tid = TenantId(tenant_id)
    customer = await make_customer(session, tenant_id)
    await service.record_event(
        tid, customer.id, "message.summarised", channel="whatsapp", dedupe_key="evt-1"
    )
    await service.record_event(
        tid, customer.id, "order.created", channel="whatsapp", dedupe_key="evt-2"
    )
    await service.record_consent(
        tid, customer.id, purpose="marketing", granted=True, source="whatsapp_opt_in"
    )
    await service.record_consent(
        tid, customer.id, purpose="marketing", granted=False, source="opt_out"
    )
    await service.derive_traits(tid, customer.id)
    await make_order(
        session,
        tenant_id,
        customer.id,
        total_cents=5000,
        payment_status="completed",
        status="paid",
    )
    await make_order(
        session,
        tenant_id,
        customer.id,
        total_cents=7000,
        payment_status="completed",
        status="paid",
    )
    return customer.id


async def test_export_returns_full_subject_record(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        customer_id = await _seed_full_record(session, tenant.id)
        payload = await CRMService(session).export_customer(TenantId(tenant.id), customer_id)

    assert set(payload) == {"customer", "events", "traits", "consents", "orders"}

    assert payload["customer"]["id"] == str(customer_id)
    assert payload["customer"]["phone"] == "254712345678"
    assert payload["customer"]["name"] == "Test Customer"
    assert payload["customer"]["email"] == "customer@example.com"
    assert payload["customer"]["default_area"] == "Kilimani"
    assert payload["customer"]["is_active"] is True
    assert payload["customer"]["created_at"]

    assert len(payload["events"]) == 2
    event_types = [e["event_type"] for e in payload["events"]]
    assert event_types == ["message.summarised", "order.created"]
    for event in payload["events"]:
        assert set(event) == {"id", "event_type", "channel", "summary", "occurred_at"}
        assert event["occurred_at"]

    trait_names = {t["name"] for t in payload["traits"]}
    assert "order_count" in trait_names
    assert "lifetime_value_cents" in trait_names
    for trait in payload["traits"]:
        assert set(trait) == {"name", "value", "confidence", "version", "evidence"}
        assert trait["version"] == 1
        assert trait["evidence"], "exported traits must carry their evidence"

    assert len(payload["consents"]) == 2
    assert [c["granted"] for c in payload["consents"]] == [True, False]
    assert {c["source"] for c in payload["consents"]} == {"whatsapp_opt_in", "opt_out"}
    for consent in payload["consents"]:
        assert consent["purpose"] == "marketing"
        assert consent["recorded_at"]

    assert len(payload["orders"]) == 2
    assert {o["total_cents"] for o in payload["orders"]} == {5000, 7000}
    for order in payload["orders"]:
        assert set(order) == {"id", "status", "total_cents", "created_at"}
        assert order["status"] == "paid"


async def test_export_unknown_customer_raises_not_found(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        with pytest.raises(CustomerNotFoundError):
            await CRMService(session).export_customer(TenantId(tenant.id), uuid4())


async def test_export_is_tenant_scoped(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Tenant A cannot export a data subject belonging to tenant B."""
    async with session_factory() as session, session.begin():
        tenant_b = await make_tenant(session, slug_prefix="export-other")
        customer_b = await make_customer(session, tenant_b.id, phone="254755555555")
        with pytest.raises(CustomerNotFoundError):
            await CRMService(session).export_customer(TenantId(tenant.id), customer_b.id)


def test_delete_customer_requires_human_approval_parameter() -> None:
    """Plan invariant 5: erasure is human-approved — approver is mandatory."""
    sig = inspect.signature(CRMService.delete_customer)
    param = sig.parameters["approved_by"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


async def test_delete_unknown_customer_raises_not_found(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        with pytest.raises(CustomerNotFoundError):
            await CRMService(session).delete_customer(
                TenantId(tenant.id), uuid4(), approved_by=approver.id
            )


async def test_delete_customer_is_tenant_scoped(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Erasing with the wrong tenant id fails closed and touches nothing."""
    async with session_factory() as session, session.begin():
        tenant_b = await make_tenant(session, slug_prefix="erase-other")
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        customer_b = await make_customer(session, tenant_b.id, phone="254744444444")
        service = CRMService(session)
        await service.record_event(
            TenantId(tenant_b.id), customer_b.id, "message.summarised", dedupe_key="b-evt"
        )

        with pytest.raises(CustomerNotFoundError):
            await service.delete_customer(
                TenantId(tenant.id), customer_b.id, approved_by=approver.id
            )

        # Tenant B's CRM data is untouched.
        still_there = await service.repo.list_events(TenantId(tenant_b.id), customer_b.id)
        assert len(still_there) == 1


async def test_erasure_cascade_removes_crm_data_scrubs_pii_and_audits(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The full erasure contract (plan invariant 5 + Slice 8a acceptance).

    Removes events/traits/consents for the customer only, anonymises the
    customer row, scrubs the personal identifiers on their orders while keeping
    the order records (financial record), and writes an audit entry naming the
    human approver. A bystander customer in the same tenant must be untouched.
    """
    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        approver_id = approver.id
        service = CRMService(session)
        tid = TenantId(tenant.id)

        customer = await make_customer(session, tenant.id)
        customer_id = customer.id
        bystander = await make_customer(session, tenant.id, phone="254788888888")
        bystander_id = bystander.id

        await service.record_event(tid, customer_id, "message.summarised", dedupe_key="e1")
        await service.record_event(tid, customer_id, "order.created", dedupe_key="e2")
        await service.record_consent(
            tid, customer_id, purpose="marketing", granted=True, source="whatsapp_opt_in"
        )
        await service.derive_traits(tid, customer_id)
        order = await make_order(
            session,
            tenant.id,
            customer_id,
            total_cents=1234,
            payment_status="completed",
            status="delivered",
        )
        order_id = order.id
        await service.record_event(tid, bystander_id, "message.summarised", dedupe_key="b1")

        await service.delete_customer(tid, customer_id, approved_by=approver_id)

        tombstone = erasure_tombstone(customer_id)
        assert len(tombstone) <= 20, "tombstone must fit customers.phone VARCHAR(20)"

        for table in ("customer_events", "customer_traits", "consent_records"):
            count = await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE customer_id = :c AND tenant_id = :t"),
                {"c": str(customer_id), "t": str(tenant.id)},
            )
            assert int(count.scalar() or 0) == 0, f"{table} rows must be erased"

        erased_customer = (
            await session.execute(
                text(
                    "SELECT phone, name, email, default_address, default_area, is_active "
                    "FROM customers WHERE id = :c"
                ),
                {"c": str(customer_id)},
            )
        ).one()
        assert erased_customer.phone == tombstone
        assert erased_customer.name is None
        assert erased_customer.email is None
        assert erased_customer.default_address is None
        assert erased_customer.default_area is None
        assert erased_customer.is_active is False

        # Order records are retained as the financial trace, without identifiers.
        kept_order = (
            await session.execute(
                text("SELECT customer_phone, customer_name FROM orders WHERE id = :o"),
                {"o": str(order_id)},
            )
        ).one()
        assert kept_order.customer_phone == tombstone
        assert kept_order.customer_name is None
        order_count = await session.execute(
            text("SELECT count(*) FROM orders WHERE customer_id = :c"),
            {"c": str(customer_id)},
        )
        assert int(order_count.scalar() or 0) == 1, "orders themselves must be retained"

        # The bystander customer in the same tenant is untouched.
        bystander_rows = (
            await session.execute(
                text("SELECT phone, name, is_active FROM customers WHERE id = :c"),
                {"c": str(bystander_id)},
            )
        ).one()
        assert bystander_rows.phone == "254788888888"
        assert bystander_rows.name == "Test Customer"
        assert bystander_rows.is_active is True
        bystander_events = (
            await session.execute(
                text("SELECT count(*) FROM customer_events WHERE customer_id = :c"),
                {"c": str(bystander_id)},
            )
        ).scalar()
        assert int(bystander_events or 0) == 1

        # Fully audited: who approved, what was erased.
        audit = (
            await session.execute(
                text(
                    "SELECT user_id, action, resource, resource_id FROM audit_log "
                    "WHERE action = 'crm.customer_erased'"
                )
            )
        ).one()
        assert audit.user_id == approver_id
        assert audit.resource == "customer"
        assert str(audit.resource_id) == str(customer_id)


async def test_erasure_clears_all_crm_rows_checked_via_orm(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Cross-check the cascade with ORM selects on a fresh session."""
    async with session_factory() as session, session.begin():
        approver = await make_user(session, tenant.id, role=UserRole.OWNER)
        service = CRMService(session)
        customer = await make_customer(session, tenant.id)
        customer_id = customer.id
        tid = TenantId(tenant.id)
        await service.record_event(tid, customer_id, "message.summarised", dedupe_key="x1")
        await service.record_consent(
            tid, customer_id, purpose="marketing", granted=True, source="opt_in"
        )
        await service.derive_traits(tid, customer_id)

    async with session_factory() as session, session.begin():
        await CRMService(session).delete_customer(
            TenantId(tenant.id), customer_id, approved_by=approver.id
        )

    async with session_factory() as session:
        events = (
            await session.execute(
                text("SELECT count(*) FROM customer_events WHERE customer_id = :c"),
                {"c": str(customer_id)},
            )
        ).scalar()
        traits = (
            await session.execute(
                text("SELECT count(*) FROM customer_traits WHERE customer_id = :c"),
                {"c": str(customer_id)},
            )
        ).scalar()
        consents = (
            await session.execute(
                text("SELECT count(*) FROM consent_records WHERE customer_id = :c"),
                {"c": str(customer_id)},
            )
        ).scalar()

    assert int(events or 0) == 0
    assert int(traits or 0) == 0
    assert int(consents or 0) == 0
