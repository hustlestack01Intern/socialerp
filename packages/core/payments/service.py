"""
Payment Service (Phase 4)

Orchestrates M-Pesa STK Push payments against orders:

- ``initiate_stk_payment``: validate the order is payable, create a payment
  attempt, ask the provider to push, and record the correlation ids.
- ``handle_callback``: verify authenticity, match the amount against the order
  (never trust callback amounts), apply idempotency, move the order through the
  state machine, and emit domain events via the outbox.

The LLM never participates. All arithmetic and state changes are deterministic
and tested. Stock release on cancellation is owned by the reservation service
(Phase 3/6) and is intentionally not duplicated here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from socialerp.outbox import add_outbox_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.domain.order import (
    IllegalTransitionError,
    OrderStatus,
    is_legal_transition,
)
from packages.core.domain.payment import (
    CallbackVerificationError,
    FailureReason,
    PaymentProvider,
    PaymentProviderError,
    ProviderPaymentStatus,
    STKPushRequest,
)
from packages.core.models.order import Order
from packages.core.models.payment import Payment, PaymentRecordStatus


class PaymentError(Exception):
    """Base error for the payment service."""


class OrderNotFoundError(PaymentError):
    """The order does not exist for this tenant."""


class OrderNotPayableError(PaymentError):
    """The order is not in a state that accepts an STK push."""


class PaymentNotFoundError(PaymentError):
    """No payment attempt matches the given checkout request id."""


class MalformedCallbackError(PaymentError):
    """The callback payload could not be parsed."""


# Terminal payment states: a callback arriving after one of these is a
# duplicate/late callback and must be a safe no-op (idempotency).
_TERMINAL_STATES = frozenset({
    PaymentRecordStatus.COMPLETED,
    PaymentRecordStatus.FAILED,
    PaymentRecordStatus.CANCELLED,
    PaymentRecordStatus.PARTIAL,
    PaymentRecordStatus.REFUNDED,
})


class PaymentService:
    """Application service for M-Pesa STK payments."""

    def __init__(self, session: AsyncSession, provider: PaymentProvider) -> None:
        self.session = session
        self.provider = provider
        self.audit = AuditLogger(session)

    # -- helpers -----------------------------------------------------------

    async def _get_order(self, tenant_id: UUID, order_id: UUID) -> Order:
        stmt = select(Order).where(Order.id == order_id, Order.tenant_id == tenant_id)
        order = (await self.session.execute(stmt)).scalar_one_or_none()
        if order is None:
            raise OrderNotFoundError(f"Order {order_id} not found")
        return order

    @staticmethod
    def _transition(order: Order, new_status: OrderStatus) -> None:
        current = OrderStatus(order.status)
        if not is_legal_transition(current, new_status):
            raise IllegalTransitionError(
                f"Illegal transition: {current.value} -> {new_status.value}"
            )
        now = datetime.now(UTC)
        order.status = new_status.value
        if new_status is OrderStatus.PAID:
            order.paid_at = now
        elif new_status is OrderStatus.CANCELLED:
            order.cancelled_at = now

    # -- initiate ----------------------------------------------------------

    async def initiate_stk_payment(
        self,
        *,
        tenant_id: UUID,
        order_id: UUID,
        phone_number: str,
        callback_url: str,
        idempotency_key: str,
    ) -> Payment:
        """Create an STK push attempt for a payable order."""
        order = await self._get_order(tenant_id, order_id)
        if order.status != OrderStatus.AWAITING_PAYMENT.value:
            raise OrderNotPayableError(
                f"Order {order_id} is {order.status!r}, expected 'awaiting_payment'"
            )

        # Reuse an in-flight attempt for the same idempotency key (double-tap
        # protection). A new key always creates a new attempt (retry).
        existing_stmt = select(Payment).where(
            Payment.tenant_id == tenant_id,
            Payment.order_id == order_id,
            Payment.status.in_([
                PaymentRecordStatus.INITIATED.value,
                PaymentRecordStatus.PENDING.value,
            ]),
        )
        existing = (await self.session.execute(existing_stmt)).scalars().first()
        if (
            existing is not None
            and existing.metadata_json.get("idempotency_key") == idempotency_key
        ):
            return existing

        payment = Payment(
            tenant_id=tenant_id,
            order_id=order_id,
            provider="daraja",
            phone_number=phone_number,
            amount_cents=order.total_cents,
            status=PaymentRecordStatus.INITIATED,
            metadata_json={"idempotency_key": idempotency_key},
        )
        self.session.add(payment)
        await self.session.flush()

        try:
            response = await self.provider.initiate_stk_push(
                STKPushRequest(
                    phone_number=phone_number,
                    amount_cents=order.total_cents,
                    account_reference=str(order_id),
                    transaction_desc=f"Order {str(order_id)[:8]}",
                    callback_url=callback_url,
                    idempotency_key=idempotency_key,
                )
            )
        except PaymentProviderError as exc:
            payment.status = PaymentRecordStatus.FAILED
            payment.failure_reason = FailureReason.UNKNOWN.value
            payment.result_desc = str(exc)
            await self.session.flush()
            raise

        payment.checkout_request_id = response.checkout_request_id
        payment.merchant_request_id = response.merchant_request_id
        payment.metadata_json = {
            **payment.metadata_json,
            "customer_message": response.customer_message,
        }
        await self.session.flush()

        await add_outbox_event(
            event_type="payment.initiated",
            payload={
                "payment_id": str(payment.id),
                "order_id": str(order_id),
                "checkout_request_id": response.checkout_request_id,
                "phone": phone_number,
                "amount_cents": order.total_cents,
                "provider": "daraja",
            },
            idempotency_key=f"payment_initiated:{payment.id}",
            tenant_id=tenant_id,
            session=self.session,
        )
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="payment.initiated",
            resource="payment",
            resource_id=payment.id,
            after={
                "order_id": str(order_id),
                "amount_cents": order.total_cents,
                "checkout_request_id": response.checkout_request_id,
            },
        )
        return payment

    # -- callback ----------------------------------------------------------

    async def handle_callback(
        self,
        *,
        tenant_id: UUID,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> Payment:
        """Process an inbound provider callback idempotently."""
        if not await self.provider.validate_callback(payload, headers):
            raise CallbackVerificationError("Invalid callback payload")

        result = await self.provider.parse_callback(payload)
        checkout_request_id = result.checkout_request_id

        stmt = (
            select(Payment)
            .where(
                Payment.tenant_id == tenant_id,
                Payment.checkout_request_id == checkout_request_id,
            )
            .with_for_update()
        )
        payment = (await self.session.execute(stmt)).scalar_one_or_none()
        if payment is None:
            raise PaymentNotFoundError(
                f"No payment for checkout_request_id {checkout_request_id!r}"
            )

        # Duplicate / late callback: already terminal -> safe no-op.
        if PaymentRecordStatus(payment.status) in _TERMINAL_STATES:
            return payment

        order = await self._get_order(tenant_id, payment.order_id)

        payment.result_code = result.result_code
        payment.result_desc = result.result_desc
        payment.failure_reason = (
            result.failure_reason.value if result.failure_reason is not None else None
        )

        if result.status is ProviderPaymentStatus.COMPLETED:
            await self._complete_payment(
                payment, order, result.amount_cents, result.mpesa_receipt_number
            )
        elif result.status is ProviderPaymentStatus.CANCELLED:
            await self._cancel_payment(payment, order, result.result_desc)
        elif result.status is ProviderPaymentStatus.PENDING:
            payment.status = PaymentRecordStatus.PENDING
        else:
            await self._fail_payment(payment, order, result.result_desc)

        await self.session.flush()
        return payment

    async def _complete_payment(
        self,
        payment: Payment,
        order: Order,
        amount_cents: int | None,
        mpesa_receipt_number: str | None,
    ) -> None:
        # NEVER trust the callback amount: it must match the attempt exactly.
        if amount_cents is None or amount_cents != payment.amount_cents:
            payment.status = PaymentRecordStatus.FAILED
            payment.failure_reason = FailureReason.WRONG_AMOUNT.value
            await self.audit.log_action(
                tenant_id=payment.tenant_id,
                user_id=None,
                action="payment.amount_mismatch",
                resource="payment",
                resource_id=payment.id,
                metadata={
                    "expected_cents": payment.amount_cents,
                    "received_cents": amount_cents,
                    "checkout_request_id": payment.checkout_request_id,
                },
            )
            return

        now = datetime.now(UTC)
        payment.status = PaymentRecordStatus.COMPLETED
        payment.mpesa_receipt_number = mpesa_receipt_number
        payment.completed_at = now

        order.payment_status = "completed"
        order.payment_reference = mpesa_receipt_number
        order.payment_amount_cents = amount_cents
        order.payment_paid_at = now
        self._transition(order, OrderStatus.PAID)

        await add_outbox_event(
            event_type="payment.completed",
            payload={
                "payment_id": str(payment.id),
                "order_id": str(order.id),
                "checkout_request_id": payment.checkout_request_id,
                "mpesa_receipt_number": mpesa_receipt_number,
                "amount_cents": amount_cents,
            },
            idempotency_key=f"payment_completed:{payment.id}",
            tenant_id=payment.tenant_id,
            session=self.session,
        )
        await self.audit.log_action(
            tenant_id=payment.tenant_id,
            user_id=None,
            action="payment.completed",
            resource="payment",
            resource_id=payment.id,
            after={
                "order_id": str(order.id),
                "mpesa_receipt_number": mpesa_receipt_number,
                "amount_cents": amount_cents,
            },
        )

    async def _cancel_payment(self, payment: Payment, order: Order, reason: str | None) -> None:
        payment.status = PaymentRecordStatus.CANCELLED
        order.payment_status = "failed"
        if OrderStatus(order.status) is OrderStatus.AWAITING_PAYMENT:
            self._transition(order, OrderStatus.CANCELLED)
            order.cancelled_reason = "payment cancelled by customer"
        await self._emit_failure(payment, order, FailureReason.CANCELLED_BY_USER.value)

    async def _fail_payment(self, payment: Payment, order: Order, reason: str | None) -> None:
        payment.status = PaymentRecordStatus.FAILED
        order.payment_status = "failed"
        await self._emit_failure(
            payment, order, payment.failure_reason or FailureReason.UNKNOWN.value
        )

    async def _emit_failure(self, payment: Payment, order: Order, reason: str) -> None:
        await add_outbox_event(
            event_type="payment.failed",
            payload={
                "payment_id": str(payment.id),
                "order_id": str(order.id),
                "checkout_request_id": payment.checkout_request_id,
                "reason": reason,
                "result_code": str(payment.result_code)
                if payment.result_code is not None
                else None,
            },
            idempotency_key=f"payment_failed:{payment.id}:{reason}",
            tenant_id=payment.tenant_id,
            session=self.session,
        )
        await self.audit.log_action(
            tenant_id=payment.tenant_id,
            user_id=None,
            action="payment.failed",
            resource="payment",
            resource_id=payment.id,
            metadata={
                "order_id": str(order.id),
                "checkout_request_id": payment.checkout_request_id,
                "reason": reason,
                "result_code": payment.result_code,
            },
        )


__all__ = [
    "MalformedCallbackError",
    "OrderNotFoundError",
    "OrderNotPayableError",
    "PaymentError",
    "PaymentNotFoundError",
    "PaymentService",
]
