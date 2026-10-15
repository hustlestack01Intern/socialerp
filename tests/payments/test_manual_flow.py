"""
Phase 4 manual-provider end-to-end: initiate a manual payment, then confirm it
through the callback path. Proves the fallback reuses the same money-safe service
(amount matching, order transition, idempotency) as Daraja.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from socialerp.outbox import Outbox
from sqlalchemy import select

from packages.core.domain.order import OrderStatus
from packages.core.models.order import Customer, Order, PaymentMethod
from packages.core.models.payment import PaymentRecordStatus
from packages.core.payments import PaymentService
from packages.integrations.manual import ManualPaymentProvider


async def _make_order(session: Any, tenant_id: UUID, total_cents: int = 500_000) -> Order:
    customer = Customer(tenant_id=tenant_id, phone="254712345678", name="Buyer")
    session.add(customer)
    await session.flush()
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone="254712345678",
        total_cents=total_cents,
        status=OrderStatus.AWAITING_PAYMENT,
        payment_method=PaymentMethod.MPESA_STK,
    )
    session.add(order)
    await session.flush()
    return order


async def test_manual_flow_confirms_and_pays(tenant_session, tenant) -> None:
    provider = ManualPaymentProvider(callback_token="tok", till="123456")
    order = await _make_order(tenant_session, tenant.id)
    service = PaymentService(tenant_session, provider)

    payment = await service.initiate_stk_payment(
        tenant_id=tenant.id,
        order_id=order.id,
        phone_number="254712345678",
        callback_url="https://example.test/callback",
        idempotency_key="k1",
    )
    assert payment.checkout_request_id is not None
    assert payment.checkout_request_id.startswith("MANUAL-")
    assert payment.status == PaymentRecordStatus.INITIATED

    await service.handle_callback(
        tenant_id=tenant.id,
        payload={
            "checkout_request_id": payment.checkout_request_id,
            "mpesa_receipt_number": "REC999",
            "amount_cents": 500_000,
        },
        headers={"x-callback-token": "tok"},
    )
    await tenant_session.refresh(payment)
    await tenant_session.refresh(order)
    assert payment.status == PaymentRecordStatus.COMPLETED
    assert payment.mpesa_receipt_number == "REC999"
    assert order.status == OrderStatus.PAID.value
    events = (await tenant_session.execute(select(Outbox.event_type))).scalars().all()
    assert "payment.completed" in events


async def test_manual_flow_rejects_wrong_token(tenant_session, tenant) -> None:
    provider = ManualPaymentProvider(callback_token="tok")
    order = await _make_order(tenant_session, tenant.id)
    service = PaymentService(tenant_session, provider)
    payment = await service.initiate_stk_payment(
        tenant_id=tenant.id,
        order_id=order.id,
        phone_number="254712345678",
        callback_url="https://example.test/callback",
        idempotency_key="k1",
    )

    import pytest

    from packages.core.domain.payment import CallbackVerificationError

    with pytest.raises(CallbackVerificationError):
        await service.handle_callback(
            tenant_id=tenant.id,
            payload={
                "checkout_request_id": payment.checkout_request_id,
                "mpesa_receipt_number": "REC999",
                "amount_cents": 500_000,
            },
            headers={"x-callback-token": "wrong"},
        )


async def test_manual_flow_amount_mismatch_not_credited(tenant_session, tenant) -> None:
    provider = ManualPaymentProvider(callback_token="tok")
    order = await _make_order(tenant_session, tenant.id)
    service = PaymentService(tenant_session, provider)
    payment = await service.initiate_stk_payment(
        tenant_id=tenant.id,
        order_id=order.id,
        phone_number="254712345678",
        callback_url="https://example.test/callback",
        idempotency_key="k1",
    )

    await service.handle_callback(
        tenant_id=tenant.id,
        payload={
            "checkout_request_id": payment.checkout_request_id,
            "mpesa_receipt_number": "REC999",
            "amount_cents": 400_000,  # less than the order total
        },
        headers={"x-callback-token": "tok"},
    )
    await tenant_session.refresh(payment)
    await tenant_session.refresh(order)
    assert payment.status == PaymentRecordStatus.FAILED
    assert order.status == OrderStatus.AWAITING_PAYMENT.value
