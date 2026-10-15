# PLAN: M3 - Catalog, Stock Ledger, Orders

## Goal and Non-Goals
**Goal:** Implement catalog (products/variants), event-sourced stock ledger with reservations, and order state machine. All with strict tenant isolation, property-based tests, and concurrency safety.

**Non-Goals:**
- No payments integration (M4)
- No delivery/rider flows (M7)
- No agent L2 transact logic (M6)
- No Instagram/TikTok channels (M9+)

---

## Classification
- **Type:** Feature
- **Risk:** High (money/stock arithmetic, concurrency, financial correctness)
- **Complexity:** High (event sourcing, reservations, state machine, property tests)

---

## Assumptions with Confidence

| Assumption | Confidence | Why | How to Verify |
|------------|------------|-----|---------------|
| Event-sourced ledger (append-only) is correct pattern | 0.95 | SPEC §5 mandates immutable movements | Property tests for conservation laws |
| Reservation TTL via background job works | 0.9 | Standard pattern | Integration test with time manipulation |
| Order state machine transitions are exhaustive | 0.95 | SPEC §5 lists 9 states + transitions | Property test for all legal/illegal transitions |
| RLS on new tables enforces tenant isolation | 0.95 | M1 pattern extends to new tables | Cross-tenant test suite |
| Concurrent last-item race test catches overselling | 0.9 | Hypothesis stateful testing | Property test with concurrent transactions |

---

## Derivation: First-Principles Reasoning and Invariants

### Invariant 1: Stock = Sum of Immutable Movements
- **Stock is never overwritten** — only new movement rows added
- **Movement types:** `receive`, `reserve`, `release`, `sell`, `adjust`, `return`
- **Available stock** = Σ(receive) - Σ(reserve) + Σ(release) - Σ(sell) + Σ(adjust) + Σ(return)
- **Conservation law:** Total movements net to zero across all products over time (excluding external receive/return)

### Invariant 2: Reservations are Ephemeral Claims on Stock
- **Created** on order creation (`reserve` movement)
- **Expire** via background job (`release` movement) if unpaid
- **Convert to sale** on payment (`sell` movement, reservation released)
- **Cancelled** on order cancel (`release` movement)
- **Invariant:** Σ(active reservations) ≤ available stock at all times

### Invariant 3: Order State Machine is Strict
```
DRAFT → AWAITING_PAYMENT → PAID → PACKED → OUT_FOR_DELIVERY → DELIVERED
                    ↘ CANCELLED          ↘ REFUNDED        ↘ FAILED_DELIVERY
```
- **Illegal transitions raise exceptions** (tested exhaustively)
- **Each transition emits domain event** for audit/outbox
- **Payment triggers** `PAID` transition only after callback confirmation

### Invariant 4: Money Never Floats
- All prices in **integer cents (KES)**
- **No floating-point arithmetic** anywhere
- **Total = Σ(line items) + delivery fee** — validated server-side

---

## Options Considered (3+ candidates)

| Option | Description | Verdict |
|--------|-------------|---------|
| **A: Event-sourced ledger + reservations + strict state machine (Chosen)** | Immutable movements, explicit reservations, strict state machine with property tests | Matches SPEC §5, §8 exactly; strongest correctness guarantees |
| B: Current-stock column + soft reservations | Single `stock_quantity` column, `reserved_quantity` column, UPDATE with locks | Rejected: violates "never overwrite quantities" (SPEC §5); race conditions |
| C: Optimistic locking on stock column | Version column on products, retry on conflict | Rejected: still overwrites quantities; doesn't provide audit trail |
| D: External inventory service | Separate microservice for stock | Rejected: premature splitting; modular monolith first (SPEC §4) |

---

## Steps (ordered, each a small commit with tests)

### Step M3.1: Catalog Domain Models + SQLAlchemy Models
- `packages/core/domain/catalog.py`: `Product`, `Variant`, `Price`, `Category` value objects
- `packages/core/models/catalog.py`: SQLAlchemy models with RLS
- Properties: SKU, name, description, cost_price, sell_price, reorder_level, supplier, location
- Variant attributes: color, size, weight, dimensions
- **Tests:** Property tests for price arithmetic, SKU uniqueness, variant combinations

### Step M3.2: Stock Ledger (Event-Sourced)
- `packages/core/domain/stock.py`: `StockMovement` domain model, `StockLedger` aggregate
- `packages/core/models/stock.py`: `StockMovement` table (immutable, append-only)
- Movement types enum: `RECEIVE`, `RESERVE`, `RELEASE`, `SELL`, `ADJUST`, `RETURN`
- `StockLedger` methods: `record_receive()`, `reserve()`, `release()`, `sell()`, `adjust()`, `get_available()`
- **Tests:** Property tests for conservation laws, `get_available()` correctness, movement immutability

### Step M3.3: Reservation Logic
- `packages/core/domain/reservation.py`: `Reservation` domain model
- `packages/core/models/reservation.py`: `Reservation` table with `expires_at`, `status` (active/expired/released/fulfilled)
- `ReservationManager`: `create()`, `expire()`, `release()`, `fulfill()`, `get_active_for_product()`
- Background job: `expire_reservations()` — runs every minute, releases expired
- **Tests:** Property tests for reservation lifecycle, expiry correctness, concurrent reservation race test

