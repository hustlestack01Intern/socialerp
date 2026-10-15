"""
Manual "pay to Till and confirm" payment provider (multi-merchant option 3).

Used when a tenant has no Daraja go-live yet and no aggregator: the customer pays
to the business Till out-of-band and a staff member records the payment, which the
provider surfaces as a callback-shaped result. There is no network call and no
money arithmetic here — the payment service still matches the amount against the
attempt and never trusts a payload blindly. Confirmation requires a shared token
(fail-closed).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from packages.core.domain.payment import (
    NotSupportedError,
    PaymentProvider,
    ProviderPaymentResult,
    ProviderPaymentStatus,
    STKPushRequest,
    STKPushResponse,
)

CHECKOUT_PREFIX = "MANUAL-"


class ManualPaymentProvider(PaymentProvider):
    """Record-and-confirm fallback for tenants without Daraja/aggregator."""

    def __init__(
        self,
        *,
        callback_token: str,
        till: str = "",
    ) -> None:
        self.callback_token = callback_token
        self.till = till

    async def initiate_stk_push(self, request: STKPushRequest) -> STKPushResponse:
        checkout_request_id = f"{CHECKOUT_PREFIX}{uuid4()}"
        instructions = f"Pay to Till {self.till}." if self.till else "Pay to the business Till."
        return STKPushResponse(
            checkout_request_id=checkout_request_id,
            merchant_request_id=f"manual_{uuid4().hex[:12]}",
            response_code="0",
            response_description="Awaiting manual confirmation",
            customer_message=(f"{instructions} A team member will confirm your payment shortly."),
        )

    async def query_status(self, checkout_request_id: str) -> ProviderPaymentResult:
        # No provider to ask: manual confirmations only arrive via callback.
        return ProviderPaymentResult(
            status=ProviderPaymentStatus.PENDING,
            checkout_request_id=checkout_request_id,
            result_desc="Awaiting manual confirmation",
        )

    async def validate_callback(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        # Fail closed: the shared token must be configured and must match.
        if not self.callback_token:
            return False
        token = headers.get("x-callback-token")
        if token != self.callback_token:
            return False
        return (
            isinstance(payload.get("checkout_request_id"), str)
            and isinstance(payload.get("mpesa_receipt_number"), str)
            and isinstance(payload.get("amount_cents"), int)
        )

    async def parse_callback(self, payload: dict[str, Any]) -> ProviderPaymentResult:
        checkout_request_id = payload.get("checkout_request_id")
        if not isinstance(checkout_request_id, str):
            raise TypeError("manual confirmation missing checkout_request_id")
        return ProviderPaymentResult(
            status=ProviderPaymentStatus.COMPLETED,
            checkout_request_id=checkout_request_id,
            mpesa_receipt_number=payload.get("mpesa_receipt_number"),
            result_code=0,
            result_desc="Manually confirmed",
            amount_cents=payload.get("amount_cents"),
            phone_number=payload.get("phone_number"),
            raw=payload,
        )

    async def refund(self, mpesa_receipt_number: str, amount_cents: int, reason: str) -> bool:
        raise NotSupportedError("Manual payments are reversed out-of-band, not via the provider")


__all__ = ["ManualPaymentProvider"]
