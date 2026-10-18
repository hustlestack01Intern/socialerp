"""Unit tests for the manual (pay-to-Till and confirm) payment provider."""

from __future__ import annotations

import pytest

from packages.core.domain.payment import (
    NotSupportedError,
    ProviderPaymentStatus,
    STKPushRequest,
)
from packages.integrations.manual import ManualPaymentProvider


def _request() -> STKPushRequest:
    return STKPushRequest(
        phone_number="254712345678",
        amount_cents=500_000,
        account_reference="order-1",
        transaction_desc="Order",
        callback_url="https://example.test/callback",
        idempotency_key="k1",
    )


async def test_initiate_returns_manual_checkout_id() -> None:
    provider = ManualPaymentProvider(callback_token="tok", till="123456")
    resp = await provider.initiate_stk_push(_request())
    assert resp.checkout_request_id.startswith("MANUAL-")
    assert "123456" in resp.customer_message


async def test_query_status_is_pending() -> None:
    provider = ManualPaymentProvider(callback_token="tok")
    result = await provider.query_status("MANUAL-abc")
    assert result.status is ProviderPaymentStatus.PENDING


async def test_validate_callback_fails_closed_without_token() -> None:
    provider = ManualPaymentProvider(callback_token="")
    payload = {
        "checkout_request_id": "MANUAL-abc",
        "mpesa_receipt_number": "R1",
        "amount_cents": 100,
    }
    assert await provider.validate_callback(payload, {"x-callback-token": "anything"}) is False


async def test_validate_callback_rejects_wrong_token() -> None:
    provider = ManualPaymentProvider(callback_token="tok")
    payload = {
        "checkout_request_id": "MANUAL-abc",
        "mpesa_receipt_number": "R1",
        "amount_cents": 100,
    }
    assert await provider.validate_callback(payload, {"x-callback-token": "nope"}) is False


async def test_validate_callback_rejects_malformed_payload() -> None:
    provider = ManualPaymentProvider(callback_token="tok")
    assert (
        await provider.validate_callback(
            {"checkout_request_id": "MANUAL-abc"}, {"x-callback-token": "tok"},
        )
        is False
    )


async def test_validate_and_parse_success() -> None:
    provider = ManualPaymentProvider(callback_token="tok")
    payload = {
        "checkout_request_id": "MANUAL-abc",
        "mpesa_receipt_number": "R1",
        "amount_cents": 500_000,
    }
    assert await provider.validate_callback(payload, {"x-callback-token": "tok"}) is True
    result = await provider.parse_callback(payload)
    assert result.status is ProviderPaymentStatus.COMPLETED
    assert result.amount_cents == 500_000
    assert result.mpesa_receipt_number == "R1"


async def test_refund_not_supported() -> None:
    provider = ManualPaymentProvider(callback_token="tok")
    with pytest.raises(NotSupportedError):
        await provider.refund("R1", 100, "no")