### Step M3.4: Order State Machine
- `packages/core/domain/order.py`: `Order`, `OrderItem`, `OrderStatus` enum (9 states)
- `packages/core/models/order.py`: `Order`, `OrderItem` tables with RLS
- `OrderManager`: `create_from_cart()`, `transition_to()`, `cancel()`, `refund()`, `mark_paid()`, `mark_packed()`, `mark_out_for_delivery()`, `mark_delivered()`, `mark_failed_delivery()`
- Legal transitions validated; illegal raises `InvalidTransitionError`
- Each transition emits domain event (`OrderCreated`, `OrderStatusChanged`, etc.)
- **Tests:** Property tests for all legal transitions, illegal transition rejection, state machine invariants

### Step M3.5: API Endpoints
- `apps/api/routes/catalog.py`: CRUD for products/variants, listing with pagination/filtering
- `apps/api/routes/orders.py`: Create order, get order, cancel, list with filters
- `apps/api/routes/stock.py`: Get stock levels, adjustments (owner only)
- Request/response models with pydantic validation
- **Tests:** Integration tests for all endpoints, authZ, validation, tenant isolation

### Step M3.6: Database Migration + RLS
- Alembic migration for: `products`, `variants`, `categories`, `stock_movements`, `reservations`, `orders`, `order_items`
- RLS policies on all tables using `app.current_tenant_id()`
- Grants for `socialerp_app` role
- **Tests:** Migration applies cleanly, RLS verified via cross-tenant tests

### Step M3.7: Property-Based Tests (Critical)
- `tests/property/test_stock_ledger.py`: Conservation laws, `get_available()` = expected, movement immutability
- `tests/property/test_reservations.py`: Reservation lifecycle, expiry, no double-booking
- `tests/property/test_order_state_machine.py`: All legal transitions, illegal rejection, state invariants
- `tests/property/test_concurrent_last_item.py`: **Concurrent last-item race test** — 50 concurrent orders for 1 item, verify no overselling

### Step M3.8: Documentation & ADRs
- ADR: Event-sourced stock ledger design
- ADR: Reservation expiry mechanism
- ADR: Order state machine design
- Update MAP.md, STATE.md, UNCERTAINTY.md

---

## Blast Radius and Rollback
- **Blast radius:** Catalog, stock, orders tables + new domain modules. No M0-M2 code broken.
- **Rollback:** `alembic downgrade` for migrations; git revert for code.
- **Verification:** Each step's tests pass before next; M3.7 concurrent test is gate for M3 completion.

---

## Incentive Review

| Stakeholder | Gains | Abuse Risk | Mitigation |
|-------------|-------|------------|------------|
| **Owner** | Accurate stock, no overselling | Staff adjusting stock incorrectly | Audit log on all adjustments; `adjust` movement requires reason |
| **Customer** | Reliable stock info, fair reservations | Gaming reservations (hold stock) | Reservation TTL (15 min default); limit concurrent reservations |
| **Attacker** | Oversell via race condition | Concurrent orders on last item | Property test proves no overselling; DB constraints |
| **Staff** | Easy stock adjustments | Fraudulent adjustments | Audit log + reason required; owner approval for large adjustments |

---

## Failure Modes and Detection

| Failure Mode | Detection | Response |
|--------------|-----------|----------|
| Stock goes negative | Property test `get_available() >= 0` fails | Fix reservation logic; add DB check constraint |
| Reservation not expiring | Background job alert + test | Fix job scheduler; add monitoring |
| Illegal state transition | Property test rejects invalid transitions | Fix transition validation; add logging |
| Concurrent oversell | `test_concurrent_last_item` fails | Fix reservation locking; add DB constraint |
| Migration fails RLS | CI runs migration on clean DB | Fix policy syntax; re-run |

---

## Acceptance Criteria (Measurable)

1. **Catalog CRUD:** Create/update/list products/variants with validation; tenant isolation verified
2. **Stock Ledger:** `get_available()` returns correct value after any sequence of movements; property tests pass
3. **Reservations:** Create → expire → release lifecycle works; concurrent 50 orders for 1 item → max 1 succeeds
4. **Order State Machine:** All 9 states reachable via legal transitions; 100% illegal transitions rejected
5. **Reservation Expiry Job:** Runs every minute, releases expired, logs audit trail
5. **Cross-Tenant Isolation:** All RLS tests pass (0 rows returned for other tenant)
6. **Audit Trail:** Every stock/order action logged with before/after state
7. **CI:** `make check` passes (ruff + mypy --strict + pytest + property tests)

---

## Docs to Update
- `/docs/STATE.md` — M3 done, next = M4
- `/docs/MAP.md` — Add `packages/core/domain/catalog.py`, `stock.py`, `order.py`, `reservation.py`, `packages/core/models/catalog.py`, `stock.py`, `order.py`, `reservation.py`, `packages/conversation` (already added)
- `/docs/UNCERTAINTY.md` — M3 assumptions
- `/docs/adr/0010-event-sourced-stock-ledger.md`
- `/docs/adr/0011-reservation-expiry.md`
- `/docs/adr/0012-order-state-machine.md`

---

## Open Questions for Human (max 5)

1. **Default Reservation TTL:** 15 minutes (SPEC §8 says "unpaid reservations expire by rule")? 
2. **Stock Adjustment Approval:** Require owner approval for adjustments > 10% of stock?
3. **Product Categories:** Pre-seed categories (Clothing, Electronics, Home, etc.) or free-form?
4. **Low Stock Alert Threshold:** Default 5 units (configurable per product via `reorder_level`)?
5. **Order Number Format:** Sequential per-tenant (ORD-0001) or UUID-based?

---

**Ready for your "go" to begin M3 implementation.**