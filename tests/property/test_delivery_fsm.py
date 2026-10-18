"""
Property-based tests for the delivery state machine.

Derived from the delivery FSM invariants (packages/core/domain/delivery.py):
terminal states have no outgoing transitions; non-terminal states have at
least one; no self-transitions; transitions are deterministic. Mirrors the
order FSM property tests (test_m3_catalog_stock_orders.py) — the delivery FSM
is the physical-lifecycle sibling of the money-lifecycle order FSM (ADR 0006).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from packages.core.domain.delivery import (
    LEGAL_DELIVERY_TRANSITIONS,
    DeliveryStatus,
    is_legal_delivery_transition,
)


class TestDeliveryFSM:
    @given(st.sampled_from(list(DeliveryStatus)))
    def test_terminal_state_has_no_transitions(self, status: DeliveryStatus) -> None:
        """DELIVERED is terminal; every other state has an outgoing edge."""
        if status is DeliveryStatus.DELIVERED:
            assert len(LEGAL_DELIVERY_TRANSITIONS[status]) == 0
        else:
            assert len(LEGAL_DELIVERY_TRANSITIONS[status]) >= 1

    @given(
        st.sampled_from(list(DeliveryStatus)),
        st.sampled_from(list(DeliveryStatus)),
    )
    def test_transition_is_deterministic(
        self, from_status: DeliveryStatus, to_status: DeliveryStatus,
    ) -> None:
        """Same input always yields the same verdict."""
        assert is_legal_delivery_transition(from_status, to_status) is is_legal_delivery_transition(
            from_status, to_status,
        )

    @given(st.sampled_from(list(DeliveryStatus)))
    def test_no_self_transitions(self, status: DeliveryStatus) -> None:
        """No delivery state can transition to itself."""
        assert not is_legal_delivery_transition(status, status)

    @given(st.sampled_from(list(DeliveryStatus)))
    def test_failed_always_reachable_from_active_states(self, status: DeliveryStatus) -> None:
        """Any in-flight delivery can fail — the owner always has a path to
        handle a failed delivery (retry or human-approved refund)."""
        if status in (DeliveryStatus.ASSIGNED, DeliveryStatus.PICKED_UP, DeliveryStatus.ARRIVED):
            assert DeliveryStatus.FAILED in LEGAL_DELIVERY_TRANSITIONS[status]
