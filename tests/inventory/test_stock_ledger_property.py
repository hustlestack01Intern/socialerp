"""
Property-based tests for stock ledger invariants (ported to packages.core API).

Invariants preserved from the original ADR-era suite, now expressed against the
event-sourced StockLedger in packages.core.domain.stock:

1. Available stock = sum of all movement deltas (conservation)
2. Available stock never negative
3. Reserved stock never negative; available = on_hand - reserved
4. Reservations are keyed by reference (order id); release/sell cannot exceed
   the remaining reserved quantity for that reference
5. Reserve only succeeds when available >= requested
"""

from __future__ import annotations

from uuid import UUID, uuid4

from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from packages.core.domain import ProductId, TenantId, VariantId
from packages.core.domain.stock import (
    StockLedger,
    StockMovementReason,
)

settings.register_profile("ci", max_examples=100)
settings.register_profile("dev", max_examples=500)
settings.load_profile("ci")

quantities = st.integers(min_value=1, max_value=1000)


def make_ledger() -> StockLedger:
    """Create a fresh loaded ledger for each test."""
    ledger = StockLedger(
        tenant_id=TenantId(uuid4()),
        product_id=ProductId(uuid4()),
        variant_id=VariantId(uuid4()),
    )
    ledger.load([])  # Initialize with empty movements
    return ledger


# =============================================================================
# Stateful Machine
# =============================================================================


class StockLedgerMachine(RuleBasedStateMachine):
    """Stateful property tests for stock ledger conservation laws."""

    def __init__(self) -> None:
        super().__init__()
        self.ledger = make_ledger()
        self.open_references: list[UUID] = []

    @initialize()
    def setup(self) -> None:
        self.ledger = make_ledger()
        self.open_references = []

    @rule(quantity=st.integers(min_value=1, max_value=1000))
    def receive_stock(self, quantity: int) -> None:
        """Receive new stock from supplier."""
        self.ledger.record_receive(quantity, 1000, uuid4(), "purchase_order")

    @rule(quantity=st.integers(min_value=1, max_value=100))
    def reserve_stock(self, quantity: int) -> None:
        """Reserve stock for an order (reference-keyed)."""
        if self.ledger.can_reserve(quantity):
            ref = uuid4()
            self.ledger.reserve(quantity, ref, "order")
            self.open_references.append(ref)

    @rule()
    def release_reservation(self) -> None:
        """Release one whole open reservation (cancellation/expiry)."""
        if self.open_references:
            ref = self.open_references.pop()
            self.ledger.release(self.ledger.reserved_quantity_for(ref), ref, "order")

    @rule()
    def sell_stock(self) -> None:
        """Sell one whole open reservation (payment confirmed)."""
        if self.open_references:
            ref = self.open_references.pop()
            self.ledger.sell(self.ledger.reserved_quantity_for(ref), ref, "order")

    @rule(quantity=st.integers(min_value=1, max_value=50))
    def adjust_stock(self, quantity: int) -> None:
        """Manual adjustment (delta from current available)."""
        self.ledger.adjust(
            self.ledger.available + quantity,
            StockMovementReason.MANUAL_COUNT,
            notes="count",
        )

    @rule(quantity=st.integers(min_value=1, max_value=50))
    def return_stock(self, quantity: int) -> None:
        """Customer return."""
        self.ledger.return_stock(quantity, uuid4(), "order")

    @invariant()
    def stock_never_negative(self) -> None:
        """Invariant: available stock should never be negative."""
        assert self.ledger.available >= 0, f"Available stock went negative: {self.ledger.available}"

    @invariant()
    def available_equals_sum_of_movements(self) -> None:
        """Invariant: available == sum of all movement deltas."""
        calculated = sum(m.quantity_change for m in self.ledger.get_movements())
        assert self.ledger.available == calculated, (
            f"Available ({self.ledger.available}) != sum of movements ({calculated})"
        )

    @invariant()
    def reserved_stock_non_negative(self) -> None:
        """Invariant: reserved stock should never be negative."""
        assert self.ledger.reserved >= 0, f"Reserved stock went negative: {self.ledger.reserved}"

    @invariant()
    def available_equals_on_hand_minus_reserved(self) -> None:
        """Invariant: available = on_hand - reserved."""
        assert self.ledger.available == self.ledger.on_hand - self.ledger.reserved, (
            f"Available ({self.ledger.available}) != On_hand ({self.ledger.on_hand}) "
            f"- Reserved ({self.ledger.reserved})"
        )

    @invariant()
    def all_open_references_still_reserved(self) -> None:
        """Invariant: every tracked open reference still contributes to reserved."""
        tracked = sum(self.ledger.reserved_quantity_for(ref) for ref in self.open_references)
        assert tracked <= self.ledger.reserved, (
            f"Tracked reservations ({tracked}) exceed total reserved ({self.ledger.reserved})"
        )


