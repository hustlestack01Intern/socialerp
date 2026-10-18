"""Phase 8b: pure segment evaluator — truth tables, purity, allow-list (plan AC).

Covers plan acceptance criteria:
- "Segment evaluator: all predicates covered by truth tables; missing trait =>
  no match; determinism holds across runs."
- "No sensitive trait derivation/usage introduced (negative test)."
- Failure modes: fabricated/missing trait match, sensitive predicate attempted,
  non-determinism.

These tests are pure (no DB, no fixtures): the evaluator is a deterministic
function over a plain ``name -> value`` mapping, which is exactly what the
service feeds it after resolving latest trait versions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from packages.core.crm.segments import (
    AT_RISK_MIN_CHURN,
    HIGH_VALUE_MIN_CENTS,
    SEGMENT_RULES,
    PredicateError,
    SegmentKey,
    evaluate_predicate,
    iter_predicates,
    validate_predicate,
)
from packages.core.crm.service import ALLOWED_TRAITS, SENSITIVE_TRAIT_NAMES


def _eval_rule(key: SegmentKey, traits: dict[str, Any]) -> bool:
    return evaluate_predicate(SEGMENT_RULES[key].predicate, traits)


# =============================================================================
# Truth tables, one per registered segment
# =============================================================================


def test_first_time_truth_table() -> None:
    assert _eval_rule(SegmentKey.FIRST_TIME, {"order_count": 1}) is True
    assert _eval_rule(SegmentKey.FIRST_TIME, {"order_count": 2}) is False
    assert _eval_rule(SegmentKey.FIRST_TIME, {"order_count": 0}) is False
    assert _eval_rule(SegmentKey.FIRST_TIME, {"order_count": 1.0}) is True  # 1 == 1.0
    assert _eval_rule(SegmentKey.FIRST_TIME, {}) is False  # missing => no match


def test_repeat_buyers_truth_table() -> None:
    assert _eval_rule(SegmentKey.REPEAT_BUYERS, {"order_count": 2}) is True
    assert _eval_rule(SegmentKey.REPEAT_BUYERS, {"order_count": 3}) is True
    assert _eval_rule(SegmentKey.REPEAT_BUYERS, {"order_count": 1}) is False
    assert _eval_rule(SegmentKey.REPEAT_BUYERS, {"order_count": 0}) is False
    assert _eval_rule(SegmentKey.REPEAT_BUYERS, {}) is False


def test_high_value_truth_table() -> None:
    assert _eval_rule(SegmentKey.HIGH_VALUE, {"lifetime_value_cents": HIGH_VALUE_MIN_CENTS}) is True
    assert (
        _eval_rule(SegmentKey.HIGH_VALUE, {"lifetime_value_cents": HIGH_VALUE_MIN_CENTS + 1})
        is True
    )
    assert (
        _eval_rule(SegmentKey.HIGH_VALUE, {"lifetime_value_cents": HIGH_VALUE_MIN_CENTS - 1})
        is False
    )
    assert _eval_rule(SegmentKey.HIGH_VALUE, {"lifetime_value_cents": 0}) is False
    assert _eval_rule(SegmentKey.HIGH_VALUE, {}) is False


def test_at_risk_truth_table() -> None:
    assert _eval_rule(SegmentKey.AT_RISK, {"churn_risk": AT_RISK_MIN_CHURN}) is True
    assert _eval_rule(SegmentKey.AT_RISK, {"churn_risk": 1.0}) is True
    assert _eval_rule(SegmentKey.AT_RISK, {"churn_risk": AT_RISK_MIN_CHURN - 0.001}) is False
    assert _eval_rule(SegmentKey.AT_RISK, {"churn_risk": 0.0}) is False
    assert _eval_rule(SegmentKey.AT_RISK, {}) is False


def test_reorder_due_truth_table() -> None:
    # Past its own cycle and has a measured cycle => due.
    assert _eval_rule(SegmentKey.REORDER_DUE, {"reorder_cycle_days": 7, "churn_risk": 0.1}) is True
    # Zero-day measured cycle (same-day orders) still counts as present.
    assert _eval_rule(SegmentKey.REORDER_DUE, {"reorder_cycle_days": 0, "churn_risk": 0.01}) is True
    # Within the cycle (churn_risk == 0.0) => not due yet.
    assert _eval_rule(SegmentKey.REORDER_DUE, {"reorder_cycle_days": 7, "churn_risk": 0.0}) is False
    # Churn evidence exists but no measured reorder cycle (first-time buyer)
    # => missing trait must NOT match.
    assert _eval_rule(SegmentKey.REORDER_DUE, {"churn_risk": 0.9}) is False
    assert _eval_rule(SegmentKey.REORDER_DUE, {"reorder_cycle_days": 7}) is False
    assert _eval_rule(SegmentKey.REORDER_DUE, {}) is False


def test_every_registered_segment_declines_empty_traits() -> None:
    """Plan invariant 6: missing traits => no membership, for EVERY segment."""
    for key in SegmentKey:
        assert _eval_rule(key, {}) is False, f"{key} matched on no data"


def test_every_registered_segment_has_expected_keys() -> None:
    # The fixed enum of plan Option 3 — a silent addition needs a plan change.
    assert set(SEGMENT_RULES) == {
        SegmentKey.FIRST_TIME,
        SegmentKey.REPEAT_BUYERS,
        SegmentKey.HIGH_VALUE,
        SegmentKey.AT_RISK,
        SegmentKey.REORDER_DUE,
    }


# =============================================================================
# Determinism and purity (plan invariant 1)
# =============================================================================


def test_evaluation_is_deterministic_across_runs() -> None:
    traits = {"order_count": 4, "churn_risk": 0.7, "lifetime_value_cents": 250_000}
    for key in SegmentKey:
        first = _eval_rule(key, traits)
        for _ in range(50):
            assert _eval_rule(key, traits) is first


def test_evaluation_does_not_mutate_input() -> None:
    traits = {"rfm_score": {"r": 5, "f": 3, "m": 2}, "order_count": 3}
    snapshot = deepcopy(traits)
    evaluate_predicate(
        {"op": "and", "preds": [{"field": "rfm_score.f", "op": ">=", "value": 3}]},
        traits,
    )
    assert traits == snapshot, "evaluation must be pure (no writes to inputs)"


def test_evaluation_is_independent_of_dict_insertion_order() -> None:
    a: dict[str, Any] = {"order_count": 3, "churn_risk": 0.2}
    b: dict[str, Any] = {"churn_risk": 0.2, "order_count": 3}
    for key in SegmentKey:
        assert _eval_rule(key, a) == _eval_rule(key, b)


# =============================================================================
# Predicate edge cases
# =============================================================================


def test_boolean_composition_and_or_nested() -> None:
    predicate = {
        "op": "and",
        "preds": [
            {"field": "order_count", "op": ">=", "value": 2},
            {
                "op": "or",
                "preds": [
                    {"field": "preferred_size", "op": "==", "value": "M"},
                    {"field": "preferred_color", "op": "==", "value": "black"},
                ],
            },
        ],
    }
    assert evaluate_predicate(predicate, {"order_count": 3, "preferred_size": "M"}) is True
    assert evaluate_predicate(predicate, {"order_count": 3, "preferred_color": "black"}) is True
    assert evaluate_predicate(predicate, {"order_count": 3, "preferred_size": "L"}) is False
    assert evaluate_predicate(predicate, {"order_count": 1, "preferred_size": "M"}) is False
    # A missing leaf inside `or` must not fabricate the alternative.
    assert evaluate_predicate(predicate, {"order_count": 3}) is False


def test_missing_leaf_is_false_even_for_not_equal() -> None:
    assert evaluate_predicate({"field": "order_count", "op": "!=", "value": 1}, {}) is False
    assert (
        evaluate_predicate({"field": "order_count", "op": "!=", "value": 1}, {"order_count": 1})
        is False
    )
    assert (
        evaluate_predicate({"field": "order_count", "op": "!=", "value": 1}, {"order_count": 2})
        is True
    )


def test_type_mismatch_compares_as_no_match() -> None:
    # Ordered op against a string: uncertain => False, never an exception.
    assert (
        evaluate_predicate({"field": "order_count", "op": ">=", "value": "two"}, {"order_count": 3})
        is False
    )
    # Ordered op on non-numeric traits is refused as a match.
    assert (
        evaluate_predicate(
            {"field": "preferred_size", "op": "<", "value": "N"}, {"preferred_size": "M"},
        )
        is False
    )
    # bool is an int subclass in Python but must not read as a count.
    assert (
        evaluate_predicate({"field": "order_count", "op": ">=", "value": 1}, {"order_count": True})
        is False
    )
    # Equality across types is simply not equal (no error, no match).
    assert (
        evaluate_predicate({"field": "order_count", "op": "==", "value": "1"}, {"order_count": 1})
        is False
    )


def test_string_equality_matches_preference_traits() -> None:
    assert (
        evaluate_predicate(
            {"field": "preferred_size", "op": "==", "value": "M"}, {"preferred_size": "M"},
        )
        is True
    )
    assert (
        evaluate_predicate(
            {"field": "preferred_color", "op": "==", "value": "black"}, {"preferred_size": "M"},
        )
        is False
    )  # preferred_color missing => no match


def test_dotted_subfield_resolution() -> None:
    predicate = {"field": "rfm_score.f", "op": ">=", "value": 3}
    assert evaluate_predicate(predicate, {"rfm_score": {"r": 5, "f": 4, "m": 1}}) is True
    assert evaluate_predicate(predicate, {"rfm_score": {"r": 5, "f": 2, "m": 1}}) is False
    # Missing sub-key and missing root both count as absent.
    assert evaluate_predicate(predicate, {"rfm_score": {"r": 5}}) is False
    assert evaluate_predicate(predicate, {}) is False
    # Root present but not a mapping (malformed value) => no match, no crash.
    assert evaluate_predicate(predicate, {"rfm_score": 7}) is False


# =============================================================================
# Allow-list / sensitive refusal (plan negative tests)
# =============================================================================


def test_sensitive_trait_field_is_refused() -> None:
    for name in sorted(SENSITIVE_TRAIT_NAMES):
        with pytest.raises(PredicateError):
            evaluate_predicate({"field": name, "op": "==", "value": "x"}, {})


def test_non_allow_listed_field_is_refused() -> None:
    with pytest.raises(PredicateError):
        evaluate_predicate({"field": "secret_score", "op": ">=", "value": 1}, {})


def test_registered_predicates_use_only_allow_listed_traits() -> None:
    """Static sweep: no registered rule may read anything but ALLOWED_TRAITS."""
    for key, rule in SEGMENT_RULES.items():
        leaves = iter_predicates(rule.predicate)
        assert leaves, f"{key} has no leaves"
        for leaf in leaves:
            root = str(leaf["field"]).split(".", 1)[0]
            assert root in ALLOWED_TRAITS, f"{key} reads non-allow-listed {root!r}"
            assert root not in SENSITIVE_TRAIT_NAMES


def test_malformed_predicates_are_refused() -> None:
    with pytest.raises(PredicateError):  # unknown op
        validate_predicate({"field": "order_count", "op": "~=", "value": 1})
    with pytest.raises(PredicateError):  # empty boolean composition
        validate_predicate({"op": "and", "preds": []})
    with pytest.raises(PredicateError):  # missing value
        validate_predicate({"field": "order_count", "op": ">="})
    with pytest.raises(PredicateError):  # child must be an object
        validate_predicate({"op": "or", "preds": [{"field": "order_count"}]})
    with pytest.raises(PredicateError):  # non-string field
        validate_predicate({"field": 7, "op": "==", "value": 1})
