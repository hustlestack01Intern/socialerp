"""
Unit tests for DirectDarajaProvider.

These use httpx.MockTransport so they are fast, deterministic and need no
running server. Integration coverage against the fake Daraja server lives in
tests/integrations/daraja/test_daraja_payment_service.py.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from packages.core.domain.payment import (
    CallbackVerificationError,
    FailureReason,
    NotSupportedError,
    PaymentProviderError,
    ProviderPaymentStatus,
    STKPushRequest,
)
from packages.integrations.daraja.client import DirectDarajaProvider

BASE_URL = "https://sandbox.example.test"


def _make_provider(handler: Any, **overrides: Any) -> DirectDarajaProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    kwargs: dict[str, Any] = {
        "consumer_key": "ck",
        "consumer_secret": "cs",
        "passkey": "test_passkey",
        "shortcode": "174379",
        "base_url": BASE_URL,
        "callback_url": "https://app.example.test/api/v1/payments/stk/callback",
    }
    kwargs.update(overrides)
    return DirectDarajaProvider(http_client=client, **kwargs)


def _router(responses: dict[str, httpx.Response]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        for path, response in responses.items():
            if request.url.path == path:
                return response
        return httpx.Response(404, json={"error": f"no mock for {request.url.path}"})

    return handler


def _stk_request(**overrides: Any) -> STKPushRequest:
    data: dict[str, Any] = {
        "phone_number": "254712345678",
        "amount_cents": 150000,  # KES 1500.00
        "account_reference": "ORDER-123",
        "transaction_desc": "Order 123",
        "callback_url": "https://app.example.test/cb",
        "idempotency_key": "idem-1",
    }
    data.update(overrides)
    return STKPushRequest(**data)


# =============================================================================
# initiate_stk_push
# =============================================================================


class TestInitiateStkPush:
    @pytest.mark.asyncio
    async def test_success_builds_daraja_payload(self) -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth/v1/generate":
                assert request.url.params["grant_type"] == "client_credentials"
                return httpx.Response(200, json={"access_token": "tok", "expires_in": "3599"})
            if request.url.path == "/mpesa/stkpush/v1/processrequest":
                captured["body"] = json.loads(request.content)
                captured["auth"] = request.headers["authorization"]
                return httpx.Response(
                    200,
                    json={
                        "MerchantRequestID": "mreq-1",
                        "CheckoutRequestID": "ws_CO_1",
                        "ResponseCode": "0",
                        "ResponseDescription": "Success. Request accepted",
                        "CustomerMessage": "Enter PIN",
                    },
                )
            return httpx.Response(404)

        provider = _make_provider(handler)
        result = await provider.initiate_stk_push(_stk_request())

        assert result.checkout_request_id == "ws_CO_1"
        assert result.merchant_request_id == "mreq-1"
        assert captured["auth"] == "Bearer tok"
        body = captured["body"]
        assert body["Amount"] == 1500  # whole shillings, not cents
        assert body["PhoneNumber"] == "254712345678"
        assert body["PartyA"] == "254712345678"
        assert body["PartyB"] == "174379"
        assert body["BusinessShortCode"] == "174379"
        assert body["CallBackURL"] == "https://app.example.test/cb"
        # Password = base64(shortcode + passkey + timestamp)
        decoded = base64.b64decode(body["Password"]).decode()
        assert decoded.startswith("174379test_passkey")

    @pytest.mark.asyncio
    async def test_rejects_non_whole_shilling_amount(self) -> None:
        provider = _make_provider(_router({}))
        with pytest.raises(PaymentProviderError, match="whole shillings"):
            await provider.initiate_stk_push(_stk_request(amount_cents=150001))

    @pytest.mark.asyncio
    async def test_raises_when_daraja_rejects(self) -> None:
        handler = _router({
            "/oauth/v1/generate": httpx.Response(
                200, json={"access_token": "tok", "expires_in": "3599"}
            ),
            "/mpesa/stkpush/v1/processrequest": httpx.Response(
                200,
                json={"ResponseCode": "1", "ResponseDescription": "Rejected"},
            ),
        })
        provider = _make_provider(handler)
        with pytest.raises(PaymentProviderError, match="rejected"):
            await provider.initiate_stk_push(_stk_request())

    @pytest.mark.asyncio
    async def test_raises_on_auth_failure(self) -> None:
        handler = _router({"/oauth/v1/generate": httpx.Response(401, json={})})
        provider = _make_provider(handler)
        with pytest.raises(PaymentProviderError, match="auth failed"):
            await provider.initiate_stk_push(_stk_request())


# =============================================================================
# query_status
# =============================================================================


class TestQueryStatus:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        handler = _router({
            "/oauth/v1/generate": httpx.Response(
                200, json={"access_token": "tok", "expires_in": "3599"}
            ),
            "/mpesa/stkpushquery/v1/query": httpx.Response(
                200,
                json={
                    "ResultCode": "0",
                    "ResultDesc": "Success",
                    "MpesaReceiptNumber": "NLJ123ABC",
                },
            ),
        })
        provider = _make_provider(handler)
        result = await provider.query_status("ws_CO_1")
        assert result.status is ProviderPaymentStatus.COMPLETED
        assert result.mpesa_receipt_number == "NLJ123ABC"

    @pytest.mark.asyncio
    async def test_missing_result_code_is_pending(self) -> None:
        handler = _router({
            "/oauth/v1/generate": httpx.Response(
                200, json={"access_token": "tok", "expires_in": "3599"}
            ),
            "/mpesa/stkpushquery/v1/query": httpx.Response(
                500, json={"errorMessage": "still processing"}
            ),
        })
        provider = _make_provider(handler)
        result = await provider.query_status("ws_CO_1")
        assert result.status is ProviderPaymentStatus.PENDING


# =============================================================================
# parse_callback
# =============================================================================


def _callback(result_code: int, items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "mreq-1",
                "CheckoutRequestID": "ws_CO_1",
                "ResultCode": result_code,
                "ResultDesc": "desc",
                "CallbackMetadata": {"Item": items or []},
            }
        }
    }


class TestParseCallback:
    @pytest.mark.asyncio
    async def test_success_extracts_receipt_and_amount(self) -> None:
        provider = _make_provider(_router({}))
        payload = _callback(
            0,
            [
                {"Name": "Amount", "Value": 1500},
                {"Name": "MpesaReceiptNumber", "Value": "NLJ999"},
                {"Name": "PhoneNumber", "Value": 254712345678},
                {"Name": "TransactionDate", "Value": 20240115103000},
            ],
        )
        result = await provider.parse_callback(payload)
        assert result.status is ProviderPaymentStatus.COMPLETED
        assert result.amount_cents == 150000
        assert result.mpesa_receipt_number == "NLJ999"
        assert result.failure_reason is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("code", "status", "reason"),
        [
            (1, ProviderPaymentStatus.FAILED, FailureReason.INSUFFICIENT_FUNDS),
            (1032, ProviderPaymentStatus.CANCELLED, FailureReason.CANCELLED_BY_USER),
            (2001, ProviderPaymentStatus.FAILED, FailureReason.WRONG_PIN),
            (1037, ProviderPaymentStatus.FAILED, FailureReason.TIMEOUT),
            (9999, ProviderPaymentStatus.FAILED, FailureReason.UNKNOWN),
        ],
    )
    async def test_failure_codes(
        self, code: int, status: ProviderPaymentStatus, reason: FailureReason
    ) -> None:
        provider = _make_provider(_router({}))
        result = await provider.parse_callback(_callback(code))
        assert result.status is status
        assert result.failure_reason is reason

    @pytest.mark.asyncio
    async def test_malformed_callback_raises(self) -> None:
        provider = _make_provider(_router({}))
        with pytest.raises(PaymentProviderError, match="Malformed"):
            await provider.parse_callback({"Body": {}})


# =============================================================================
# validate_callback
# =============================================================================


class TestValidateCallback:
    @pytest.mark.asyncio
    async def test_valid_shape(self) -> None:
        provider = _make_provider(_router({}))
        assert await provider.validate_callback(_callback(0), {}) is True

    @pytest.mark.asyncio
    async def test_malformed_shape_false(self) -> None:
        provider = _make_provider(_router({}))
        assert await provider.validate_callback({"Body": {}}, {}) is False

    @pytest.mark.asyncio
    async def test_token_required(self) -> None:
        provider = _make_provider(_router({}), callback_token="secret")
        with pytest.raises(CallbackVerificationError):
            await provider.validate_callback(_callback(0), {})
        assert await provider.validate_callback(_callback(0), {"x-callback-token": "secret"})

    @pytest.mark.asyncio
    async def test_ip_allowlist(self) -> None:
        provider = _make_provider(_router({}), allowed_callback_ips=("196.201.214.200",))
        with pytest.raises(CallbackVerificationError):
            await provider.validate_callback(_callback(0), {"x-real-ip": "10.0.0.1"})
        assert await provider.validate_callback(
            _callback(0), {"x-forwarded-for": "196.201.214.200, 10.0.0.1"}
        )


# =============================================================================
# refund
# =============================================================================


class TestRefund:
    @pytest.mark.asyncio
    async def test_refund_not_supported(self) -> None:
        provider = _make_provider(_router({}))
        with pytest.raises(NotSupportedError):
            await provider.refund("NLJ123", 150000, "customer request")
