# PLAN: Stabilize Phase 7a + POD — restore `make check`, fix B1–B7

Date: 2026-10-16. Author: Chief Engineer. Approved by human: "option A".

## Goal and non-goals

**Goal.** Restore `make check` to green on the current working tree (HEAD
`3b52fc3` + uncommitted POD work) and eliminate the money/stock conservation
defects B1–B7, without weakening any test or gate.

- B1 circular import `orders.service <-> delivery.service` (blocks pytest collection).
- B2 SELL-after-RELEASE oversell.
- B3 POD flow unreachable through the order FSM.
- B4 `mark_delivered` never checks payment and makes POD proof optional.
- B5 duplicate POD STK attempts can both complete (double charge).
- B6 `Reservation.order_id` does not exist (AttributeError in expiry and tests).
- B7 duplicate `OrderStatus` enum + `is`-comparison -> timestamps silently unset.

**Non-goals (explicitly out of scope).**
- Phase 7 features beyond the defects (rider WhatsApp flow, tracking page).
- The known payment-cancel -> reservation-release gap (STATE.md) — separate
  protected-zone decision.
- B8 (FAILED delivery pins a reservation forever), B9–B12, aggregator statement
  feed, RLS middleware wiring. Documented as known gaps, not fixed here.
- No new dependency; no schema migration (no DB constraint added in this pass).
- Do not touch `/evals`, security/tenant-isolation tests, permission configs, or
  the constitution.

## Classification

- Type: defect-remediation / build-gate restoration (import-cycle refactor,
  enum-identity unification, data-model bugfix, invariant guards, FSM-edge completion).
- Risk: **high** (money/stock conservation; touches order FSM and payment service).
- Complexity: medium-high (7 sub-fixes across `orders`, `delivery`, `payments`,
  `domain`; ~6 reviewable commits).

## Assumptions (confidence) — also copied to /docs/UNCERTAINTY.md

1. `packages.core.domain.order.OrderStatus` is the canonical enum; the
   `models.order` copy exists only for ORM column definitions (0.9).
2. No code imports `OrderStatus` from `packages.core.domain` (the duplicate at
   `domain/__init__.py:361`) — verified by grep (0.95).
3. The correct dependency direction is `delivery -> orders` (delivery is
   downstream); `orders` must not import `delivery` at module scope (0.9).
4. `Reservation.reference_id` is the order id for order-created reservations
   (`reference_type="order"`) (0.95).
5. A partial DB unique index `UNIQUE(order_id) WHERE status='completed'` would be
   the strongest B5 guard, but is deferred to avoid a migration in a stabilization
   pass; the application guard is sufficient for the tested (sequential +
   single-transaction) cases (0.7). Concurrency proof is deferred.

## Derivation (first principles + invariants)

- **I1 (stock conservation).** `available = sum(quantity_change)`. RESERVE is
  `-q`, RELEASE `+q`, SELL `0`. A SELL's only economic content is "close an open
  reservation". If RELEASE already refunded `-q`, a later SELL creates a second
  paid claim on a unit that is now available to others -> oversell. Therefore
  SELL requires an open (unreleased, unsold) reservation for the reference, and
  SELL must be serialized with expiry via the same `FOR UPDATE` variant lock.
- **I2 (delivery settlement).** DELIVERED is terminal and settles money. For POD,
  goods leave before money arrives, so `mark_delivered` MUST require a completed
  payment; proof is mandatory whenever money changes hands at the door.
- **I5 (one completed payment per order).** Attempts are per
  `checkout_request_id`; completion is order-level. A second attempt completing
  would overwrite the receipt and emit a second sale event -> double charge.
- **I7 (single canonical enum identity).** `is`-comparisons require one enum
  object; three copies break identity silently.

## Options (>=3) and the chosen one

- **B1.** (a) shared transition helper only; (b) inject a delivery-lookup port;
  (c) deferred import; (d) re-key expiry purely on order status.
  **Chosen: shared helper (`orders/transitions.py`) + an injectable
  `active_delivery_lookup` whose default is a deferred import.** Removes the
  module-level cycle (the actual failure), keeps a test seam, and does not change
  expiry semantics (so existing expiry tests keep their intended meaning). (d) is
  rejected: it would change behaviour and break the PACKED-without-delivery
  control test.
