"""
Property-based tests for order state machine invariants (ported to packages.core API).

The transition table is the canonical one from ADR 0006:
- draft -> {awaiting_payment, cancelled}
- awaiting_payment -> {paid, cancelled}
- paid -> {packed, refunded}
- packed -> {out_for_delivery}
- out_for_delivery -> {delivered, failed_delivery}
- failed_delivery -> {out_for_delivery, refunded}
- delivered, cancelled, refunded are terminal

Invariants preserved from the original suite:
1. Status is always a valid OrderStatus
2. Terminal states have no outgoing transitions
3. The only cycle is out_for_delivery <-> failed_delivery
4. paid can never be cancelled directly (must refund)
5. can_transition always returns a bool
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from packages.core.domain.order import (
    LEGAL_TRANSITIONS,
    IllegalTransitionError,
    Order,
    OrderStatus,
    is_legal_transition,
)


def make_order(status: OrderStatus = OrderStatus.DRAFT) -> Order:
    """Construct a minimal valid order in the given status."""
    ts = datetime.now(UTC)
    order = Order(
        tenant_id=uuid4(),
        customer_id=uuid4(),
        customer_phone="254712345678",
        status=status,
        created_at=ts,
        updated_at=ts,
    )
    return order


class OrderStateMachineModel(RuleBasedStateMachine):
    """Stateful property tests for order state machine."""

    def __init__(self) -> None:
        super().__init__()
        self.order = make_order()

    @initialize()
    def setup(self) -> None:
        self.order = make_order()

    @rule()
    def submit_order(self) -> None:
        """Transition from draft to awaiting_payment."""
        if self.order.can_transition_to(OrderStatus.AWAITING_PAYMENT):
            self.order = self.order.transition_to(OrderStatus.AWAITING_PAYMENT)

    @rule()
    def cancel_draft(self) -> None:
        """Cancel from draft."""
        if self.order.can_transition_to(OrderStatus.CANCELLED):
            self.order = self.order.transition_to(OrderStatus.CANCELLED)

    @rule()
    def payment_success(self) -> None:
        """Payment callback success."""
        if self.order.can_transition_to(OrderStatus.PAID):
            self.order = self.order.transition_to(OrderStatus.PAID)

    @rule()
    def payment_failed(self) -> None:
        """Payment failed/timeout -> cancel."""
        if self.order.can_transition_to(OrderStatus.CANCELLED):
            self.order = self.order.transition_to(OrderStatus.CANCELLED)

    @rule()
    def pack_order(self) -> None:
        """Mark as packed."""
        if self.order.can_transition_to(OrderStatus.PACKED):
            self.order = self.order.transition_to(OrderStatus.PACKED)

    @rule()
    def dispatch_order(self) -> None:
        """Dispatch to rider."""
        if self.order.can_transition_to(OrderStatus.OUT_FOR_DELIVERY):
            self.order = self.order.transition_to(OrderStatus.OUT_FOR_DELIVERY)

    @rule()
    def deliver_order(self) -> None:
        """Mark as delivered."""
        if self.order.can_transition_to(OrderStatus.DELIVERED):
            self.order = self.order.transition_to(OrderStatus.DELIVERED)

    @rule()
    def fail_delivery(self) -> None:
        """Mark delivery as failed."""
        if self.order.can_transition_to(OrderStatus.FAILED_DELIVERY):
            self.order = self.order.transition_to(OrderStatus.FAILED_DELIVERY)

    @rule()
    def retry_delivery(self) -> None:
        """Retry failed delivery."""
        if self.order.can_transition_to(OrderStatus.OUT_FOR_DELIVERY):
            self.order = self.order.transition_to(OrderStatus.OUT_FOR_DELIVERY)

    @rule()
    def refund_order(self) -> None:
        """Process refund."""
        if self.order.can_transition_to(OrderStatus.REFUNDED):
            self.order = self.order.transition_to(OrderStatus.REFUNDED)

    @invariant()
    def status_is_valid_enum(self) -> None:
        """Invariant: status is always a valid OrderStatus."""
        assert isinstance(self.order.status, OrderStatus)

    @invariant()
    def terminal_states_are_terminal(self) -> None:
        """Invariant: once in terminal state, no transitions possible."""
        terminal_states = {OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.REFUNDED}
        if self.order.status in terminal_states:
            assert len(LEGAL_TRANSITIONS[self.order.status]) == 0, (
                f"Terminal state {self.order.status} has transitions: "
                f"{LEGAL_TRANSITIONS[self.order.status]}"
            )

    @invariant()
    def paid_is_never_cancelled(self) -> None:
        """Invariant: a paid order can only be refunded, never cancelled."""
        if self.order.status == OrderStatus.PAID:
            assert self.order.can_transition_to(OrderStatus.REFUNDED)
            assert not self.order.can_transition_to(OrderStatus.CANCELLED)


TestOrderStateMachine = OrderStateMachineModel.TestCase


# =============================================================================
# SIMPLE PROPERTY TESTS
# =============================================================================


@given(st.sampled_from(list(OrderStatus)))
@settings(max_examples=50)
def test_all_states_defined_in_transitions(from_status: OrderStatus) -> None:
    """Property: every state has defined transitions (even if empty)."""
    assert from_status in LEGAL_TRANSITIONS


@given(
    st.sampled_from(list(OrderStatus)),
    st.sampled_from(list(OrderStatus)),
)
@settings(max_examples=200)
def test_transition_symmetry(from_status: OrderStatus, to_status: OrderStatus) -> None:
    """Property: the only cycle is failed_delivery <-> out_for_delivery (a DAG otherwise)."""
    can_forward = is_legal_transition(from_status, to_status)
    can_backward = is_legal_transition(to_status, from_status)

    is_retry_cycle = {from_status, to_status} == {
        OrderStatus.FAILED_DELIVERY,
        OrderStatus.OUT_FOR_DELIVERY,
    }
    if is_retry_cycle:
        assert can_forward and can_backward
    elif from_status == to_status:
        assert not can_forward and not can_backward
    else:
        assert not (can_forward and can_backward), (
            f"Unexpected cycle detected: {from_status} <-> {to_status}"
        )


@given(st.sampled_from(list(OrderStatus)))
@settings(max_examples=50)
def test_terminal_states_have_no_outgoing(from_status: OrderStatus) -> None:
    """Property: terminal states have no outgoing transitions."""
    terminal = {OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.REFUNDED}
    if from_status in terminal:
        assert len(LEGAL_TRANSITIONS[from_status]) == 0


@given(st.sampled_from(list(OrderStatus)))
@settings(max_examples=100)
def test_can_transition_returns_bool(from_status: OrderStatus) -> None:
    """Property: can_transition always returns bool."""
    for to_status in OrderStatus:
        assert isinstance(is_legal_transition(from_status, to_status), bool)


# =============================================================================
# UNIT TESTS FOR CRITICAL PATHS (ADR 0006)
# =============================================================================


class TestOrderStateMachineUnit:
    """Unit tests for the ADR 0006 state machine."""

    def test_happy_path_draft_to_delivered(self) -> None:
        """Full happy path: draft -> awaiting_payment -> paid -> packed.

        ... -> out_for_delivery -> delivered.
        """
        order = make_order()
        order = order.transition_to(OrderStatus.AWAITING_PAYMENT)
        assert order.status == OrderStatus.AWAITING_PAYMENT
        order = order.transition_to(OrderStatus.PAID)
        assert order.status == OrderStatus.PAID
        order = order.transition_to(OrderStatus.PACKED)
        assert order.status == OrderStatus.PACKED
        order = order.transition_to(OrderStatus.OUT_FOR_DELIVERY)
        assert order.status == OrderStatus.OUT_FOR_DELIVERY
        order = order.transition_to(OrderStatus.DELIVERED)
        assert order.status == OrderStatus.DELIVERED

    def test_illegal_transition_raises(self) -> None:
        """Illegal transitions raise IllegalTransitionError."""
        order = make_order()
        with pytest.raises(IllegalTransitionError):
            order.transition_to(OrderStatus.PAID)
        with pytest.raises(IllegalTransitionError):
            order.transition_to(OrderStatus.DELIVERED)

    def test_cancel_from_draft(self) -> None:
        """Can cancel from draft."""
        order = make_order().transition_to(OrderStatus.CANCELLED)
        assert order.status == OrderStatus.CANCELLED

    def test_cancel_from_awaiting_payment(self) -> None:
        """Can cancel from awaiting_payment."""
        order = make_order(OrderStatus.AWAITING_PAYMENT).transition_to(OrderStatus.CANCELLED)
        assert order.status == OrderStatus.CANCELLED

    def test_cannot_cancel_from_paid(self) -> None:
        """Cannot cancel from paid (must refund)."""
        order = make_order(OrderStatus.PAID)
        with pytest.raises(IllegalTransitionError):
            order.transition_to(OrderStatus.CANCELLED)

    def test_refund_from_paid(self) -> None:
        """Can refund from paid."""
        order = make_order(OrderStatus.PAID).transition_to(OrderStatus.REFUNDED)
        assert order.status == OrderStatus.REFUNDED

    def test_refund_from_failed_delivery(self) -> None:
        """Can refund from failed_delivery after max retries."""
        order = make_order(OrderStatus.FAILED_DELIVERY).transition_to(OrderStatus.REFUNDED)
        assert order.status == OrderStatus.REFUNDED

    def test_failed_delivery_can_retry(self) -> None:
        """Failed delivery can be retried."""
        order = make_order(OrderStatus.FAILED_DELIVERY).transition_to(OrderStatus.OUT_FOR_DELIVERY)
        assert order.status == OrderStatus.OUT_FOR_DELIVERY

    def test_terminal_states_block_transitions(self) -> None:
        """Terminal states block all transitions."""
        for terminal in [OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.REFUNDED]:
            order = make_order(terminal)
            for target in OrderStatus:
                if target != terminal:
                    with pytest.raises(IllegalTransitionError):
                        order.transition_to(target)

    def test_can_transition_returns_correct_values(self) -> None:
        """can_transition returns correct values for ADR 0006 transitions."""
        assert is_legal_transition(OrderStatus.DRAFT, OrderStatus.AWAITING_PAYMENT)
        assert is_legal_transition(OrderStatus.DRAFT, OrderStatus.CANCELLED)
        assert not is_legal_transition(OrderStatus.DRAFT, OrderStatus.PAID)
        assert is_legal_transition(OrderStatus.AWAITING_PAYMENT, OrderStatus.PAID)
        assert is_legal_transition(OrderStatus.PAID, OrderStatus.PACKED)
        assert is_legal_transition(OrderStatus.PACKED, OrderStatus.OUT_FOR_DELIVERY)
        assert is_legal_transition(OrderStatus.OUT_FOR_DELIVERY, OrderStatus.DELIVERED)
        assert is_legal_transition(OrderStatus.OUT_FOR_DELIVERY, OrderStatus.FAILED_DELIVERY)
        assert is_legal_transition(OrderStatus.FAILED_DELIVERY, OrderStatus.OUT_FOR_DELIVERY)
        assert is_legal_transition(OrderStatus.FAILED_DELIVERY, OrderStatus.REFUNDED)
        assert is_legal_transition(OrderStatus.PAID, OrderStatus.REFUNDED)
        assert not is_legal_transition(OrderStatus.DELIVERED, OrderStatus.REFUNDED)

    def test_delivered_is_terminal(self) -> None:
        """Delivered is terminal per ADR 0006 (no delivered->refunded transition)."""
        order = make_order(OrderStatus.DELIVERED)
        for target in OrderStatus:
            if target != OrderStatus.DELIVERED:
                with pytest.raises(IllegalTransitionError):
                    order.transition_to(target)
