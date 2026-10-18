"""Direct tests for post-commit pay-on-delivery collection.

The rider flow must NOT fire the STK request inline: the worker commits the
arrival first, then calls ``trigger_pod_collection`` in a fresh transaction.
Re-running it (an at-least-once retry) must never double-prompt the customer.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from socialerp.tenancy import Tenant
from sqlalchemy import select, text

from packages.conversation import rider_flow
from packages.conversation.rider_flow import trigger_pod_collection
from packages.core.delivery.service import DeliveryService
from packages.core.domain.payment import STKPushRequest, STKPushResponse
from packages.core.domain.stock import StockLedger, StockMovementRepository
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.delivery import DeliveryZone, Rider
from packages.core.models.order import Customer, Order
from packages.core.models.payment import Payment

PHONE = "254712345678"
RIDER_PHONE = "254700000123"


class FakeProvider:
    def __init__(self) -> None:
        self.pushes: list[STKPushRequest] = []

    async def initiate_stk_push(self, request: STKPushRequest) -> STKPushResponse:
        self.pushes.append(request)
        n = len(self.pushes)
        return STKPushResponse(
            checkout_request_id=f"ws_CO_{n}",
            merchant_request_id=f"mreq_{n}",
            response_code="0",
            response_description="Success. Request accepted for processing",
            customer_message="Enter PIN",
        )


async def _set_config(session, tenant_id) -> None:
    await session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tenant_id)},
    )


async def _seed_arrived(
    session, tenant_id, *, payment_method: str, payment_status: str | None = None,
) -> tuple[UUID, UUID]:
    """Return (order_id, delivery_id) already in the `arrived` state."""
    await _set_config(session, tenant_id)
    tenant = await session.get(Tenant, tenant_id)
    tenant.settings = {}

    product = Product(tenant_id=tenant_id, name="Hoodie", status=ProductStatus.ACTIVE.value)
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        tenant_id=tenant_id,
        product_id=product.id,
        sku="HOODIE-POD",
        attributes=[],
        cost_price_cents=2500,
        sell_price_cents=5000,
        quantity=10,
        is_active=True,
    )
    session.add(variant)
    await session.flush()
    ledger = StockLedger(tenant_id=tenant_id, product_id=product.id, variant_id=variant.id)
    ledger.load([])
    await StockMovementRepository(session).add(
        ledger.record_receive(
            quantity=10, unit_cost_cents=2500, reference_id=None, reference_type="purchase_order",
        ),
    )

    zone = DeliveryZone(tenant_id=tenant_id, name="Zone", fee_cents=2000)
    session.add(zone)
    await session.flush()
    rider = Rider(tenant_id=tenant_id, phone=RIDER_PHONE, name="Rider", active=True)
    session.add(rider)
    await session.flush()

    customer = Customer(tenant_id=tenant_id, phone=PHONE)
    session.add(customer)
    await session.flush()
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone=PHONE,
        total_cents=5000,
        status="packed",
        payment_method=payment_method,
        payment_status=payment_status,
    )
    session.add(order)
    await session.flush()

    service = DeliveryService(session)
    delivery = await service.dispatch(
        tenant_id, order_id=order.id, rider_id=rider.id, zone_id=zone.id,
    )
    await service.mark_picked_up(tenant_id, delivery.id)
    await service.mark_arrived(tenant_id, delivery.id)
    await session.flush()
    return order.id, delivery.id


async def _arrived_delivery(session_factory, tenant_id, *, payment_method="mpesa_pod"):
    async with session_factory() as session, session.begin():
        return await _seed_arrived(session, tenant_id, payment_method=payment_method)


@pytest.fixture
def _callback_url(monkeypatch):
    monkeypatch.setattr(
        rider_flow,
        "get_settings",
        lambda: SimpleNamespace(DARAJA_CALLBACK_URL="https://example.test/cb"),
    )


@pytest.mark.asyncio
async def test_trigger_pushes_once_and_is_idempotent(
    tenant, session_factory, monkeypatch, _callback_url,
) -> None:
    order_id, delivery_id = await _arrived_delivery(session_factory, tenant.id)
    provider = FakeProvider()

    async def _resolver(_session: Any, _tenant_id: UUID):
        return provider

    first = await trigger_pod_collection(
        tenant_id=tenant.id,
        delivery_id=delivery_id,
        session_factory=session_factory,
        resolve_provider=_resolver,
    )
    # A retry (same event delivered twice) must not prompt again.
    second = await trigger_pod_collection(
        tenant_id=tenant.id,
        delivery_id=delivery_id,
        session_factory=session_factory,
        resolve_provider=_resolver,
    )

    assert first is True
    assert second is True  # same in-flight payment returned, not a new push
    assert len(provider.pushes) == 1
    assert provider.pushes[0].idempotency_key == f"delivery:{delivery_id}:arrived"

    async with session_factory() as session:
        await _set_config(session, tenant.id)
        payments = (
            (await session.execute(select(Payment).where(Payment.order_id == order_id)))
            .scalars()
            .all()
        )
        assert len(payments) == 1


@pytest.mark.asyncio
async def test_trigger_skips_already_paid_order(
    tenant, session_factory, monkeypatch, _callback_url,
) -> None:
    order_id, delivery_id = await _arrived_delivery(session_factory, tenant.id)
    async with session_factory() as session, session.begin():
        await _set_config(session, tenant.id)
        order = await session.get(Order, order_id)
        order.payment_status = "completed"

    provider = FakeProvider()

    async def _resolver(_session, _tenant_id):
        return provider

    result = await trigger_pod_collection(
        tenant_id=tenant.id,
        delivery_id=delivery_id,
        session_factory=session_factory,
        resolve_provider=_resolver,
    )
    assert result is False
    assert provider.pushes == []


@pytest.mark.asyncio
async def test_trigger_skips_prepay_order(
    tenant, session_factory, monkeypatch, _callback_url,
) -> None:
    _order_id, delivery_id = await _arrived_delivery(
        session_factory, tenant.id, payment_method="mpesa_stk",
    )
    provider = FakeProvider()

    async def _resolver(_session, _tenant_id):
        return provider

    result = await trigger_pod_collection(
        tenant_id=tenant.id,
        delivery_id=delivery_id,
        session_factory=session_factory,
        resolve_provider=_resolver,
    )
    assert result is False
    assert provider.pushes == []


@pytest.mark.asyncio
async def test_trigger_returns_false_when_callback_url_unset(
    tenant, session_factory, monkeypatch,
) -> None:
    monkeypatch.setattr(rider_flow, "get_settings", lambda: SimpleNamespace(DARAJA_CALLBACK_URL=""))
    _order_id, delivery_id = await _arrived_delivery(session_factory, tenant.id)
    provider = FakeProvider()

    async def _resolver(_session, _tenant_id):
        return provider

    result = await trigger_pod_collection(
        tenant_id=tenant.id,
        delivery_id=delivery_id,
        session_factory=session_factory,
        resolve_provider=_resolver,
    )
    assert result is False
    assert provider.pushes == []


@pytest.mark.asyncio
async def test_trigger_returns_false_for_unknown_delivery(
    tenant, session_factory, monkeypatch, _callback_url,
) -> None:
    async with session_factory() as session, session.begin():
        await _set_config(session, tenant.id)

    async def _resolver(_session, _tenant_id):  # pragma: no cover - must not be called
        raise AssertionError("provider must not be resolved for a missing delivery")

    result = await trigger_pod_collection(
        tenant_id=tenant.id,
        delivery_id=UUID(int=999),
        session_factory=session_factory,
        resolve_provider=_resolver,
    )
    assert result is False
