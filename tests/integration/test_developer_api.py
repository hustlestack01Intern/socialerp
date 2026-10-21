"""The developer API: what "API access" on a plan actually opens (M1.6, U80).

Before M1.6 the platform could mint an API key and no endpoint accepted one.
These tests pin the three things that make the feature real:

* a key opens the developer API;
* a key reads only its own tenant (the tenant comes from the key row, never
  from a parameter);
* a key without the read scope opens nothing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.main import app
from packages.core.auth.dependencies import get_db
from packages.core.auth.jwt import create_access_token
from packages.core.models.core import UserRole
from tests.crm.helpers import make_user

pytestmark = pytest.mark.integration


async def _owner_token(
    session_factory: async_sessionmaker[AsyncSession], tenant_id
) -> str:
    async with session_factory() as session, session.begin():
        user = await make_user(session, tenant_id, role=UserRole.OWNER)
        return create_access_token(str(user.id), str(tenant_id), [UserRole.OWNER.value])


def _client_for(
    session_factory: async_sessionmaker[AsyncSession], token: str
) -> AsyncClient:
    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        # The real dependency commits on success; the override must do the
        # same or the API key is rolled back before it can be used.
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            await session.commit()

    app.dependency_overrides[get_db] = override_get_db
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    client.headers["Authorization"] = f"Bearer {token}"
    return client


async def _set_plan(
    session_factory: async_sessionmaker[AsyncSession], tenant_id, plan: str
) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE subscriptions SET plan = :plan, status = 'active', "
                "period_start = now(), period_end = now() + interval '30 days' "
                "WHERE tenant_id = :tid"
            ),
            {"plan": plan, "tid": str(tenant_id)},
        )


async def _make_key(
    session_factory: async_sessionmaker[AsyncSession], tenant_id, token: str
) -> str:
    async with _client_for(session_factory, token) as client:
        resp = await client.post(
            f"/api/v1/auth/tenants/{tenant_id}/api-keys", json={"name": "erp"}
        )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["key"])


async def test_a_key_opens_the_developer_api(tenant, session_factory) -> None:
    await _set_plan(session_factory, tenant.id, "professional")
    token = await _owner_token(session_factory, tenant.id)
    try:
        key = await _make_key(session_factory, tenant.id, token)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/v1/developer/catalog/products",
                headers={"Authorization": f"Bearer {key}"},
            )
        assert resp.status_code == 200, resp.text
        assert "items" in resp.json()
    finally:
        app.dependency_overrides.clear()


async def test_a_key_without_the_read_scope_opens_nothing(tenant, session_factory) -> None:
    await _set_plan(session_factory, tenant.id, "professional")
    token = await _owner_token(session_factory, tenant.id)
    try:
        key = await _make_key(session_factory, tenant.id, token)
        # Revoke the read scope by writing the key row directly.
        async with session_factory() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE api_keys SET scopes = CAST(:scopes AS jsonb) "
                    "WHERE tenant_id = :tid"
                ),
                {"scopes": json.dumps(["write"]), "tid": str(tenant.id)},
            )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/v1/developer/orders",
                headers={"Authorization": f"Bearer {key}"},
            )
        assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.clear()


async def test_a_key_reads_only_its_own_tenant(tenant, session_factory) -> None:
    """The tenant comes from the key row, never from a parameter."""
    await _set_plan(session_factory, tenant.id, "professional")
    token = await _owner_token(session_factory, tenant.id)
    other_id = uuid4()
    try:
        key = await _make_key(session_factory, tenant.id, token)
        # A second tenant with a product of its own.
        async with session_factory() as session, session.begin():
            from packages.core.models.catalog import Product, ProductStatus
            from packages.core.models.core import Tenant

            session.add(
                Tenant(
                    id=other_id,
                    name="Other Biz",
                    slug=f"other-{uuid4().hex[:8]}",
                    owner_phone=f"2547{uuid4().hex[:8]}",
                    owner_email=f"other-{uuid4().hex[:6]}@example.com",
                    is_active=True,
                    settings={},
                )
            )
            await session.flush()
            session.add(
                Product(
                    tenant_id=other_id,
                    name="Other Secret Hoodie",
                    status=ProductStatus.ACTIVE.value,
                )
            )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/v1/developer/catalog/products",
                headers={"Authorization": f"Bearer {key}"},
            )
        assert resp.status_code == 200, resp.text
        names = [item["name"] for item in resp.json()["items"]]
        assert "Other Secret Hoodie" not in names
    finally:
        app.dependency_overrides.clear()


async def test_no_credentials_means_no_access(tenant, session_factory) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/developer/catalog/products")
    assert resp.status_code == 401, resp.text
