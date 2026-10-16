"""
Phase 7b delivery API tests (routes over the real app + Postgres).

Thin HTTP layer over :class:`DeliveryService`: zone/rider CRUD, dispatch and the
rider quick-reply transitions, including the pay-on-delivery STK trigger fired
when a rider marks "arrived".
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from packages.core.auth.dependencies import get_current_tenant_id, get_db
from packages.core.domain.payment import STKPushResponse
from packages.core.domain.stock import StockLedger, StockMovementRepository
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.order import Order

TENANT_PHONE = "254712345678"


class _FakeProvider:
    """Providers abstract the M-Pesa hop; here it only records STK pushes."""

    def __init__(self) -> None:
        self.pushes: list[Any] = []

    async def initiate_stk_push(self, request: Any) -> STKPushResponse:
        self.pushes.append(request)
        n = len(self.pushes)
        return STKPushResponse(
            checkout_request_id=f"ws_CO_{n}",
            merchant_request_id=f"mreq_{n}",
            response_code="0",
            response_description="Success. Request accepted for processing",
            customer_message="Enter PIN",
        )


async def _seed_variant(session: AsyncSession, tenant_id: UUID, *, price_cents: int = 5000):
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
        quantity=10,
        is_active=True,
    )
    session.add(variant)
    await session.flush()
    ledger = StockLedger(tenant_id=tenant_id, product_id=product.id, variant_id=variant.id)
    ledger.load([])
    movement = ledger.record_receive(
        quantity=10,
        unit_cost_cents=price_cents // 2,
        reference_id=None,
        reference_type="purchase_order",
    )
    await StockMovementRepository(session).add(movement)
    return variant


@pytest.fixture
def fake_provider() -> _FakeProvider:
    return _FakeProvider()


@pytest.fixture
async def delivery_client(
    tenant, session_factory, fake_provider, monkeypatch
) -> AsyncGenerator[AsyncClient, None]:
    """Client whose POD provider resolution is stubbed with the fake.

    The route resolves the provider *inside* the handler (so a prepay arrival
    never needs a configured provider), hence a monkeypatch on the module
    symbol rather than a FastAPI dependency override.
    """
    from apps.api.routes import delivery as delivery_routes

    async def _resolver(tenant_id, db):
        return fake_provider

    monkeypatch.setattr(delivery_routes, "get_tenant_payment_provider", _resolver)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
async def plain_client(tenant, session_factory) -> AsyncGenerator[AsyncClient, None]:
    """Client with NO provider stub: proves prepay arrival never resolves one."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


