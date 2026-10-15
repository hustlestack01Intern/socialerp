# PLAN: Phase 6 — Order creation, stock reservation and the paid→packed path

Date: 2026-10-15 · Author: Chief Engineer · Status: **step 1 landed** (order core first slice,
`make check` green: ruff + mypy + 401 tests + evals). Step 2 (agent L2 tool) still pending.

## Goal and non-goals
- Goal: deterministically **create an order** from catalog items, **reserve stock** in the
  event-sourced ledger, compute **totals in tested code** (never the LLM), and drive the order
  through `draft → awaiting_payment` so the Phase-4 STK flow can pay it.
- Goal: record the **sale (SELL movement)** and fulfil the reservation when the payment
  completes, closing the loop `order → STK → paid → packed`.
- Goal: **cancel** an order and **release** its reservation, and **mark packed** after payment.
- Goal: expose the flow over the orders API (authenticated, tenant-scoped) and wire the
  `payment.completed` outbox side effect.
- Non-goal: the agent L2 `create_order` tool (next commit), delivery/rider (Phase 7),
  pay-on-delivery trigger (Phase 7), aggregator provider, live messaging.

## Classification (taxonomist)
- Type: new application service (`packages/core/orders`) + repositories + API routes + worker handler.
- Risk: **high** (money and stock arithmetic, tenant isolation, state machine).
- Complexity: **high**.

## Assumptions (confidence)
- The **event-sourced `StockMovement` ledger is the source of truth** for availability
  (AGENTS.md §5/§8); `ProductVariant.quantity` is treated as a cached catalogue field and is
  **not** decremented here (0.7). If the owner wants `quantity` kept in sync, that is a follow-up.
- `PaymentService` (Phase 4) already transitions the order to `PAID` on a verified callback
  (1.0). Phase 6 must only add the missing **stock** side effect.
- Payment/refund code is a **PROTECTED ZONE** (constitution §5): this plan does **not** edit it.
  The SELL side effect is delivered via the existing **outbox** (`payment.completed`), not by
  modifying `PaymentService` (0.85).

## Derivation and invariants
- `available = Σ quantity_change` over immutable movements; a reservation is `RESERVE(-q)` keyed
  by the order id, released by `RELEASE(+q)` and closed by `SELL(0)` (see `StockLedger`).
- Money is integer **cents**; `subtotal = Σ unit_price_cents × qty` and
  `total = subtotal + delivery_fee − discount`; `total` must never be recomputed from model output.
- `discount ≥ 0` and `discount ≤ subtotal` (a sale can never be negative).
- Legal transitions only (`LEGAL_TRANSITIONS`): `draft→awaiting_payment`, `awaiting_payment→cancelled`,
  `paid→packed`. Illegal transitions raise `IllegalTransitionError`.
- Idempotency: creating with the same key returns the existing order; the SELL side effect is a
  no-op if a SELL movement already exists for the order.

## Options considered
1. **Reserve at creation; SELL on payment via outbox** (chosen). Matches AGENTS.md and keeps
   payment code untouched.
2. Reserve at creation; SELL inside `PaymentService._complete_payment`. Rejected: edits a protected
   zone and couples stock to payments.
3. No reservation until payment. Rejected: oversells the last item; breaks the M3 design.
4. Synchronous in-request reservation only (no movements persisted). Rejected: loses the ledger.

## Steps (each a small commit)
1. **This commit** — plan + ADR 0013; migration `0009` renaming the drifted `metadata` columns to
   `metadata_json`; align ORM enum storage for reservations/stock/catalog variants; `CustomerRepository`
   + `OrderRepository`; `OrderService` (create/cancel/mark_packed/record_sale); worker
   `payment.completed` handler; orders API; tests (happy + failure paths, RLS, concurrent last item).
2. Next — agent L2 `create_order` tool + policy L2 rules + eval promotion.
3. Then — pay-on-delivery trigger, delivery/rider (Phase 7).

## Blast radius and rollback
- New modules; touches `models/{catalog,reservation,stock}.py` (enum storage only), `apps/worker/main.py`
  (register a handler), `apps/api/routes/orders.py` (replace placeholders), `tests/conftest.py`
  (add order tables to the RLS list — strengthening, not weakening).
- Rollback: revert the commit; migration `0009` has a symmetric `downgrade`.

## Incentive review
- Owner: correct totals and no overselling; faster fulfilment. Customer: reserve-then-pay removes
  "sold out after paying". Fraudster: cannot inject prices (catalog-authoritative) or pay less
  (amount match is in Phase 4); cancel-after-pay is blocked.

## Failure modes and detection
- Oversell under concurrency → row lock on the product's movements + ledger re-check; race test.
- Price injected by caller → compare to catalog price; `PriceMismatchError`; test.
- Double SELL on replayed `payment.completed` → existence check; idempotency test.
- Cross-tenant read/write → RLS + explicit `tenant_id` predicates; isolation test.
- Illegal status jumps → `LEGAL_TRANSITIONS`; test.

## Acceptance criteria
- Happy path: create → `awaiting_payment`, reservation RESERVE movement persisted, totals exact;
  pay (Phase 4 callback) → `paid`; `payment.completed` handler records SELL exactly once and
  fulfils the reservation; `pack` → `packed`.
- Failure paths covered by tests: unknown/inactive variant, insufficient stock, qty≤0, empty items,
  duplicate idempotency key, price mismatch, discount>subtotal, illegal pack-before-paid,
  cancel-after-paid, cross-tenant access, concurrent last-item race.
- `ruff`, `mypy --strict`, `pytest` and evals all green.

## Docs to update
- `docs/adr/0013-order-creation-reservation-and-sale.md`, `docs/STATE.md`, this plan.
