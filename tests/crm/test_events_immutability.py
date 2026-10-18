"""Phase 8a: append-only event log (plan invariant 3).

- ``record_event`` only appends: summarised (300-char cap), PII-minimal events,
  idempotent per ``dedupe_key`` (pre-check + unique-constraint backstop + race
  safe), and audited.
- The DB trigger (mirrored in the test schema from migration 0012) rejects any
  UPDATE or DELETE on ``customer_events`` — even for raw SQL — except the
  narrowly-scoped erasure path that sets ``app.allow_event_erasure``.
- The service/repository expose no update/delete path for events.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import CursorResult, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.crm import CRMService
from packages.core.crm.repository import CRMRepository
from packages.core.domain import TenantId
from packages.core.models.core import AuditLog, Tenant
from packages.core.models.crm import CustomerEvent
from tests.crm.helpers import make_customer

APPEND_ONLY_ERROR = "customer_events is append-only"


async def _record(
    service: CRMService,
    tenant_id: UUID,
    customer_id: UUID,
    *,
    dedupe_key: str | None = None,
    summary: str = "synthetic summary",
) -> CustomerEvent:
    return await service.record_event(
        TenantId(tenant_id),
        customer_id,
        "message.summarised",
        channel="whatsapp",
        summary=summary,
        data={"intent": "order_capture"},
        dedupe_key=dedupe_key,
    )


async def test_record_event_appends_summarised_event(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        before = datetime.now(UTC)
        event = await _record(service, tenant.id, customer.id)

        assert event.id is not None
        assert event.tenant_id == tenant.id
        assert event.customer_id == customer.id
        assert event.event_type == "message.summarised"
        assert event.channel == "whatsapp"
        assert event.summary == "synthetic summary"
        assert event.data_json == {"intent": "order_capture"}
        assert event.occurred_at is not None
        assert before - datetime.resolution <= event.occurred_at <= datetime.now(UTC)


async def test_record_event_truncates_summary_to_300_chars(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Plan invariant 3: events summarise; raw bodies are never stored wholesale."""
    long_summary = "x" * 400
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        event = await _record(service, tenant.id, customer.id, summary=long_summary)

    assert len(event.summary) == 300
    assert event.summary == "x" * 300


async def test_record_event_is_idempotent_per_dedupe_key(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        first = await _record(service, tenant.id, customer.id, dedupe_key="evt-1")
        second = await _record(service, tenant.id, customer.id, dedupe_key="evt-1")
        no_key_a = await _record(service, tenant.id, customer.id)
        no_key_b = await _record(service, tenant.id, customer.id)

        stored = (
            (
                await session.execute(
                    select(CustomerEvent).where(CustomerEvent.customer_id == customer.id),
                )
            )
            .scalars()
            .all()
        )

    assert first.id == second.id
    assert no_key_a.id != no_key_b.id, "without a dedupe key every call appends"
    assert len(stored) == 3


async def test_dedupe_unique_constraint_is_the_race_backstop(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Bypassing the service pre-check hits the (tenant_id, dedupe_key) constraint."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        await CRMService(session).record_event(
            TenantId(tenant.id), customer.id, "k", dedupe_key="dupe",
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO customer_events "
                        "(tenant_id, customer_id, event_type, dedupe_key) "
                        "VALUES (:t, :c, 'k', 'dupe')",
                    ),
                    {"t": str(tenant.id), "c": str(customer.id)},
                )


async def test_record_event_is_race_safe(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two concurrent appends with the same dedupe key yield exactly one event."""
    async with session_factory() as setup, setup.begin():
        customer = await make_customer(setup, tenant.id)
        customer_id = customer.id

    async def _append() -> None:
        async with session_factory() as session, session.begin():
            await CRMService(session).record_event(
                TenantId(tenant.id), customer_id, "k", dedupe_key="race-key",
            )

    await asyncio.gather(_append(), _append())

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(CustomerEvent).where(CustomerEvent.dedupe_key == "race-key"),
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


async def test_record_event_is_audited(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        event = await _record(CRMService(session), tenant.id, customer.id)
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id, AuditLog.action == "crm.event_recorded",
                ),
            )
        ).scalar_one()

    assert audit.resource == "customer_event"
    assert audit.resource_id == event.id
    assert audit.after_json == {"event_type": "message.summarised", "channel": "whatsapp"}


async def test_raw_sql_update_and_delete_are_rejected_by_trigger(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Plan invariant 3: the log cannot be rewritten or erased via raw SQL."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        event = await _record(CRMService(session), tenant.id, customer.id)
        event_id = event.id

        with pytest.raises(DBAPIError) as update_info:
            async with session.begin_nested():
                await session.execute(
                    text("UPDATE customer_events SET summary = 'hacked' WHERE id = :id"),
                    {"id": str(event_id)},
                )
        assert APPEND_ONLY_ERROR in str(update_info.value)

        with pytest.raises(DBAPIError) as delete_info:
            async with session.begin_nested():
                await session.execute(
                    text("DELETE FROM customer_events WHERE id = :id"),
                    {"id": str(event_id)},
                )
        assert APPEND_ONLY_ERROR in str(delete_info.value)

        surviving = (
            await session.execute(select(CustomerEvent).where(CustomerEvent.id == event_id))
        ).scalar_one()
        assert surviving.summary == "synthetic summary", "the failed UPDATE must not apply"


async def test_delete_allowed_only_with_erasure_session_flag(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The erasure backstop: DELETE succeeds only when the session flag is set."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        event = await _record(CRMService(session), tenant.id, customer.id)

        await session.execute(text("SELECT set_config('app.allow_event_erasure', 'on', true)"))
        result = await session.execute(
            text("DELETE FROM customer_events WHERE id = :id"), {"id": str(event.id)},
        )
        assert isinstance(result, CursorResult)
        assert result.rowcount == 1

        remaining = (
            (
                await session.execute(
                    select(CustomerEvent).where(CustomerEvent.customer_id == customer.id),
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []


async def test_service_and_repository_expose_no_event_mutation_path() -> None:
    """Plan invariant 3: no update/delete path for events in the service layer."""
    forbidden = ("update_event", "delete_event", "remove_event", "patch_event", "update_events")
    for name in forbidden:
        assert not hasattr(CRMService, name), f"CRMService must not expose {name}"
        assert not hasattr(CRMRepository, name), f"CRMRepository must not expose {name}"


async def test_record_event_requires_existing_customer(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        service = CRMService(session)
        with pytest.raises(IntegrityError):
            await service.record_event(TenantId(tenant.id), uuid4(), "k")


async def test_event_dedupe_is_tenant_scoped(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The same dedupe key may be reused by different tenants independently."""
    from tests.crm.helpers import make_tenant

    async with session_factory() as session, session.begin():
        tenant_b = await make_tenant(session, slug_prefix="evt-other")
        customer_a = await make_customer(session, tenant.id)
        customer_b = await make_customer(session, tenant_b.id, phone="254799999999")
        service = CRMService(session)
        event_a = await service.record_event(
            TenantId(tenant.id), customer_a.id, "k", dedupe_key="shared-key",
        )
        event_b = await service.record_event(
            TenantId(tenant_b.id), customer_b.id, "k", dedupe_key="shared-key",
        )

    assert event_a.id != event_b.id