- **B7.** Keep `domain.order.OrderStatus` canonical; delete the unused duplicate
  in `domain/__init__.py`; make every service import the canonical enum; centralize
  the timestamp-setting helper.
- **B2.** Guard: do not write SELL unless an open reservation covers the line;
  attempt a re-reserve under the lock first; otherwise flag for reconciliation.
  Chosen over "silently skip" (which hides taken money).
- **B3.** Add the legal edge `AWAITING_PAYMENT -> PACKED` and guard `mark_packed`
  at the service layer (POD only). Chosen over "prepay-only" (which deletes
  working POD code) because the roadmap resumes Phase 7 next.
- **B5.** Application guard in the payment service (protected zone, minimal
  touch): refuse to complete when a completed payment already exists for the
  order; refuse to initiate when the order is already paid; key the
  `payment.completed` outbox event by order id.
- **B4.** Gate inside `mark_delivered` for POD only (so prepay stays unchanged).

## Steps (each a small commit with its tests)

1. **C1 (B1+B6+B7).** New `packages/core/orders/transitions.py`
   (`apply_order_transition`); remove the module-level `delivery` import and the
   private `_transition` from `orders/service.py`; add `active_delivery_lookup`
   injection + deferred default; fix `reservation.order_id -> reference_id`;
   make `delivery/service.py` and `payments/service.py` use the canonical enum +
   shared helper; remove the unused duplicate `OrderStatus` in `domain/__init__.py`;
   add a module-level import-boundary test and a transition-helper unit test.
2. **C2 (B2).** SELL-after-RELEASE guard in `record_sale_for_order` + regression test.
3. **C3 (B5).** Duplicate-completion + already-paid guards + order-keyed outbox
   event + tests (replayed callback, second attempt).
4. **C4 (B4).** POD payment gate + mandatory proof in `mark_delivered` + tests.
5. **C5 (B3).** FSM edge + guarded `mark_packed` + tests + ADR update.
6. **C6.** Full `make check`; `@skeptic`; `@scribe` (STATE/MAP/ADR/UNCERTAINTY).

## Blast radius and rollback

- Modules: `packages/core/orders/{service,transitions}.py`,
  `packages/core/delivery/service.py`, `packages/core/payments/service.py`,
  `packages/core/domain/{order,__init__}.py`; tests under `tests/orders`,
  `tests/delivery`, `tests/architecture`.
- No schema change, no data migration. Rollback = `git revert` per commit.
- Highest-risk change: B3 (core FSM). Mitigation: property tests keep passing
  (no cycle created), service-layer guard, ADR amendment.

## Incentive review

- Owner: wants sales not lost to over-eager expiry -> guarded re-reserve.
- Customer: must not be double-charged -> I5 guard.
- Rider: must not be sent to collect when goods are already gone -> I1 guard.
- Fraudster: POD to thin addresses + repeated STK -> the guards reduce repeat
  attempts; fraud flags are Phase 7+ (out of scope).
- Us: do not weaken gates to look green.

## Failure modes and detection

| Failure | Detection |
|---|---|
| Import cycle returns | module-level boundary test fails |
| SELL after RELEASE | invariant regression test + audit event `order.sale_conflict_unreserved` |
| Double completion | I5 test: exactly one COMPLETED + one outbox event |
| POD delivered unpaid | B4 test raises |
| Illegal FSM edge | property tests + service guard error |
| `order_id` attribute bug | mypy `--strict` + the expiry tests |

## Acceptance criteria (measurable)

- `uv run ruff check .` -> All checks passed.
- `uv run ruff format --check .` -> all files formatted.
- `uv run mypy packages apps` -> Success, no issues.
- `uv run pytest` -> all pass (>= 462 collected + new tests), 0 errors.
- `uv run python -m evals.runner` -> 8/8, 0 hallucinations (no regression).
- New tests: boundary, transition helper, SELL-after-RELEASE, duplicate completion,
  POD delivered-unpaid, POD packed-from-awaiting_payment, prepay packed-from-awaiting_payment-raises.

## Docs to update

- `docs/adr/0015-order-transition-boundary-and-pod-fsm-edge.md` (new).
- `docs/adr/0006-order-state-machine.md` (amend: new edge).
- `docs/STATE.md`, `docs/MAP.md`, `docs/UNCERTAINTY.md` (via `@scribe`).
