"""
Phase 4 payment service tests (service level, real Postgres, fake provider).

These exercise the deterministic core: order-state checks, idempotent STK
initiation, callback processing (success / cancel / fail / pending), amount
matching, duplicate-callback safety and audit/outbox side effects.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from socialerp.outbox import Outbox
from sqlalchemy import select

from packages.core.domain.order import OrderStatus
from packages.core.domain.payment import (
    CallbackVerificationError,
    FailureReason,
    PaymentProvider,
    PaymentProviderError,
    ProviderPaymentResult,
    ProviderPaymentStatus,
)
from packages.core.domain.payment import (
    STKPushRequest as DomainSTKPushRequest,
)
from packages.core.domain.payment import (
    STKPushResponse as DomainSTKPushResponse,
)
from packages.core.models import AuditLog
from packages.core.models.order import Customer, Order, PaymentMethod
from packages.core.models.payment import Payment, PaymentRecordStatus
from packages.core.orders.service import is_order_paid
from packages.core.payments import (
    OrderNotFoundError,
    OrderNotPayableError,
    PaymentNotFoundError,
    PaymentService,
)

TENANT_PHONE = "254712345678"


class FakeProvider(PaymentProvider):
    """Configurable provider double (no network)."""

    def __init__(self) -> None:
        self.valid_callback = True
        self.result: ProviderPaymentResult | None = None
        self.push_error: PaymentProviderError | None = None
        self.pushes: list[DomainSTKPushRequest] = []
        self._seq = 0

    async def initiate_stk_push(self, request: DomainSTKPushRequest) -> DomainSTKPushResponse:
        self.pushes.append(request)
        if self.push_error is not None:
            raise self.push_error
        self._seq += 1
        return DomainSTKPushResponse(
            checkout_request_id=f"ws_CO_{self._seq}",
            merchant_request_id=f"mreq_{self._seq}",
            response_code="0",
            response_description="Success. Request accepted for processing",
            customer_message="Enter PIN",
        )

    async def query_status(self, checkout_request_id: str) -> ProviderPaymentResult:
        assert self.result is not None
        return self.result

    async def validate_callback(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        return self.valid_callback

    async def parse_callback(self, payload: dict[str, Any]) -> ProviderPaymentResult:
        assert self.result is not None
        return self.result

    async def refund(self, mpesa_receipt_number: str, amount_cents: int, reason: str) -> bool:
        return False


async def _make_order(
    session: Any,
    tenant_id: UUID,
    *,
    status: OrderStatus = OrderStatus.AWAITING_PAYMENT,
    total_cents: int = 500_000,
) -> Order:
    customer = Customer(tenant_id=tenant_id, phone=TENANT_PHONE, name="Test Buyer")
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
    return order


async def _initiate(session: Any, tenant_id: UUID, order: Order, provider: FakeProvider) -> Payment:
    service = PaymentService(session, provider)
    return await service.initiate_stk_payment(
        tenant_id=tenant_id,
        order_id=order.id,
        phone_number=TENANT_PHONE,
        callback_url="https://example.test/callback",
        idempotency_key=f"stk:{order.id}",
    )


# =============================================================================
# initiate
# =============================================================================


async def test_initiate_creates_initiated_payment(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    payment = await _initiate(tenant_session, tenant.id, order, provider)

    assert payment.status == PaymentRecordStatus.INITIATED
    assert payment.checkout_request_id == "ws_CO_1"
    assert payment.amount_cents == 500_000
    assert payment.order_id == order.id
    assert provider.pushes[0].amount_cents == 500_000

    events = (await tenant_session.execute(select(Outbox.event_type))).scalars().all()
    assert "payment.initiated" in events


async def test_initiate_is_idempotent_for_same_key(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    first = await _initiate(tenant_session, tenant.id, order, provider)
    second = await _initiate(tenant_session, tenant.id, order, provider)

    assert first.id == second.id
    assert len(provider.pushes) == 1


async def test_initiate_rejects_non_payable_order(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id, status=OrderStatus.DRAFT)
    service = PaymentService(tenant_session, provider)
    with pytest.raises(OrderNotPayableError):
        await service.initiate_stk_payment(
            tenant_id=tenant.id,
            order_id=order.id,
            phone_number=TENANT_PHONE,
            callback_url="https://example.test/callback",
            idempotency_key="k1",
        )


async def test_initiate_unknown_order_raises(tenant_session, tenant) -> None:
    provider = FakeProvider()
    service = PaymentService(tenant_session, provider)
    with pytest.raises(OrderNotFoundError):
        await service.initiate_stk_payment(
            tenant_id=tenant.id,
            order_id=UUID("00000000-0000-0000-0000-000000000000"),
            phone_number=TENANT_PHONE,
            callback_url="https://example.test/callback",
            idempotency_key="k1",
        )


async def test_initiate_provider_failure_marks_failed(tenant_session, tenant) -> None:
    provider = FakeProvider()
    provider.push_error = PaymentProviderError("boom")
    order = await _make_order(tenant_session, tenant.id)
    service = PaymentService(tenant_session, provider)
    with pytest.raises(PaymentProviderError):
        await service.initiate_stk_payment(
            tenant_id=tenant.id,
            order_id=order.id,
            phone_number=TENANT_PHONE,
            callback_url="https://example.test/callback",
            idempotency_key="k1",
        )
    payments = (await tenant_session.execute(select(Payment))).scalars().all()
    assert len(payments) == 1
    assert payments[0].status == PaymentRecordStatus.FAILED


# =============================================================================
# callback: success
# =============================================================================


def _success_result(checkout_request_id: str, amount_cents: int) -> ProviderPaymentResult:
    return ProviderPaymentResult(
        status=ProviderPaymentStatus.COMPLETED,
        checkout_request_id=checkout_request_id,
        merchant_request_id="mreq_1",
        mpesa_receipt_number="MPE123456789",
        result_code=0,
        result_desc="The service request is processed successfully.",
        amount_cents=amount_cents,
        phone_number=TENANT_PHONE,
    )


async def test_callback_success_completes_payment_and_order(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    payment = await _initiate(tenant_session, tenant.id, order, provider)

    provider.result = _success_result(payment.checkout_request_id or "", 500_000)
    service = PaymentService(tenant_session, provider)
    updated = await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    assert updated.status == PaymentRecordStatus.COMPLETED
    assert updated.mpesa_receipt_number == "MPE123456789"
    await tenant_session.refresh(order)
    assert order.status == OrderStatus.PAID
    assert order.payment_status == "completed"
    assert order.paid_at is not None

    events = (await tenant_session.execute(select(Outbox.event_type))).scalars().all()
    assert events.count("payment.completed") == 1


async def test_duplicate_success_callback_does_not_double_credit(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    payment = await _initiate(tenant_session, tenant.id, order, provider)
    provider.result = _success_result(payment.checkout_request_id or "", 500_000)
    service = PaymentService(tenant_session, provider)

    first = await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})
    await tenant_session.refresh(order)
    paid_at = order.paid_at
    second = await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    assert first.id == second.id
    await tenant_session.refresh(order)
    assert order.paid_at == paid_at
    events = (await tenant_session.execute(select(Outbox.event_type))).scalars().all()
    assert events.count("payment.completed") == 1


async def test_second_stk_attempt_cannot_double_credit_paid_order(tenant_session, tenant) -> None:
    """Two STK attempts for one order: the second success must not credit again."""
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    service = PaymentService(tenant_session, provider)

    first = await service.initiate_stk_payment(
        tenant_id=tenant.id,
        order_id=order.id,
        phone_number=TENANT_PHONE,
        callback_url="https://example.test/callback",
        idempotency_key="stk:1",
    )
    second = await service.initiate_stk_payment(
        tenant_id=tenant.id,
        order_id=order.id,
        phone_number=TENANT_PHONE,
        callback_url="https://example.test/callback",
        idempotency_key="stk:2",
    )

    # First attempt succeeds.
    provider.result = _success_result(first.checkout_request_id or "", 500_000)
    await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})
    await tenant_session.refresh(order)
    paid_at = order.paid_at

    # Second attempt also reports success (customer double-charged at M-Pesa).
    provider.result = _success_result(second.checkout_request_id or "", 500_000)
    await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    await tenant_session.refresh(order)
    assert order.paid_at == paid_at  # not re-credited
    events = (await tenant_session.execute(select(Outbox.event_type))).scalars().all()
    assert events.count("payment.completed") == 1

    await tenant_session.refresh(second)
    assert second.status == PaymentRecordStatus.COMPLETED
    assert second.metadata_json.get("duplicate_payment") is True

    actions = (await tenant_session.execute(select(AuditLog.action))).scalars().all()
    assert "payment.duplicate_detected" in actions


async def test_initiate_refuses_stk_for_already_paid_pod_order(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id, status=OrderStatus.OUT_FOR_DELIVERY)
    order.payment_method = PaymentMethod.MPESA_PAY_ON_DELIVERY.value
    order.payment_status = "completed"
    await tenant_session.flush()

    service = PaymentService(tenant_session, provider)
    with pytest.raises(OrderNotPayableError):
        await service.initiate_stk_payment(
            tenant_id=tenant.id,
            order_id=order.id,
            phone_number=TENANT_PHONE,
            callback_url="https://example.test/callback",
            idempotency_key="stk:pod-retry",
        )


async def _two_attempts_with_first_paid(
    tenant_session, tenant
) -> tuple[PaymentService, Order, Payment, Payment]:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    service = PaymentService(tenant_session, provider)
    first = await service.initiate_stk_payment(
        tenant_id=tenant.id,
        order_id=order.id,
        phone_number=TENANT_PHONE,
        callback_url="https://example.test/callback",
        idempotency_key="stk:1",
    )
    second = await service.initiate_stk_payment(
        tenant_id=tenant.id,
        order_id=order.id,
        phone_number=TENANT_PHONE,
        callback_url="https://example.test/callback",
        idempotency_key="stk:2",
    )
    provider.result = _success_result(first.checkout_request_id or "", 500_000)
    await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})
    await tenant_session.refresh(order)
    assert is_order_paid(order)
    return service, order, first, second


async def test_stale_cancel_callback_does_not_downgrade_paid_order(tenant_session, tenant) -> None:
    """A late cancel for a *different* attempt must not un-pay a paid order (B5)."""
    service, order, _, second = await _two_attempts_with_first_paid(tenant_session, tenant)
    paid_at = order.paid_at

    await service.apply_query_result(
        tenant_id=tenant.id,
        result=ProviderPaymentResult(
            status=ProviderPaymentStatus.CANCELLED,
            checkout_request_id=second.checkout_request_id or "",
            result_code=1032,
            result_desc="Request cancelled by user",
            failure_reason=FailureReason.CANCELLED_BY_USER,
        ),
    )
    await tenant_session.refresh(order)
    assert order.payment_status == "completed"
    assert order.paid_at == paid_at

    # ...and the paid order still refuses a fresh STK push (no double charge).
    with pytest.raises(OrderNotPayableError):
        await service.initiate_stk_payment(
            tenant_id=tenant.id,
            order_id=order.id,
            phone_number=TENANT_PHONE,
            callback_url="https://example.test/callback",
            idempotency_key="stk:3",
        )


async def test_stale_failure_callback_does_not_downgrade_paid_order(tenant_session, tenant) -> None:
    service, order, _, second = await _two_attempts_with_first_paid(tenant_session, tenant)
    paid_at = order.paid_at

    await service.apply_query_result(
        tenant_id=tenant.id,
        result=ProviderPaymentResult(
            status=ProviderPaymentStatus.FAILED,
            checkout_request_id=second.checkout_request_id or "",
            result_code=2001,
            result_desc="The user entered an incorrect PIN",
            failure_reason=FailureReason.WRONG_PIN,
        ),
    )
    await tenant_session.refresh(order)
    assert order.payment_status == "completed"
    assert order.paid_at == paid_at


def _cancelled_result(checkout_request_id: str) -> ProviderPaymentResult:
    return ProviderPaymentResult(
        status=ProviderPaymentStatus.CANCELLED,
        checkout_request_id=checkout_request_id,
        result_code=1032,
        result_desc="Request cancelled by user",
        failure_reason=FailureReason.CANCELLED_BY_USER,
    )


@pytest.mark.parametrize(
    "terminal_status",
    [OrderStatus.CANCELLED, OrderStatus.FAILED_DELIVERY],
)
async def test_success_callback_on_terminal_order_is_captured_not_lost(
    tenant_session, tenant, terminal_status
) -> None:
    """A late success on a cancelled/failed order must not 500 or vanish.

    The money was collected. Raising IllegalTransitionError would roll the
    callback back (500) and lose the collection to infinite retries. But the
    order must NOT be marked collected either: that would let a POD delivery
    proceed with no sale ever recorded. Capture the collection on the Payment,
    leave the order alone, and raise a refund/review signal.
    """
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    payment = await _initiate(tenant_session, tenant.id, order, provider)
    order.status = terminal_status.value
    await tenant_session.flush()

    provider.result = _success_result(payment.checkout_request_id or "", 500_000)
    service = PaymentService(tenant_session, provider)
    updated = await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    assert updated.status == PaymentRecordStatus.COMPLETED
    assert updated.metadata_json.get("collected_after_terminal") is True
    await tenant_session.refresh(order)
    assert order.status == terminal_status.value  # not illegally transitioned
    # The order is deliberately NOT marked collected: the POD delivery gate
    # must keep blocking until a human resolves the collection.
    assert not is_order_paid(order)
    assert order.paid_at is None

    actions = (await tenant_session.execute(select(AuditLog.action))).scalars().all()
    assert "payment.collected_after_terminal" in actions
    events = (await tenant_session.execute(select(Outbox.event_type))).scalars().all()
    assert "payment.collected_after_terminal" in events
    assert "payment.completed" not in events


async def test_pod_packed_late_success_is_captured_and_not_collected(
    tenant_session, tenant
) -> None:
    """POD order packed before transit + late success: capture, keep gate closed.

    This is the state where the fail-safe actually protects physical goods: the
    order is not marked collected, so mark_delivered stays blocked and stock
    cannot ship without a recorded sale.
    """
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)  # awaiting_payment
    order.payment_method = PaymentMethod.MPESA_PAY_ON_DELIVERY.value
    await tenant_session.flush()
    payment = await _initiate(tenant_session, tenant.id, order, provider)

    order.status = OrderStatus.PACKED.value
    await tenant_session.flush()

    provider.result = _success_result(payment.checkout_request_id or "", 500_000)
    service = PaymentService(tenant_session, provider)
    updated = await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    assert updated.status == PaymentRecordStatus.COMPLETED
    assert updated.metadata_json.get("collected_after_terminal") is True
    await tenant_session.refresh(order)
    assert order.status == OrderStatus.PACKED.value
    assert not is_order_paid(order)  # POD delivery gate stays closed
    assert order.paid_at is None
    events = (await tenant_session.execute(select(Outbox.event_type))).scalars().all()
    assert "payment.collected_after_terminal" in events
    assert "payment.completed" not in events


async def test_cancel_of_retry_attempt_does_not_cancel_order(tenant_session, tenant) -> None:
    """A retry attempt's cancel must not cancel the order (other attempt in flight)."""
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    service = PaymentService(tenant_session, provider)
    await service.initiate_stk_payment(
        tenant_id=tenant.id,
        order_id=order.id,
        phone_number=TENANT_PHONE,
        callback_url="https://example.test/callback",
        idempotency_key="stk:1",
    )
    second = await service.initiate_stk_payment(
        tenant_id=tenant.id,
        order_id=order.id,
        phone_number=TENANT_PHONE,
        callback_url="https://example.test/callback",
        idempotency_key="stk:2",
    )

    provider.result = _cancelled_result(second.checkout_request_id or "")
    await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})
    await tenant_session.refresh(order)

    assert order.status == OrderStatus.AWAITING_PAYMENT.value
    assert order.payment_status != "failed"
    actions = (await tenant_session.execute(select(AuditLog.action))).scalars().all()
    assert "payment.cancel_deferred" in actions