TestStockLedger = StockLedgerMachine.TestCase


# =============================================================================
# SIMPLE PROPERTY TESTS
# =============================================================================


@given(st.lists(st.integers(min_value=1, max_value=100), min_size=1, max_size=20))
@settings(max_examples=100)
def test_stock_equals_sum_of_movements(movements: list[int]) -> None:
    """Property: available stock always equals sum of movement deltas."""
    ledger = make_ledger()
    for qty in movements:
        ledger.record_receive(qty, 1000, uuid4(), "purchase_order")
        if ledger.can_reserve(qty // 2) and qty // 2 > 0:
            ref = uuid4()
            ledger.reserve(qty // 2, ref, "order")
            ledger.release(qty // 2, ref, "order")
        if qty >= 4:
            ledger.sell(qty // 4, uuid4(), "order")

    assert ledger.available == sum(m.quantity_change for m in ledger.get_movements())


@given(st.integers(min_value=0, max_value=1000), st.integers(min_value=1, max_value=1000))
@settings(max_examples=100)
def test_reserve_never_exceeds_available(initial_stock: int, reserve_qty: int) -> None:
    """Property: reservation only succeeds if available >= requested."""
    ledger = make_ledger()
    if initial_stock > 0:
        ledger.record_receive(initial_stock, 1000, uuid4(), "purchase_order")

    if ledger.can_reserve(reserve_qty):
        ledger.reserve(reserve_qty, uuid4(), "order")
        assert ledger.available == initial_stock - reserve_qty
    else:
        assert initial_stock < reserve_qty
        assert ledger.available == initial_stock


@given(st.integers(min_value=1, max_value=100), st.integers(min_value=1, max_value=100))
@settings(max_examples=100)
def test_release_does_not_exceed_reserved(reserve_qty: int, over_release: int) -> None:
    """Property: release cannot exceed the remaining reserved quantity for a reference."""
    ledger = make_ledger()
    ledger.record_receive(reserve_qty * 2, 1000, uuid4(), "purchase_order")

    ref = uuid4()
    ledger.reserve(reserve_qty, ref, "order")

    # Releasing the full reserved quantity is valid...
    ledger.release(reserve_qty, ref, "order")
    assert ledger.reserved == 0

    # ...but releasing again (or more than reserved) must fail.
    import pytest

    with pytest.raises(ValueError):
        ledger.release(max(1, over_release), ref, "order")


@given(st.integers(min_value=1, max_value=100))
@settings(max_examples=100)
def test_release_with_unknown_reference_rejected(initial_stock: int) -> None:
    """Property: releasing a reference that was never reserved is rejected."""
    ledger = make_ledger()
    ledger.record_receive(initial_stock, 1000, uuid4(), "purchase_order")

    import pytest

    with pytest.raises(ValueError, match=r"(?i)unknown|no open reservation|not reserved"):
        ledger.release(initial_stock, uuid4(), "order")


@given(st.integers(min_value=1, max_value=100), st.integers(min_value=1, max_value=100))
@settings(max_examples=100)
def test_sell_does_not_exceed_on_hand(initial_stock: int, sell_qty: int) -> None:
    """Property: sell cannot exceed on-hand stock."""
    ledger = make_ledger()
    ledger.record_receive(initial_stock, 1000, uuid4(), "purchase_order")

    if ledger.on_hand >= sell_qty:
        ledger.sell(sell_qty, uuid4(), "order")
        assert ledger.on_hand >= 0
    else:
        import pytest

        with pytest.raises(ValueError):
            ledger.sell(sell_qty, uuid4(), "order")


@given(st.integers(min_value=1, max_value=100), st.integers(min_value=1, max_value=100))
@settings(max_examples=100)
def test_partial_release_keeps_remaining_reserved(reserve_qty: int, release_qty: int) -> None:
    """Property: partial release reduces reserved without closing the reference."""
    ledger = make_ledger()
    ledger.record_receive(reserve_qty + release_qty, 1000, uuid4(), "purchase_order")

    ref = uuid4()
    ledger.reserve(reserve_qty + release_qty, ref, "order")

    ledger.release(release_qty, ref, "order")
    assert ledger.reserved == reserve_qty
    assert ledger.available == (reserve_qty + release_qty) - reserve_qty
