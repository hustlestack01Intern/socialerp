"""
Phase 4 payments API tests (routes over the real app + Postgres).

Uses dependency overrides for the tenant id, database session and provider so
the endpoints can be driven without external services. The callback endpoint is
unauthenticated by design; the initiate/list endpoints are tenant-scoped.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.main import app
from apps.api.routes.payments import get_payment_provider
from packages.core.auth.dependencies import get_current_tenant_id, get_db
from packages.core.domain.order import OrderStatus
from packages.core.domain.payment import (
    PaymentProvider,
    ProviderPaymentResult,
    ProviderPaymentStatus,
)
from packages.core.domain.payment import (
    STKPushRequest as DomainSTKPushRequest,
)
from packages.core.domain.payment import (
    STKPushResponse as DomainSTKPushResponse,
)
from packages.core.models.order import Customer, Order, PaymentMethod
from packages.core.models.payment import Payment, PaymentRecordStatus

TENANT_PHONE = "254712345678"


class FakeProvider(PaymentProvider):
    def __init__(self) -> None:
        self.result: ProviderPaymentResult | None = None
        self._seq = 0

    async def initiate_stk_push(self, request: DomainSTKPushRequest) -> DomainSTKPushResponse:
        self._seq += 1
        return DomainSTKPushResponse(
            checkout_request_id=f"ws_CO_{self._seq}",
            merchant_request_id=f"mreq_{self._seq}",
            response_code="0",
            response_description="Accepted",
            customer_message="Enter PIN",
        )

    async def query_status(self, checkout_request_id: str) -> ProviderPaymentResult:
        assert self.result is not None
        return self.result

    async def validate_callback(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        return True

    async def parse_callback(self, payload: dict[str, Any]) -> ProviderPaymentResult:
        assert self.result is not None
        return self.result

    async def refund(self, mpesa_receipt_number: str, amount_cents: int, reason: str) -> bool:
        return False


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def payments_client(
    tenant, session_factory, provider: FakeProvider
) -> AsyncGenerator[AsyncClient, None]:
    """Client with tenant/db/provider overridden for the payments routes."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_tenant_id] = lambda: tenant.id
    app.dependency_overrides[get_payment_provider] = lambda: provider

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


async def _seed_order(
    session_factory: Any,
    tenant_id: UUID,
    *,
    status: OrderStatus = OrderStatus.AWAITING_PAYMENT,
    total_cents: int = 500_000,
) -> UUID:
    async with session_factory() as session, session.begin():
        customer = Customer(tenant_id=tenant_id, phone=TENANT_PHONE, name="Buyer")
        session.add(customer)
        await session.flush()
        order = Order(
            tenant_id=tenant_id,
            customer_id=customer.id,
            customer_phone=TENANT_PHONE,
            total_cents=total_cents,
            status=status,
            payment_method=PaymentMethod.MPESA_STK,
        )
        session.add(order)
        await session.flush()
        return order.id


async def test_initiate_stk_endpoint(payments_client, tenant, session_factory) -> None:
    order_id = await _seed_order(session_factory, tenant.id)
    resp = await payments_client.post(
        f"/api/v1/payments/orders/{order_id}/stk",
        json={"phone_number": TENANT_PHONE},
        headers={"Idempotency-Key": "k1"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == PaymentRecordStatus.INITIATED.value
    assert body["checkout_request_id"] == "ws_CO_1"
    assert body["amount_cents"] == 500_000


async def test_initiate_stk_rejects_non_draft(payments_client, tenant, session_factory) -> None:
    order_id = await _seed_order(session_factory, tenant.id, status=OrderStatus.DRAFT)
    resp = await payments_client.post(
        f"/api/v1/payments/orders/{order_id}/stk",
        json={"phone_number": TENANT_PHONE},
    )
    assert resp.status_code == 409


async def test_list_order_payments(payments_client, tenant, session_factory) -> None:
    order_id = await _seed_order(session_factory, tenant.id)
    await payments_client.post(
        f"/api/v1/payments/orders/{order_id}/stk",
        json={"phone_number": TENANT_PHONE},
    )
    resp = await payments_client.get(f"/api/v1/payments/orders/{order_id}")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["checkout_request_id"] == "ws_CO_1"


async def test_callback_completes_payment_end_to_end(
    payments_client, tenant, session_factory, provider: FakeProvider
) -> None:
    order_id = await _seed_order(session_factory, tenant.id)
    init = await payments_client.post(
        f"/api/v1/payments/orders/{order_id}/stk",
        json={"phone_number": TENANT_PHONE},
    )
    checkout_id = init.json()["checkout_request_id"]

    provider.result = ProviderPaymentResult(
        status=ProviderPaymentStatus.COMPLETED,
        checkout_request_id=checkout_id,
        mpesa_receipt_number="MPE999",
        result_code=0,
        result_desc="OK",
        amount_cents=500_000,
    )
    callback = await payments_client.post(
        f"/api/v1/payments/stk/callback/{tenant.id}/token",
        json={"Body": {"stkCallback": {"CheckoutRequestID": checkout_id, "ResultCode": 0}}},
    )
    assert callback.status_code == 200, callback.text
    assert callback.json()["ResultCode"] == 0

    async with session_factory() as session:
        payment = (
            await session.execute(select(Payment).where(Payment.order_id == order_id))
        ).scalar_one()
        assert payment.status == PaymentRecordStatus.COMPLETED
        assert payment.mpesa_receipt_number == "MPE999"
        order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()
        assert order.status == OrderStatus.PAID


async def test_callback_unknown_checkout_is_acknowledged(
    payments_client, tenant, provider: FakeProvider
) -> None:
    provider.result = ProviderPaymentResult(
        status=ProviderPaymentStatus.COMPLETED,
        checkout_request_id="ws_CO_unknown",
        result_code=0,
        amount_cents=500_000,
    )
    resp = await payments_client.post(
        f"/api/v1/payments/stk/callback/{tenant.id}/token",
        json={"Body": {"stkCallback": {"CheckoutRequestID": "ws_CO_unknown", "ResultCode": 0}}},
    )
    assert resp.status_code == 200
    assert resp.json()["ResultCode"] == 0


async def test_callback_wrong_path_token_rejected(
    payments_client, tenant, session_factory, provider: FakeProvider, monkeypatch
) -> None:
    from socialerp.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "DARAJA_CALLBACK_TOKEN", "secret")
    resp = await payments_client.post(
        f"/api/v1/payments/stk/callback/{tenant.id}/wrong",
        json={"Body": {"stkCallback": {"CheckoutRequestID": "x", "ResultCode": 0}}},
    )
    assert resp.status_code == 403
