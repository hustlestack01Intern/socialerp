"""Phase 8b: segments API — owner-only gate, pagination, tenant scope, no PII.

Covers plan acceptance criteria:
- "API authZ: owner-only for insights/segment membership; non-owner denied."
- Tenant scoping on member listing.
- "No new PII stored by segments/insights" — member payloads carry ids only.

NOTE: like every other route module, the Phase 8b routers must be mounted in
``apps/api/main.py`` (the wiring lines are reported alongside this wave) before
these tests can pass; they exercise the real app, matching tests/crm +
tests/alerts house style. Until wired, every call below fails loudly with 404 —
by design, an unwired router is a delivery bug, not a test bug.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.main import app
from packages.core.auth.dependencies import (
    get_current_tenant_id,
    get_current_user_id,
    get_db,
    require_owner,
)
from packages.core.crm.segments import SEGMENT_RULES, SegmentKey
from packages.core.models.core import Tenant, UserRole
from tests.segments.helpers import add_trait, make_customer, make_tenant, make_user


def _deny_owner() -> Any:
    raise HTTPException(status_code=403, detail="owner only")


def _install_overrides(tenant: Any, user_id: UUID, session_factory: Any) -> None:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant.id
    app.dependency_overrides[get_current_user_id] = lambda: user_id


async def _seed_owner(session_factory: async_sessionmaker[AsyncSession], tenant: Tenant) -> UUID:
    async with session_factory() as session, session.begin():
        user = await make_user(session, tenant.id, role=UserRole.OWNER)
        return user.id


@pytest.fixture
async def owner_client(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncGenerator[AsyncClient, None]:
    user_id = await _seed_owner(session_factory, tenant)
    _install_overrides(tenant, user_id, session_factory)
    app.dependency_overrides[require_owner] = lambda: None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
async def staff_client(
    tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncGenerator[AsyncClient, None]:
    user_id = await _seed_owner(session_factory, tenant)
    _install_overrides(tenant, user_id, session_factory)
    app.dependency_overrides[require_owner] = _deny_owner

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# =============================================================================
# AuthZ: owner-only (plan AC)
# =============================================================================


async def test_list_segments_allowed_for_owner(owner_client: AsyncClient) -> None:
    resp = await owner_client.get("/api/v1/segments")
    assert resp.status_code == 200, resp.text
    keys = {row["key"] for row in resp.json()}
    assert keys == {key.value for key in SegmentKey}
    for row in resp.json():
        assert row["predicate"], "every segment exposes its (static) predicate"


async def test_segments_denied_for_non_owner(staff_client: AsyncClient) -> None:
    listing = await staff_client.get("/api/v1/segments")
    assert listing.status_code == 403, listing.text
    members = await staff_client.get("/api/v1/segments/first_time/members")
    assert members.status_code == 403, members.text


# =============================================================================
# Members: pagination, tenant scope, validation, PII
# =============================================================================


async def test_members_endpoint_pagination_and_content(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        ids = []
        for i in range(3):
            customer = await make_customer(session, tenant.id)
            await add_trait(session, tenant.id, customer.id, "order_count", 1 + (i % 2))
            ids.append(customer.id)
        expected_first_time = [c for i, c in enumerate(ids) if i % 2 == 0]

    resp = await owner_client.get(
        "/api/v1/segments/first_time/members", params={"limit": 10, "offset": 0}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["segment"] == "first_time"
    assert body["total"] == len(expected_first_time)
    assert set(body["customer_ids"]) == {str(cid) for cid in expected_first_time}

    page = await owner_client.get(
        "/api/v1/segments/first_time/members", params={"limit": 1, "offset": 1}
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == len(expected_first_time)
    assert len(page.json()["customer_ids"]) == 1


async def test_members_endpoint_404_for_unknown_segment(owner_client: AsyncClient) -> None:
    resp = await owner_client.get("/api/v1/segments/not_a_segment/members")
    assert resp.status_code == 404, resp.text


async def test_members_endpoint_422_for_invalid_pagination(owner_client: AsyncClient) -> None:
    resp = await owner_client.get("/api/v1/segments/first_time/members", params={"limit": 0})
    assert resp.status_code == 422, resp.text


async def test_members_endpoint_is_tenant_scoped(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Tenant B's customers never appear in tenant A's member listing."""
    async with session_factory() as session, session.begin():
        other = await make_tenant(session, slug_prefix="seg-other")
        mine = await make_customer(session, tenant.id)
        theirs = await make_customer(session, other.id)
        await add_trait(session, tenant.id, mine.id, "order_count", 1)
        await add_trait(session, other.id, theirs.id, "order_count", 1)

    resp = await owner_client.get("/api/v1/segments/first_time/members")
    assert resp.status_code == 200, resp.text
    ids = resp.json()["customer_ids"]
    assert ids == [str(mine.id)]
    assert str(theirs.id) not in ids


async def test_members_payload_carries_no_pii(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Plan invariant 4: no new PII — ids only, no phone/name in the payload."""
    async with session_factory() as session, session.begin():
        customer = await make_customer(
            session, tenant.id, phone="254711122233", name="Jane Confidential"
        )
        await add_trait(session, tenant.id, customer.id, "order_count", 1)

    listing = await owner_client.get("/api/v1/segments")
    members = await owner_client.get("/api/v1/segments/first_time/members")
    assert listing.status_code == 200 and members.status_code == 200
    blob = listing.text + members.text
    assert "254711122233" not in blob
    assert "Jane Confidential" not in blob
    # A reformatted phone must not leak either.
    assert "254 711 122 233" not in blob


async def test_registry_predicates_are_exposed_readably(owner_client: AsyncClient) -> None:
    """The static rule set is complete in the API (explainability surface)."""
    resp = await owner_client.get("/api/v1/segments")
    assert resp.status_code == 200, resp.text
    by_key = {row["key"]: row for row in resp.json()}
    assert len(by_key) == len(SEGMENT_RULES)
    assert by_key["high_value"]["predicate"]["field"] == "lifetime_value_cents"
