"""
Phase 6 orders API tests (routes over the real app + Postgres).

Tenant id and DB session are injected via dependency overrides; catalog data is
seeded directly. Payment is not exercised here (see tests/payments).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from packages.core.auth.dependencies import get_current_tenant_id, get_db
from packages.core.domain.stock import StockLedger, StockMovementRepository
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.order import Order

PHONE = "254712345678"


async def _seed_variant(session, tenant_id: UUID, *, price_cents: int = 5000, qty: int = 10):
    product = Product(tenant_id=tenant_id, name="Hoodie", status=ProductStatus.ACTIVE.value)
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        tenant_id=tenant_id,
        product_id=product.id,
        sku=f"SKU-{uuid4().hex[:8].upper()}",
        attributes=[],
        cost_price_cents=price_cents // 2,
        sell_price_cents=price_cents,
        quantity=qty,
        is_active=True,
    )
    session.add(variant)
    await session.flush()
    ledger = StockLedger(tenant_id=tenant_id, product_id=product.id, variant_id=variant.id)
    ledger.load([])
    movement = ledger.record_receive(
        quantity=qty,
        unit_cost_cents=price_cents // 2,
        reference_id=None,
        reference_type="purchase_order",
    )
    await StockMovementRepository(session).add(movement)
    return variant


@pytest.fixture
async def orders_client(tenant, session_factory) -> AsyncGenerator[AsyncClient]:
    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


async def test_create_and_get_order(orders_client, tenant, session_factory) -> None:
    async with session_factory() as session, session.begin():
        variant = await _seed_variant(session, tenant.id, price_cents=5000, qty=10)
        variant_id = str(variant.id)

    resp = await orders_client.post(
        "/api/v1/orders",
        json={
            "customer_phone": PHONE,
            "items": [{"variant_id": variant_id, "quantity": 2}],
            "delivery_fee_cents": 200,
        },
        headers={"Idempotency-Key": "api-key-1"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "awaiting_payment"
    assert body["subtotal_cents"] == 10_000
    assert body["total_cents"] == 10_200
    assert len(body["items"]) == 1

    order_id = body["id"]
    get = await orders_client.get(f"/api/v1/orders/{order_id}")
    assert get.status_code == 200
    assert get.json()["id"] == order_id

    listing = await orders_client.get("/api/v1/orders")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


async def test_create_order_idempotent_header(orders_client, tenant, session_factory) -> None:
    async with session_factory() as session, session.begin():
        variant = await _seed_variant(session, tenant.id, qty=10)
        variant_id = str(variant.id)

    payload = {
        "customer_phone": PHONE,
        "items": [{"variant_id": variant_id, "quantity": 1}],
    }
    first = await orders_client.post(
        "/api/v1/orders", json=payload, headers={"Idempotency-Key": "dup-1"},
    )
    second = await orders_client.post(
        "/api/v1/orders", json=payload, headers={"Idempotency-Key": "dup-1"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


async def test_create_order_insufficient_stock_returns_409(
    orders_client, tenant, session_factory,
) -> None:
    async with session_factory() as session, session.begin():
        variant = await _seed_variant(session, tenant.id, qty=1)
        variant_id = str(variant.id)

    resp = await orders_client.post(
        "/api/v1/orders",
        json={"customer_phone": PHONE, "items": [{"variant_id": variant_id, "quantity": 5}]},
    )
    assert resp.status_code == 409


async def test_create_order_unknown_variant_returns_400(orders_client, tenant) -> None:
    resp = await orders_client.post(
        "/api/v1/orders",
        json={"customer_phone": PHONE, "items": [{"variant_id": str(uuid4()), "quantity": 1}]},
    )
    assert resp.status_code == 400


async def test_cancel_and_pack_order(orders_client, tenant, session_factory) -> None:
    async with session_factory() as session, session.begin():
        variant = await _seed_variant(session, tenant.id, qty=10)
        variant_id = str(variant.id)

    created = await orders_client.post(
        "/api/v1/orders",
        json={"customer_phone": PHONE, "items": [{"variant_id": variant_id, "quantity": 2}]},
    )
    order_id = created.json()["id"]

    not_found = await orders_client.get(f"/api/v1/orders/{uuid4()}")
    assert not_found.status_code == 404

    cancelled = await orders_client.post(
        f"/api/v1/orders/{order_id}/cancel", json={"reason": "no longer needed"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    # Packing a cancelled order is an illegal transition.
    packed = await orders_client.post(f"/api/v1/orders/{order_id}/pack")
    assert packed.status_code == 409


async def test_pack_paid_order(orders_client, tenant, session_factory) -> None:
    async with session_factory() as session, session.begin():
        variant = await _seed_variant(session, tenant.id, qty=10)
        variant_id = str(variant.id)

    created = await orders_client.post(
        "/api/v1/orders",
        json={"customer_phone": PHONE, "items": [{"variant_id": variant_id, "quantity": 1}]},
    )
    order_id = created.json()["id"]

    async with session_factory() as session, session.begin():
        order = (
            await session.execute(select(Order).where(Order.id == UUID(order_id)))
        ).scalar_one()
        order.status = "paid"
        await session.flush()

    packed = await orders_client.post(f"/api/v1/orders/{order_id}/pack")
    assert packed.status_code == 200
    assert packed.json()["status"] == "packed"
