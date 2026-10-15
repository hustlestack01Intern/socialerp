# ADR 0013: Order creation, stock reservation and the sale side effect

- Status: Accepted
- Date: 2026-10-15
- Phase: 6 (L2 transact core)

## Context
Phase 4 can take an order from `awaiting_payment` to `paid` via a verified M-Pesa STK callback,
and Phase 3 defined the event-sourced stock ledger and the order state machine. What was missing
was (a) deterministic **order creation** (catalog validation, totals, reservation) and (b) the
**stock side effect** that turns a reservation into a sale when payment completes.

`PaymentService` (`packages/core/payments/service.py`) and refunds are a **PROTECTED ZONE** per
CONSTITUTION.md §5: they must not be self-edited. So the sale side effect cannot be added by
calling into the ledger from inside `PaymentService._complete_payment`.

## Decisions
1. **Order creation is a deterministic application service** (`OrderService.create_order`).
   The caller supplies identity + items + delivery, never prices. Unit prices come from the
   catalog (`ProductVariant.sell_price_cents`); the service computes `subtotal`, `total` and
   checks stock. This satisfies AGENTS.md §5 ("the LLM never does money or stock arithmetic").
   The domain `Order` aggregate (frozen, totals validated in `__post_init__`) is the arithmetic
   authority for the model layer as well.

2. **Reserve at creation.** Creating an order writes a `RESERVE` movement
   (`reference_id = order_id`, `reference_type = "order"`) and a `Reservation` row
   (`status = active`, TTL 15 min) per line, then transitions `draft → awaiting_payment`.
   This prevents selling the same last unit twice while the customer pays.

3. **The event-sourced ledger is the source of truth for availability.** Availability is
   `Σ quantity_change` over immutable `StockMovement`s, not `ProductVariant.quantity`.
   `ProductVariant.quantity` is treated as a cached catalogue field and is not decremented by
   this service (a follow-up may reconcile it, or drop it in favour of the ledger).

4. **The sale side effect is delivered through the outbox, not by editing payment code.**
   `PaymentService` already emits `payment.completed` (order id, amount, receipt). A worker
   outbox handler loads the paid order and calls `OrderService.record_sale_for_order`, which
   writes one `SELL` movement per line (closing the reservation) and marks the `Reservation`
   `fulfilled`. This keeps the protected payment path untouched and uses the prescribed
   at-least-once/idempotent outbox pattern.

5. **Enums persist their lowercase *values*, matching the Alembic `String` columns.** The
   migrations define `status`/`reason`/`movement_type` as `String` with lowercase defaults
   (`'active'`, `'receive'`, …), but several ORM columns used SQLAlchemy's default
   name-based storage (`ACTIVE`, `RECEIVE`, …). A shared `values_callable` aligns the ORM with the
   schema for `reservations`, `stock_movements` and catalogue status. (The orders and payments
   models already did this.)

6. **The drifted `metadata` columns are renamed to `metadata_json`** (migration `0009`). The ORM
   attributes are `metadata_json`; the earlier migrations created a column literally named
   `metadata`, so ORM access against a migrated database failed. The rename aligns every table
   with the model and the `audit_log`/`payments` convention.

## Consequences
- Idempotency: `create_order` with a repeated key returns the existing order; `record_sale_for_order`
  is a no-op if a `SELL` for the order already exists, so a replayed `payment.completed` cannot
  double-count a sale.
- Cancelling (`draft`/`awaiting_payment`) writes a `RELEASE` movement and releases the reservation.
  Cancelling a `paid` order is illegal (a paid order can only be refunded — Phase 4, human-approved).
- The order API is thin and tenant-scoped; concurrency is handled by re-loading and re-checking the
  ledger under a row lock on the product's movements.
- `ProductVariant.quantity` can drift from the ledger until reconciled; this is documented as a
  known gap, not silently hidden.
