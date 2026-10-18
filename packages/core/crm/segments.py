"""
Customer segments (Phase 8b) — fixed enum rules, pure read-time evaluation.

What: a closed set of named segments (:class:`SegmentKey`), each backed by a
JSON predicate tree (:class:`SegmentRule`) evaluated over a customer's LATEST
trait values (per ``(customer, name)`` highest ``version``, as written by
``CRMService.record_trait`` / ``derive_traits``).

Why: the CRM trait graph (Phase 8a) is only useful to the owner if it can be
sliced ("who is at risk?", "who is due to reorder?"). The plan chose fixed
enum segments with a JSON predicate tree as the internal representation
(plan ``2026-10-18-phase-8b-segments-insights.md``, Options 3 + 1): smallest
blast radius now, rules stay data-driven so selected ones can later become
owner-configurable without rewriting the evaluator.

How: ``evaluate_predicate(predicate, traits) -> bool`` is a PURE function over a
``name -> value`` mapping. No DB, no I/O, no LLM (AGENTS.md §5: the model never
computes a number; constitution §2: derive, don't memorize).

Invariants (plan 8b):
1. Determinism — same ``traits`` mapping always yields the same membership;
   evaluation performs no side effects and reads nothing but the mapping.
2. Allow-listed fields only — a predicate field root must be in
   ``ALLOWED_TRAITS``; ``SENSITIVE_TRAIT_NAMES`` (and anything else) raises
   :class:`PredicateError` at validation time, so a sensitive predicate cannot
   exist in a registered rule (negative tests pin this).
3. Missing trait => no match — a leaf whose field (or dotted sub-key) is
   absent evaluates ``False``, including under ``!=`` (invariant 6: uncertain
   values must not fabricate membership).
4. No writes — this module imports nothing that mutates state.

Example::

    evaluate_predicate(
        {"op": "and", "preds": [
            {"field": "order_count", "op": ">=", "value": 2},
            {"field": "rfm_score.f", "op": ">=", "value": 3},
        ]},
        {"order_count": 4, "rfm_score": {"r": 5, "f": 4, "m": 2}},
    )  # True

Threshold derivations (constitution §2: no magic numbers):
- ``HIGH_VALUE_MIN_CENTS`` and ``AT_RISK_MIN_CHURN`` are derived from the
  RFM/churn arithmetic already shipped in ``packages/core/crm/service.py``;
  each constant's comment cites its source.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# The allow-lists are the canonical ones from the CRM service (single source of
# truth; READ-only import — segment evaluation never widens what may be stored).
from packages.core.crm.service import ALLOWED_TRAITS, SENSITIVE_TRAIT_NAMES

# =============================================================================
# Thresholds (each with its derivation — no magic numbers, constitution §2)
# =============================================================================

# "High value" = lifetime paid value at or above the 2nd RFM monetary bucket
# boundary that CRMService already uses to bucket customers (m >= 2 at
# 100_000 cents; packages/core/crm/service.py `_monetary_bucket`):
# KES 1,000 = 100,000 cents (KES 1 = 100 cents). Reusing the existing LTV
# scale means "high value" here and the RFM `m` bucket can never disagree.
HIGH_VALUE_MIN_CENTS = 100_000

# churn_risk = min(1, (days_since_last_order - expected_cycle) / expected_cycle)
# (packages/core/crm/service.py, churn derivation). 0.5 therefore means the
# customer is at least HALF a reorder cycle overdue (30-day cycle -> 45 days
# since the last order): clearly lapsed on the very scale churn_risk was
# built for, before which they merely count as "reorder due".
AT_RISK_MIN_CHURN = 0.5

# =============================================================================
# Predicate language
# =============================================================================

# Boolean composition ops (plan derivation: "all/any boolean composition" —
# deliberately NO `not`: negating a missing field would fabricate membership
# from absence, violating invariant 3/6).
BOOLEAN_OPS = frozenset({"and", "or"})
# Comparison ops applied to a single trait field. Ordered ops accept numbers
# only (bools excluded — bool is an int subclass in Python but is not a count
# or an amount here).
COMPARISON_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})

# A predicate node is JSON: either a boolean node
#   {"op": "and"|"or", "preds": [<predicate>, ...]}   (non-empty)
# or a leaf
#   {"field": "<trait>[.<subkey>]*", "op": <comparison>, "value": <json>}.
Predicate = dict[str, Any]


class PredicateError(ValueError):
    """Raised for a malformed predicate or a field outside the allow-list."""


def validate_predicate(predicate: Mapping[str, Any]) -> None:
    """Structurally validate a predicate tree; refuse non-allow-listed fields.

    Walks the whole tree so an invalid rule can never be evaluated "partly".
    Raises :class:`PredicateError` on unknown ops, empty boolean compositions,
    missing ``field``/``value`` keys, or a trait root outside
    ``ALLOWED_TRAITS`` (sensitive names included — defence in depth).
    """
    op = predicate.get("op")
    if op in BOOLEAN_OPS:
        preds = predicate.get("preds")
        if not isinstance(preds, Sequence) or isinstance(preds, (str, bytes)) or not preds:
            raise PredicateError(f"boolean op {op!r} requires a non-empty 'preds' list")
        for child in preds:
            if not isinstance(child, Mapping):
                raise PredicateError(
                    f"predicate child must be an object, got {type(child).__name__}"
                )
            validate_predicate(child)
        return
    if op in COMPARISON_OPS:
        field = predicate.get("field")
        if not isinstance(field, str) or not field:
            raise PredicateError(f"comparison op {op!r} requires a non-empty string 'field'")
        if "value" not in predicate:
            raise PredicateError(f"comparison op {op!r} requires a 'value'")
        root = field.split(".", 1)[0]
        if root in SENSITIVE_TRAIT_NAMES or root not in ALLOWED_TRAITS:
            # Refuses both the sensitive set (defence in depth) and anything
            # simply not on the allow-list — segments may only read traits the
            # CRM is allowed to store.
            raise PredicateError(f"predicate field not allowed: {field!r}")
        return
    raise PredicateError(f"unknown predicate op: {op!r}")


def evaluate_predicate(predicate: Mapping[str, Any], traits: Mapping[str, Any]) -> bool:
    """Pure, deterministic evaluation of ``predicate`` over latest ``traits``.

    ``traits`` maps trait name -> LATEST value (the service resolves versions;
    this function never sees historical rows). Validation runs first, then
    evaluation. Semantics:

    - boolean ``and``/``or``: all children / any child (non-empty, enforced).
    - leaf: resolve the (possibly dotted) field; if absent at any step ->
      ``False`` (missing trait never matches, for every op including ``!=``).
    - ``==``/``!=``: plain equality on the present value (Python equality;
      ``int != str`` is simply not equal, never an error).
    - ordered ops (``<``, ``<=``, ``>``, ``>=``): both sides must be numbers
      (``bool`` excluded). A type mismatch means the data is uncertain, so the
      answer is ``False`` — never an exception, never fabricated membership.
    """
    validate_predicate(predicate)
    return _evaluate(predicate, traits)


def _evaluate(predicate: Mapping[str, Any], traits: Mapping[str, Any]) -> bool:
    op = predicate["op"]
    if op in BOOLEAN_OPS:
        children: Sequence[Any] = predicate["preds"]
        if op == "and":
            return all(_evaluate(child, traits) for child in children)
        return any(_evaluate(child, traits) for child in children)

    present, actual = _resolve(str(predicate["field"]), traits)
    if not present:
        return False  # invariant: missing trait => no match
    return _compare(str(predicate["op"]), actual, predicate["value"])


def _resolve(field: str, traits: Mapping[str, Any]) -> tuple[bool, Any]:
    """Resolve a dotted trait field; ``(False, None)`` if any step is missing."""
    parts = field.split(".")
    current: Any = traits.get(parts[0], None)
    if parts[0] not in traits:
        return False, None
    for part in parts[1:]:
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "==":
        return bool(actual == expected)
    if op == "!=":
        return bool(actual != expected)
    # Ordered comparisons: numbers only, bool excluded (bool subclasses int).
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    if not isinstance(actual, int | float) or not isinstance(expected, int | float):
        return False
    if op == "<":
        return actual < expected
    if op == "<=":
        return actual <= expected
    if op == ">":
        return actual > expected
    return actual >= expected


# =============================================================================
# Segment definitions (fixed enum baseline — plan Option 3)
# =============================================================================


class SegmentKey(str, enum.Enum):
    """Closed set of segments for Phase 8b (plan Option 3: fixed enum first)."""

    FIRST_TIME = "first_time"
    REPEAT_BUYERS = "repeat_buyers"
    HIGH_VALUE = "high_value"
    AT_RISK = "at_risk"
    REORDER_DUE = "reorder_due"


@dataclass(frozen=True)
class SegmentRule:
    """One segment: key + owner-facing copy + its JSON predicate tree."""

    key: SegmentKey
    title: str
    description: str
    predicate: Predicate


def _build_rules() -> dict[SegmentKey, SegmentRule]:
    """Register the fixed rules, validating every field at import time.

    An invalid or non-allow-listed field fails fast here (and in tests), so a
    sensitive predicate can never ship inside a registered rule.
    """
    rules: dict[SegmentKey, SegmentRule] = {
        SegmentKey.FIRST_TIME: SegmentRule(
            key=SegmentKey.FIRST_TIME,
            title="First-time buyer",
            description="Exactly one order on record (evidence: order_count trait).",
            # Derivation: order_count == 1 is the literal definition of a
            # first-time buyer; order_count is evidence-backed (order ids).
            predicate={"field": "order_count", "op": "==", "value": 1},
        ),
        SegmentKey.REPEAT_BUYERS: SegmentRule(
            key=SegmentKey.REPEAT_BUYERS,
            title="Repeat buyers",
            description="Two or more orders on record.",
            # Derivation: >= 2 orders is the literal definition of repeat.
            predicate={"field": "order_count", "op": ">=", "value": 2},
        ),
        SegmentKey.HIGH_VALUE: SegmentRule(
            key=SegmentKey.HIGH_VALUE,
            title="High-value customers",
            description=(
                f"Lifetime paid value at or above {HIGH_VALUE_MIN_CENTS} cents "
                f"(KES {HIGH_VALUE_MIN_CENTS // 100:,})."
            ),
            # Derivation: HIGH_VALUE_MIN_CENTS (see module header) — the m >= 2
            # RFM monetary bucket boundary, so segments and RFM agree.
            predicate={
                "field": "lifetime_value_cents",
                "op": ">=",
                "value": HIGH_VALUE_MIN_CENTS,
            },
        ),
        SegmentKey.AT_RISK: SegmentRule(
            key=SegmentKey.AT_RISK,
            title="At risk of churning",
            description="At least half a reorder cycle past their expected next order.",
            # Derivation: AT_RISK_MIN_CHURN (see module header) — 0.5 on the
            # churn_risk scale means >= 50% of a cycle overdue.
            predicate={"field": "churn_risk", "op": ">=", "value": AT_RISK_MIN_CHURN},
        ),
        SegmentKey.REORDER_DUE: SegmentRule(
            key=SegmentKey.REORDER_DUE,
            title="Due to reorder",
            description="Past their own expected reorder cycle (repeat customers).",
            # Derivation: churn_risk is 0.0 while within the expected cycle and
            # > 0.0 the moment days_since_last_order exceeds it
            # (packages/core/crm/service.py: over <= 0 -> 0.0). The
            # reorder_cycle_days leaf is a presence check for a non-negative day
            # count (>= 0 matches any real value; a MISSING trait fails the
            # comparison per invariant 6), so first-time customers — who have no
            # measured cycle — are excluded rather than guessed about. The
            # churn_risk evidence (days_since_last_order, expected_cycle_days)
            # keeps each match explainable.
            predicate={
                "op": "and",
                "preds": [
                    {"field": "reorder_cycle_days", "op": ">=", "value": 0},
                    {"field": "churn_risk", "op": ">", "value": 0.0},
                ],
            },
        ),
    }
    for rule in rules.values():
        validate_predicate(rule.predicate)
    return rules


# The closed registry. Membership for a customer = evaluate the rule over that
# customer's latest traits; nothing is persisted (read-time evaluation).
SEGMENT_RULES: Mapping[SegmentKey, SegmentRule] = _build_rules()


def iter_predicates(predicate: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """All leaf nodes of a predicate tree (for registry-wide static checks)."""
    op = predicate.get("op")
    if op in BOOLEAN_OPS:
        leaves: list[Mapping[str, Any]] = []
        for child in predicate["preds"]:
            leaves.extend(iter_predicates(child))
        return leaves
    return [predicate]


__all__ = [
    "AT_RISK_MIN_CHURN",
    "BOOLEAN_OPS",
    "COMPARISON_OPS",
    "HIGH_VALUE_MIN_CENTS",
    "SEGMENT_RULES",
    "Predicate",
    "PredicateError",
    "SegmentKey",
    "SegmentRule",
    "evaluate_predicate",
    "iter_predicates",
    "validate_predicate",
]
