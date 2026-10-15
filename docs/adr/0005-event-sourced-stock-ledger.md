# ADR 0005: Event-Sourced Stock Ledger

**Status**: Accepted
**Date**: 2024-01-15
**Deciders**: Chief Engineer

---

## Context

Inventory accuracy is a north-star metric. We need absolute confidence that `system_stock == physical_stock` at all times. Traditional "update quantity" approaches lose history and cause drift.

---

## Decision

**Event-sourced stock ledger**: Stock = sum of immutable movements. Never overwrite quantities.

---

## Movement Types

| Type | Description | Delta | Triggers |
|------|-------------|-------|----------|
| `receive` | New stock from supplier | + | Purchase order receipt |
| `reserve` | Reserved for order | - | Order created (awaiting payment) |
| `release` | Reservation expired/cancelled | + | Payment timeout, order cancelled |
| `sell` | Confirmed sale | - | Payment confirmed |
| `adjust` | Manual correction (count) | ± | Stock take variance |
| `return` | Customer return | + | Refund processed, item received |
| `damage` | Written off | - | Damage report |
| `transfer` | Move between locations | ± | Multi-location |

---

## Schema

```python
class StockMovement(Base, TenantMixin):
    __tablename__ = "stock_movements"

    id: Mapped[UUID] = mapped_column(PG_UUID, primary_key=True, default=uuid4)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    variant_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("variants.id"), nullable=True, index=True
    )
    location_id: Mapped[UUID] = mapped_column(
        ForeignKey("locations.id"), nullable=False, index=True
    )

    movement_type: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # receive, reserve, ...
    quantity: Mapped[int] = mapped_column(nullable=False)  # Always positive; direction from type
    reference_id: Mapped[Optional[UUID]] = mapped_column(
        nullable=True, index=True
    )  # Order ID, PO ID, etc.
    reference_type: Mapped[Optional[str]] = mapped_column(
        String(30), nullable=True
    )  # "order", "purchase_order"
    unit_cost: Mapped[Optional[int]] = mapped_column(nullable=True)  # KSH cents at time of movement
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[UUID]] = mapped_column(nullable=True)  # User or agent run ID
    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, nullable=False, index=True
    )

    # Computed property (not stored)
    @property
    def delta(self) -> int:
        positive = {"receive", "release", "return", "transfer_in", "adjust_up"}
        negative = {"reserve", "sell", "damage", "transfer_out", "adjust_down"}
        if self.movement_type in positive:
            return self.quantity
        elif self.movement_type in negative:
            return -self.quantity
        return 0
```

---

## Current Stock Calculation

```python
async def get_current_stock(
    product_id: UUID, variant_id: UUID = None, location_id: UUID = None
) -> int:
    """Sum of all movements for product/variant/location."""
    stmt = select(func.sum(StockMovement.delta)).where(
        StockMovement.product_id == product_id,
        StockMovement.tenant_id == get_current_tenant_id(),
    )
    if variant_id:
        stmt = stmt.where(StockMovement.variant_id == variant_id)
    if location_id:
        stmt = stmt.where(StockMovement.location_id == location_id)
    result = await session.execute(stmt)
    return result.scalar() or 0
```

**Materialized view for performance** (refreshed on movement insert):
```sql
CREATE MATERIALIZED VIEW current_stock AS
SELECT tenant_id, product_id, variant_id, location_id, SUM(delta) as quantity
FROM stock_movements
GROUP BY tenant_id, product_id, variant_id, location_id;
```

---

## Reservation Logic

```python
async def reserve_stock(product_id: UUID, variant_id: UUID, quantity: int, order_id: UUID) -> bool:
    """Atomically reserve stock if available."""
    current = await get_current_stock(product_id, variant_id)
    reserved = await get_reserved_quantity(product_id, variant_id)  # Sum of 'reserve' - 'release'
    available = current - reserved

    if available < quantity:
        return False  # Insufficient stock

    # Create reservation movement
    await add_movement(
        product_id=product_id,
        variant_id=variant_id,
        movement_type="reserve",
        quantity=quantity,
        reference_id=order_id,
        reference_type="order",
    )
    return True


async def release_reservation(order_id: UUID) -> None:
    """Release all reservations for an order."""
    movements = await get_movements(reference_id=order_id, movement_type="reserve")
    for m in movements:
        await add_movement(
            product_id=m.product_id,
            variant_id=m.variant_id,
            movement_type="release",
            quantity=m.quantity,
            reference_id=order_id,
            reference_type="order",
        )
```

---

## Rules (Owner Configurable)

| Rule | Implementation |
|------|----------------|
| Hide when stock = 0 | `available <= 0` → exclude from catalog |
| Alert below reorder | Scheduled job checks `available < reorder_level` |
| Suggest reorder qty | `14-day sell-through * lead_time_days - available` |
| Never sell below cost | Validate `sell_price >= cost_price * (1 + min_margin)` |
| Max discount 10% | Validate `discount_percent <= 10` |
| Auto-release unpaid | Job releases `reserve` older than `payment_timeout_minutes` |

---

## Property-Based Tests (Invariant)

```python
# tests/inventory/test_stock_ledger_property.py
from hypothesis import given, strategies as st


@given(st.lists(st.integers(min_value=1, max_value=100), min_size=1, max_size=50))
def test_stock_equals_sum_of_movements(movements):
    """Invariant: current_stock == sum of all movement deltas."""
    ledger = StockLedger()
    for qty in movements:
        ledger.add_movement("receive", qty)
        ledger.add_movement("reserve", qty // 2)
        ledger.add_movement("release", qty // 4)
        ledger.add_movement("sell", qty // 4)

    assert ledger.current_stock == sum(m.delta for m in ledger.movements)


@given(st.integers(min_value=0, max_value=1000), st.integers(min_value=0, max_value=1000))
def test_reserve_never_exceeds_available(initial_stock, reserve_qty):
    """Invariant: reservation only succeeds if available >= requested."""
    ledger = StockLedger()
    ledger.add_movement("receive", initial_stock)

    success = ledger.reserve(reserve_qty)
    available = ledger.available_stock

    if success:
        assert available >= 0
    else:
        assert available < reserve_qty
```

---

## Concurrency Control

- **Optimistic locking**: Version column on product; retry on conflict
- **Reservation race**: `SELECT FOR UPDATE` on product row during reserve
- **Last item**: Test concurrent orders on last item (only one succeeds)

---

## Consequences

### Positive
- **Complete audit trail**: Every stock change traceable to source
- **Reconciliation**: Physical count vs ledger = variance report
- **Time travel**: Stock at any point in history
- **No drift**: Impossible to have "ghost" stock

### Negative
- More storage (mitigated: partition by month, archive old)
- Read performance (mitigated: materialized view, caching)
- Complexity in reservation logic

---

## Related
- ADR 0002: Tenancy (stock is tenant-scoped)
- ADR 0006: Order State Machine (triggers movements)