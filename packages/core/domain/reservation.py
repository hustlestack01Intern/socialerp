"""
Reservation Domain Models

Reservation logic for stock - created on order, expires via job, converts on payment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

# =============================================================================
# Enums
# =============================================================================


class ReservationStatus(str, Enum):
    """Reservation lifecycle status."""

    ACTIVE = "active"  # Valid, holding stock
    EXPIRED = "expired"  # TTL expired, stock released
    RELEASED = "released"  # Manually released (cancelled)
    FULFILLED = "fulfilled"  # Converted to sale (payment confirmed)


class ReservationReason(str, Enum):
    """Reason for reservation."""

    ORDER_CREATED = "order_created"
    MANUAL = "manual"


# =============================================================================
# Value Objects
# =============================================================================


@dataclass(frozen=True)
class Reservation:
    """Stock reservation - ephemeral claim on stock."""

    tenant_id: UUID
    product_id: UUID
    variant_id: UUID | None
    quantity: int
    reference_id: UUID | None = None  # Order ID
    reference_type: str = "order"
    id: UUID = field(default_factory=uuid4)
    status: ReservationStatus = ReservationStatus.ACTIVE
    reason: ReservationReason = ReservationReason.ORDER_CREATED
    expires_at: datetime = field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=15))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    released_at: datetime | None = None
    fulfilled_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Reservation quantity must be positive")
        if self.expires_at <= datetime.now(UTC):
            raise ValueError("Expiry must be in the future")

    @property
    def is_active(self) -> bool:
        return self.status == ReservationStatus.ACTIVE and self.expires_at > datetime.now(UTC)

    @property
    def is_expired(self) -> bool:
        return self.status == ReservationStatus.EXPIRED or self.expires_at <= datetime.now(UTC)

    @property
    def time_remaining(self) -> timedelta | None:
        if self.status != ReservationStatus.ACTIVE:
            return None
        remaining = self.expires_at - datetime.now(UTC)
        return max(timedelta(0), remaining)

    def with_status(
        self, new_status: ReservationStatus, timestamp: datetime | None = None
    ) -> Reservation:
        """Return new reservation with updated status (immutable)."""
        ts = timestamp or datetime.now(UTC)

        released_at = self.released_at
        fulfilled_at = self.fulfilled_at
        if new_status == ReservationStatus.RELEASED:
            released_at = ts
        elif new_status == ReservationStatus.FULFILLED:
            fulfilled_at = ts
        elif new_status == ReservationStatus.EXPIRED:
            released_at = ts

        return Reservation(
            id=self.id,
            tenant_id=self.tenant_id,
            product_id=self.product_id,
            variant_id=self.variant_id,
            quantity=self.quantity,
            status=new_status,
            reason=self.reason,
            reference_id=self.reference_id,
            reference_type=self.reference_type,
            expires_at=self.expires_at,
            created_at=self.created_at,
            released_at=released_at,
            fulfilled_at=fulfilled_at,
            metadata=self.metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "product_id": str(self.product_id),
            "variant_id": str(self.variant_id) if self.variant_id else None,
            "quantity": self.quantity,
            "status": self.status.value,
            "reason": self.reason.value,
            "reference_id": str(self.reference_id) if self.reference_id else None,
            "reference_type": self.reference_type,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "released_at": self.released_at.isoformat() if self.released_at else None,
            "fulfilled_at": self.fulfilled_at.isoformat() if self.fulfilled_at else None,
        }


# =============================================================================
# Reservation Manager
# =============================================================================


@dataclass
class ReservationManager:
    """
    Manages reservation lifecycle.

    Responsibilities:
    - Create reservations on order creation
    - Expire reservations via background job
    - Release reservations on cancellation
    - Fulfill reservations on payment
    """

    tenant_id: UUID
    default_ttl_minutes: int = 15

    # In-memory cache (for performance)
    _reservations: dict[UUID, Reservation] = field(default_factory=dict, init=False, repr=False)

    def create(
        self,
        product_id: UUID,
        variant_id: UUID | None,
        quantity: int,
        reference_id: UUID,  # Order ID
        reference_type: str = "order",
        ttl_minutes: int | None = None,
    ) -> Reservation:
        """Create a new reservation."""

        expires_at = datetime.now(UTC) + timedelta(minutes=ttl_minutes or self.default_ttl_minutes)

        reservation = Reservation(
            tenant_id=self.tenant_id,
            product_id=product_id,
            variant_id=variant_id,
            quantity=quantity,
            reference_id=reference_id,
            reference_type=reference_type,
            expires_at=expires_at,
        )

        self._reservations[reservation.id] = reservation
        return reservation

    def release(self, reservation_id: UUID, reason: str = "cancelled") -> bool:
        """Release a reservation (cancelled/expired)."""
        reservation = self._reservations.get(reservation_id)
        if not reservation or reservation.status != ReservationStatus.ACTIVE:
            return False

        self._reservations[reservation_id] = reservation.with_status(ReservationStatus.RELEASED)
        return True

    def fulfill(self, reservation_id: UUID) -> bool:
        """Mark reservation as fulfilled (payment confirmed)."""
        reservation = self._reservations.get(reservation_id)
        if not reservation or reservation.status != ReservationStatus.ACTIVE:
            return False

        self._reservations[reservation_id] = reservation.with_status(ReservationStatus.FULFILLED)
        return True

    def expire(self, reservation_id: UUID) -> bool:
        """Mark reservation as expired (called by background job)."""
        reservation = self._reservations.get(reservation_id)
        if not reservation or reservation.status != ReservationStatus.ACTIVE:
            return False

        self._reservations[reservation_id] = reservation.with_status(ReservationStatus.EXPIRED)
        return True

    def get_active_for_product(
        self, product_id: UUID, variant_id: UUID | None = None
    ) -> list[Reservation]:
        """Get all active reservations for a product."""
        results: list[Reservation] = []
        for r in self._reservations.values():
            if (
                r.product_id == product_id
                and r.status == ReservationStatus.ACTIVE
                and (variant_id is None or r.variant_id == variant_id)
            ):
                results.append(r)
        return results

    def get_total_reserved(self, product_id: UUID, variant_id: UUID | None = None) -> int:
        """Get total reserved quantity for a product."""
        total = 0
        for r in self._reservations.values():
            if (
                r.product_id == product_id
                and r.status == ReservationStatus.ACTIVE
                and (variant_id is None or r.variant_id == variant_id)
            ):
                total += r.quantity
        return total

    def get_active_reservations(self) -> list[Reservation]:
        """Get all active reservations."""
        return [r for r in self._reservations.values() if r.status == ReservationStatus.ACTIVE]

    def get_expired_reservations(self) -> list[Reservation]:
        """Get reservations that have expired but not yet released."""
        return [
            r
            for r in self._reservations.values()
            if r.status == ReservationStatus.ACTIVE and r.expires_at <= datetime.now(UTC)
        ]


# Need to import Any
