"""
Stock Domain Models

Event-sourced stock ledger - immutable movements, never overwrites quantities.
Stock = sum of immutable movements (receive, reserve, release, sell, adjust, return).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

# =============================================================================
# Enums
# =============================================================================


class StockMovementType(str, Enum):
    """Types of stock movements - immutable ledger entries."""

    RECEIVE = "receive"  # Stock received from supplier
    RESERVE = "reserve"  # Stock reserved for order
    RELEASE = "release"  # Reservation released (expired/cancelled)
    SELL = "sell"  # Stock sold (confirmed payment)
    ADJUST = "adjust"  # Manual adjustment (count correction)
    RETURN = "return"  # Customer return


class StockMovementReason(str, Enum):
    """Reasons for stock movements."""

    PURCHASE_ORDER = "purchase_order"
    ORDER_CREATED = "order_created"
    PAYMENT_CONFIRMED = "payment_confirmed"
    ORDER_CANCELLED = "order_cancelled"
    RESERVATION_EXPIRED = "reservation_expired"
    MANUAL_COUNT = "manual_count"
    DAMAGED = "damaged"
    RETURNED = "returned"
    OTHER = "other"


# =============================================================================
# Value Objects
# =============================================================================


@dataclass(frozen=True)
class StockMovement:
    """Immutable stock movement record - append-only ledger entry."""

    tenant_id: UUID
    product_id: UUID
    variant_id: UUID | None
    movement_type: StockMovementType
    quantity_change: int  # Positive for receive/return/release, negative for reserve/sell/adjust
    reason: StockMovementReason
    reference_id: UUID | None  # Order ID, Reservation ID, etc.
    reference_type: str | None  # "order", "reservation", "purchase_order", etc.
    previous_available: int
    new_available: int
    id: UUID = field(default_factory=uuid4)
    unit_cost_cents: int | None = None  # For receive/adjust
    notes: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_by: UUID | None = None  # User ID who initiated

    def __post_init__(self) -> None:
        # SELL movements have quantity_change=0 (reservation already reduced available)
        if self.quantity_change == 0 and self.movement_type != StockMovementType.SELL:
            raise ValueError("Quantity change cannot be zero (except for SELL)")
        if self.previous_available < 0 or self.new_available < 0:
            raise ValueError("Available quantities cannot be negative")

    @property
    def is_increase(self) -> bool:
        return self.quantity_change > 0

    @property
    def is_decrease(self) -> bool:
        return self.quantity_change < 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "product_id": str(self.product_id),
            "variant_id": str(self.variant_id) if self.variant_id else None,
            "movement_type": self.movement_type.value,
            "quantity_change": self.quantity_change,
            "reason": self.reason.value,
            "reference_id": str(self.reference_id) if self.reference_id else None,
            "reference_type": self.reference_type,
            "previous_available": self.previous_available,
            "new_available": self.new_available,
            "unit_cost_cents": self.unit_cost_cents,
            "notes": self.notes,
            "created_at": self.created_at.isoformat(),
            "created_by": str(self.created_by) if self.created_by else None,
        }


# =============================================================================
# Stock Ledger Aggregate
# =============================================================================


@dataclass
class StockLedger:
    """
    Event-sourced stock ledger - never overwrites quantities.

    Stock = SUM of all immutable movements.
    All operations create new movement records.
    """

    tenant_id: UUID
    product_id: UUID
    variant_id: UUID | None

    # In-memory cache of movements (loaded from DB on first access)
    _movements: list[StockMovement] = field(default_factory=list, init=False, repr=False)
    _available_cache: int | None = field(default=None, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)

    def load(self, movements: list[StockMovement]) -> None:
        """Load movements from database."""
        self._movements = movements
        self._recalculate_available()
        self._loaded = True

    def _recalculate_available(self) -> None:
        """Recalculate available stock from all movements."""
        self._available_cache = sum(m.quantity_change for m in self._movements)

    @property
    def available(self) -> int:
        """Current available stock.

        Exactly the sum of movement deltas; a negative value signals data
        corruption and is surfaced, never silently clamped.
        """
        if not self._loaded:
            raise RuntimeError("Ledger not loaded - call load() first")
        return self._available_cache or 0

    @property
    def reserved(self) -> int:
        """Total reserved quantity (active reservations, reference-keyed)."""
        if not self._loaded:
            raise RuntimeError("Ledger not loaded")
        reference_ids = {
            m.reference_id
            for m in self._movements
            if m.movement_type == StockMovementType.RESERVE and m.reference_id is not None
        }
        return sum(self._reference_remaining(ref) for ref in reference_ids)

    @property
    def on_hand(self) -> int:
        """Physical stock on hand = available + reserved."""
        return self.available + self.reserved

    def reserved_quantity_for(self, reference_id: UUID) -> int:
        """Remaining active reserved quantity for a single reference."""
        return self._reference_remaining(reference_id)

    def _reference_remaining(self, reference_id: UUID) -> int:
        """Active reserved balance for a reference: reserves - releases, closed by a sale.

        Reservations are keyed by reference (the order id passed to ``reserve``).
        A SELL movement carries quantity_change=0 (the reservation already reduced
        available), so a sale closes the reference entirely.
        """
        reserved_qty = sum(
            abs(m.quantity_change)
            for m in self._movements
            if m.movement_type == StockMovementType.RESERVE and m.reference_id == reference_id
        )
        released_qty = sum(
            m.quantity_change
            for m in self._movements
            if m.movement_type == StockMovementType.RELEASE and m.reference_id == reference_id
        )
        if any(
            m.movement_type == StockMovementType.SELL and m.reference_id == reference_id
            for m in self._movements
        ):
            return 0  # Sale closes the reservation
        return max(0, reserved_qty - released_qty)

    def can_reserve(self, quantity: int) -> bool:
        """Check if quantity can be reserved."""
        return self.available >= quantity

    def record_receive(
        self,
        quantity: int,
        unit_cost_cents: int | None,
        reference_id: UUID | None,
        reference_type: str,
        reason: StockMovementReason = StockMovementReason.PURCHASE_ORDER,
        notes: str = "",
        created_by: UUID | None = None,
    ) -> StockMovement:
        """Record stock receipt from supplier."""
        if not isinstance(quantity, int):
            raise TypeError(f"Receive quantity must be an integer, got {type(quantity).__name__}")
        if quantity <= 0:
            raise ValueError("Receive quantity must be positive")
        prev = self.available
        new = prev + quantity

        movement = StockMovement(
            tenant_id=self.tenant_id,
            product_id=self.product_id,
            variant_id=self.variant_id,
            movement_type=StockMovementType.RECEIVE,
            quantity_change=quantity,
            reason=reason,
            reference_id=reference_id,
            reference_type=reference_type,
            previous_available=prev,
            new_available=new,
            unit_cost_cents=unit_cost_cents,
            notes=notes,
            created_by=reference_id,
        )

        self._movements.append(movement)
        self._recalculate_available()
        return movement

    def reserve(
        self,
        quantity: int,
        reference_id: UUID,  # Order ID
        reference_type: str = "order",
        reason: StockMovementReason = StockMovementReason.ORDER_CREATED,
        notes: str = "",
    ) -> StockMovement:
        """Reserve stock for an order."""
        if not isinstance(quantity, int):
            raise TypeError(f"Reserve quantity must be an integer, got {type(quantity).__name__}")
        if quantity <= 0:
            raise ValueError("Reserve quantity must be positive")
        if not self.can_reserve(quantity):
            raise ValueError(
                f"Insufficient stock: available={self.available}, requested={quantity}"
            )

        prev = self.available
        new = prev - quantity

        movement = StockMovement(
            tenant_id=self.tenant_id,
            product_id=self.product_id,
            variant_id=self.variant_id,
            movement_type=StockMovementType.RESERVE,
            quantity_change=-quantity,
            reason=reason,
            reference_id=reference_id,
            reference_type=reference_type,
            previous_available=prev,
            new_available=new,
            notes=notes,
        )

        self._movements.append(movement)
        self._recalculate_available()
        return movement

    def release(
        self,
        quantity: int,
        reference_id: UUID,  # Reservation reference (the order id used at reserve time)
        reference_type: str = "order",
        reason: StockMovementReason = StockMovementReason.ORDER_CANCELLED,
        notes: str = "",
    ) -> StockMovement:
        """Release a reservation (expired or cancelled).

        The reference must have an open reservation, and the released quantity
        cannot exceed what is still reserved for it. Releasing unknown or
        already-closed references raises ValueError (no stock can be conjured).
        """
        if not isinstance(quantity, int):
            raise TypeError(f"Release quantity must be an integer, got {type(quantity).__name__}")
        if quantity <= 0:
            raise ValueError("Release quantity must be positive")
        remaining = self._reference_remaining(reference_id)
        if remaining <= 0:
            raise ValueError(f"No open reservation for reference {reference_id}")
        if quantity > remaining:
            raise ValueError(
                f"Cannot release {quantity}; only {remaining} "
                f"remaining reserved for reference {reference_id}"
            )

        prev = self.available
        new = prev + quantity

        movement = StockMovement(
            tenant_id=self.tenant_id,
            product_id=self.product_id,
            variant_id=self.variant_id,
            movement_type=StockMovementType.RELEASE,
            quantity_change=quantity,
            reason=reason,
            reference_id=reference_id,
            reference_type=reference_type,
            previous_available=prev,
            new_available=new,
            notes=notes,
        )

        self._movements.append(movement)
        self._recalculate_available()
        return movement

    def sell(
        self,
        quantity: int,
        reference_id: UUID,  # Order ID (closes the reservation reserved under it)
        reference_type: str = "order",
        reason: StockMovementReason = StockMovementReason.PAYMENT_CONFIRMED,
        notes: str = "",
    ) -> StockMovement:
        """Record sale (converts reservation to sale).
        Does not change available (reservation already reduced it).
        Checks against on_hand (physical stock)."""
        if not isinstance(quantity, int):
            raise TypeError(f"Sell quantity must be an integer, got {type(quantity).__name__}")
        if quantity <= 0:
            raise ValueError("Sell quantity must be positive")
        if self.on_hand < quantity:
            raise ValueError(
                f"Insufficient on-hand stock: on_hand={self.on_hand}, requested={quantity}"
            )

        prev = self.available
        new = prev  # Available doesn't change - reservation already accounted for it

        movement = StockMovement(
            tenant_id=self.tenant_id,
            product_id=self.product_id,
            variant_id=self.variant_id,
            movement_type=StockMovementType.SELL,
            quantity_change=0,  # Does not affect available
            reason=reason,
            reference_id=reference_id,
            reference_type=reference_type,
            previous_available=prev,
            new_available=new,
            notes=notes,
        )

        self._movements.append(movement)
        self._recalculate_available()
        return movement

    def adjust(
        self,
        new_quantity: int,
        reason: StockMovementReason = StockMovementReason.MANUAL_COUNT,
        reference_id: UUID | None = None,
        notes: str = "",
        created_by: UUID | None = None,
    ) -> StockMovement:
        """Adjust stock to a specific quantity (manual count)."""
        if not isinstance(new_quantity, int):
            raise TypeError(
                f"Adjust quantity must be an integer, got {type(new_quantity).__name__}"
            )
        prev = self.available
        quantity_change = new_quantity - prev

        if quantity_change == 0:
            raise ValueError("Adjustment would not change quantity")

        movement = StockMovement(
            tenant_id=self.tenant_id,
            product_id=self.product_id,
            variant_id=self.variant_id,
            movement_type=StockMovementType.ADJUST,
            quantity_change=quantity_change,
            reason=reason,
            reference_id=reference_id,
            reference_type="adjustment",
            previous_available=prev,
            new_available=new_quantity,
            notes=notes,
            created_by=reference_id,  # Using reference_id as created_by for adjustments
        )

        self._movements.append(movement)
        self._recalculate_available()
        return movement

    def return_stock(
        self,
        quantity: int,
        reference_id: UUID,  # Order ID
        reference_type: str = "order",
        reason: StockMovementReason = StockMovementReason.RETURNED,
        notes: str = "",
    ) -> StockMovement:
        """Record customer return."""
        if not isinstance(quantity, int):
            raise TypeError(f"Return quantity must be an integer, got {type(quantity).__name__}")
        if quantity <= 0:
            raise ValueError("Return quantity must be positive")
        prev = self.available
        new = prev + quantity

        movement = StockMovement(
            tenant_id=self.tenant_id,
            product_id=self.product_id,
            variant_id=self.variant_id,
            movement_type=StockMovementType.RETURN,
            quantity_change=quantity,
            reason=reason,
            reference_id=reference_id,
            reference_type=reference_type,
            previous_available=prev,
            new_available=new,
            notes=notes,
        )

        self._movements.append(movement)
        self._recalculate_available()
        return movement

    def get_movements(
        self,
        movement_type: StockMovementType | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[StockMovement]:
        """Get filtered movements."""
        movements = self._movements

        if movement_type:
            movements = [m for m in movements if m.movement_type == movement_type]

        if since:
            movements = [m for m in movements if m.created_at >= since]

        movements = sorted(movements, key=lambda m: m.created_at, reverse=True)

        if limit:
            movements = movements[:limit]

        return movements

    def get_reservations(self) -> list[StockMovement]:
        """Get active reservations (references with remaining reserved quantity).

        Released/sold reservations are excluded. Uses the same reference-keyed
        accounting as ``reserved``.
        """
        seen: set[UUID] = set()
        reservations: list[StockMovement] = []
        for m in self._movements:
            if (
                m.movement_type == StockMovementType.RESERVE
                and m.reference_id is not None
                and m.reference_id not in seen
                and self._reference_remaining(m.reference_id) > 0
            ):
                seen.add(m.reference_id)
                reservations.append(m)
        return reservations


# =============================================================================
# Movement Repository (for persistence)
# =============================================================================


class StockMovementRepository:
    """Repository for stock movements."""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def add(self, movement: StockMovement) -> StockMovement:
        """Add a new movement to the ledger."""
        from packages.core.models.stock import StockMovement as StockMovementModel

        model = StockMovementModel(
            id=movement.id,
            tenant_id=movement.tenant_id,
            product_id=movement.product_id,
            variant_id=movement.variant_id,
            movement_type=movement.movement_type.value,
            quantity_change=movement.quantity_change,
            reason=movement.reason.value,
            reference_id=movement.reference_id,
            reference_type=movement.reference_type,
            previous_available=movement.previous_available,
            new_available=movement.new_available,
            unit_cost_cents=movement.unit_cost_cents,
            notes=movement.notes,
            created_at=movement.created_at,
            created_by=movement.created_by,
        )

        self.session.add(model)
        await self.session.flush()
        return movement

    async def get_by_tenant_product(
        self,
        tenant_id: UUID,
        product_id: UUID,
        variant_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        """Get movements for a product (for loading ledger)."""
        from sqlalchemy import desc, select

        from packages.core.models.stock import StockMovement as StockMovementModel

        query = select(StockMovementModel).where(
            StockMovementModel.tenant_id == tenant_id,
            StockMovementModel.product_id == product_id,
        )

        if variant_id:
            query = query.where(StockMovementModel.variant_id == variant_id)

        query = query.order_by(desc(StockMovementModel.created_at))

        if limit:
            query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_movements_for_product(
        self,
        tenant_id: UUID,
        product_id: UUID,
        variant_id: UUID | None = None,
        movement_type: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        """Get filtered movements for a product."""
        from sqlalchemy import desc, select

        from packages.core.models.stock import StockMovement as StockMovementModel

        query = select(StockMovementModel).where(
            StockMovementModel.tenant_id == tenant_id,
            StockMovementModel.product_id == product_id,
        )

        if variant_id:
            query = query.where(StockMovementModel.variant_id == variant_id)

        if movement_type:
            query = query.where(StockMovementModel.movement_type == movement_type)

        if since:
            query = query.where(StockMovementModel.created_at >= since)

        query = query.order_by(desc(StockMovementModel.created_at))

        if limit:
            query = query.limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())
