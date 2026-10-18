"""Phase 8b: insights API — owner-only gate, shapes, tenant scope, no PII.

Covers plan acceptance criteria:
- "API authZ: owner-only for insights/segment membership; non-owner denied."
- Report/stock-alert payload shapes match the route schemas.
- Tenant scoping through the real dependency chain (tenant_id override).
- No new PII: payloads carry counts, money-in-cents and product data only.

NOTE: the Phase 8b routers must be mounted in ``apps/api/main.py`` (the wiring
lines are reported alongside this wave) before these tests can pass; like
tests/segments/test_segments_api.py they exercise the real app, matching
tests/crm + tests/alerts house style. Until wired, every call below fails
loudly with 404 — an unwired router is a delivery bug, not a test bug.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date
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
from packages.core.models.core import Tenant, UserRole
from tests.insights.helpers import (
    add_item,
    add_order,
    add_product,
    add_variant,
    make_customer,
    make_tenant,
    utc_day,
)

DAY = date(2026, 10, 10)


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
    from tests.segments.helpers import make_user

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


async def test_insights_denied_for_non_owner(staff_client: AsyncClient) -> None:
    daily = await staff_client.get("/api/v1/insights/daily")
    weekly = await staff_client.get("/api/v1/insights/weekly")
    stock = await staff_client.get("/api/v1/insights/stock-alerts")
    assert daily.status_code == 403, daily.text
    assert weekly.status_code == 403, weekly.text
    assert stock.status_code == 403, stock.text


async def test_insights_allowed_for_owner(owner_client: AsyncClient) -> None:
    daily = await owner_client.get("/api/v1/insights/daily", params={"date": "2026-01-01"})
    assert daily.status_code == 200, daily.text
    body = daily.json()
    assert body["period_start"] == "2026-01-01"
    assert body["period_end"] == "2026-01-02"
    assert body["orders_count"] == 0
    assert body["revenue_cents"] == 0
    assert body["aov_cents"] == 0
    assert body["top_items"] == []
    assert body["anomaly"] is None, "empty baseline => no anomaly"


# =============================================================================
# Shapes and semantics through the HTTP layer
# =============================================================================


async def test_daily_report_shape_with_real_rows(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        product = await add_product(session, tenant.id, name="Hoodie")
        order = await add_order(
            session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 9), paid=True
        )
        await add_item(session, tenant.id, order, product, quantity=2, name="Hoodie", sku="H-1")

    resp = await owner_client.get("/api/v1/insights/daily", params={"date": "2026-10-10"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["orders_count"] == 1
    assert body["paid_orders_count"] == 1
    assert body["revenue_cents"] == 100_00
    assert body["aov_cents"] == 100_00
    assert body["new_customers"] == 1
    assert body["returning_customers"] == 0
    assert body["top_items"] == [
        {"product_id": str(product.id), "name": "Hoodie", "sku": "H-1", "quantity": 2}
    ]
    assert "anomaly" in body


async def test_weekly_report_shape(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(session, tenant.id)
        await add_order(session, tenant.id, customer, created_at=utc_day(2026, 10, 6, 9), paid=True)

    resp = await owner_client.get("/api/v1/insights/weekly", params={"week_start": "2026-10-05"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["period_start"] == "2026-10-05"
    assert body["period_end"] == "2026-10-12"
    assert body["orders_count"] == 1
    assert body["anomaly"] is None, "weekly reports never carry an anomaly"


async def test_stock_alerts_shape(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        product = await add_product(session, tenant.id, name="Hoodie")
        variant = await add_variant(
            session, tenant.id, product, sku="LOW-1", quantity=1, reorder_level=5
        )

    resp = await owner_client.get("/api/v1/insights/stock-alerts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == [
        {
            "variant_id": str(variant.id),
            "product_id": str(product.id),
            "product_name": "Hoodie",
            "sku": "LOW-1",
            "quantity": 1,
            "reorder_level": 5,
        }
    ]


async def test_insights_endpoints_are_tenant_scoped(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Another tenant's paid order must not appear in this tenant's report."""
    async with session_factory() as session, session.begin():
        other = await make_tenant(session, slug_prefix="insapi-other")
        theirs = await make_customer(session, other.id)
        await add_order(session, other.id, theirs, created_at=utc_day(2026, 10, 10, 9), paid=True)

    resp = await owner_client.get("/api/v1/insights/daily", params={"date": "2026-10-10"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["orders_count"] == 0
    assert resp.json()["revenue_cents"] == 0

    stock = await owner_client.get("/api/v1/insights/stock-alerts")
    assert stock.status_code == 200, stock.text
    assert stock.json() == []


async def test_insights_payload_carries_no_pii(
    owner_client: AsyncClient, tenant: Tenant, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session, session.begin():
        customer = await make_customer(
            session, tenant.id, phone="254799988877", name="PII Confident"
        )
        await add_order(
            session, tenant.id, customer, created_at=utc_day(2026, 10, 10, 9), paid=True
        )

    daily = await owner_client.get("/api/v1/insights/daily", params={"date": "2026-10-10"})
    weekly = await owner_client.get("/api/v1/insights/weekly", params={"week_start": "2026-10-05"})
    stock = await owner_client.get("/api/v1/insights/stock-alerts")
    blob = daily.text + weekly.text + stock.text
    assert "254799988877" not in blob
    assert "PII Confident" not in blob


async def test_invalid_date_is_422(owner_client: AsyncClient) -> None:
    resp = await owner_client.get("/api/v1/insights/daily", params={"date": "not-a-date"})
    assert resp.status_code == 422, resp.text
