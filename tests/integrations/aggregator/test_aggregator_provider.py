"""
Unit tests for AggregatorPaymentProvider (Pesapal / IntaSend / generic).

These use httpx.MockTransport so they are fast, deterministic and need no
running server. The provider only translates between the aggregator wire
format and the provider-neutral domain types; no business arithmetic and no
state changes to orders happen here.
"""

from __future__ import annotations

import hmac
import json
from typing import Any

import httpx
import pytest

from packages.core.domain.payment import (
    CallbackVerificationError,
    PaymentProviderError,
    ProviderPaymentStatus,
    STKPushRequest,
)
from packages.integrations.aggregator.client import AggregatorPaymentProvider

BASE_URL = "https://api.aggregator.example.test"


def _make_provider(
    handler: Any,
    **overrides: Any,
) -> AggregatorPaymentProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    kwargs: dict[str, Any] = {
        "api_key": "ak",
        "api_secret": "as",
        "base_url": BASE_URL,
        "callback_url": "https://app.example.test/cb",
        "webhook_secret": "",
        "http_client": client,
    }
    kwargs.update(overrides)
    return AggregatorPaymentProvider(**kwargs)


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


@pytest.mark.asyncio
async def test_initiate_stk_push_success_normalizes_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mpesa/stkpush"
        body = json.loads(request.content)
        assert body["amount"] == 1500  # whole shillings, not cents
        assert body["account_reference"] == "ORDER-123"
        return httpx.Response(
            200,
            json={
                "transaction_id": "agg_123",
                "merchant_reference": "mref_1",
                "status_code": "0",
                "message": "Accepted",
                "customer_message": "Enter PIN",
            },
        )

    provider = _make_provider(handler)
    response = await provider.initiate_stk_push(_stk_request())
    assert response.checkout_request_id == "agg_123"
    assert response.merchant_request_id == "mref_1"
    assert response.response_code == "0"
    assert response.customer_message == "Enter PIN"
    await provider.aclose()


@pytest.mark.asyncio
async def test_initiate_stk_push_accepts_success_codes() -> None:
    for code in ("success", "pending", "200"):

        def handler(request: httpx.Request, _code: str = code) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "checkout_request_id": "agg_x",
                    "status_code": _code,
                },
            )

        provider = _make_provider(handler)
        response = await provider.initiate_stk_push(_stk_request())
        assert response.checkout_request_id == "agg_x"
        await provider.aclose()


@pytest.mark.asyncio
async def test_initiate_stk_push_rejects_failure_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "checkout_request_id": "agg_x",
                "status_code": "1",
                "message": "Declined",
            },
        )

    provider = _make_provider(handler)
    with pytest.raises(PaymentProviderError, match="rejected"):
        await provider.initiate_stk_push(_stk_request())
    await provider.aclose()


@pytest.mark.asyncio
async def test_initiate_stk_push_missing_checkout_id_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status_code": "0"})

    provider = _make_provider(handler)
    with pytest.raises(PaymentProviderError, match="checkout_request_id"):
        await provider.initiate_stk_push(_stk_request())
    await provider.aclose()


@pytest.mark.asyncio
async def test_initiate_stk_push_rejects_sub_shilling_amounts() -> None:
    provider = _make_provider(lambda _request: httpx.Response(200, json={}))
    with pytest.raises(PaymentProviderError, match="whole shillings"):
        await provider.initiate_stk_push(_stk_request(amount_cents=15050))
    await provider.aclose()


@pytest.mark.asyncio
async def test_initiate_stk_push_http_error_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    provider = _make_provider(handler)
    with pytest.raises(PaymentProviderError, match="STK push failed"):
        await provider.initiate_stk_push(_stk_request())
    await provider.aclose()


# =============================================================================
# query_status
# =============================================================================


@pytest.mark.asyncio
async def test_query_status_completed_normalizes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mpesa/stkpush/status/agg_1"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "mpesa_receipt_number": "MPE123",
                "amount": 1500,  # KSH -> cents
            },
        )

    provider = _make_provider(handler)
    result = await provider.query_status("agg_1")
    assert result.status is ProviderPaymentStatus.COMPLETED
    assert result.mpesa_receipt_number == "MPE123"
    assert result.amount_cents == 150000
    await provider.aclose()


@pytest.mark.asyncio
async def test_query_status_not_found_is_pending() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    provider = _make_provider(handler)
    result = await provider.query_status("agg_missing")
    assert result.status is ProviderPaymentStatus.PENDING
    await provider.aclose()