async def test_concurrent_success_and_cancel_callbacks_cannot_downgrade_paid_order(
    session_factory, tenant
) -> None:
    """A cancel callback must not downgrade an order a success callback paid.

    This forces the dangerous interleave: the cancel callback reads the order,
    then -- while it is mid-flight -- the success callback completes and pays.
    The cancel callback then resumes and must NOT overwrite the paid order.
    Passes only with both the order-row lock (no stale read) and the
    don't-cancel-while-another-attempt-is-in-flight guard.
    """
    provider = FakeProvider()
    async with session_factory() as session, session.begin():
        order = await _make_order(session, tenant.id)
        service = PaymentService(session, provider)
        first = await service.initiate_stk_payment(
            tenant_id=tenant.id,
            order_id=order.id,
            phone_number=TENANT_PHONE,
            callback_url="https://example.test/callback",
            idempotency_key="stk:1",
        )
        second = await service.initiate_stk_payment(
            tenant_id=tenant.id,
            order_id=order.id,
            phone_number=TENANT_PHONE,
            callback_url="https://example.test/callback",
            idempotency_key="stk:2",
        )
        order_id = order.id
        first_cid = first.checkout_request_id or ""
        second_cid = second.checkout_request_id or ""

    success_provider = FakeProvider()
    success_provider.result = _success_result(first_cid, 500_000)
    cancel_provider = FakeProvider()
    cancel_provider.result = _cancelled_result(second_cid)

    cancel_read = asyncio.Event()
    cancel_may_write = asyncio.Event()

    async def run_cancel() -> None:
        async with session_factory() as session, session.begin():
            service = PaymentService(session, cancel_provider)
            original = service._get_order

            async def pausing_get_order(tenant_id, order_id, *, for_update=False):
                order = await original(tenant_id, order_id, for_update=for_update)
                cancel_read.set()
                await cancel_may_write.wait()
                return order

            service._get_order = pausing_get_order  # type: ignore[method-assign]
            await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    async def run_success() -> None:
        await cancel_read.wait()
        async with session_factory() as session, session.begin():
            service = PaymentService(session, success_provider)
            await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    cancel_task = asyncio.create_task(run_cancel())
    await asyncio.wait_for(cancel_read.wait(), timeout=10)
    success_task = asyncio.create_task(run_success())
    # The success callback must block on the order lock the cancel callback holds.
    await asyncio.sleep(0.2)
    cancel_may_write.set()
    await asyncio.wait_for(asyncio.gather(cancel_task, success_task), timeout=10)

    async with session_factory() as session:
        order = await session.get(Order, order_id)
        assert order is not None
        assert is_order_paid(order)
        assert order.payment_status == "completed"
        assert order.status == OrderStatus.PAID.value
        assert order.cancelled_reason is None
        attempts = (
            (await session.execute(select(Payment).where(Payment.order_id == order_id)))
            .scalars()
            .all()
        )
        by_status = sorted(p.status for p in attempts)
        assert by_status == [
            PaymentRecordStatus.CANCELLED.value,
            PaymentRecordStatus.COMPLETED.value,
        ]


