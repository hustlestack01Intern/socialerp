"""Phase 8a: consent gating — explicit, sourced, latest-wins (plan invariant 4).

- Absence of any record means DENIED (fail closed).
- ``record_consent`` stores the decision with its source and timestamp and is
  audited.
- ``opt_out`` records a marketing denial and is honoured by ``has_consent``.
- The latest record per (customer, purpose) wins; purposes are independent and
  consent is tenant-scoped.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.crm import PURPOSE_MARKETING, CRMService
from packages.core.domain import TenantId
from packages.core.models.core import AuditLog, Tenant
from tests.crm.helpers import make_customer


async def _has_marketing_consent(service: CRMService, tenant_id: UUID, customer_id: UUID) -> bool:
    return await service.has_consent(TenantId(tenant_id), customer_id, PURPOSE_MARKETING)


async def test_consent_defaults_to_denied(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        assert await _has_marketing_consent(service, tenant.id, customer.id) is False
        assert await service.has_consent(TenantId(tenant.id), customer.id, "shipping") is False


async def test_granted_consent_is_honoured_with_source_and_time(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        record = await service.record_consent(
            TenantId(tenant.id),
            customer.id,
            purpose=PURPOSE_MARKETING,
            granted=True,
            source="whatsapp_opt_in",
            evidence="replied YES to opt-in prompt",
        )
        granted = await _has_marketing_consent(service, tenant.id, customer.id)

    assert granted is True
    assert record.granted is True
    assert record.source == "whatsapp_opt_in"
    assert record.evidence == "replied YES to opt-in prompt"
    assert record.recorded_at is not None, "consent must record when it was given"


async def test_record_consent_is_audited(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        record = await service.record_consent(
            TenantId(tenant.id),
            customer.id,
            purpose=PURPOSE_MARKETING,
            granted=True,
            source="whatsapp_opt_in",
        )
        record_id = record.id
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant.id, AuditLog.action == "crm.consent_recorded",
                ),
            )
        ).scalar_one()

    assert audit.resource == "consent_record"
    assert audit.resource_id == record_id
    assert audit.after_json == {
        "purpose": PURPOSE_MARKETING,
        "granted": True,
        "source": "whatsapp_opt_in",
    }


async def test_opt_out_is_honoured(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        tid = TenantId(tenant.id)
        await service.record_consent(
            tid, customer.id, purpose=PURPOSE_MARKETING, granted=True, source="whatsapp_opt_in",
        )
        opt_out = await service.opt_out(tid, customer.id)
        still_granted = await _has_marketing_consent(service, tenant.id, customer.id)

    assert still_granted is False, "a marketing opt-out must override an earlier grant"
    assert opt_out.granted is False
    assert opt_out.purpose == PURPOSE_MARKETING
    assert opt_out.source == "opt_out"
    assert opt_out.recorded_at is not None


async def test_latest_decision_wins_in_both_directions(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        tid = TenantId(tenant.id)

        await service.opt_out(tid, customer.id)
        assert await _has_marketing_consent(service, tenant.id, customer.id) is False

        await service.record_consent(
            tid, customer.id, purpose=PURPOSE_MARKETING, granted=True, source="whatsapp_opt_in",
        )
        assert await _has_marketing_consent(service, tenant.id, customer.id) is True

        await service.opt_out(tid, customer.id)
        assert await _has_marketing_consent(service, tenant.id, customer.id) is False


async def test_purposes_are_independent(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Granting 'shipping' consent must not leak into the marketing purpose."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        await service.record_consent(
            TenantId(tenant.id),
            customer.id,
            purpose="shipping",
            granted=True,
            source="checkout_toggle",
        )
        marketing = await _has_marketing_consent(service, tenant.id, customer.id)
        shipping = await service.has_consent(TenantId(tenant.id), customer.id, "shipping")

    assert marketing is False
    assert shipping is True


async def test_consent_is_tenant_scoped(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A consent recorded for tenant A must not satisfy a check from tenant B."""
    from tests.crm.helpers import make_tenant

    async with session_factory() as session, session.begin():
        tenant_b = await make_tenant(session, slug_prefix="other")
        customer_a = await make_customer(session, tenant.id)
        service = CRMService(session)
        await service.record_consent(
            TenantId(tenant.id),
            customer_a.id,
            purpose=PURPOSE_MARKETING,
            granted=True,
            source="whatsapp_opt_in",
        )
        from_a = await _has_marketing_consent(service, tenant.id, customer_a.id)
        from_b = await _has_marketing_consent(service, tenant_b.id, customer_a.id)

    assert from_a is True
    assert from_b is False, "consent must never cross the tenant boundary"


async def test_has_consent_follows_only_latest_recorded_at(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two grants then an opt-out: only the newest record decides."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        tid = TenantId(tenant.id)
        await service.record_consent(
            tid, customer.id, purpose=PURPOSE_MARKETING, granted=True, source="whatsapp_opt_in",
        )
        await service.record_consent(
            tid, customer.id, purpose=PURPOSE_MARKETING, granted=True, source="owner_manual",
        )
        await service.record_consent(
            tid, customer.id, purpose=PURPOSE_MARKETING, granted=False, source="opt_out",
        )
        result = await _has_marketing_consent(service, tenant.id, customer.id)

    assert result is False