@pytest.mark.asyncio
async def test_query_status_cancelled_and_failed_map() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "cancelled" in request.url.path:
            return httpx.Response(200, json={"status": "cancelled_by_user"})
        return httpx.Response(200, json={"status": "timeout"})

    provider = _make_provider(handler)
    cancelled = await provider.query_status("cancelled")
    assert cancelled.status is ProviderPaymentStatus.CANCELLED
    failed = await provider.query_status("timeout")
    assert failed.status is ProviderPaymentStatus.FAILED
    assert failed.failure_reason == "timeout"
    await provider.aclose()


# =============================================================================
# validate_callback / parse_callback
# =============================================================================


@pytest.mark.asyncio
async def test_validate_callback_hmac_pass_and_fail() -> None:
    payload = {"checkout_request_id": "agg_1", "status": "completed"}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    good = hmac.new(b"whsec", body, "sha256").hexdigest()

    provider = _make_provider(lambda _request: httpx.Response(200), webhook_secret="whsec")
    assert await provider.validate_callback(payload, {"x-webhook-signature": good}) is True

    with pytest.raises(CallbackVerificationError, match="Invalid webhook signature"):
        await provider.validate_callback(payload, {"x-webhook-signature": "deadbeef"})
    await provider.aclose()


@pytest.mark.asyncio
async def test_validate_callback_missing_signature_rejected() -> None:
    provider = _make_provider(lambda _request: httpx.Response(200), webhook_secret="whsec")
    with pytest.raises(CallbackVerificationError, match="Missing webhook signature"):
        await provider.validate_callback({"checkout_request_id": "x", "status": "completed"}, {})
    await provider.aclose()


@pytest.mark.asyncio
async def test_validate_callback_ip_allowlist() -> None:
    payload = {"checkout_request_id": "agg_1", "status": "completed"}
    provider = _make_provider(
        lambda _request: httpx.Response(200),
        webhook_secret="",
        allowed_callback_ips=("203.0.113.10",),
    )
    assert await provider.validate_callback(payload, {"x-real-ip": "203.0.113.10"}) is True
    with pytest.raises(CallbackVerificationError, match="not allowed"):
        await provider.validate_callback(payload, {"x-forwarded-for": "198.51.100.9, 10.0.0.1"})
    await provider.aclose()


@pytest.mark.asyncio
async def test_validate_callback_requires_fields() -> None:
    provider = _make_provider(lambda _request: httpx.Response(200))
    assert await provider.validate_callback({"status": "completed"}, {}) is False
    await provider.aclose()


@pytest.mark.asyncio
async def test_parse_callback_normalizes_amount_cents() -> None:
    provider = _make_provider(lambda _request: httpx.Response(200))
    result = await provider.parse_callback({
        "checkout_request_id": "agg_9",
        "status": "success",
        "receipt_number": "MPE999",
        "amount_cents": 250000,
    })
    assert result.status is ProviderPaymentStatus.COMPLETED
    assert result.checkout_request_id == "agg_9"
    assert result.mpesa_receipt_number == "MPE999"
    assert result.amount_cents == 250000
    await provider.aclose()


# =============================================================================
# refund
# =============================================================================


@pytest.mark.asyncio
async def test_refund_success_and_failure() -> None:
    def ok_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mpesa/refund"
        return httpx.Response(200, json={"status": "refunded"})

    provider = _make_provider(ok_handler)
    assert await provider.refund("MPE1", 50000, "Customer request") is True
    await provider.aclose()

    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "rejected"})

    provider = _make_provider(fail_handler)
    assert await provider.refund("MPE1", 50000, "Customer request") is False
    await provider.aclose()


@pytest.mark.asyncio
async def test_factory_builds_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings are an import-time singleton (env read once), so patch the
    # instance attributes directly — the same pattern the payments API tests
    # use for DARAJA_CALLBACK_TOKEN.
    from packages.core.config import get_settings
    from packages.integrations.aggregator.factory import build_aggregator_provider

    settings = get_settings()
    monkeypatch.setattr(settings, "aggregator_api_key", "ak")
    monkeypatch.setattr(settings, "aggregator_api_secret", "as")
    monkeypatch.delenv("AGGREGATOR_BASE_URL", raising=False)

    provider = build_aggregator_provider()
    assert isinstance(provider, AggregatorPaymentProvider)
    # Factory fallback when no base URL is configured.
    assert provider._base_url == "https://api.aggregator.example"
    await provider.aclose()