async def _create_order(client: AsyncClient, variant_id: str) -> str:
    resp = await client.post(
        "/api/v1/orders",
        json={"customer_phone": TENANT_PHONE, "items": [{"variant_id": variant_id, "quantity": 1}]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _seed_order(
    client, session_factory, tenant, *, status: str, payment_method: str = "mpesa_stk"
) -> str:
    async with session_factory() as session, session.begin():
        variant = await _seed_variant(session, tenant.id)
        variant_id = str(variant.id)

    order_id = await _create_order(client, variant_id)

    async with session_factory() as session, session.begin():
        order = (
            await session.execute(select(Order).where(Order.id == UUID(order_id)))
        ).scalar_one()
        order.status = status
        order.payment_method = payment_method
        if payment_method == "mpesa_stk":
            order.payment_status = "completed"
        await session.flush()
    return order_id


async def _make_zone_and_rider(client: AsyncClient) -> tuple[str, str]:
    zone = await client.post(
        "/api/v1/delivery/zones", json={"name": "Kilimani", "fee_cents": 20000}
    )
    assert zone.status_code == 201, zone.text
    rider = await client.post(
        "/api/v1/delivery/riders", json={"phone": "254700000001", "name": "Rider A"}
    )
    assert rider.status_code == 201, rider.text
    return zone.json()["id"], rider.json()["id"]


async def test_zone_crud(delivery_client: AsyncClient) -> None:
    created = await delivery_client.post(
        "/api/v1/delivery/zones", json={"name": "Westlands", "fee_cents": 25000}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Westlands"
    assert body["fee_cents"] == 25000
    assert body["active"] is True

    listing = await delivery_client.get("/api/v1/delivery/zones")
    assert listing.status_code == 200
    assert any(z["id"] == body["id"] for z in listing.json())

    updated = await delivery_client.patch(
        f"/api/v1/delivery/zones/{body['id']}", json={"fee_cents": 30000, "active": False}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["fee_cents"] == 30000
    assert updated.json()["active"] is False


async def test_rider_crud_and_deactivate(delivery_client: AsyncClient) -> None:
    created = await delivery_client.post(
        "/api/v1/delivery/riders", json={"phone": "254711111111", "name": "Rider B"}
    )
    assert created.status_code == 201, created.text
    rider_id = created.json()["id"]

    listing = await delivery_client.get("/api/v1/delivery/riders")
    assert listing.status_code == 200
    assert any(r["id"] == rider_id for r in listing.json())

    deactivated = await delivery_client.post(f"/api/v1/delivery/riders/{rider_id}/deactivate")
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["active"] is False


async def test_dispatch_requires_packed(
    delivery_client: AsyncClient, tenant, session_factory
) -> None:
    order_id = await _seed_order(
        delivery_client, session_factory, tenant, status="awaiting_payment"
    )
    async with session_factory() as session, session.begin():
        order = (
            await session.execute(select(Order).where(Order.id == UUID(order_id)))
        ).scalar_one()
        order.payment_status = "failed"
        await session.flush()

    zone_id, rider_id = await _make_zone_and_rider(delivery_client)
    resp = await delivery_client.post(
        f"/api/v1/delivery/orders/{order_id}/dispatch",
        json={"rider_id": rider_id, "zone_id": zone_id},
    )
    assert resp.status_code == 409, resp.text


async def test_full_delivery_lifecycle(
    delivery_client: AsyncClient, tenant, session_factory
) -> None:
    order_id = await _seed_order(delivery_client, session_factory, tenant, status="packed")
    zone_id, rider_id = await _make_zone_and_rider(delivery_client)

    dispatched = await delivery_client.post(
        f"/api/v1/delivery/orders/{order_id}/dispatch",
        json={"rider_id": rider_id, "zone_id": zone_id},
    )
    assert dispatched.status_code == 201, dispatched.text
    delivery_id = dispatched.json()["id"]
    assert dispatched.json()["status"] == "assigned"

    # Dispatch is idempotent: same in-flight delivery is returned.
    again = await delivery_client.post(
        f"/api/v1/delivery/orders/{order_id}/dispatch",
        json={"rider_id": rider_id, "zone_id": zone_id},
    )
    assert again.status_code == 201
    assert again.json()["id"] == delivery_id

    picked = await delivery_client.post(f"/api/v1/delivery/deliveries/{delivery_id}/picked-up")
    assert picked.status_code == 200, picked.text
    assert picked.json()["status"] == "picked_up"

    arrived = await delivery_client.post(f"/api/v1/delivery/deliveries/{delivery_id}/arrived")
    assert arrived.status_code == 200, arrived.text
    assert arrived.json()["delivery"]["status"] == "arrived"
    assert arrived.json()["stk"] is None  # prepay: no STK at arrival

    delivered = await delivery_client.post(
        f"/api/v1/delivery/deliveries/{delivery_id}/delivered",
        json={"notes": "left with guard"},
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["status"] == "delivered"

    order = await delivery_client.get(f"/api/v1/orders/{order_id}")
    assert order.json()["status"] == "delivered"


async def test_delivery_illegal_transition_returns_409(
    delivery_client: AsyncClient, session_factory, tenant
) -> None:
    order_id = await _seed_order(delivery_client, session_factory, tenant, status="packed")
    zone_id, rider_id = await _make_zone_and_rider(delivery_client)
    dispatched = await delivery_client.post(
        f"/api/v1/delivery/orders/{order_id}/dispatch",
        json={"rider_id": rider_id, "zone_id": zone_id},
    )
    delivery_id = dispatched.json()["id"]

    # arrived before picked_up is illegal.
    bad = await delivery_client.post(f"/api/v1/delivery/deliveries/{delivery_id}/arrived")
    assert bad.status_code == 409


async def test_pod_arrived_triggers_stk(
    delivery_client: AsyncClient, session_factory, tenant, fake_provider
) -> None:
    order_id = await _seed_order(
        delivery_client,
        session_factory,
        tenant,
        status="packed",
        payment_method="mpesa_pod",
    )
    zone_id, rider_id = await _make_zone_and_rider(delivery_client)

    dispatched = await delivery_client.post(
        f"/api/v1/delivery/orders/{order_id}/dispatch",
        json={"rider_id": rider_id, "zone_id": zone_id},
    )
    delivery_id = dispatched.json()["id"]

    await delivery_client.post(f"/api/v1/delivery/deliveries/{delivery_id}/picked-up")
    arrived = await delivery_client.post(f"/api/v1/delivery/deliveries/{delivery_id}/arrived")
    assert arrived.status_code == 200, arrived.text
    stk = arrived.json()["stk"]
    assert stk is not None
    assert stk["order_id"] == order_id
    assert stk["amount_cents"] == 5000

    # A POD delivery cannot be marked delivered until payment is collected.
    blocked = await delivery_client.post(
        f"/api/v1/delivery/deliveries/{delivery_id}/delivered",
        json={"proof_photo": "http://x/p.jpg"},
    )
    assert blocked.status_code == 409

    # Exactly one STK push was requested for the arrival.
    assert len(fake_provider.pushes) == 1


async def test_failed_on_assigned_keeps_order_packed(
    delivery_client: AsyncClient, session_factory, tenant
) -> None:
    """Failing an assigned delivery must NOT 500 (order packed -> failed_delivery
    is not a legal edge). The delivery fails; the order stays packed so it can be
    re-dispatched or retried with a picked_up transition."""
    order_id = await _seed_order(delivery_client, session_factory, tenant, status="packed")
    zone_id, rider_id = await _make_zone_and_rider(delivery_client)
    dispatched = await delivery_client.post(
        f"/api/v1/delivery/orders/{order_id}/dispatch",
        json={"rider_id": rider_id, "zone_id": zone_id},
    )
    delivery_id = dispatched.json()["id"]

    failed = await delivery_client.post(
        f"/api/v1/delivery/deliveries/{delivery_id}/failed", json={"reason": "no stock"}
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"

    order = await delivery_client.get(f"/api/v1/orders/{order_id}")
    assert order.json()["status"] == "packed"

    # Retry puts the delivery back on the road and the order out_for_delivery.
    retried = await delivery_client.post(f"/api/v1/delivery/deliveries/{delivery_id}/retry")
    assert retried.status_code == 200, retried.text
    order = await delivery_client.get(f"/api/v1/orders/{order_id}")
    assert order.json()["status"] == "out_for_delivery"


async def test_prepay_arrival_never_resolves_a_provider(
    plain_client: AsyncClient, session_factory, tenant
) -> None:
    """A prepay arrival must work with no payment provider configured at all.

    The old route declared the provider as a FastAPI dependency, so even a
    prepay arrival forced provider construction and 500'd on missing Daraja
    credentials. The provider is now resolved only inside the POD branch.
    """
    order_id = await _seed_order(plain_client, session_factory, tenant, status="packed")
    zone_id, rider_id = await _make_zone_and_rider(plain_client)
    dispatched = await plain_client.post(
        f"/api/v1/delivery/orders/{order_id}/dispatch",
        json={"rider_id": rider_id, "zone_id": zone_id},
    )
    delivery_id = dispatched.json()["id"]

    await plain_client.post(f"/api/v1/delivery/deliveries/{delivery_id}/picked-up")
    arrived = await plain_client.post(f"/api/v1/delivery/deliveries/{delivery_id}/arrived")
    assert arrived.status_code == 200, arrived.text
    assert arrived.json()["stk"] is None
