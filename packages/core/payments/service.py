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

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from socialerp.outbox import add_outbox_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.domain.order import (
    OrderStatus,
    is_legal_transition,
)
from packages.core.domain.payment import (
    CallbackVerificationError,
    FailureReason,
    NotSupportedError,
    PaymentProvider,
    PaymentProviderError,
    ProviderPaymentResult,
    ProviderPaymentStatus,
    STKPushRequest,
)
from packages.core.models.order import Order
from packages.core.models.payment import Payment, PaymentRecordStatus
from packages.core.orders.service import is_order_paid, is_pod_in_transit
from packages.core.orders.transitions import apply_order_transition


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


class RefundNotApprovedError(PaymentError):
    """A refund was attempted without an explicit human approver."""


class RefundNotAllowedError(PaymentError):
    """The payment/order is not in a state that can be refunded."""


class RefundError(PaymentError):
    """The refund could not be carried out."""


class RefundOutcome(str, Enum):
    """Result of a (human-approved) refund attempt."""

    REFUNDED = "refunded"
    MANUAL_REQUIRED = "manual_required"  # provider cannot refund; do it offline


@dataclass(frozen=True)
class RefundResult:
    outcome: RefundOutcome
    payment: Payment


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

    async def _get_order(
        self, tenant_id: UUID, order_id: UUID, *, for_update: bool = False
    ) -> Order:
        stmt = select(Order).where(Order.id == order_id, Order.tenant_id == tenant_id)
        if for_update:
            # B5: guard-and-write on the order must be atomic. Without this lock
            # a success callback and a failure/cancel callback for two attempts
            # of the same order can interleave (each locks only its own Payment
            # row) and downgrade a paid order. Lock order is payment -> order,
            # matching _load_payment_for_update. Lock only the orders table:
            # Order eagerly joins customers, which cannot be locked.
            stmt = stmt.with_for_update(of=Order)
        order = (await self.session.execute(stmt)).scalar_one_or_none()
        if order is None:
            raise OrderNotFoundError(f"Order {order_id} not found")
        return order

    @staticmethod
    def _transition(order: Order, new_status: OrderStatus) -> None:
        apply_order_transition(order, new_status)

    @staticmethod
    def _record_order_collection(
        order: Order, reference: str | None, amount_cents: int, now: datetime
    ) -> None:
        order.payment_status = "completed"
        order.payment_reference = reference
        order.payment_amount_cents = amount_cents
        order.payment_paid_at = now

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
        # Deliberately NO order row lock here: this method awaits the provider's
        # network call below, and holding FOR UPDATE across network I/O would
        # block concurrent callbacks for the order. The double-charge this guard
        # protects against is the callback path's job (order lock + duplicate
        # detection); a concurrent initiation that slips past only creates an
        # extra attempt whose outcome the duplicate guard handles.
        order = await self._get_order(tenant_id, order_id)
        # B5: an order is charged at most once. Refuse a fresh STK push for an
        # order that is already paid -- this is especially reachable for
        # pay-on-delivery, where the order stays out_for_delivery after payment
        # and would otherwise accept another (double-charging) push. Checks both
        # payment_status and paid_at so a stale failure callback cannot reopen it.
        if is_order_paid(order):
            raise OrderNotPayableError(
                f"Order {order_id} is already paid; refusing another STK push"
            )
        # Payable: awaiting_payment (prepay) or a pay-on-delivery order already
        # out_for_delivery (STK fires at rider arrival; see is_pod_in_transit).
        if order.status != OrderStatus.AWAITING_PAYMENT.value and not is_pod_in_transit(order):
            raise OrderNotPayableError(
                f"Order {order_id} is {order.status!r}, expected 'awaiting_payment' "
                "or pay-on-delivery in transit"
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

    async def _load_payment_for_update(
        self, tenant_id: UUID, checkout_request_id: str
    ) -> Payment | None:
        stmt = (
            select(Payment)
            .where(
                Payment.tenant_id == tenant_id,
                Payment.checkout_request_id == checkout_request_id,
            )
            .with_for_update()
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

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
        payment = await self._load_payment_for_update(tenant_id, result.checkout_request_id)
        if payment is None:
            raise PaymentNotFoundError(
                f"No payment for checkout_request_id {result.checkout_request_id!r}"
            )

        # Duplicate / late callback: already terminal -> safe no-op.
        if PaymentRecordStatus(payment.status) in _TERMINAL_STATES:
            return payment

        order = await self._get_order(tenant_id, payment.order_id, for_update=True)
        await self._apply_result(payment, order, result, trusted_amount=True)
        await self.session.flush()
        return payment

    async def apply_query_result(
        self,
        *,
        tenant_id: UUID,
        result: ProviderPaymentResult,
    ) -> Payment:
        """Apply a status-query result (fallback for missing/late callbacks).

        The Daraja status query is authenticated but does **not** return the
        amount, so a "completed" query result cannot be auto-credited: it is
        flagged for human review instead. Failures/cancellations are safe to
        apply.
        """
        payment = await self._load_payment_for_update(tenant_id, result.checkout_request_id)
        if payment is None:
            raise PaymentNotFoundError(
                f"No payment for checkout_request_id {result.checkout_request_id!r}"
            )
        if PaymentRecordStatus(payment.status) in _TERMINAL_STATES:
            return payment

        order = await self._get_order(tenant_id, payment.order_id, for_update=True)
        await self._apply_result(payment, order, result, trusted_amount=False)
        await self.session.flush()
        return payment

    async def _apply_result(
        self,
        payment: Payment,
        order: Order,
        result: ProviderPaymentResult,
        *,
        trusted_amount: bool,
    ) -> None:
        payment.result_code = result.result_code
        payment.result_desc = result.result_desc
        payment.failure_reason = (
            result.failure_reason.value if result.failure_reason is not None else None
        )

        if result.status is ProviderPaymentStatus.COMPLETED:
            if trusted_amount:
                await self._complete_payment(
                    payment, order, result.amount_cents, result.mpesa_receipt_number
                )
            else:
                # Cannot verify the amount from a status query: never auto-credit.
                payment.status = PaymentRecordStatus.PENDING
                await self.audit.log_action(
                    tenant_id=payment.tenant_id,
                    user_id=None,
                    action="payment.reconciliation_review_required",
                    resource="payment",
                    resource_id=payment.id,
                    metadata={
                        "order_id": str(order.id),
                        "checkout_request_id": payment.checkout_request_id,
                        "reason": "status query reports success without a verified amount",
                    },
                )
        elif result.status is ProviderPaymentStatus.CANCELLED:
            await self._cancel_payment(payment, order, result.result_desc)
        elif result.status is ProviderPaymentStatus.PENDING:
            payment.status = PaymentRecordStatus.PENDING
        else:
            await self._fail_payment(payment, order, result.result_desc)

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

        # B5: an order is credited at most once. If a *different* STK attempt
        # already completed this order, this second successful callback must not
        # credit it again (double revenue, double sale side effect). Record this
        # attempt truthfully and flag it for reconciliation/refund instead.
        if is_order_paid(order):
            payment.status = PaymentRecordStatus.COMPLETED
            payment.mpesa_receipt_number = mpesa_receipt_number
            payment.completed_at = now
            payment.metadata_json = {**(payment.metadata_json or {}), "duplicate_payment": True}
            await self.audit.log_action(
                tenant_id=payment.tenant_id,
                user_id=None,
                action="payment.duplicate_detected",
                resource="payment",
                resource_id=payment.id,
                metadata={
                    "order_id": str(order.id),
                    "checkout_request_id": payment.checkout_request_id,
                    "mpesa_receipt_number": mpesa_receipt_number,
                    "amount_cents": amount_cents,
                    "reason": "order already paid; second successful attempt flagged",
                },
            )
            return

        payment.status = PaymentRecordStatus.COMPLETED
        payment.mpesa_receipt_number = mpesa_receipt_number
        payment.completed_at = now

        # Prepay: awaiting_payment -> paid. Pay-on-delivery orders are already
        # out_for_delivery (goods moving before payment) and must NOT transition
        # to 'paid' (illegal FSM edge); they proceed to delivered on rider
        # confirmation. paid_at is still recorded for reconciliation.
        if is_pod_in_transit(order):
            self._record_order_collection(order, mpesa_receipt_number, amount_cents, now)
            order.paid_at = now
        elif OrderStatus(order.status) is OrderStatus.AWAITING_PAYMENT:
            self._record_order_collection(order, mpesa_receipt_number, amount_cents, now)
            self._transition(order, OrderStatus.PAID)
        else:
            # The customer's money WAS collected, but this order is not in a
            # state that can be marked collected. This branch is reached only
            # when is_order_paid() is False, i.e. cancelled / failed_delivery,
            # or a POD order whose goods are not yet out for delivery (packed).
            # (A refunded/delivered order keeps paid_at, so it took the
            # duplicate_payment branch above.) Raising here would roll the
            # callback back (500) and lose the collection to retries forever.
            # So: record the collection truthfully on the Payment, leave the
            # order untouched (the POD delivery gate then blocks until a human
            # resolves it), and raise a review signal.
            payment.metadata_json = {
                **(payment.metadata_json or {}),
                "collected_after_terminal": True,
            }
            await add_outbox_event(
                event_type="payment.collected_after_terminal",
                payload={
                    "payment_id": str(payment.id),
                    "order_id": str(order.id),
                    "order_status": str(order.status),
                    "checkout_request_id": payment.checkout_request_id,
                    "mpesa_receipt_number": mpesa_receipt_number,
                    "amount_cents": amount_cents,
                },
                idempotency_key=f"payment_collected_after_terminal:{order.id}",
                tenant_id=payment.tenant_id,
                session=self.session,
            )
            await self.audit.log_action(
                tenant_id=payment.tenant_id,
                user_id=None,
                action="payment.collected_after_terminal",
                resource="payment",
                resource_id=payment.id,
                after={
                    "order_id": str(order.id),
                    "order_status": order.status,
                    "mpesa_receipt_number": mpesa_receipt_number,
                    "amount_cents": amount_cents,
                    "reason": "funds collected on an order that cannot be marked paid",
                },
            )
            return

        await add_outbox_event(
            event_type="payment.completed",
            payload={
                "payment_id": str(payment.id),
                "order_id": str(order.id),
                "checkout_request_id": payment.checkout_request_id,
                "mpesa_receipt_number": mpesa_receipt_number,
                "amount_cents": amount_cents,
            },
            # Keyed by order (not payment): the completion is an order-level fact,
            # so replayed/duplicate attempts collapse to a single outbox event.
            idempotency_key=f"payment_completed:{order.id}",
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
        # B5: a stale cancel for one attempt must never downgrade an order that
        # another attempt already paid (late callback after success).
        if is_order_paid(order):
            await self._audit_stale_failure(payment, order, "cancelled")
            return
        # B5/atomicity: a retry attempt's cancel must not cancel the whole order
        # while another attempt can still succeed -- otherwise a late success on
        # that other attempt finds a cancelled order (money collected, sale
        # lost). Leave the order payable; the in-flight attempt decides its fate.
        if await self._has_other_in_flight_attempt(payment, order):
            await self.audit.log_action(
                tenant_id=payment.tenant_id,
                user_id=None,
                action="payment.cancel_deferred",
                resource="payment",
                resource_id=payment.id,
                metadata={
                    "order_id": str(order.id),
                    "checkout_request_id": payment.checkout_request_id,
                    "reason": "another payment attempt is still in flight",
                },
            )
            return
        order.payment_status = "failed"
        if OrderStatus(order.status) is OrderStatus.AWAITING_PAYMENT:
            self._transition(order, OrderStatus.CANCELLED)
            order.cancelled_reason = "payment cancelled by customer"
        await self._emit_failure(payment, order, FailureReason.CANCELLED_BY_USER.value)

    async def _has_other_in_flight_attempt(self, payment: Payment, order: Order) -> bool:
        """Whether the order has a second attempt still open (INITIATED/PENDING)."""
        stmt = select(Payment.id).where(
            Payment.tenant_id == order.tenant_id,
            Payment.order_id == order.id,
            Payment.id != payment.id,
            Payment.status.in_([
                PaymentRecordStatus.INITIATED.value,
                PaymentRecordStatus.PENDING.value,
            ]),
        )
        return (await self.session.execute(stmt)).first() is not None

    async def _fail_payment(self, payment: Payment, order: Order, reason: str | None) -> None:
        payment.status = PaymentRecordStatus.FAILED
        # B5: see _cancel_payment -- never downgrade a paid order.
        if is_order_paid(order):
            await self._audit_stale_failure(payment, order, "failed")
            return
        order.payment_status = "failed"
        await self._emit_failure(
            payment, order, payment.failure_reason or FailureReason.UNKNOWN.value
        )

    async def _audit_stale_failure(self, payment: Payment, order: Order, outcome: str) -> None:
        """Record (but do not apply) a failure for an attempt on a paid order."""
        await self.audit.log_action(
            tenant_id=payment.tenant_id,
            user_id=None,
            action="payment.stale_failure_ignored",
            resource="payment",
            resource_id=payment.id,
            metadata={
                "order_id": str(order.id),
                "checkout_request_id": payment.checkout_request_id,
                "attempt_outcome": outcome,
                "reason": "order already paid by another attempt; failure not applied",
            },
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

    # -- refunds (human-approved only) ------------------------------------

    async def refund_payment(
        self,
        *,
        tenant_id: UUID,
        payment_id: UUID,
        approved_by: UUID | None,
        reason: str,
        amount_cents: int | None = None,
    ) -> RefundResult:
        """Refund a completed payment. Requires an explicit human approver.

        Daraja exposes no self-serve refund API, so a provider that cannot
        refund raises ``NotSupportedError``; in that case the attempt is
        recorded as ``payment.refund_manual_required`` (an operator completes it
        offline) and the ledger is left unchanged rather than faked.
        """
        if approved_by is None:
            raise RefundNotApprovedError("Refunds require an explicit human approver")

        stmt = (
            select(Payment)
            .where(Payment.id == payment_id, Payment.tenant_id == tenant_id)
            .with_for_update()
        )
        payment = (await self.session.execute(stmt)).scalar_one_or_none()
        if payment is None:
            raise PaymentNotFoundError(f"No payment {payment_id} for this tenant")

        # Idempotent: an already-refunded payment is a safe no-op.
        if PaymentRecordStatus(payment.status) is PaymentRecordStatus.REFUNDED:
            return RefundResult(RefundOutcome.REFUNDED, payment)

        if PaymentRecordStatus(payment.status) is not PaymentRecordStatus.COMPLETED:
            raise RefundNotAllowedError(
                f"Only completed payments can be refunded (status={payment.status!r})"
            )

        order = await self._get_order(tenant_id, payment.order_id)
        if not is_legal_transition(OrderStatus(order.status), OrderStatus.REFUNDED):
            raise RefundNotAllowedError(
                f"Order {order.id} is {order.status!r} and cannot be refunded"
            )

        amount = payment.amount_cents if amount_cents is None else amount_cents
        if amount <= 0 or amount > payment.amount_cents:
            raise RefundError("Refund amount must be positive and not exceed the paid amount")

        receipt = payment.mpesa_receipt_number
        if not receipt:
            raise RefundError("Payment has no M-Pesa receipt to refund against")

        try:
            provider_accepted = await self.provider.refund(receipt, amount, reason)
        except NotSupportedError:
            await self.audit.log_action(
                tenant_id=payment.tenant_id,
                user_id=approved_by,
                action="payment.refund_manual_required",
                resource="payment",
                resource_id=payment.id,
                metadata={
                    "order_id": str(order.id),
                    "mpesa_receipt_number": receipt,
                    "amount_cents": amount,
                    "reason": reason,
                },
            )
            await self.session.flush()
            return RefundResult(RefundOutcome.MANUAL_REQUIRED, payment)

        if not provider_accepted:
            await self.audit.log_action(
                tenant_id=payment.tenant_id,
                user_id=approved_by,
                action="payment.refund_failed",
                resource="payment",
                resource_id=payment.id,
                metadata={
                    "order_id": str(order.id),
                    "mpesa_receipt_number": receipt,
                    "amount_cents": amount,
                    "reason": reason,
                },
            )
            await self.session.flush()
            raise RefundError("Provider declined the refund")

        payment.status = PaymentRecordStatus.REFUNDED
        order.payment_status = "refunded"
        self._transition(order, OrderStatus.REFUNDED)

        await add_outbox_event(
            event_type="payment.refunded",
            payload={
                "payment_id": str(payment.id),
                "order_id": str(order.id),
                "mpesa_receipt_number": receipt,
                "amount_cents": amount,
                "approved_by": str(approved_by),
                "reason": reason,
            },
            idempotency_key=f"payment_refunded:{payment.id}",
            tenant_id=payment.tenant_id,
            session=self.session,
        )
        await self.audit.log_action(
            tenant_id=payment.tenant_id,
            user_id=approved_by,
            action="payment.refunded",
            resource="payment",
            resource_id=payment.id,
            after={
                "order_id": str(order.id),
                "mpesa_receipt_number": receipt,
                "amount_cents": amount,
                "reason": reason,
            },
        )
        await self.session.flush()
        return RefundResult(RefundOutcome.REFUNDED, payment)


__all__ = [
    "MalformedCallbackError",
    "OrderNotFoundError",
    "OrderNotPayableError",
    "PaymentError",
    "PaymentNotFoundError",
    "PaymentService",
    "RefundError",
    "RefundNotAllowedError",
    "RefundNotApprovedError",
    "RefundOutcome",
    "RefundResult",
]
