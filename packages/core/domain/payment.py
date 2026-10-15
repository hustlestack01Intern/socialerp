"""
Payment Domain: PaymentProvider interface and value objects.

Implements the contract from ADR 0004. Concrete providers (a tenant's own
Daraja credentials, a licensed aggregator, or the manual fallback) live in
``packages.integrations`` and are the only code that knows provider-specific
payload shapes.

Naming note (ADR 0004): the ADR calls the status value object ``PaymentStatus``.
The name ``PaymentStatus`` is already taken by the order/payment status enum
(``packages.core.domain`` and ``packages.core.models.order``), so this module
uses ``ProviderPaymentResult`` for the provider-neutral status value object.
The interface method names and fields match the ADR.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

# =============================================================================
# Provider-neutral status
# =============================================================================


class ProviderPaymentStatus(str, Enum):
    """Standardized payment status returned by any provider."""

    PENDING = "pending"  # still processing / no terminal result yet
    COMPLETED = "completed"  # money received and matched
    FAILED = "failed"  # terminal failure (funds, wrong PIN, timeout)
    CANCELLED = "cancelled"  # customer cancelled the prompt
    PARTIAL = "partial"  # less than the expected amount received


class FailureReason(str, Enum):
    """Why a payment failed, independent of provider result codes."""

    INSUFFICIENT_FUNDS = "insufficient_funds"
    WRONG_PIN = "wrong_pin"
    CANCELLED_BY_USER = "cancelled_by_user"
    TIMEOUT = "timeout"
    WRONG_AMOUNT = "wrong_amount"
    PARTIAL_PAYMENT = "partial_payment"
    UNKNOWN = "unknown"


# Daraja (M-Pesa) result codes we care about.
RESULT_CODE_SUCCESS = 0
RESULT_CODE_INSUFFICIENT_FUNDS = 1
RESULT_CODE_CANCELLED_BY_USER = 1032
RESULT_CODE_TIMEOUT = 1037  # user did not enter PIN / DS timeout
RESULT_CODE_WRONG_PIN = 2001
RESULT_CODE_STILL_PROCESSING = 1001  # "unable to complete" / still in flight


def classify_result_code(result_code: int) -> tuple[ProviderPaymentStatus, FailureReason | None]:
    """Map a Daraja result code to (status, failure_reason).

    ``failure_reason`` is None only for COMPLETED and PENDING.
    """
    if result_code == RESULT_CODE_SUCCESS:
        return ProviderPaymentStatus.COMPLETED, None
    if result_code == RESULT_CODE_STILL_PROCESSING:
        return ProviderPaymentStatus.PENDING, None
    if result_code == RESULT_CODE_CANCELLED_BY_USER:
        return ProviderPaymentStatus.CANCELLED, FailureReason.CANCELLED_BY_USER
    if result_code == RESULT_CODE_INSUFFICIENT_FUNDS:
        return ProviderPaymentStatus.FAILED, FailureReason.INSUFFICIENT_FUNDS
    if result_code == RESULT_CODE_WRONG_PIN:
        return ProviderPaymentStatus.FAILED, FailureReason.WRONG_PIN
    if result_code == RESULT_CODE_TIMEOUT:
        return ProviderPaymentStatus.FAILED, FailureReason.TIMEOUT
    return ProviderPaymentStatus.FAILED, FailureReason.UNKNOWN


# =============================================================================
# Value objects (ADR 0004)
# =============================================================================


@dataclass(frozen=True)
class STKPushRequest:
    """Input to ``PaymentProvider.initiate_stk_push``."""

    phone_number: str  # Normalized 2547XXXXXXXX / 2541XXXXXXXX
    amount_cents: int  # KES cents (whole shillings sent to Daraja)
    account_reference: str  # usually the order id
    transaction_desc: str
    callback_url: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.amount_cents <= 0:
            raise ValueError("STK push amount must be positive")
        if not self.phone_number.startswith("254"):
            raise ValueError("Phone number must be normalized to 254XXXXXXXXX")


@dataclass(frozen=True)
class STKPushResponse:
    """Immediate response to an STK push (the callback arrives later)."""

    checkout_request_id: str
    merchant_request_id: str
    response_code: str
    response_description: str
    customer_message: str = ""


@dataclass(frozen=True)
class ProviderPaymentResult:
    """Standardized provider status (ADR 0004 ``PaymentStatus``)."""

    status: ProviderPaymentStatus
    checkout_request_id: str
    merchant_request_id: str | None = None
    mpesa_receipt_number: str | None = None
    result_code: int | None = None
    result_desc: str | None = None
    amount_cents: int | None = None
    phone_number: str | None = None
    transaction_date: str | None = None
    failure_reason: FailureReason | None = None
    raw: dict[str, Any] | None = None


# =============================================================================
# Errors
# =============================================================================


class PaymentProviderError(Exception):
    """Base error for payment provider operations."""


class CallbackVerificationError(PaymentProviderError):
    """Raised when an inbound callback fails authenticity checks."""


class AmountMismatchError(PaymentProviderError):
    """Raised when a callback amount does not match the expected order total."""


class NotSupportedError(PaymentProviderError):
    """Raised when a provider does not support an operation (e.g. refunds)."""


# =============================================================================
# Interface (ADR 0004)
# =============================================================================


class PaymentProvider(ABC):
    """Abstract M-Pesa payment provider.

    Implementations must never do business arithmetic: they translate between
    provider payloads and this interface. Validation and state changes belong
    to the payment service.
    """

    @abstractmethod
    async def initiate_stk_push(self, request: STKPushRequest) -> STKPushResponse:
        """Initiate STK Push. Returns immediately; callback comes later."""

    @abstractmethod
    async def query_status(self, checkout_request_id: str) -> ProviderPaymentResult:
        """Query status (fallback for missing/late callbacks)."""

    @abstractmethod
    async def validate_callback(self, payload: dict[str, Any], headers: dict[str, str]) -> bool:
        """Verify callback authenticity (source allowlist, secret token, shape)."""

    @abstractmethod
    async def parse_callback(self, payload: dict[str, Any]) -> ProviderPaymentResult:
        """Extract standardized status from a callback payload."""

    @abstractmethod
    async def refund(self, mpesa_receipt_number: str, amount_cents: int, reason: str) -> bool:
        """Initiate a refund (human-approved only)."""


__all__ = [
    "AmountMismatchError",
    "CallbackVerificationError",
    "FailureReason",
    "NotSupportedError",
    "PaymentProvider",
    "PaymentProviderError",
    "ProviderPaymentResult",
    "ProviderPaymentStatus",
    "STKPushRequest",
    "STKPushResponse",
    "classify_result_code",
]
