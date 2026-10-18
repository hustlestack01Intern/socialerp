"""Phase 8a: CRM memory API tests (routes over the real app + Postgres).

Tenant id, acting user and DB session are injected via dependency overrides;
CRM data is seeded directly. The role-gated dependencies (``require_staff`` /
``require_owner``) are overridden per client so owner-only data-subject
endpoints are exercised as both staff (403) and owner (allowed).

Covers: happy paths for reads/writes/derive/consent/opt-out/export/erase,
cross-tenant 404s, unknown customer 404s, malformed UUID 422s, request
validation 422s, and the owner-only gate on the DSR endpoints.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.main import app
from packages.core.auth.dependencies import (
    get_current_tenant_id,
    get_current_user_id,
    get_db,
    require_owner,
    require_staff,
)
from packages.core.auth.jwt import create_access_token
from packages.core.crm import CRMService
from packages.core.domain import TenantId
from packages.core.models.core import Tenant, UserRole
from tests.crm.helpers import (
    make_customer,
    make_tenant,
    make_user,
    seed_paid_history,
)


async def _seed_actor(session_factory: Any, tenant: Any) -> UUID:
    async with session_factory() as session, session.begin():
        user = await make_user(session, tenant.id, role=UserRole.OWNER)
        return user.id


def _install_overrides(tenant: Any, user_id: UUID, session_factory: Any) -> None:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant.id
    app.dependency_overrides[get_current_user_id] = lambda: user_id


def _deny_owner() -> Any:
    raise HTTPException(status_code=403, detail="owner only")


@pytest.fixture
async def owner_client(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncGenerator[AsyncClient, None]:
    user_id = await _seed_actor(session_factory, tenant)
    _install_overrides(tenant, user_id, session_factory)
    app.dependency_overrides[require_staff] = lambda: None
    app.dependency_overrides[require_owner] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
async def staff_client(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncGenerator[AsyncClient, None]:
    user_id = await _seed_actor(session_factory, tenant)
    _install_overrides(tenant, user_id, session_factory)
    app.dependency_overrides[require_staff] = lambda: None
    app.dependency_overrides[require_owner] = _deny_owner

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


async def test_derive_traits_via_api_then_read_models(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        customer, _orders, _product = await seed_paid_history(session, tenant.id)
        customer_id = customer.id
        await CRMService(session).record_event(
            TenantId(tenant.id), customer_id, "message.summarised", dedupe_key="api-evt-1"
        )
        await CRMService(session).record_consent(
            TenantId(tenant.id),
            customer_id,
            purpose="marketing",
            granted=True,
            source="whatsapp_opt_in",
        )

    derive = await owner_client.post(f"/api/v1/crm/customers/{customer_id}/derive")
    assert derive.status_code == 200, derive.text
    first_traits = derive.json()["traits"]
    assert first_traits, "derive must return the recomputed traits"
    assert derive.json()["customer_id"] == str(customer_id)
    for trait in first_traits:
        assert trait["version"] == 1
        assert trait["evidence"], "every derived trait must carry evidence"
        assert trait["computed_at"]

    # Re-derivation appends version 2.
    rederive = await owner_client.post(f"/api/v1/crm/customers/{customer_id}/derive")
    assert rederive.status_code == 200
    assert all(t["version"] == 2 for t in rederive.json()["traits"])

    # All versions, with evidence, from the traits endpoint.
    listed = await owner_client.get(f"/api/v1/crm/customers/{customer_id}/traits")
    assert listed.status_code == 200
    traits = listed.json()["traits"]
    assert len(traits) == 2 * len(first_traits)
    versions_by_name: dict[str, list[int]] = {}
    for trait in traits:
        assert trait["evidence"]
        versions_by_name.setdefault(trait["name"], []).append(trait["version"])
    for versions in versions_by_name.values():
        assert sorted(versions) == [1, 2]

    # Memory view: latest trait per name + consents + recent events.
    memory = await owner_client.get(f"/api/v1/crm/customers/{customer_id}/memory")
    assert memory.status_code == 200
    body = memory.json()
    assert body["customer_id"] == str(customer_id)
    assert all(t["version"] == 2 for t in body["traits"])
    names = [t["name"] for t in body["traits"]]
    assert len(names) == len(set(names)), "memory must show one version per trait name"
    assert len(body["consents"]) == 1
    assert body["consents"][0]["source"] == "whatsapp_opt_in"
    assert [e["event_type"] for e in body["recent_events"]] == ["message.summarised"]


async def test_events_endpoint_lists_log_oldest_first(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        service = CRMService(session)
        tid = TenantId(tenant.id)
        for key in ("e1", "e2", "e3"):
            await service.record_event(tid, customer.id, "message.summarised", dedupe_key=key)
        customer_id = customer.id

    listing = await owner_client.get(f"/api/v1/crm/customers/{customer_id}/events")
    assert listing.status_code == 200
    body = listing.json()
    assert body["customer_id"] == str(customer_id)
    assert len(body["events"]) == 3
    for event in body["events"]:
        assert set(event) == {"id", "event_type", "channel", "summary", "occurred_at"}

    limited = await owner_client.get(
        f"/api/v1/crm/customers/{customer_id}/events", params={"limit": 2}
    )
    assert limited.status_code == 200
    assert len(limited.json()["events"]) == 2


async def test_consent_and_opt_out_endpoints(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        customer_id = customer.id

    granted = await owner_client.post(
        f"/api/v1/crm/customers/{customer_id}/consent",
        json={
            "purpose": "marketing",
            "granted": True,
            "source": "whatsapp_opt_in",
            "evidence": "replied YES",
        },
    )
    assert granted.status_code == 200, granted.text
    assert granted.json() == {
        "purpose": "marketing",
        "granted": True,
        "source": "whatsapp_opt_in",
        "recorded_at": granted.json()["recorded_at"],
    }
    assert granted.json()["recorded_at"]

    opted_out = await owner_client.post(f"/api/v1/crm/customers/{customer_id}/opt-out")
    assert opted_out.status_code == 200, opted_out.text
    assert opted_out.json()["purpose"] == "marketing"
    assert opted_out.json()["granted"] is False
    assert opted_out.json()["source"] == "opt_out"

    memory = await owner_client.get(f"/api/v1/crm/customers/{customer_id}/memory")
    consents = memory.json()["consents"]
    assert [c["granted"] for c in consents] == [True, False]


async def test_consent_request_validation_returns_422(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        customer_id = customer.id
    url = f"/api/v1/crm/customers/{customer_id}/consent"

    too_long_purpose = await owner_client.post(
        url, json={"purpose": "p" * 31, "granted": True, "source": "opt_in"}
    )
    assert too_long_purpose.status_code == 422

    empty_source = await owner_client.post(
        url, json={"purpose": "marketing", "granted": True, "source": ""}
    )
    assert empty_source.status_code == 422

    missing_granted = await owner_client.post(
        url, json={"purpose": "marketing", "source": "opt_in"}
    )
    assert missing_granted.status_code == 422


async def test_query_parameter_bounds_return_422(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        customer_id = customer.id

    for params in ({"limit": 0}, {"limit": 501}):
        resp = await owner_client.get(f"/api/v1/crm/customers/{customer_id}/events", params=params)
        assert resp.status_code == 422

    for params in ({"recent_limit": 0}, {"recent_limit": 201}):
        resp = await owner_client.get(f"/api/v1/crm/customers/{customer_id}/memory", params=params)
        assert resp.status_code == 422


async def test_staff_cannot_use_data_subject_endpoints(
    staff_client: AsyncClient,
    tenant: Tenant,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Export and erasure are owner-only (human approval zone)."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        customer_id = customer.id

    export = await staff_client.get(f"/api/v1/crm/customers/{customer_id}/export")
    assert export.status_code == 403

    deleted = await staff_client.delete(f"/api/v1/crm/customers/{customer_id}")
    assert deleted.status_code == 403

    # The staff denial must not have erased anything.
    async with session_factory() as session:
        still_active = await session.execute(
            text("SELECT is_active FROM customers WHERE id = :c"), {"c": str(customer_id)}
        )
    assert bool(still_active.scalar()) is True


async def test_owner_can_export_data_subject_record(
    owner_client: AsyncClient,
    tenant: Tenant,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        customer_id = customer.id

    export = await owner_client.get(f"/api/v1/crm/customers/{customer_id}/export")
    assert export.status_code == 200, export.text
    assert export.json()["customer"]["id"] == str(customer_id)


async def test_owner_erase_customer_via_api(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """DELETE /customers/{id} erases the CRM memory and reports success."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        customer_id = customer.id
        service = CRMService(session)
        tid = TenantId(tenant.id)
        await service.record_event(tid, customer_id, "message.summarised", dedupe_key="del-1")
        await service.record_consent(
            tid, customer_id, purpose="marketing", granted=True, source="opt_in"
        )

    deleted = await owner_client.delete(f"/api/v1/crm/customers/{customer_id}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"customer_id": str(customer_id), "erased": True}

    async with session_factory() as session:
        events = await session.execute(
            text("SELECT count(*) FROM customer_events WHERE customer_id = :c"),
            {"c": str(customer_id)},
        )
        consents = await session.execute(
            text("SELECT count(*) FROM consent_records WHERE customer_id = :c"),
            {"c": str(customer_id)},
        )
        active = await session.execute(
            text("SELECT is_active FROM customers WHERE id = :c"), {"c": str(customer_id)}
        )
        assert int(events.scalar() or 0) == 0
        assert int(consents.scalar() or 0) == 0
        assert bool(active.scalar()) is False


async def test_cross_tenant_access_returns_404(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A customer belonging to tenant B is invisible to tenant A on every route."""
    async with session_factory() as session, session.begin():
        tenant_b = await make_tenant(session, slug_prefix="api-other")
        customer_b = await make_customer(session, tenant_b.id, phone="254733333333")
        foreign_id = customer_b.id

    base = f"/api/v1/crm/customers/{foreign_id}"
    for method, url in (
        ("GET", f"{base}/memory"),
        ("GET", f"{base}/events"),
        ("GET", f"{base}/traits"),
        ("GET", f"{base}/export"),
        ("POST", f"{base}/consent"),
        ("POST", f"{base}/opt-out"),
        ("POST", f"{base}/derive"),
        ("DELETE", base),
    ):
        if method == "GET":
            resp = await owner_client.get(url)
        elif method == "POST":
            resp = await owner_client.post(
                url, json={"purpose": "marketing", "granted": True, "source": "x"}
            )
        else:
            resp = await owner_client.delete(url)
        assert resp.status_code == 404, (
            f"{method} {url} must 404 cross-tenant, got {resp.status_code}"
        )

    # Tenant B's customer is intact.
    async with session_factory() as session:
        phone = await session.execute(
            text("SELECT phone FROM customers WHERE id = :c"), {"c": str(foreign_id)}
        )
        assert phone.scalar() == "254733333333"


async def test_unknown_customer_returns_404(owner_client: AsyncClient, tenant: Tenant) -> None:
    unknown = uuid4()
    base = f"/api/v1/crm/customers/{unknown}"
    assert (await owner_client.get(f"{base}/memory")).status_code == 404
    assert (await owner_client.get(f"{base}/events")).status_code == 404
    assert (await owner_client.get(f"{base}/traits")).status_code == 404
    assert (await owner_client.get(f"{base}/export")).status_code == 404
    assert (await owner_client.delete(base)).status_code == 404
    derive = await owner_client.post(f"{base}/derive")
    assert derive.status_code == 404


async def test_malformed_uuid_returns_422(owner_client: AsyncClient, tenant: Tenant) -> None:
    base = "/api/v1/crm/customers/not-a-uuid"
    assert (await owner_client.get(f"{base}/memory")).status_code == 422
    assert (await owner_client.get(f"{base}/export")).status_code == 422
    assert (await owner_client.delete(base)).status_code == 422


async def test_dsr_role_gates_enforced_with_real_jwt_tokens(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """U37: owner-only DSR gates proven with REAL tokens (no stubbed role deps).

    Only ``get_db`` is redirected to the test database; the whole auth chain —
    bearer decode, DB user lookup, ``require_owner``/``require_staff``, tenant
    id from the token — runs unstubbed, so a wiring regression that dropped a
    role dependency would surface here as a 200 for staff instead of a 403.
    """
    async with session_factory() as session, session.begin():
        customer_id = (await make_customer(session, tenant.id)).id
        owner_id = (await make_user(session, tenant.id, role=UserRole.OWNER)).id
        staff_id = (await make_user(session, tenant.id, role=UserRole.STAFF)).id

    owner_token = create_access_token(str(owner_id), str(tenant.id), [UserRole.OWNER.value])
    staff_token = create_access_token(str(staff_id), str(tenant.id), [UserRole.STAFF.value])

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    base = f"/api/v1/crm/customers/{customer_id}"
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as staff_client:
            staff_client.headers["Authorization"] = f"Bearer {staff_token}"
            export = await staff_client.get(f"{base}/export")
            assert export.status_code == 403, (
                f"staff export must be 403 (owner-only), got {export.status_code}"
            )
            deleted = await staff_client.delete(base)
            assert deleted.status_code == 403, (
                f"staff erase must be 403 (owner-only), got {deleted.status_code}"
            )

        # The staff denial must not have erased anything.
        async with session_factory() as session:
            active = await session.execute(
                text("SELECT is_active FROM customers WHERE id = :c"), {"c": str(customer_id)}
            )
        assert bool(active.scalar()) is True

        async with AsyncClient(transport=transport, base_url="http://test") as owner_client:
            owner_client.headers["Authorization"] = f"Bearer {owner_token}"
            export = await owner_client.get(f"{base}/export")
            assert export.status_code == 200, (
                f"owner export must pass the role gate, got {export.status_code}"
            )
            deleted = await owner_client.delete(base)
            assert deleted.status_code == 200, (
                f"owner erase must pass the role gate, got {deleted.status_code}"
            )
    finally:
        app.dependency_overrides.clear()
