"""
Phase 4 reconciliation tests: the status-query fallback for missing/late
callbacks. A successful query result is never auto-credited (no verifiable
amount); it is flagged for review. Failures/cancellations are applied.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from packages.core.domain.order import OrderStatus
from packages.core.domain.payment import (
    FailureReason,
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
from packages.core.models import AuditLog
from packages.core.models.order import Customer, Order, PaymentMethod
from packages.core.models.payment import Payment, PaymentRecordStatus
from packages.core.payments import (
    StatementEntry,
    reconcile_statement,
    reconcile_tenant,
    stale_payments,
)


class FakeProvider(PaymentProvider):
    def __init__(self) -> None:
        self.query_result: ProviderPaymentResult | None = None
        self.query_calls: list[str] = []

    async def initiate_stk_push(self, request: DomainSTKPushRequest) -> DomainSTKPushResponse:
        raise AssertionError("not used in reconciliation tests")

    async def query_status(self, checkout_request_id: str) -> ProviderPaymentResult:
        self.query_calls.append(checkout_request_id)
        assert self.query_result is not None
        return self.query_result

    async def validate_callback(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        return True

    async def parse_callback(self, payload: dict[str, Any]) -> ProviderPaymentResult:
        raise AssertionError("not used in reconciliation tests")

    async def refund(self, mpesa_receipt_number: str, amount_cents: int, reason: str) -> bool:
        return False


async def _seed_payment(
    session: Any,
    tenant_id: UUID,
    *,
    status: PaymentRecordStatus = PaymentRecordStatus.INITIATED,
    created_at: datetime | None = None,
    checkout_request_id: str = "ws_CO_stale",
) -> Payment:
    phone = f"2547{abs(hash(checkout_request_id)) % 10**8:08d}"
    customer = Customer(tenant_id=tenant_id, phone=phone, name="Buyer")
    session.add(customer)
    await session.flush()
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone="254712345678",
        total_cents=500_000,
        status=OrderStatus.AWAITING_PAYMENT,
        payment_method=PaymentMethod.MPESA_STK,
    )
    session.add(order)
    await session.flush()
    payment = Payment(
        tenant_id=tenant_id,
        order_id=order.id,
        phone_number="254712345678",
        amount_cents=500_000,
        status=status,
        checkout_request_id=checkout_request_id,
        created_at=created_at or (datetime.now(UTC) - timedelta(hours=1)),
    )
    session.add(payment)
    await session.flush()
    return payment


async def test_stale_payments_selects_only_old_unsettled(tenant_session, tenant) -> None:
    old = await _seed_payment(tenant_session, tenant.id, checkout_request_id="ws_CO_old")
    await _seed_payment(
        tenant_session,
        tenant.id,
        status=PaymentRecordStatus.COMPLETED,
        checkout_request_id="ws_CO_done",
    )
    await _seed_payment(
        tenant_session,
        tenant.id,
        created_at=datetime.now(UTC),
        checkout_request_id="ws_CO_fresh",
    )

    stale = await stale_payments(tenant_session, tenant.id, older_than_seconds=900)
    ids = {p.id for p in stale}
    assert old.id in ids
    assert len(stale) == 1


async def test_reconcile_failure_applies(tenant_session, tenant) -> None:
    payment = await _seed_payment(tenant_session, tenant.id)
    provider = FakeProvider()
    provider.query_result = ProviderPaymentResult(
        status=ProviderPaymentStatus.FAILED,
        checkout_request_id=payment.checkout_request_id or "",
        result_code=1,
        result_desc="Insufficient funds",
        failure_reason=FailureReason.INSUFFICIENT_FUNDS,
    )
    queried = await reconcile_tenant(tenant_session, tenant.id, provider)
    assert queried == 1
    await tenant_session.refresh(payment)
    assert payment.status == PaymentRecordStatus.FAILED


async def test_reconcile_success_without_amount_requires_review(tenant_session, tenant) -> None:
    payment = await _seed_payment(tenant_session, tenant.id)
    provider = FakeProvider()
    provider.query_result = ProviderPaymentResult(
        status=ProviderPaymentStatus.COMPLETED,
        checkout_request_id=payment.checkout_request_id or "",
        result_code=0,
        result_desc="Success",
        amount_cents=None,  # status query has no amount to verify
    )
    await reconcile_tenant(tenant_session, tenant.id, provider)

    await tenant_session.refresh(payment)
    assert payment.status == PaymentRecordStatus.PENDING
    actions = (await tenant_session.execute(select(AuditLog.action))).scalars().all()
    assert "payment.reconciliation_review_required" in actions


async def test_reconcile_cancel_cancels_order(tenant_session, tenant) -> None:
    payment = await _seed_payment(tenant_session, tenant.id)
    provider = FakeProvider()
    provider.query_result = ProviderPaymentResult(
        status=ProviderPaymentStatus.CANCELLED,
        checkout_request_id=payment.checkout_request_id or "",
        result_code=1032,
        result_desc="Cancelled",
        failure_reason=FailureReason.CANCELLED_BY_USER,
    )
    await reconcile_tenant(tenant_session, tenant.id, provider)
    await tenant_session.refresh(payment)
    assert payment.status == PaymentRecordStatus.CANCELLED
    order = (await tenant_session.execute(select(Order))).scalar_one()
    assert order.status == OrderStatus.CANCELLED


async def _seed_completed(
    session: Any,
    tenant_id: UUID,
    *,
    receipt: str,
    amount_cents: int = 500_000,
) -> Payment:
    phone = f"2547{abs(hash(receipt)) % 10**8:08d}"
    customer = Customer(tenant_id=tenant_id, phone=phone, name="Buyer")
    session.add(customer)
    await session.flush()
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer.id,
        customer_phone=phone,
        total_cents=amount_cents,
        status=OrderStatus.PAID,
        payment_method=PaymentMethod.MPESA_STK,
        payment_status="completed",
    )
    session.add(order)
    await session.flush()
    payment = Payment(
        tenant_id=tenant_id,
        order_id=order.id,
        phone_number=phone,
        amount_cents=amount_cents,
        status=PaymentRecordStatus.COMPLETED,
        checkout_request_id=f"ws_CO_{receipt}",
        mpesa_receipt_number=receipt,
        completed_at=datetime.now(UTC),
    )
    session.add(payment)
    await session.flush()
    return payment


async def test_statement_all_matched(tenant_session, tenant) -> None:
    await _seed_completed(tenant_session, tenant.id, receipt="R1")
    await _seed_completed(tenant_session, tenant.id, receipt="R2")
    result = await reconcile_statement(
        tenant_session,
        tenant.id,
        [StatementEntry("R1", 500_000), StatementEntry("R2", 500_000)],
    )
    assert result.ok
    assert result.matched == 2


async def test_statement_missing_at_provider(tenant_session, tenant) -> None:
    await _seed_completed(tenant_session, tenant.id, receipt="R1")
    result = await reconcile_statement(tenant_session, tenant.id, [])
    kinds = {m.kind for m in result.mismatches}
    assert kinds == {"missing_at_provider"}


async def test_statement_missing_in_ledger(tenant_session, tenant) -> None:
    result = await reconcile_statement(
        tenant_session, tenant.id, [StatementEntry("GHOST", 500_000)]
    )
    kinds = {m.kind for m in result.mismatches}
    assert kinds == {"missing_in_ledger"}
    assert result.mismatches[0].provider_amount_cents == 500_000


async def test_statement_amount_mismatch_audited(tenant_session, tenant) -> None:
    await _seed_completed(tenant_session, tenant.id, receipt="R1", amount_cents=500_000)
    result = await reconcile_statement(tenant_session, tenant.id, [StatementEntry("R1", 499_000)])
    assert {m.kind for m in result.mismatches} == {"amount_mismatch"}
    actions = (await tenant_session.execute(select(AuditLog.action))).scalars().all()
    assert "payment.reconciliation_mismatch" in actions