# =============================================================================
# callback: wrong amount
# =============================================================================


async def test_callback_wrong_amount_does_not_credit(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    payment = await _initiate(tenant_session, tenant.id, order, provider)

    provider.result = _success_result(payment.checkout_request_id or "", 100)
    service = PaymentService(tenant_session, provider)
    updated = await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    assert updated.status == PaymentRecordStatus.FAILED
    assert updated.failure_reason == FailureReason.WRONG_AMOUNT.value
    await tenant_session.refresh(order)
    assert order.status == OrderStatus.AWAITING_PAYMENT
    assert order.paid_at is None

    actions = (await tenant_session.execute(select(AuditLog.action))).scalars().all()
    assert "payment.amount_mismatch" in actions


# =============================================================================
# callback: cancelled / failed / pending
# =============================================================================


async def test_callback_cancelled_cancels_order(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    payment = await _initiate(tenant_session, tenant.id, order, provider)
    provider.result = ProviderPaymentResult(
        status=ProviderPaymentStatus.CANCELLED,
        checkout_request_id=payment.checkout_request_id or "",
        result_code=1032,
        result_desc="Request cancelled by user",
        failure_reason=FailureReason.CANCELLED_BY_USER,
    )
    service = PaymentService(tenant_session, provider)
    updated = await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    assert updated.status == PaymentRecordStatus.CANCELLED
    await tenant_session.refresh(order)
    assert order.status == OrderStatus.CANCELLED


async def test_callback_insufficient_funds_keeps_order_payable(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    payment = await _initiate(tenant_session, tenant.id, order, provider)
    provider.result = ProviderPaymentResult(
        status=ProviderPaymentStatus.FAILED,
        checkout_request_id=payment.checkout_request_id or "",
        result_code=1,
        result_desc="The balance is insufficient",
        failure_reason=FailureReason.INSUFFICIENT_FUNDS,
    )
    service = PaymentService(tenant_session, provider)
    updated = await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})

    assert updated.status == PaymentRecordStatus.FAILED
    assert updated.failure_reason == FailureReason.INSUFFICIENT_FUNDS.value
    await tenant_session.refresh(order)
    assert order.status == OrderStatus.AWAITING_PAYMENT


async def test_callback_pending_marks_pending(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    payment = await _initiate(tenant_session, tenant.id, order, provider)
    provider.result = ProviderPaymentResult(
        status=ProviderPaymentStatus.PENDING,
        checkout_request_id=payment.checkout_request_id or "",
        result_code=1001,
        result_desc="Transaction is being processed",
    )
    service = PaymentService(tenant_session, provider)
    updated = await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})
    assert updated.status == PaymentRecordStatus.PENDING


# =============================================================================
# callback: verification / unknown
# =============================================================================


async def test_callback_invalid_signature_rejected(tenant_session, tenant) -> None:
    provider = FakeProvider()
    order = await _make_order(tenant_session, tenant.id)
    payment = await _initiate(tenant_session, tenant.id, order, provider)
    provider.valid_callback = False
    provider.result = _success_result(payment.checkout_request_id or "", 500_000)
    service = PaymentService(tenant_session, provider)
    with pytest.raises(CallbackVerificationError):
        await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})


async def test_callback_unknown_checkout_raises(tenant_session, tenant) -> None:
    provider = FakeProvider()
    provider.result = _success_result("ws_CO_unknown", 500_000)
    service = PaymentService(tenant_session, provider)
    with pytest.raises(PaymentNotFoundError):
        await service.handle_callback(tenant_id=tenant.id, payload={"Body": {}}, headers={})
