# PLAN: Reconcile the namespace/generation fork and reach a testable baseline

Date: 2026-10-14 · Author: Chief Engineer · Status: **awaiting human decision (one question)**

## Goal and non-goals
- Goal: one installable environment, `socialerp` and `packages.core.*` both import, the app boots, and
  the full test suite runs against real Postgres/Redis with a green (or explicitly-explained) result.
- Goal: resolve the two-generation duplication of infrastructure ORM classes and the two Alembic trees.
- Non-goal: no new product features. No edits to protected zones without approval.

## Classification (taxonomist)
- Type: build/tooling-reconciliation + namespace fork + ORM unification.
- Risk: **high** (touches tenancy isolation and schema).
- Complexity: **medium-high**.

## Verified facts (2026-10-14, reproduced in-process)
1. `socialerp` was not installed in the root venv (root pyproject installed `packages*` and excluded
   `packages.core.socialerp*`); the sub-package `packages/core` was not a workspace member. → FIXED via
   `[tool.uv.workspace] members=["packages/core"]` + `socialerp-core` dependency/source.
2. `socialerp/observability.py` used `Iterator` without importing it → `import socialerp` raised
   `NameError`. → FIXED (one-line import).
3. `packages/integrations/daraja/fakes/main.py` put `#` comments inside a `CREATE TABLE` SQL string →
   the fake Daraja server crashed on startup (`sqlite3.OperationalError: unrecognized token: "#"`). → FIXED.
4. Two SQLAlchemy declarative `Base` objects with overlapping tables: `tenants`, `audit_log` exist in BOTH
   `socialerp.database.Base` (4 tables) and `packages.core.models.base.Base` (20 tables), with different
   columns. This is a schema fork (RLS/migration hazard).
5. Two Alembic trees: root `alembic/` targets `packages.core.models.Base`; `packages/core/alembic/` targets
   `socialerp.database.Base`; `make migrate` runs the second while the app mixes both.
6. `apps/api/routes/auth.py` was written against a `User` with `full_name`/`phone` and an
   `ApiKeyManager(session).create_api_key()` API that the canonical `socialerp.models.core.User`
   (re-exported from `packages.core.models.core`) does not have → 201 mypy errors concentrated here.
7. Domain code `packages.core.domain.*` is healthy: 149 tests pass; `Money` rejects floats; `OrderItem`
   is constructible; over-release raises; FSM matches ADR 0006. The 2026-10-13 failure report is **stale**
   on these points.

## Measured result this session
| Gate | Before | After |
|---|---|---|
| `import socialerp` (root venv) | ModuleNotFoundError | OK (with `.env`) |
| `pytest --collect-only` | hard error at conftest | **241 collected, 0 errors** |
| `pytest` (real PG+Redis) | n/a (0 run) | **234 passed, 1 failed, 6 skipped** |
| ruff | n/a | 8 errors |
| mypy --strict | n/a | 201 errors (20 files) |

## Derivation / invariants that must hold
- One `tenants` table; one `audit_log`; one `Base`; RLS on every tenant table.
- Money/stock arithmetic deterministic and integer-exact; LLM never computes money/stock.
- Protected tests (tenant isolation, security) must pass **unmodified** — if they must change, STOP and ask.
- No test may be weakened to make a gate pass (Constitution §4/§5).

## Options (decision-scientist, scored)
1. **D (recommended): two namespaces, ONE Base.** Keep `socialerp` as the public facade; make it
   re-export infra from `packages.core`; delete the duplicate `Tenant`/`AuditLog`/`Outbox` classes; keep
   the function APIs as thin wrappers; retire `packages/core/alembic` in favour of root `alembic/`.
   Weighted score **+3.45** (best).
2. **A: install the shim, keep both Bases** (+2.00) — prerequisite packaging only; leaves the tenant fork.
3. **F: generate the shim** from an explicit mapping (+1.75) — hardening layer on D.
4. **G: quarantine `socialerp`** (+1.20) — fallback if one Base cannot be achieved.
5. **E: pytest path hack** (+0.10) — rejected (masks the hazard next to a protected gate).
6. **B: `packages.core` only** (−0.15) — viable, higher effort.
7. **C: `socialerp` only** (−0.90) — rejected for now.
Disagreement: **HIGH** (5 of 7 scoring variables are maximally polarised); deciding input is a human
naming/ownership call, not empirics.

## Steps (small commits, tests first)
1. [DONE] Stabilize: install workspace member; fix `Iterator`; fix fake-Daraja SQL; measure suite.
2. [NEXT, needs go] Unify `Base`; convert duplicate ORM classes to re-exports; keep function APIs.
3. [NEXT, needs go] Collapse to ONE Alembic tree (root `alembic/`); add M3 tables + RLS migration.
4. Fix `routes/auth.py` against the canonical `User`/`ApiKeyManager`; fix double router prefixes.
5. Fix lint/typecheck to zero; run `make check`.
6. **Protected zone (needs approval):** fix `Makefile`/`.github/workflows/ci.yml` to use one venv + root
   `tests/` (they currently cd into non-existent per-package dirs and an empty `tests/contract`).

## Blast radius and rollback
- Touch points: `packages/core/socialerp/{database,tenancy,audit,outbox,idempotency}.py`, root
  `alembic/`, `apps/api/routes/auth.py`, `Makefile`/ci.yml (protected).
- Rollback: each step is a small commit; the Base unification is mostly additive re-exports and is
  revertible. No production data exists yet.

## Incentive review
- Owner gains a runnable product; risk is schema divergence silently shipping wrong tenant data → highest
  incentive to unify Base. Attacker/fraudster gains if RLS is applied to the wrong Base (cross-tenant read)
  → invariant guarded by protected RLS tests. No party gains from keeping two Bases.

## Failure modes and detection
- One Base impossible without editing protected RLS tests → detect via M1/M2 in the spike; fall back to G/A.
- Migration tree collision → detect by running each `alembic upgrade head` on a scratch DB.
- Silent weakening of tests → detect via git diff on protected paths; forbidden.

## Acceptance criteria (measurable)
- `import socialerp` and `import packages.core` in ONE venv; `socialerp.database.Base is
  packages.core.models.base.Base`.
- `pytest --collect-only` → 0 errors; full suite → 0 failures (or each failure has a written ADR exception).
- `ruff` → 0 errors; `mypy` → 0 errors.
- `make check` green (requires the protected Makefile edit).
- API boots and `/health` + `/docs` respond.

## Docs to update
STATE.md, MAP.md (current phase), UNCERTAINTY.md, an ADR recording the namespace decision, and per-module
READMEs touched.

## The ONE question for the human (blocker)
Confirm the target: **`packages.core` is the system of record (domain + infra), `socialerp` becomes a thin
public facade (option D)** — and approve the two protected-zone edits (Makefile/CI gate; retain or adjust
the tenant-isolation test harness). If instead `socialerp` must remain the implementation, say so and we
switch to option B/C.
