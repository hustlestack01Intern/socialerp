"""The matrix enforced over HTTP (M1.6, U80).

The unit tests pin the decisions; these pin the *answers*: a tenant whose plan
does not include a feature gets a 402 that says which plan and what is
missing, and nothing is created on the way out.

Covered:
- an API key cannot be minted on a plan without API access;
- a user cannot be added when the plan has no free seat;
- insights (the owner's own data) are still readable - the gate may not gate
  them (invariant I3).
"""

from __future__ import annotations

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
        async with session_factory() as session:
            yield session

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


async def _count(session_factory, sql: str, **params) -> int:
    async with session_factory() as session:
        result = await session.execute(text(sql), params)
        return int(result.scalar_one())


async def test_api_key_needs_a_plan_with_api_access(tenant, session_factory) -> None:
    """A trial cannot mint an API key; a professional plan can."""
    token = await _owner_token(session_factory, tenant.id)
    try:
        async with _client_for(session_factory, token) as client:
            refused = await client.post(
                f"/api/v1/auth/tenants/{tenant.id}/api-keys",
                json={"name": "laptop"},
            )
        assert refused.status_code == 402, refused.text
        detail = refused.json()["detail"]
        assert "API access" in detail
        assert "Trial" in detail

        assert (
            await _count(
                session_factory,
                "SELECT count(*) FROM api_keys WHERE tenant_id = :tid",
                tid=str(tenant.id),
            )
            == 0
        )

        await _set_plan(session_factory, tenant.id, "professional")
        async with _client_for(session_factory, token) as client:
            allowed = await client.post(
                f"/api/v1/auth/tenants/{tenant.id}/api-keys",
                json={"name": "laptop"},
            )
        assert allowed.status_code == 201, allowed.text
    finally:
        app.dependency_overrides.clear()


async def test_a_user_needs_a_free_seat(tenant, session_factory) -> None:
    """A trial has one seat; the owner already uses it."""
    token = await _owner_token(session_factory, tenant.id)
    try:
        async with _client_for(session_factory, token) as client:
            refused = await client.post(
                f"/api/v1/auth/tenants/{tenant.id}/users",
                json={
                    "email": f"staff-{uuid4().hex[:6]}@example.com",
                    "password": "S3cret-pass!",
                    "full_name": "Staff",
                    "role": "staff",
                },
            )
        assert refused.status_code == 402, refused.text
        assert "seat" in refused.json()["detail"].lower()

        assert (
            await _count(
                session_factory,
                "SELECT count(*) FROM users WHERE tenant_id = :tid",
                tid=str(tenant.id),
            )
            == 1  # the owner only
        )

        await _set_plan(session_factory, tenant.id, "professional")
        async with _client_for(session_factory, token) as client:
            allowed = await client.post(
                f"/api/v1/auth/tenants/{tenant.id}/users",
                json={
                    "email": f"staff-{uuid4().hex[:6]}@example.com",
                    "password": "S3cret-pass!",
                    "full_name": "Staff",
                    "role": "staff",
                },
            )
        assert allowed.status_code == 201, allowed.text
    finally:
        app.dependency_overrides.clear()


async def test_insights_stay_readable(tenant, session_factory) -> None:
    """I3: the gate may not gate the owner's own data."""
    token = await _owner_token(session_factory, tenant.id)
    try:
        async with _client_for(session_factory, token) as client:
            resp = await client.get("/api/v1/insights/daily")
        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()
