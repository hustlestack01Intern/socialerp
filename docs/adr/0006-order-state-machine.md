# ADR 0006: Order State Machine

**Status**: Accepted
**Date**: 2024-01-15
**Deciders**: Chief Engineer

---

## Context

Orders are the core transactional entity. We need explicit, validated state transitions with audit trail. Illegal transitions must be impossible.

---

## Decision

**Explicit state machine** with defined legal transitions. Enforced at database and application level.

---

## States

```python
class OrderStatus(str, Enum):
    DRAFT = "draft"  # Cart, not yet submitted
    AWAITING_PAYMENT = "awaiting_payment"  # STK Push sent, waiting callback
    PAID = "paid"  # Payment confirmed
    PACKED = "packed"  # Ready for pickup
    OUT_FOR_DELIVERY = "out_for_delivery"  # Rider picked up
    DELIVERED = "delivered"  # Completed successfully
    CANCELLED = "cancelled"  # Cancelled before payment
    REFUNDED = "refunded"  # Paid then refunded
    FAILED_DELIVERY = "failed_delivery"  # Delivery failed, may retry
```

---

## Legal Transitions

```
DRAFT ─────────────────► AWAITING_PAYMENT
    │                        │
    │ (cancel)               │ (STK success)
    ▼                        ▼
CANCELLED               PAID
                             │
                             │ (pack)
                             ▼
                         PACKED
                             │
                             │ (dispatch)
                             ▼
                    OUT_FOR_DELIVERY
                       │           │
              (delivered)    (failed)
                 ▼             ▼
             DELIVERED   FAILED_DELIVERY
                              │
                         (retry/return)
                              ▼
                       OUT_FOR_DELIVERY / REFUNDED

PAID ─────────────────────────────► REFUNDED
    │ (refund approved)
    ▼
REFUNDED
```

### Transition Table

| From | To | Trigger | Side Effects |
|------|-----|---------|--------------|
| `draft` | `awaiting_payment` | Customer confirms order | Reserve stock, send STK Push |
| `draft` | `cancelled` | Customer/owner cancels | Release any reservations |
| `awaiting_payment` | `paid` | STK Push callback success | Release reservation → sell movement |
| `awaiting_payment` | `cancelled` | STK Push failed/timeout/cancelled | Release reservation |
| `paid` | `packed` | Owner/staff marks packed | - |
| `packed` | `out_for_delivery` | Rider picks up | Notify customer, start tracking |
| `out_for_delivery` | `delivered` | Rider marks delivered | Notify customer, complete |
| `out_for_delivery` | `failed_delivery` | Rider marks failed | Schedule retry, notify |
| `failed_delivery` | `out_for_delivery` | Retry dispatch | New rider assignment |
| `failed_delivery` | `refunded` | Max retries exceeded | Process refund |
| `paid` | `refunded` | Owner approves refund | Release stock, initiate refund |

---

## Enforcement

### Database Level (Check Constraint)

```sql
-- Valid transitions stored in table
CREATE TABLE order_valid_transitions (
    from_status VARCHAR(30) NOT NULL,
    to_status VARCHAR(30) NOT NULL,
    PRIMARY KEY (from_status, to_status)
);

INSERT INTO order_valid_transitions VALUES
    ('draft', 'awaiting_payment'),
    ('draft', 'cancelled'),
    ('awaiting_payment', 'paid'),
    ('awaiting_payment', 'cancelled'),
    ('paid', 'packed'),
    ('paid', 'refunded'),
    ('packed', 'out_for_delivery'),
    ('out_for_delivery', 'delivered'),
    ('out_for_delivery', 'failed_delivery'),
    ('failed_delivery', 'out_for_delivery'),
    ('failed_delivery', 'refunded');
```

### Application Level (State Machine)

