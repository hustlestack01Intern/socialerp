# ADR 0015: Order transition boundary and the pay-on-delivery FSM edge

**Status**: Accepted
**Date**: 2026-10-16
**Deciders**: Chief Engineer
**Supersedes/amends**: ADR 0006 (adds one legal edge; adds a canonical helper)

---

## Context

Phase 7a (delivery) and the pay-on-delivery (POD) work introduced three coupled
problems that broke the build gate and threatened money/stock conservation:

1. **Circular import.** `packages/core/orders/service.py` imported
   `packages/core/delivery/repository.py` at module scope (used only in
   `expire_stale_reservations`), while `packages/core/delivery/service.py`
   imported `orders.service` (to call `OrderService._transition`). The
   `delivery/__init__.py` eagerly imports `delivery.service`, so importing
   `packages.core.orders` raised `ImportError: cannot import name
   'OrderNotFoundError' from partially initialized module`, aborting test
   collection.

2. **Split enum identity.** Three `OrderStatus` classes existed
   (`domain/order.py`, `models/order.py`, `domain/__init__.py`). Services compared
   with `is`/`is not`; passing the `models.order` enum made every comparison
   silently false, so timestamps (`paid_at`, `packed_at`, ...) were never set.
   `mypy --strict` flagged four such calls; the runtime damage was silent.

3. **Unreachable POD flow.** The order FSM (ADR 0006) had no
   `AWAITING_PAYMENT -> PACKED` edge, but POD packs goods *before* payment
   (payment fires at rider arrival). POD orders could not legally reach
   `packed`/`out_for_delivery`, so the entire POD path was dead and the
   `is_pod_in_transit` special-case was unreachable through the normal flow.

## Decision

1. **Single canonical enum + shared transition helper.**
   `packages.core.domain.order.OrderStatus` is the only order-status enum used by
   application code. The unused duplicate in `domain/__init__.py` is removed.
   `models.order.OrderStatus` remains *only* as the ORM column definition.
   A new pure function `packages/core/orders/transitions.py::apply_order_transition`
   is the single place that validates a transition (via ADR 0006's table) and sets
   the status plus its timestamp. `OrderService`, `PaymentService` and
   `DeliveryService` all call it; the private `OrderService._transition` is gone.

2. **Dependency direction `delivery -> orders`.** `orders` must not import
   `delivery` at module scope. `expire_stale_reservations` now uses an injectable
   `active_delivery_lookup` callable whose default is a **deferred (function-scope)
   import** of `DeliveryRepository`. This removes the import-time cycle; a
   module-level import-boundary test (`tests/architecture/test_boundaries.py`)
   prevents regression.

3. **One new legal edge: `AWAITING_PAYMENT -> PACKED`, guarded at the service
   layer.** `mark_packed` allows the edge only for POD orders
   (`payment_method == mpesa_pod`) that are `awaiting_payment`; for every other
   method an `awaiting_payment` order still requires `paid` first. The FSM table
   is intentionally broader than the service guard because the table is
   method-agnostic; the guard carries the money rule.

## Rationale

- The transition helper removes two duplicated `_transition` implementations and
  the cross-module private-method call, and makes the enum-identity bug
  impossible (one enum object).
- A deferred import is the smallest change that removes the *import-time* cycle
  while keeping `OrderService(session)` constructible without wiring. The port
  parameter gives tests/composition a clean seam and makes a future full
  inversion (no runtime `orders -> delivery` reference) a one-line change.
- Adding the FSM edge (guarded) preserves the working POD design and the planned
  Phase 7 resume, rather than deleting it. The property tests (no cycles, no
  self-transitions, terminal states) still hold.

## Consequences

### Positive
- Gate restores: no import cycle, one enum identity, typed transitions.
- One code path sets order timestamps; no silent misses.
- POD is reachable end-to-end and money-gated at pack and at delivery.

### Negative / risks
- The FSM table admits `awaiting_payment -> packed` for *all* methods; safety
  relies on the `mark_packed` guard. A future caller that bypasses
  `mark_packed` (e.g. a raw status write) could pack an unpaid prepay order.
  Mitigation: callers must go through the service; add an integration test.

### Invariants preserved
- ADR 0006's table minus the new guarded edge; terminal states unchanged.
- Stock/payment invariants I1/I2/I5 (see the stabilization plan).

## Alternatives considered

- **Port injection only (no helper):** insufficient — `orders` still imported
  `delivery` at module scope, so the cycle persisted.
- **Re-key expiry purely on order status (drop the delivery lookup):** changes
  semantics and breaks the PACKED-without-delivery control test; larger blast radius.
- **Make POD prepay-only / delete POD code:** throws away working Phase 7 work
  and contradicts the roadmap.

## Related
- ADR 0006: Order State Machine (amended: adds `awaiting_payment -> packed`).
- ADR 0013: Order creation, reservation and sale.
- ADR 0014: Agent conversation pipeline.
- Plan: `docs/plans/2026-10-16-stabilize-phase7-pod.md`.
