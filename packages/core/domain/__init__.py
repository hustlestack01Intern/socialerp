"""
Domain Primitives

Strongly-typed value objects for the SocialERP domain.
These types enforce invariants at the type level and prevent primitive obsession.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import NewType
from uuid import UUID, uuid4

try:
    from typing import Self
except ImportError:
    from typing import Self


# =============================================================================
# Identifier Types (NewType for zero-cost abstraction)
# =============================================================================

TenantId = NewType("TenantId", UUID)
UserId = NewType("UserId", UUID)
CustomerId = NewType("CustomerId", UUID)
ProductId = NewType("ProductId", UUID)
VariantId = NewType("VariantId", UUID)
OrderId = NewType("OrderId", UUID)
ReservationId = NewType("ReservationId", UUID)
PaymentId = NewType("PaymentId", UUID)
DeliveryId = NewType("DeliveryId", UUID)
RiderId = NewType("RiderId", UUID)
ConversationId = NewType("ConversationId", UUID)
MessageId = NewType("MessageId", UUID)
WebhookId = NewType("WebhookId", str)  # Provider message ID for idempotency
CheckoutRequestId = NewType("CheckoutRequestId", str)  # Daraja STK Push ID


def generate_tenant_id() -> TenantId:
    return TenantId(uuid4())


def generate_user_id() -> UserId:
    return UserId(uuid4())


def generate_customer_id() -> CustomerId:
    return CustomerId(uuid4())


def generate_product_id() -> ProductId:
    return ProductId(uuid4())


def generate_variant_id() -> VariantId:
    return VariantId(uuid4())


def generate_order_id() -> OrderId:
    return OrderId(uuid4())


def generate_reservation_id() -> ReservationId:
    return ReservationId(uuid4())


def generate_payment_id() -> PaymentId:
    return PaymentId(uuid4())


def generate_delivery_id() -> DeliveryId:
    return DeliveryId(uuid4())


def generate_rider_id() -> RiderId:
    return RiderId(uuid4())


def generate_conversation_id() -> ConversationId:
    return ConversationId(uuid4())


def generate_message_id() -> MessageId:
    return MessageId(uuid4())


# =============================================================================
# Value Objects
# =============================================================================


@dataclass(frozen=True)
class CustomerPhone:
    """
    Kenyan phone number normalized to 2547XXXXXXXX or 2541XXXXXXXX format.

    Invariants:
    - Always stored as 254XXXXXXXXX (12 digits, no +, no spaces)
    - Must start with 2547 or 2541 (Safaricom/Airtel/Telkom prefixes)
    - Validation on construction
    """

    value: str

    _KENYA_PATTERN = re.compile(r"^254[17]\d{8}$")

    def __post_init__(self) -> None:
        normalized = self._normalize(self.value)
        if not self._KENYA_PATTERN.match(normalized):
            raise ValueError(
                f"Invalid Kenyan phone number: {self.value}. Must be 2547XXXXXXXX or 2541XXXXXXXX"
            )
        object.__setattr__(self, "value", normalized)

    @classmethod
    def _normalize(cls, phone: str) -> str:
        """Normalize various phone formats to 254XXXXXXXXX."""
        # Remove all non-digits
        digits = re.sub(r"\D", "", phone)

        # Handle common formats
        if digits.startswith("254") and len(digits) == 12:
            return digits
        elif digits.startswith("0") and len(digits) == 10:
            return "254" + digits[1:]
        elif (digits.startswith("7") and len(digits) == 9) or (
            digits.startswith("1") and len(digits) == 9
        ):
            return "254" + digits
        elif digits.startswith("254") and len(digits) == 13:
            # Handle +254
            return digits
        else:
            # Return as-is; validation will catch invalid
            return digits

    @classmethod
    def from_raw(cls, phone: str) -> Self:
        """Create from raw input (handles +254, 07xx, 7xx, etc.)."""
        return cls(phone)

    def __str__(self) -> str:
        return self.value

    def masked(self) -> str:
        """Return masked version for logging (e.g., 2547****1234)."""
        if len(self.value) >= 4:
            return self.value[:-4] + "****"
        return "****"


@dataclass(frozen=True)
class Money:
    """
    Monetary amount in Kenyan Shillings (KES), stored as integer cents.

    Invariants:
    - Always non-negative
    - Stored as integer cents (KES * 100)
    - Arithmetic operations preserve precision
    - No floating-point arithmetic
    """

    cents: int

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int):
            raise TypeError(f"Money cents must be an integer, got {type(self.cents).__name__}")
        if self.cents < 0:
            raise ValueError("Money cannot be negative")

    @classmethod
    def from_kes(cls, kes: float | Decimal) -> Self:
        """Create from KES amount (e.g., 100.50 -> 10050 cents).

        Rounds half-up to the nearest cent. Truncating (``int(kes * 100)``) loses
        a cent whenever the binary float sits just below the true value, e.g.
        ``1 + 82/100`` is represented as ``1.8199999999999998`` and would truncate
        to 181 cents instead of 182.
        """
        if isinstance(kes, float):
            # Parse the shortest round-trippable decimal form to avoid binary
            # float representation error (e.g. 1.8199999999999998).
            kes = Decimal(str(kes))
        elif isinstance(kes, int):
            kes = Decimal(kes)
        elif not isinstance(kes, Decimal):
            raise TypeError(
                f"Money.from_kes expects float, int or Decimal, got {type(kes).__name__}"
            )
        # Round half-up to whole cents (standard money convention).
        cents = int((kes * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        return cls(cents)

    @classmethod
    def from_cents(cls, cents: int) -> Self:
        """Create directly from cents."""
        return cls(cents)

    @property
    def kes(self) -> Decimal:
        """Return as Decimal KES (e.g., 10050 -> 100.50)."""
        return Decimal(self.cents) / 100

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.cents + other.cents)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        result = self.cents - other.cents
        if result < 0:
            raise ValueError("Money subtraction would result in negative amount")
        return Money(result)

    def __mul__(self, multiplier: object) -> Money:
        if not isinstance(multiplier, int):
            return NotImplemented
        if multiplier < 0:
            raise ValueError("Cannot multiply money by negative number")
        return Money(self.cents * multiplier)

    def __rmul__(self, multiplier: int) -> Money:
        return self.__mul__(multiplier)

    def __truediv__(self, divisor: int) -> Money:
        if not isinstance(divisor, int):
            return NotImplemented
        if divisor <= 0:
            raise ValueError("Cannot divide money by zero or negative number")
        return Money(self.cents // divisor)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.cents < other.cents

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.cents <= other.cents

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.cents > other.cents

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.cents >= other.cents

    def __str__(self) -> str:
        return f"KES {self.kes:.2f}"

    def __repr__(self) -> str:
        return f"Money(cents={self.cents})"


@dataclass(frozen=True)
class Quantity:
    """
    Quantity of items (integer units).

    Invariants:
    - Always non-negative integer
    - Represents whole units only (no fractional quantities in MVP)
    """

    value: int

    def __post_init__(self) -> None:
        if not isinstance(self.value, int):
            raise TypeError("Quantity must be an integer")
        if self.value < 0:
            raise ValueError("Quantity cannot be negative")

    def __add__(self, other: object) -> Quantity:
        if not isinstance(other, Quantity):
            return NotImplemented
        return Quantity(self.value + other.value)

    def __sub__(self, other: object) -> Quantity:
        if not isinstance(other, Quantity):
            return NotImplemented
        result = self.value - other.value
        if result < 0:
            raise ValueError("Quantity subtraction would result in negative amount")
        return Quantity(result)

    def __mul__(self, multiplier: object) -> Quantity:
        if not isinstance(multiplier, int):
            return NotImplemented
        if multiplier < 0:
            raise ValueError("Cannot multiply quantity by negative number")
        return Quantity(self.value * multiplier)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.value < other.value

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.value <= other.value

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.value > other.value

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return self.value >= other.value

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class SKU:
    """
    Stock Keeping Unit - unique product identifier within a tenant.

    Invariants:
    - Alphanumeric, hyphens, underscores only
    - Max 50 characters
    - Case-insensitive uniqueness (stored uppercase)
    """

    value: str

    _PATTERN = re.compile(r"^[A-Z0-9_-]{1,50}$")

    def __post_init__(self) -> None:
        normalized = self.value.upper().strip()
        if not self._PATTERN.match(normalized):
            raise ValueError(
                f"Invalid SKU: {self.value}. Must be alphanumeric, hyphens, underscores "
                "only, max 50 chars"
            )
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


# =============================================================================
# Enums
# =============================================================================


class PaymentStatus(str, Enum):
    """Payment statuses."""

    PENDING = "pending"
    INITIATED = "initiated"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentProvider(str, Enum):
    """Payment provider types."""

    DARAJA = "daraja"
    AGGREGATOR = "aggregator"
    MANUAL = "manual"


class AutonomyLevel(str, Enum):
    """Agent autonomy levels per tenant."""

    L0 = "L0"  # Shadow
    L1 = "L1"  # Answer
    L2 = "L2"  # Transact
    L3 = "L3"  # Optimize


class MessageDirection(str, Enum):
    """Message direction."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ChannelType(str, Enum):
    """Communication channel types."""

    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"


# =============================================================================
# Constants
# =============================================================================

# Currency
KES = "KES"

# Default values
DEFAULT_AUTONOMY_LEVEL = AutonomyLevel.L0
DEFAULT_CURRENCY = KES

# Limits
MAX_SKU_LENGTH = 50
MAX_PRODUCT_NAME_LENGTH = 200
MAX_VARIANT_NAME_LENGTH = 100
MAX_MESSAGE_LENGTH = 4096  # WhatsApp limit
