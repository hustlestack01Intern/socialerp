"""
U29 fail-closed tests: the public tracking endpoints are gated by a signed,
expiring capability token bound to (tenant_id, order_id).

A known order UUID alone must no longer be enough: missing, malformed,
tampered, expired, wrong-tenant and wrong-order tokens all return the SAME
404 as an unknown order — the endpoints never reveal whether the order
exists — and a valid token still serves only the PII-free projection.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from packages.core.auth.dependencies import get_db
from packages.core.delivery.tracking_token import (
    build_tracking_url,
    sign_tracking_token,
    verify_tracking_token,
)
from packages.core.models.order import Customer, Order

# Deliberately conspicuous so a leak is obvious in a substring assertion.
PII_PHONE = "254733333333"
PII_NAME = "John Secretly"
PII_ADDRESS = "99 Hidden Court, Karen"


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


async def _seed_order(session_factory, tenant, *, phone: str = PII_PHONE) -> str:
    """Insert a customer + order directly (no delivery needed for gating)."""
    async with session_factory() as session, session.begin():
        customer = Customer(
            tenant_id=tenant.id,
            phone=phone,
            name=PII_NAME,
            default_address=PII_ADDRESS,
            default_area="Karen",
        )
        session.add(customer)
        await session.flush()

        order = Order(
            tenant_id=tenant.id,
            customer_id=customer.id,
            customer_phone=phone,
            customer_name=PII_NAME,
            subtotal_cents=5000,
            delivery_fee_cents=20000,
            discount_cents=0,
            total_cents=25000,
            status="paid",
            payment_method="mpesa_stk",
            payment_status="completed",
            paid_at=datetime.now(UTC),
        )
        session.add(order)
        await session.flush()
        return str(order.id)


# =============================================================================
# Pure sign/verify unit tests (no HTTP)
# =============================================================================


def test_valid_token_roundtrip() -> None:
    tenant_id, order_id = uuid4(), uuid4()
    token = sign_tracking_token(tenant_id, order_id)
    assert verify_tracking_token(token, tenant_id, order_id) is True


def test_token_is_bound_to_tenant() -> None:
    order_id = uuid4()
    token = sign_tracking_token(uuid4(), order_id)
    assert verify_tracking_token(token, uuid4(), order_id) is False


def test_token_is_bound_to_order() -> None:
    tenant_id = uuid4()
    token = sign_tracking_token(tenant_id, uuid4())
    assert verify_tracking_token(token, tenant_id, uuid4()) is False


def test_expired_token_is_rejected() -> None:
    tenant_id, order_id = uuid4(), uuid4()
    token = sign_tracking_token(tenant_id, order_id, ttl=timedelta(seconds=-1))
    assert verify_tracking_token(token, tenant_id, order_id) is False


def test_token_stops_validating_after_ttl() -> None:
    """A token minted with a tiny TTL expires while the secret stays the same."""
    tenant_id, order_id = uuid4(), uuid4()
    minted_at = datetime.now(UTC)
    token = sign_tracking_token(tenant_id, order_id, ttl=timedelta(hours=1), now=minted_at)
    just_before = minted_at + timedelta(hours=1) - timedelta(seconds=1)
    just_after = minted_at + timedelta(hours=1)
    assert verify_tracking_token(token, tenant_id, order_id, now=just_before) is True
    assert verify_tracking_token(token, tenant_id, order_id, now=just_after) is False


@pytest.mark.parametrize(
    "bad_token",
    [
        None,
        "",
        "garbage",
        "v1",
        "v1.9999999999",  # two parts only
        "v1.9999999999.abc.def",  # four parts
        "v1.not-an-int.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",  # bad expiry
        "v2.9999999999.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",  # wrong version
        "v1.9999999999.!!!not-base64!!!",  # malformed signature
        "v1.9999999999.AAAA",  # truncated signature
    ],
)
def test_malformed_tokens_are_rejected(bad_token: str | None) -> None:
    tenant_id, order_id = uuid4(), uuid4()
    assert verify_tracking_token(bad_token, tenant_id, order_id) is False


def test_tampered_signature_is_rejected() -> None:
    tenant_id, order_id = uuid4(), uuid4()
    token = sign_tracking_token(tenant_id, order_id)
    version, expires, signature = token.split(".")
    # Flip a mid-string char: the LAST base64 char only carries pad bits and
    # could decode to the same digest bytes.
    idx = 10
    flipped = "B" if signature[idx] != "B" else "C"
    tampered = f"{version}.{expires}.{signature[:idx]}{flipped}{signature[idx + 1 :]}"
    assert verify_tracking_token(tampered, tenant_id, order_id) is False


def test_tampered_expiry_is_rejected() -> None:
    tenant_id, order_id = uuid4(), uuid4()
    token = sign_tracking_token(tenant_id, order_id)
    version, expires, signature = token.split(".")
    extended = f"{version}.{int(expires) + 86_400}.{signature}"
    assert verify_tracking_token(extended, tenant_id, order_id) is False


def test_build_tracking_url_embeds_token() -> None:
    tenant_id, order_id = uuid4(), uuid4()
    url = build_tracking_url("https://erp.example", tenant_id, order_id)
    prefix = f"https://erp.example/tracking/{order_id}?t="
    assert url.startswith(prefix)
    assert verify_tracking_token(url.removeprefix(prefix), tenant_id, order_id) is True


# =============================================================================
# HTTP fail-closed tests (both endpoints)
# =============================================================================


@pytest.mark.asyncio
async def test_missing_token_is_404_for_known_order(
    tracking_client: AsyncClient, tenant, session_factory
) -> None:
    """A bare order UUID (the old capability URL) now fails closed."""
    order_id = await _seed_order(session_factory, tenant)

    json_resp = await tracking_client.get(f"/api/v1/tracking/{order_id}")
    assert json_resp.status_code == 404
    page_resp = await tracking_client.get(f"/tracking/{order_id}")
    assert page_resp.status_code == 404

    empty = await tracking_client.get(f"/api/v1/tracking/{order_id}", params={"t": ""})
    assert empty.status_code == 404


@pytest.mark.asyncio
async def test_wrong_tenant_token_is_404(
    tracking_client: AsyncClient, tenant, session_factory
) -> None:
    """A token minted for another tenant never unlocks this tenant's order."""
    order_id = await _seed_order(session_factory, tenant)
    foreign_token = sign_tracking_token(uuid4(), UUID(order_id))

    json_resp = await tracking_client.get(
        f"/api/v1/tracking/{order_id}", params={"t": foreign_token}
    )
    assert json_resp.status_code == 404
    page_resp = await tracking_client.get(f"/tracking/{order_id}", params={"t": foreign_token})
    assert page_resp.status_code == 404


