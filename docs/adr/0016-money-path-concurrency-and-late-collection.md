# ADR 0016: Money-path concurrency, stale callbacks and late collection

**Status**: Accepted
**Date**: 2026-10-16
**Deciders**: Chief Engineer
**Amends**: ADR 0006 (POD delivery gate semantics), ADR 0015 (paid predicate shared)

---

## Context

The B1–B7 stabilization fixed the deterministic money/stock defects, but an
adversarial review of the STK Push callback path found a class of **concurrency
and late-callback** failures that silently lose money or stock. None were
caught by the existing tests because they require two interleaved callbacks, a
callback that arrives after the order is no longer payable, or a cancel racing
a success.

Concrete failure modes (all reproduced with real code + real Postgres):

1. **Stale failure/cancel downgrades a paid order.** Two STK attempts exist for
   one order. Attempt 1 succeeds (order `paid`). A late failure/cancel callback
   for attempt 2 unconditionally wrote `order.payment_status = "failed"` and, for
   a cancel, transitioned the order to `cancelled` — after the money was
   collected. The `payment.completed` handler then refused to record the sale
   (`record_sale_for_order` returns unless `paid`/POD-in-transit), so the sale was
   lost and the reservation eventually released to another buyer.

2. **The guard itself was not atomic (TOCTOU).** Even after adding an
   `is_order_paid` check to the failure/cancel writers, the check read the order
   with a plain `SELECT` (no lock, no version). A success callback and a
   failure/cancel callback lock *different* `Payment` rows, so under READ
   COMMITTED the failure transaction could still overwrite the success
   transaction's committed `payment_status`.

3. **A retry attempt's cancel killed the order.** `_cancel_payment` cancelled the
   whole order whenever it was `awaiting_payment`, even if another attempt was
   still in flight. A cancel-first serialization then left a late success with no
   legal transition (`cancelled -> paid` is illegal), raising
   `IllegalTransitionError` → HTTP 500 → rollback → money collected, no sale,
   Daraja retrying forever.

4. **A success callback on an already-terminal order raised.** Paying an order
   that was cancelled / failed-delivery (a documented case: "late callback after
   cancellation", SPEC §7) raised `IllegalTransitionError` and stranded the
   collection exactly as in (3).

5. **Reservation expiry could stale-write a row a sale had closed.** The expiry
   job scanned `ACTIVE` rows, then wrote `EXPIRED` without re-checking; a
   concurrent sale that committed `FULFILLED` in between was overwritten and
   miscounted as released.

6. **The POD delivery gate accepted a refunded order.** `paid_at` is retained on
   refund, so an "ever paid" predicate let a refunded POD order be delivered.

## Decision

1. **One paid predicate.** `is_order_paid(order)` (in `packages/core/orders/service.py`)
   is the single source of truth: true iff `payment_status == completed` **or**
   `paid_at is not None`. Every writer/reader of "is this paid?" uses it, except
   the POD delivery gate (see 6).

2. **Lock order, not just check it.** The two callback entry points
   (`handle_callback`, `apply_query_result`) load the order with
   `SELECT ... FOR UPDATE OF orders` (via `_get_order(..., for_update=True)`),
   so the paid-check and the write are atomic. Lock order is **payment → order**
   everywhere. `Order` eagerly joins `customers`, so the lock is scoped to the
   `orders` table (`with_for_update(of=Order)`).

3. **Stale failures are recorded, never applied.** `_cancel_payment` /
   `_fail_payment` mark the attempt's own record terminal, then if the order is
   already paid they audit `payment.stale_failure_ignored` and return without
   touching the order or emitting `payment.failed`.

4. **A retry's cancel is deferred.** If another attempt for the order is still
   in flight (`INITIATED`/`PENDING`), `_cancel_payment` cancels only this attempt
   and audits `payment.cancel_deferred`; the order stays payable so the surviving
   attempt decides its fate. This removes the `cancelled -> paid` dead end.

5. **Late collection is captured, not lost.** In `_complete_payment`, a success
   callback for an order that is neither `awaiting_payment` (prepay) nor POD
   `out_for_delivery` records the collection truthfully on the `Payment`
   (`status = completed`, receipt, `metadata_json["collected_after_terminal"] = true`),
   emits outbox `payment.collected_after_terminal` + audit, and leaves the
   **order untouched** (never marked collected, never transitioned). The worker
   registers `handle_payment_collected_after_terminal` which raises a WARNING
   alert. Rationale: fail safe — the money is never silently dropped, a sale is
   never fabricated for an order that will not ship, and the merchant is told.

6. **POD delivery gate is strict.** `delivery.mark_delivered` requires
   `order.payment_status == completed` (not `is_order_paid`), so a refunded
   order (`paid_at` retained) cannot be delivered, and a captured-but-unresolved
   collection (5) keeps delivery **blocked** until a human resolves it.

7. **Expiry re-reads under lock.** `expire_stale_reservations` re-reads
   `Reservation.status` with `FOR UPDATE` after its initial scan and skips any
   row no longer `ACTIVE`, so it never overwrites `FULFILLED`/`RELEASED`.

8. **`initiate_stk_payment` takes no order lock**, because it awaits the
   provider's network call; holding `FOR UPDATE` across network I/O would block
   concurrent callbacks. Any double-charge is prevented on the callback side by
   (1)+(2)+(5); a concurrent initiation only creates an extra attempt whose
   outcome the duplicate guard handles.

## Consequences

- The "money collected, no sale, no alert" class of failures is closed for the
  states reachable today. Every new test was mutation-verified to fail without
  its fix.
- **Accepted limitation (fail-safe):** a POD order whose payment is collected
  while it is `packed` or `failed_delivery` is captured + flagged, and delivery
  is *blocked* until a human resolves it (refund vs manual honour). This avoids
  shipping goods with no recorded sale; it is recorded in `docs/UNCERTAINTY.md`.
- **Liveness gap:** a deferred cancel followed by the other attempt failing
  leaves an order in `awaiting_payment` with a failed payment; there is no
  auto-cancel bound yet (UNCERTAINTY U13).
- **LOW:** concurrent same-idempotency-key initiate can surface a raw
  `IntegrityError` (unique index still protects correctness); should become an
  idempotent replay (UNCERTAINTY U14).
- **LOW:** the captured-collection alert is a log WARNING only; paging/notification
  is not wired (UNCERTAINTY U15).
- Migration/DB note: correctness of the callback serialization relies on
  Postgres `FOR UPDATE` with READ COMMITTED; no schema change is required. A DB
  partial unique index guaranteeing at most one `completed` payment per order
  would add defence-in-depth and remains a candidate (UNCERTAINTY U18).
