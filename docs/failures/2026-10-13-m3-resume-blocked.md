# FAILURE REPORT — 2026-10-13 — M3 resume blocked by unverified code and contradictory state

## 1. What was expected

Resume "where left off" (mid-M3: catalog/stock/orders) by:
reconciling two stale property-test files with the implemented `packages.core.domain.*`
API, aligning ADRs 0005/0006, and getting the root pytest suite to collect and pass.

## 2. What happened (real evidence, reproduced in-process)

Running the "canonical" domain code directly produced money/stock-breaking behaviour:

| Probe | Result |
|---|---|
| `OrderItem(...)` construction | `AttributeError: property 'total_price' has no setter` — the frozen field is shadowed by a read-only property. The order line-item path is dead code. |
| `Order.transition_to(AWAITING_PAYMENT)` then `PAID` | `confirmed_at` and `paid_at` both stay `None` — the timestamp updates are computed but never applied. |
| `Money(1.5)` | Accepted; `Money(1.5) + Money(1.5) == 3.0` cents. Violates "money never floats" (SPEC §5, ADR 0013 invariant 4). |
| `StockLedger`: receive 10, reserve 4, then `release(100)` | `available` becomes 106, `on_hand` becomes 110 — stock conjured from nothing. No over-release guard. |
| `get_reservations()` after reserve+release | `reserved == 0` but the released reservation is still returned as active. |
| FSM table | `paid -> cancelled` (cancel allowed AFTER payment), `failed_delivery -> cancelled` (no `failed_delivery -> refunded`), `delivered -> refunded` (DELIVERED not terminal). |

Additionally:
- Test suite: `pytest --collect-only` → 147 collected, 5 errors (2 stale-API imports, 2 missing `httpx`, 1 starlette TestClient). `make check` cannot run the real suite: Makefile test targets `cd` into empty per-package `tests/unit` dirs, `tests/contract` does not exist, and the root `.venv` lacks runtime deps.
- Docs contradict each other and the tree: `STATE.md` says "Phase 0, await go"; `MAP.md` says "M2 ✓"; the tree is M3-in-progress. ADR set has duplicate numbers (two each of 0001–0004), gaps at 0011–0012.
- Nothing since the scaffold commit is committed; there is no `.gitignore`, so `.venv`/egg-info/caches sit untracked inside the repo (secret-leak risk on any `git add -A`).

## 3. What I believed / which belief was wrong or unverified

- Believed the implemented `packages.core.domain.*` was a trustworthy oracle to port tests to.
  **Wrong** — it is a suspect implementation with release-blocking stock/money bugs.
- Believed "preserve the stale invariants" was well-defined. **Wrong** — the old tests encode
  money-affecting FSM decisions that the code contradicts (cancel-after-paid forbidden vs allowed;
  `FAILED_DELIVERY -> REFUNDED` required vs missing; DELIVERED terminal vs not). Rewriting the tests
  to match the code would silently weaken refund/cancel semantics — the exact action §4 forbids.
- Believed two namespaces (`socialerp.*` alias vs `packages.core.*`) was a local detail.
  **Unverified** — both are load-bearing in the same suite; `socialerp.domain.*` re-exports are
  themselves broken (imports of missing `StockLevel`, `Reservation`, `DeliveryStatus`, `Supplier`).

## 4. Next different strategy (do not repeat the old one)

1. Human decides the system of record FIRST (one question, below). Until then, touch no tests, no ADRs, and no FSM semantics.
2. Fix the verified stock/order bugs with failing tests written first (over-release guard, reservation-reference integrity, `OrderItem`, Money int-exactness, transition timestamps) — only against the human-chosen system of record.
3. Only then reconcile the stale tests (porting, not deleting, their invariants) and align ADR 0005/0006.
4. Stop-presenting the "concurrent last-item" test as a race proof; a real concurrent test requires DB-level state which M3 has not built (no Alembic migration for M3 tables, no RLS on them, routers not wired into `apps/api/main.py`).

## 5. Open blocker for the human

Given the contradictory state of the repo, which is the system of record and fix direction?

- (A) **The ADRs / `socialerp.*` public API** (FSM: cancel-after-paid forbidden, `FAILED_DELIVERY -> REFUNDED` required, DELIVERED terminal) — fix the code to match;
- (B) **The current `packages.core.*` code** (FSM as implemented: cancel-after-paid allowed, `failed_delivery -> cancelled`, DELIVERED -> REFUNDED) — fix the documented bugs, add a new ADR amending 0005/0006, and only afterwards rewrite tests to match;
- and in either case: pick ONE import namespace + distribution name (the code is currently split between `socialerp.*` and `packages.core.*`).

Recommended: (B), because the code is what ships, but with the FSM/cancel/refund semantics and the money/stock bugs fixed under a new ADR before any test reconciliation — and with a written human sign-off on the specific FSM deltas.