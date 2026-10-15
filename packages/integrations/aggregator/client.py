"""
Aggregator Payment Provider (Pesapal / IntaSend / generic).

Implements ``packages.core.domain.payment.PaymentProvider`` using a licensed
payment aggregator's API. The aggregator manages the Daraja relationship and
provides a normalized callback format.

Only translation between aggregator wire format and provider-neutral domain
types happens here. No business arithmetic, no state changes to orders.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

import httpx

from packages.core.domain.payment import (
    CallbackVerificationError,
    PaymentProvider,
    PaymentProviderError,
    ProviderPaymentResult,
    ProviderPaymentStatus,
    STKPushRequest,
    STKPushResponse,
)

DEFAULT_TIMEOUT_SECONDS = 15.0


class AggregatorPaymentProvider(PaymentProvider):
    """Aggregator STK Push provider (Pesapal, IntaSend, or generic).

    The aggregator handles the Daraja relationship and presents a unified
    callback format. This provider translates between the aggregator's API
    and the internal PaymentProvider interface.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str,
        callback_url: str,
        webhook_secret: str = "",
        aggregator_name: str = "generic",
        allowed_callback_ips: tuple[str, ...] = (),
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Aggregator API key and secret are required")
        if not base_url:
            raise ValueError("Aggregator base URL is required")

        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._callback_url = callback_url
        self._webhook_secret = webhook_secret
        self._aggregator_name = aggregator_name
        self._allowed_callback_ips = set(allowed_callback_ips)
        self._timeout = timeout_seconds
        self._client = http_client
        self._owns_client = http_client is None

    # -- lifecycle ---------------------------------------------------------

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    # -- helpers -----------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Build authentication headers for the aggregator API."""
        # Common pattern: API key as Bearer token or HMAC-signed request
        return {
            "Authorization": f"Bearer {self._api_key}",
            "X-API-Key": self._api_key,
        }

    @staticmethod
    def _amount_ksh(amount_cents: int) -> int:
        if amount_cents <= 0:
            raise PaymentProviderError("Amount must be positive")
        if amount_cents % 100 != 0:
            raise PaymentProviderError(
                "M-Pesa only supports whole shillings; amount_cents must be a multiple of 100"
            )
        return amount_cents // 100

    def _verify_hmac(self, payload: bytes, provided_signature: str) -> bool:
        """Verify HMAC-SHA256 signature from aggregator webhook."""
        if not self._webhook_secret:
            return False
        expected = hmac.new(self._webhook_secret.encode(), payload, "sha256").hexdigest()
        return hmac.compare_digest(expected, provided_signature)

    def _extract_client_ip(self, headers: dict[str, str]) -> str | None:
        """Extract client IP from headers for IP allowlist check."""
        lower = {k.lower(): v for k, v in headers.items()}
        return (
            lower.get("x-real-ip")
            or lower.get("client-ip")
            or (lower.get("x-forwarded-for", "").split(",")[0].strip() or None)
        )

    # -- PaymentProvider ---------------------------------------------------

    async def initiate_stk_push(self, request: STKPushRequest) -> STKPushResponse:
        """Initiate STK Push via the aggregator."""
        amount_ksh = self._amount_ksh(request.amount_cents)

        # Build aggregator-specific payload
        # Most aggregators (Pesapal, IntaSend) accept similar fields
        payload = {
            "phone_number": request.phone_number,
            "amount": amount_ksh,
            "account_reference": request.account_reference[:100],
            "transaction_desc": request.transaction_desc[:100],
            "callback_url": request.callback_url or self._callback_url,
            "idempotency_key": request.idempotency_key,
        }

        url = f"{self._base_url}/mpesa/stkpush"
        try:
            response = await self._http().post(
                url,
                json=payload,
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"Aggregator STK push failed: {exc}") from exc

        # Normalize response from aggregator format
        # Expected aggregator response fields:
        # - checkout_request_id (or transaction_id, reference_id)
        # - merchant_request_id (or merchant_reference)
        # - response_code (or status_code)
        # - response_description (or message)
        # - customer_message (optional)

        checkout_request_id = (
            data.get("checkout_request_id")
            or data.get("transaction_id")
            or data.get("reference_id")
        )
        merchant_request_id = (
            data.get("merchant_request_id")
            or data.get("merchant_reference")
            or data.get("reference_id")
        )
        response_code = str(data.get("response_code") or data.get("status_code") or "")
        response_description = str(data.get("response_description") or data.get("message") or "")
        customer_message = str(data.get("customer_message") or "")

        if not checkout_request_id:
            raise PaymentProviderError("Aggregator STK response missing checkout_request_id")

        # Most aggregators return "0" or "success" for success
        if response_code not in ("0", "success", "pending", "200"):
            raise PaymentProviderError(f"Aggregator rejected STK push: {response_description}")

        return STKPushResponse(
            checkout_request_id=str(checkout_request_id),
            merchant_request_id=str(merchant_request_id) if merchant_request_id else "",
            response_code=response_code,
            response_description=response_description,
            customer_message=customer_message,
        )

    async def query_status(self, checkout_request_id: str) -> ProviderPaymentResult:
        """Query payment status from aggregator."""
        url = f"{self._base_url}/mpesa/stkpush/status/{checkout_request_id}"
        try:
            response = await self._http().get(url, headers=self._auth_headers())
            # Some aggregators return 404 for not found, others 200 with pending status
            if response.status_code == 404:
                return ProviderPaymentResult(
                    status=ProviderPaymentStatus.PENDING,
                    checkout_request_id=checkout_request_id,
                    result_desc="Not found at aggregator",
                )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"Aggregator status query failed: {exc}") from exc

        # Normalize aggregator status to provider-neutral status
        status = self._normalize_aggregator_status(data)
        return ProviderPaymentResult(
            status=status["status"],
            checkout_request_id=checkout_request_id,
            merchant_request_id=data.get("merchant_reference"),
            mpesa_receipt_number=data.get("mpesa_receipt_number") or data.get("receipt_number"),
            result_code=status.get("result_code"),
            result_desc=data.get("result_desc") or data.get("message"),
            amount_cents=status.get("amount_cents"),
            phone_number=data.get("phone_number"),
            transaction_date=data.get("transaction_date"),
            failure_reason=status.get("failure_reason"),
            raw=data,
        )

    def _normalize_aggregator_status(self, data: dict[str, Any]) -> dict[str, Any]:
        """Map aggregator status to ProviderPaymentStatus."""
        # Common aggregator status values:
        # - "completed", "success", "paid" -> COMPLETED
        # - "failed", "cancelled", "timeout" -> FAILED/CANCELLED
        # - "pending", "processing", "initiated" -> PENDING

        raw_status = str(data.get("status") or data.get("payment_status") or "").lower()
        result_code = data.get("result_code")
        if result_code is not None:
            try:
                result_code = int(result_code)
            except (TypeError, ValueError):
                result_code = None

        if raw_status in ("completed", "success", "paid", "settled"):
            return {
                "status": ProviderPaymentStatus.COMPLETED,
                "result_code": result_code or 0,
                "amount_cents": self._extract_amount_cents(data),
                "failure_reason": None,
            }
        if raw_status in ("cancelled", "cancelled_by_user", "user_cancelled"):
            return {
                "status": ProviderPaymentStatus.CANCELLED,
                "result_code": result_code,
                "failure_reason": "cancelled_by_user",
            }
        if raw_status in ("failed", "timeout", "expired"):
            return {
                "status": ProviderPaymentStatus.FAILED,
                "result_code": result_code,
                "failure_reason": self._map_failure_reason(raw_status),
            }
        # Default to pending for unknown/in-flight statuses
        return {
            "status": ProviderPaymentStatus.PENDING,
            "result_code": result_code,
            "failure_reason": None,
        }

    def _extract_amount_cents(self, data: dict[str, Any]) -> int | None:
        """Extract amount in cents from aggregator response."""
        # Aggregators may return amount in KSH or cents
        for key in ("amount_cents", "amount", "paid_amount", "transaction_amount"):
            val = data.get(key)
            if val is not None:
                try:
                    amt = int(val)
                    # Heuristic: if amount > 100000, assume it's already in cents
                    # (e.g., 150000 cents = 1500 KSH). If <= 100000, assume KSH.
                    if amt > 100000:
                        return amt
                    return amt * 100
                except (TypeError, ValueError):
                    continue
        return None

    def _map_failure_reason(self, raw_status: str) -> str:
        """Map aggregator failure status to FailureReason."""
        if "insufficient" in raw_status or "funds" in raw_status:
            return "insufficient_funds"
        if "pin" in raw_status:
            return "wrong_pin"
        if "timeout" in raw_status or "expired" in raw_status:
            return "timeout"
        if "cancel" in raw_status:
            return "cancelled_by_user"
        return "unknown"

    async def validate_callback(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        """Verify aggregator callback authenticity."""
        # 1. Check payload shape
        if not isinstance(payload, dict):
            return False

        # Required fields (aggregator-normalized)
        required = ("checkout_request_id", "status")
        if not all(k in payload for k in required):
            return False

        # 2. Verify HMAC signature if webhook secret is configured
        if self._webhook_secret:
            provided_sig = headers.get("x-webhook-signature") or headers.get("x-signature")
            if not provided_sig:
                raise CallbackVerificationError("Missing webhook signature")
            # Re-serialize payload for verification
            payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            if not self._verify_hmac(payload_bytes, provided_sig):
                raise CallbackVerificationError("Invalid webhook signature")

        # 3. IP allowlist check
        if self._allowed_callback_ips:
            client_ip = self._extract_client_ip(headers)
            if client_ip not in self._allowed_callback_ips:
                raise CallbackVerificationError(f"Callback source not allowed: {client_ip}")

        return True

    async def parse_callback(self, payload: dict[str, Any]) -> ProviderPaymentResult:
        """Extract standardized status from aggregator callback."""
        checkout_request_id = payload.get("checkout_request_id")
        if not checkout_request_id:
            raise PaymentProviderError("Aggregator callback missing checkout_request_id")

        status_data = self._normalize_aggregator_status(payload)

        return ProviderPaymentResult(
            status=status_data["status"],
            checkout_request_id=str(checkout_request_id),
            merchant_request_id=payload.get("merchant_reference"),
            mpesa_receipt_number=payload.get("mpesa_receipt_number")
            or payload.get("receipt_number"),
            result_code=status_data.get("result_code"),
            result_desc=payload.get("result_desc") or payload.get("message"),
            amount_cents=status_data.get("amount_cents"),
            phone_number=payload.get("phone_number"),
            transaction_date=payload.get("transaction_date"),
            failure_reason=status_data.get("failure_reason"),
            raw=payload,
        )

    async def refund(self, mpesa_receipt_number: str, amount_cents: int, reason: str) -> bool:
        """Initiate refund via aggregator (if supported)."""
        # Most aggregators support refunds via their API
        url = f"{self._base_url}/mpesa/refund"
        payload = {
            "mpesa_receipt_number": mpesa_receipt_number,
            "amount_cents": amount_cents,
            "reason": reason,
        }
        try:
            response = await self._http().post(
                url,
                json=payload,
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            data = response.json()
            # Normalize refund response
            return data.get("status") in ("success", "refunded", "accepted")
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"Aggregator refund failed: {exc}") from exc


__all__ = ["AggregatorPaymentProvider"]