```python
class OrderStateMachine:
    VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
        OrderStatus.DRAFT: {OrderStatus.AWAITING_PAYMENT, OrderStatus.CANCELLED},
        OrderStatus.AWAITING_PAYMENT: {OrderStatus.PAID, OrderStatus.CANCELLED},
        OrderStatus.PAID: {OrderStatus.PACKED, OrderStatus.REFUNDED},
        OrderStatus.PACKED: {OrderStatus.OUT_FOR_DELIVERY},
        OrderStatus.OUT_FOR_DELIVERY: {OrderStatus.DELIVERED, OrderStatus.FAILED_DELIVERY},
        OrderStatus.DELIVERED: set(),  # Terminal
        OrderStatus.CANCELLED: set(),  # Terminal
        OrderStatus.REFUNDED: set(),  # Terminal
        OrderStatus.FAILED_DELIVERY: {OrderStatus.OUT_FOR_DELIVERY, OrderStatus.REFUNDED},
    }

    @classmethod
    def can_transition(cls, from_status: OrderStatus, to_status: OrderStatus) -> bool:
        return to_status in cls.VALID_TRANSITIONS.get(from_status, set())

    @classmethod
    def transition(
        cls, order: "Order", to_status: OrderStatus, actor: str, reason: str = ""
    ) -> None:
        if not cls.can_transition(order.status, to_status):
            raise IllegalTransitionError(f"Cannot transition from {order.status} to {to_status}")

        # Execute side effects
        cls._execute_side_effects(order, to_status)

        # Update status
        from_status = order.status
        order.status = to_status
        order.updated_at = datetime.utcnow()

        # Log transition
        await log_event(
            action="order.status_changed",
            payload={
                "order_id": str(order.id),
                "from_status": from_status.value,
                "to_status": to_status.value,
                "actor": actor,
                "reason": reason,
            },
            actor_type="user" if actor != "agent" else "agent",
            actor_id=actor,
            decision="allowed",
            rule_ref=f"order_fsm:{from_status.value}->{to_status.value}",
        )

    @classmethod
    def _execute_side_effects(cls, order: "Order", to_status: OrderStatus) -> None:
        if to_status == OrderStatus.AWAITING_PAYMENT:
            reserve_stock_for_order(order)
            send_stk_push(order)

        elif to_status == OrderStatus.PAID:
            release_reservation_and_sell(order)

        elif to_status == OrderStatus.CANCELLED and order.status == OrderStatus.AWAITING_PAYMENT:
            release_reservation(order.id)

        elif to_status == OrderStatus.OUT_FOR_DELIVERY:
            assign_rider_and_notify(order)

        elif to_status == OrderStatus.DELIVERED:
            complete_delivery(order)

        elif to_status == OrderStatus.REFUNDED:
            release_stock_and_refund(order)
```

---

## Idempotency

- Transition requests carry `idempotency_key` (e.g., `order_transition:{order_id}:{to_status}`)
- Duplicate requests return current state, no error

---

## Testing

### Property-Based Tests

```python
# tests/orders/test_state_machine_property.py
from hypothesis import given, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant


class OrderStateMachineTest(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.order = Order(status=OrderStatus.DRAFT)

    @rule(target_status=st.sampled_from(list(OrderStatus)))
    def attempt_transition(self, target_status):
        try:
            OrderStateMachine.transition(self.order, target_status, "test")
        except IllegalTransitionError:
            pass  # Expected for invalid transitions

    @invariant()
    def status_is_valid(self):
        assert self.order.status in OrderStatus

    @invariant()
    def only_legal_transitions_occurred(self):
        # Verify transition history follows rules
        pass
```

### Unit Tests (Critical Paths)

```python
async def test_happy_path_draft_to_delivered():
    order = await create_order(items=[...], status=OrderStatus.DRAFT)

    await transition(order, OrderStatus.AWAITING_PAYMENT, "customer")
    assert order.status == OrderStatus.AWAITING_PAYMENT

    await handle_stk_callback(order.checkout_request_id, success=True)
    assert order.status == OrderStatus.PAID

    await transition(order, OrderStatus.PACKED, "staff")
    assert order.status == OrderStatus.PACKED

    await transition(order, OrderStatus.OUT_FOR_DELIVERY, "rider")
    assert order.status == OrderStatus.OUT_FOR_DELIVERY

    await transition(order, OrderStatus.DELIVERED, "rider")
    assert order.status == OrderStatus.DELIVERED


async def test_illegal_transition_raises():
    order = await create_order(status=OrderStatus.DRAFT)

    with pytest.raises(IllegalTransitionError):
        await transition(order, OrderStatus.PAID, "test")  # Must go through awaiting_payment

    with pytest.raises(IllegalTransitionError):
        await transition(order, OrderStatus.DELIVERED, "test")  # Too far


async def test_concurrent_last_item_reservation():
    """Two orders for last item - only one gets it."""
    product = await create_product(stock=1)

    # Simulate concurrent reservation attempts
    results = await asyncio.gather(
        reserve_for_order(product, 1), reserve_for_order(product, 1), return_exceptions=True
    )

    successes = [r for r in results if r is True]
    assert len(successes) == 1  # Only one succeeds
```

---

## Consequences

### Positive
- **Impossible** to reach invalid state
- Clear audit trail of every transition
- Side effects tied to transitions (stock, payments, notifications)
- Testable with property-based testing

### Negative
- More code than simple status field
- Must define all transitions upfront
- New states require migration + transition updates

---

## Related
- ADR 0002: Tenancy (orders tenant-scoped)
- ADR 0005: Stock Ledger (transitions trigger movements)
- ADR 0004: Payment Provider (awaiting_payment → paid)
- ADR 0007: Idempotency (transition requests)