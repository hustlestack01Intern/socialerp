"""
Direct Daraja (M-Pesa) Payment Provider.

Implements ``packages.core.domain.payment.PaymentProvider`` using a tenant's
own Safaricom Daraja credentials (Lipa Na M-Pesa Online / STK Push).

Only translation between Daraja's wire format and the provider-neutral domain
types happens here. No business arithmetic, no state changes to orders.
"""

from __future__ import annotations

import base64
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from packages.core.domain.payment import (
    CallbackVerificationError,
    NotSupportedError,
    PaymentProvider,
    PaymentProviderError,
    ProviderPaymentResult,
    ProviderPaymentStatus,
    STKPushRequest,
    STKPushResponse,
    classify_result_code,
)

TOKEN_SAFETY_MARGIN_SECONDS = 30
DEFAULT_TIMEOUT_SECONDS = 15.0


class DirectDarajaProvider(PaymentProvider):
    """Daraja STK Push provider using a tenant's own credentials."""

    def __init__(
        self,
        *,
        consumer_key: str,
        consumer_secret: str,
        passkey: str,
        shortcode: str,
        base_url: str,
        callback_url: str,
        transaction_type: str = "CustomerPayBillOnline",
        callback_token: str = "",
        allowed_callback_ips: tuple[str, ...] = (),
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not consumer_key or not consumer_secret:
            raise ValueError("Daraja consumer key/secret are required")
        if not passkey or not shortcode:
            raise ValueError("Daraja passkey and shortcode are required")

        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._passkey = passkey
        self._shortcode = shortcode
        self._base_url = base_url.rstrip("/")
        self._callback_url = callback_url
        self._transaction_type = transaction_type
        self._callback_token = callback_token
        self._allowed_callback_ips = set(allowed_callback_ips)
        self._timeout = timeout_seconds
        self._client = http_client
        self._owns_client = http_client is None
        self._token: str | None = None
        self._token_expiry: float = 0.0

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

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    def _password(self, timestamp: str) -> str:
        raw = f"{self._shortcode}{self._passkey}{timestamp}"
        return base64.b64encode(raw.encode()).decode()

    @staticmethod
    def _amount_ksh(amount_cents: int) -> int:
        if amount_cents <= 0:
            raise PaymentProviderError("Amount must be positive")
        if amount_cents % 100 != 0:
            raise PaymentProviderError(
                "M-Pesa only supports whole shillings; amount_cents must be a multiple of 100"
            )
        return amount_cents // 100

    async def _access_token(self) -> str:
        now = time.monotonic()
        if self._token is not None and now < self._token_expiry:
            return self._token

        url = f"{self._base_url}/oauth/v1/generate"
        try:
            response = await self._http().get(
                url,
                params={"grant_type": "client_credentials"},
                auth=httpx.BasicAuth(self._consumer_key, self._consumer_secret),
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:  # network / non-2xx
            raise PaymentProviderError(f"Daraja auth failed: {exc}") from exc

        token = data.get("access_token")
        if not token:
            raise PaymentProviderError("Daraja auth response missing access_token")

        try:
            expires_in = int(data.get("expires_in", 3599))
        except (TypeError, ValueError):
            expires_in = 3599

        self._token = str(token)
        self._token_expiry = now + max(expires_in - TOKEN_SAFETY_MARGIN_SECONDS, 1)
        return self._token

    @staticmethod
    def _extract_metadata(stk_callback: dict[str, Any]) -> dict[str, Any]:
        items = (stk_callback.get("CallbackMetadata") or {}).get("Item") or []
        out: dict[str, Any] = {}
        for item in items:
            if isinstance(item, dict) and "Name" in item:
                out[str(item["Name"])] = item.get("Value")
        return out

    # -- PaymentProvider ---------------------------------------------------

    async def initiate_stk_push(self, request: STKPushRequest) -> STKPushResponse:
        amount_ksh = self._amount_ksh(request.amount_cents)
        timestamp = self._timestamp()
        payload = {
            "BusinessShortCode": self._shortcode,
            "Password": self._password(timestamp),
            "Timestamp": timestamp,
            "TransactionType": self._transaction_type,
            "Amount": amount_ksh,
            "PartyA": request.phone_number,
            "PartyB": self._shortcode,
            "PhoneNumber": request.phone_number,
            "CallBackURL": request.callback_url or self._callback_url,
            "AccountReference": request.account_reference[:12],
            "TransactionDesc": request.transaction_desc[:13],
        }

        token = await self._access_token()
        url = f"{self._base_url}/mpesa/stkpush/v1/processrequest"
        try:
            response = await self._http().post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": request.idempotency_key,
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"Daraja STK push failed: {exc}") from exc

        response_code = str(data.get("ResponseCode", ""))
        if response_code != "0":
            raise PaymentProviderError(
                f"Daraja rejected STK push: {data.get('ResponseDescription', 'unknown error')}"
            )

        checkout_request_id = data.get("CheckoutRequestID")
        merchant_request_id = data.get("MerchantRequestID")
        if not checkout_request_id or not merchant_request_id:
            raise PaymentProviderError("Daraja STK response missing request IDs")

        return STKPushResponse(
            checkout_request_id=str(checkout_request_id),
            merchant_request_id=str(merchant_request_id),
            response_code=response_code,
            response_description=str(data.get("ResponseDescription", "")),
            customer_message=str(data.get("CustomerMessage", "")),
        )

    async def query_status(self, checkout_request_id: str) -> ProviderPaymentResult:
        timestamp = self._timestamp()
        payload = {
            "BusinessShortCode": self._shortcode,
            "Password": self._password(timestamp),
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }

        token = await self._access_token()
        url = f"{self._base_url}/mpesa/stkpushquery/v1/query"
        try:
            response = await self._http().post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
            # Daraja returns 500 with a JSON body when the txn is still processing.
            data = response.json()
        except httpx.HTTPError as exc:
            raise PaymentProviderError(f"Daraja status query failed: {exc}") from exc

        result_code = self._as_int(data.get("ResultCode"))
        if result_code is None:
            status, reason = ProviderPaymentStatus.PENDING, None
        else:
            status, reason = classify_result_code(result_code)
        return ProviderPaymentResult(
            status=status,
            checkout_request_id=checkout_request_id,
            merchant_request_id=data.get("MerchantRequestID"),
            mpesa_receipt_number=data.get("MpesaReceiptNumber"),
            result_code=result_code,
            result_desc=data.get("ResultDesc"),
            phone_number=str(data["PhoneNumber"]) if data.get("PhoneNumber") else None,
            transaction_date=str(data["TransactionDate"]) if data.get("TransactionDate") else None,
            failure_reason=reason,
            raw=data,
        )

    async def validate_callback(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        stk_callback = (payload or {}).get("Body", {}).get("stkCallback")
        if not isinstance(stk_callback, dict):
            return False
        if "CheckoutRequestID" not in stk_callback or "ResultCode" not in stk_callback:
            return False

        if self._callback_token:
            lower = {k.lower(): v for k, v in headers.items()}
            if lower.get("x-callback-token") != self._callback_token:
                raise CallbackVerificationError("Invalid callback token")

        if self._allowed_callback_ips:
            lower = {k.lower(): v for k, v in headers.items()}
            peer = (
                lower.get("x-real-ip")
                or lower.get("client-ip")
                or (lower.get("x-forwarded-for", "").split(",")[0].strip() or None)
            )
            if peer not in self._allowed_callback_ips:
                raise CallbackVerificationError(f"Callback source not allowed: {peer}")

        return True

    async def parse_callback(self, payload: dict[str, Any]) -> ProviderPaymentResult:
        stk_callback = (payload or {}).get("Body", {}).get("stkCallback")
        if not isinstance(stk_callback, dict):
            raise PaymentProviderError("Malformed callback: missing Body.stkCallback")

        checkout_request_id = str(stk_callback.get("CheckoutRequestID", ""))
        if not checkout_request_id:
            raise PaymentProviderError("Malformed callback: missing CheckoutRequestID")

        result_code = self._as_int(stk_callback.get("ResultCode"))
        if result_code is None:
            raise PaymentProviderError("Malformed callback: missing ResultCode")
        result_desc = stk_callback.get("ResultDesc")
        metadata = self._extract_metadata(stk_callback)

        status, reason = classify_result_code(result_code)
        amount_cents: int | None = None
        if metadata.get("Amount") is not None:
            try:
                amount_cents = int(metadata["Amount"]) * 100
            except (TypeError, ValueError):
                amount_cents = None

        return ProviderPaymentResult(
            status=status,
            checkout_request_id=checkout_request_id,
            merchant_request_id=stk_callback.get("MerchantRequestID"),
            mpesa_receipt_number=(
                str(metadata["MpesaReceiptNumber"]) if metadata.get("MpesaReceiptNumber") else None
            ),
            result_code=result_code,
            result_desc=str(result_desc) if result_desc is not None else None,
            amount_cents=amount_cents,
            phone_number=str(metadata["PhoneNumber"]) if metadata.get("PhoneNumber") else None,
            transaction_date=(
                str(metadata["TransactionDate"]) if metadata.get("TransactionDate") else None
            ),
            failure_reason=reason,
            raw=payload,
        )

    async def refund(self, mpesa_receipt_number: str, amount_cents: int, reason: str) -> bool:
        raise NotSupportedError(
            "DirectDarajaProvider refunds require the Daraja Reversal API and "
            "initiator credentials; use the reconciliation runbook (human-approved)."
        )

    @staticmethod
    def _as_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


__all__ = ["DirectDarajaProvider"]
