"""
Phase 7 remainder: public order tracking tests.

The tracking endpoints are PUBLIC and must never leak PII. These tests seed a
customer with a deliberately conspicuous name/phone/address, then assert the
projection exposes status + zone + timeline but never the customer's identity.

U29 (fail closed): the endpoints are gated by a signed, expiring capability
token (``?t=``), so every success case below mints a token for the order's
tenant, and the fail-closed cases (missing/expired/wrong-tenant tokens) live
in ``test_tracking_token.py``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from packages.core.auth.dependencies import get_db
from packages.core.delivery.tracking_token import sign_tracking_token
from packages.core.models.delivery import Delivery, DeliveryZone, Rider
from packages.core.models.order import Customer, Order

# Deliberately conspicuous so a leak is obvious in a substring assertion.
PII_PHONE = "254711111111"
PII_NAME = "Jane Confidential"
PII_ADDRESS = "12 Secret Lane, Lavington"
TENANT_PHONE = PII_PHONE


@pytest.fixture
async def tracking_client(tenant, session_factory) -> AsyncGenerator[AsyncClient, None]:
    """Client for the PUBLIC tracking routes: get_db override, no auth override."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


async def _seed_order_with_delivery(session_factory, tenant, *, with_delivery: bool) -> str:
    """Insert a customer + order (and optionally a delivery) directly."""
    async with session_factory() as session, session.begin():
        customer = Customer(
            tenant_id=tenant.id,
            phone=PII_PHONE,
            name=PII_NAME,
            default_address=PII_ADDRESS,
            default_area="Lavington",
        )
        session.add(customer)
        await session.flush()

        order = Order(
            tenant_id=tenant.id,
            customer_id=customer.id,
            customer_phone=PII_PHONE,
            customer_name=PII_NAME,
            subtotal_cents=5000,
            delivery_fee_cents=20000,
            discount_cents=0,
            total_cents=25000,
            status="out_for_delivery",
            payment_method="mpesa_stk",
            payment_status="completed",
            paid_at=datetime.now(UTC),
        )
        session.add(order)
        await session.flush()

        if with_delivery:
            zone = DeliveryZone(tenant_id=tenant.id, name="Lavington", fee_cents=20000)
            session.add(zone)
            await session.flush()
            rider = Rider(
                tenant_id=tenant.id,
                phone="254700000001",
                name="Rider A",
            )
            session.add(rider)
            await session.flush()
            session.add(
                Delivery(
                    tenant_id=tenant.id,
                    order_id=order.id,
                    rider_id=rider.id,
                    zone_id=zone.id,
                    fee_cents=20000,
                    status="out_for_delivery",
                    picked_up_at=datetime.now(UTC),
                )
            )
            await session.flush()

        return str(order.id)


def _token_for(tenant, order_id: str) -> str:
    """Mint a valid tracking capability token for the order's tenant."""
    return sign_tracking_token(tenant.id, UUID(order_id))


@pytest.mark.asyncio
async def test_unknown_order_is_404(tracking_client: AsyncClient) -> None:
    """Both endpoints 404 for an order that does not exist (no existence leak).

    Holds with no token AND with a syntactically valid token for the unknown
    id: a token does not conjure an order into existence.
    """
    unknown = uuid4()
    json_resp = await tracking_client.get(f"/api/v1/tracking/{unknown}")
    assert json_resp.status_code == 404
    page_resp = await tracking_client.get(f"/tracking/{unknown}")
    assert page_resp.status_code == 404

    signed_for_unknown = sign_tracking_token(uuid4(), unknown)
    json_resp = await tracking_client.get(
        f"/api/v1/tracking/{unknown}", params={"t": signed_for_unknown}
    )
    assert json_resp.status_code == 404
    page_resp = await tracking_client.get(f"/tracking/{unknown}", params={"t": signed_for_unknown})
    assert page_resp.status_code == 404


@pytest.mark.asyncio
async def test_tracking_json_exposes_status_but_never_pii(
    tracking_client: AsyncClient, tenant, session_factory
) -> None:
    order_id = await _seed_order_with_delivery(session_factory, tenant, with_delivery=True)

    resp = await tracking_client.get(
        f"/api/v1/tracking/{order_id}", params={"t": _token_for(tenant, order_id)}
    )
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["order_status"] == "out_for_delivery"
    assert body["delivery_status"] == "out_for_delivery"
    assert body["zone_name"] == "Lavington"
    assert body["payment_method"] == "mpesa_stk"
    # A timeline with the placed step plus the out-for-delivery step.
    labels = [s["label"] for s in body["steps"]]
    assert "Order placed" in labels
    assert "Out for delivery" in labels

    # No PII anywhere in the serialized response.
    text = resp.text
    for secret in (PII_PHONE, PII_NAME, PII_ADDRESS):
        assert secret not in text, f"PII leaked into tracking JSON: {secret!r}"
    # A reformatted phone must not slip through either.
    assert re.search(r"254\s*7\d{2}\s*\d{3}\s*\d{3}", text) is None


@pytest.mark.asyncio
async def test_tracking_page_is_html_and_never_pii(
    tracking_client: AsyncClient, tenant, session_factory
) -> None:
    order_id = await _seed_order_with_delivery(session_factory, tenant, with_delivery=True)

    resp = await tracking_client.get(
        f"/tracking/{order_id}", params={"t": _token_for(tenant, order_id)}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "out for delivery" in resp.text.lower()
    assert "Lavington" in resp.text

    for secret in (PII_PHONE, PII_NAME, PII_ADDRESS):
        assert secret not in resp.text, f"PII leaked into tracking page: {secret!r}"


@pytest.mark.asyncio
async def test_tracking_json_order_without_delivery(
    tracking_client: AsyncClient, tenant, session_factory
) -> None:
    """An order with no delivery yet reports delivery_status None, still no PII."""
    order_id = await _seed_order_with_delivery(session_factory, tenant, with_delivery=False)

    resp = await tracking_client.get(
        f"/api/v1/tracking/{order_id}", params={"t": _token_for(tenant, order_id)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["delivery_status"] is None
    assert body["zone_name"] is None
    for secret in (PII_PHONE, PII_NAME, PII_ADDRESS):
        assert secret not in resp.text


@pytest.mark.asyncio
async def test_tracking_rejects_malformed_order_id(tracking_client: AsyncClient) -> None:
    """A malformed id is a 422 from the UUID converter, not a 500."""
    resp = await tracking_client.get("/api/v1/tracking/not-a-uuid")
    assert resp.status_code == 422
