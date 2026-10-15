"""
Phase 4 refund tests: human-approved refunds only.

Daraja has no self-serve refund API, so a provider that cannot refund must
surface a MANUAL_REQUIRED outcome (audited) rather than faking a credit. These
tests cover the approval gate, state guards, success, manual fallback and
provider decline.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from socialerp.outbox import Outbox
from sqlalchemy import select

from packages.core.domain.order import OrderStatus
from packages.core.domain.payment import (
    NotSupportedError,
    PaymentProvider,
    ProviderPaymentResult,
)
from packages.core.domain.payment import (
    STKPushRequest as DomainSTKPushRequest,
)
from packages.core.domain.payment import (
    STKPushResponse as DomainSTKPushResponse,
)
from packages.core.models import AuditLog
from packages.core.models.core import User, UserRole
from packages.core.models.order import Customer, Order, PaymentMethod
from packages.core.models.payment import Payment, PaymentRecordStatus
from packages.core.payments import (
    PaymentService,
    RefundError,
    RefundNotAllowedError,
    RefundNotApprovedError,
    RefundOutcome,
)


class RefundFakeProvider(PaymentProvider):
    def __init__(self) -> None:
        self.refund_accepted = True
        self.refund_raises: Exception | None = None
        self.refund_calls: list[tuple[str, int, str]] = []

    async def initiate_stk_push(self, request: DomainSTKPushRequest) -> DomainSTKPushResponse:
        raise AssertionError("not used")

    async def query_status(self, checkout_request_id: str) -> ProviderPaymentResult:
        raise AssertionError("not used")

    async def validate_callback(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        return True

    async def parse_callback(self, payload: dict[str, Any]) -> ProviderPaymentResult:
        raise AssertionError("not used")

    async def refund(self, mpesa_receipt_number: str, amount_cents: int, reason: str) -> bool:
        self.refund_calls.append((mpesa_receipt_number, amount_cents, reason))
        if self.refund_raises is not None:
            raise self.refund_raises
        return self.refund_accepted


async def _seed_approver(session: Any, tenant_id: UUID):
    user = User(
        tenant_id=tenant_id,
        email=f"approver-{uuid4().hex[:8]}@example.com",
        password_hash="x",
        full_name="Approver",
        role=UserRole.OWNER,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_completed_payment(
    session: Any,
    tenant_id: UUID,
    *,
    status: PaymentRecordStatus = PaymentRecordStatus.COMPLETED,
    order_status: OrderStatus = OrderStatus.PAID,
    amount_cents: int = 500_000,
    receipt: str | None = "REC123XYZ",
) -> tuple[Order, Payment]:
    customer = Customer(tenant_id=tenant_id, phone="254712345678", name="Buyer")
    session.add(customer)
    await session.flush()
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone="254712345678",
        total_cents=amount_cents,
        status=order_status,
        payment_method=PaymentMethod.MPESA_STK,
        payment_status="completed",
    )
    session.add(order)
    await session.flush()
    payment = Payment(
        tenant_id=tenant_id,
        order_id=order.id,
        phone_number="254712345678",
        amount_cents=amount_cents,
        status=status,
        checkout_request_id="ws_CO_refund",
        mpesa_receipt_number=receipt,
    )
    session.add(payment)
    await session.flush()
    return order, payment


async def test_refund_requires_approver(tenant_session, tenant) -> None:
    _, payment = await _seed_completed_payment(tenant_session, tenant.id)
    service = PaymentService(tenant_session, RefundFakeProvider())
    with pytest.raises(RefundNotApprovedError):
        await service.refund_payment(
            tenant_id=tenant.id,
            payment_id=payment.id,
            approved_by=None,
            reason="customer complaint",
        )


async def test_refund_rejects_non_completed_payment(tenant_session, tenant) -> None:
    _, payment = await _seed_completed_payment(
        tenant_session, tenant.id, status=PaymentRecordStatus.PENDING
    )
    service = PaymentService(tenant_session, RefundFakeProvider())
    with pytest.raises(RefundNotAllowedError):
        await service.refund_payment(
            tenant_id=tenant.id,
            payment_id=payment.id,
            approved_by=uuid4(),
            reason="oops",
        )


async def test_refund_rejects_overpaid_amount(tenant_session, tenant) -> None:
    _, payment = await _seed_completed_payment(tenant_session, tenant.id)
    service = PaymentService(tenant_session, RefundFakeProvider())
    with pytest.raises(RefundError):
        await service.refund_payment(
            tenant_id=tenant.id,
            payment_id=payment.id,
            approved_by=uuid4(),
            reason="too much",
            amount_cents=payment.amount_cents + 1,
        )


async def test_refund_success_transitions_and_audits(tenant_session, tenant) -> None:
    order, payment = await _seed_completed_payment(tenant_session, tenant.id)
    approver = (await _seed_approver(tenant_session, tenant.id)).id
    provider = RefundFakeProvider()
    service = PaymentService(tenant_session, provider)

    result = await service.refund_payment(
        tenant_id=tenant.id,
        payment_id=payment.id,
        approved_by=approver,
        reason="damaged goods",
    )

    assert result.outcome is RefundOutcome.REFUNDED
    await tenant_session.refresh(payment)
    await tenant_session.refresh(order)
    assert payment.status == PaymentRecordStatus.REFUNDED
    assert order.status == OrderStatus.REFUNDED.value
    assert order.payment_status == "refunded"
    assert provider.refund_calls == [("REC123XYZ", 500_000, "damaged goods")]

    events = (await tenant_session.execute(select(Outbox.event_type))).scalars().all()
    assert "payment.refunded" in events
    actions = (await tenant_session.execute(select(AuditLog.action))).scalars().all()
    assert "payment.refunded" in actions


async def test_refund_manual_required_when_provider_cannot(tenant_session, tenant) -> None:
    order, payment = await _seed_completed_payment(tenant_session, tenant.id)
    provider = RefundFakeProvider()
    provider.refund_raises = NotSupportedError("no refund api")
    service = PaymentService(tenant_session, provider)

    result = await service.refund_payment(
        tenant_id=tenant.id,
        payment_id=payment.id,
        approved_by=(await _seed_approver(tenant_session, tenant.id)).id,
        reason="manual",
    )

    assert result.outcome is RefundOutcome.MANUAL_REQUIRED
    await tenant_session.refresh(payment)
    await tenant_session.refresh(order)
    assert payment.status == PaymentRecordStatus.COMPLETED  # not faked
    assert order.status == OrderStatus.PAID.value
    actions = (await tenant_session.execute(select(AuditLog.action))).scalars().all()
    assert "payment.refund_manual_required" in actions


async def test_refund_provider_decline_raises(tenant_session, tenant) -> None:
    _, payment = await _seed_completed_payment(tenant_session, tenant.id)
    provider = RefundFakeProvider()
    provider.refund_accepted = False
    service = PaymentService(tenant_session, provider)

    with pytest.raises(RefundError):
        await service.refund_payment(
            tenant_id=tenant.id,
            payment_id=payment.id,
            approved_by=(await _seed_approver(tenant_session, tenant.id)).id,
            reason="no",
        )
    await tenant_session.refresh(payment)
    assert payment.status == PaymentRecordStatus.COMPLETED
    actions = (await tenant_session.execute(select(AuditLog.action))).scalars().all()
    assert "payment.refund_failed" in actions


async def test_refund_is_idempotent(tenant_session, tenant) -> None:
    _, payment = await _seed_completed_payment(
        tenant_session, tenant.id, status=PaymentRecordStatus.REFUNDED
    )
    provider = RefundFakeProvider()
    service = PaymentService(tenant_session, provider)

    result = await service.refund_payment(
        tenant_id=tenant.id,
        payment_id=payment.id,
        approved_by=uuid4(),
        reason="again",
    )
    assert result.outcome is RefundOutcome.REFUNDED
    assert provider.refund_calls == []