@pytest.mark.asyncio
async def test_token_for_other_order_is_404(
    tracking_client: AsyncClient, tenant, session_factory
) -> None:
    """A valid token for order X does not unlock order Y (same tenant)."""
    order_x = await _seed_order(session_factory, tenant, phone="254733333340")
    order_y = await _seed_order(session_factory, tenant, phone="254733333341")
    token_for_x = sign_tracking_token(tenant.id, UUID(order_x))

    resp = await tracking_client.get(f"/api/v1/tracking/{order_y}", params={"t": token_for_x})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tampered_token_is_404(tracking_client: AsyncClient, tenant, session_factory) -> None:
    order_id = await _seed_order(session_factory, tenant)
    token = sign_tracking_token(tenant.id, UUID(order_id))
    version, expires, signature = token.split(".")
    idx = 10
    flipped = "B" if signature[idx] != "B" else "C"
    tampered = f"{version}.{expires}.{signature[:idx]}{flipped}{signature[idx + 1 :]}"

    json_resp = await tracking_client.get(f"/api/v1/tracking/{order_id}", params={"t": tampered})
    assert json_resp.status_code == 404
    page_resp = await tracking_client.get(f"/tracking/{order_id}", params={"t": tampered})
    assert page_resp.status_code == 404


@pytest.mark.asyncio
async def test_expired_token_is_404(tracking_client: AsyncClient, tenant, session_factory) -> None:
    order_id = await _seed_order(session_factory, tenant)
    expired = sign_tracking_token(tenant.id, UUID(order_id), ttl=timedelta(seconds=-1))

    json_resp = await tracking_client.get(f"/api/v1/tracking/{order_id}", params={"t": expired})
    assert json_resp.status_code == 404
    page_resp = await tracking_client.get(f"/tracking/{order_id}", params={"t": expired})
    assert page_resp.status_code == 404


@pytest.mark.asyncio
async def test_malformed_token_is_404(
    tracking_client: AsyncClient, tenant, session_factory
) -> None:
    order_id = await _seed_order(session_factory, tenant)

    for bad in ("garbage", "v1.9999999999", "v9.1.2.3", "v1.x.y"):
        resp = await tracking_client.get(f"/api/v1/tracking/{order_id}", params={"t": bad})
        assert resp.status_code == 404, bad


@pytest.mark.asyncio
async def test_valid_token_serves_pii_free_projection(
    tracking_client: AsyncClient, tenant, session_factory
) -> None:
    """The happy path with a valid token: 200 on both endpoints, still no PII."""
    order_id = await _seed_order(session_factory, tenant)
    token = sign_tracking_token(tenant.id, UUID(order_id))

    json_resp = await tracking_client.get(f"/api/v1/tracking/{order_id}", params={"t": token})
    assert json_resp.status_code == 200, json_resp.text
    body = json_resp.json()
    assert body["order_status"] == "paid"
    for secret in (PII_PHONE, PII_NAME, PII_ADDRESS):
        assert secret not in json_resp.text, f"PII leaked into tracking JSON: {secret!r}"
    assert re.search(r"254\s*7\d{2}\s*\d{3}\s*\d{3}", json_resp.text) is None

    page_resp = await tracking_client.get(f"/tracking/{order_id}", params={"t": token})
    assert page_resp.status_code == 200
    assert page_resp.headers["content-type"].startswith("text/html")
    for secret in (PII_PHONE, PII_NAME, PII_ADDRESS):
        assert secret not in page_resp.text, f"PII leaked into tracking page: {secret!r}"
