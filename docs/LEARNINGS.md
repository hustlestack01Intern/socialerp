# LEARNINGS LOG

> Cadence Analyst weekly report (CONSTITUTION §1 principle 5). Honest, specific, evidence-linked.
> Each entry: what happened, the signal, the root cause, the change, and the verification.

---

## 2026-10-18 — Wave-2 Completion (Phases 8b, 8c, fix-pass, skeptic F3/F5)

### Rewards (what worked)

**EXEMPLAR — Self-fulfilling test caught by skeptic (fix-pass U30 metadata scrub).**
The fix-pass test for U30 manufactured a `phone` key in `payments.metadata_json` that production
never writes (production only writes `idempotency_key` and `customer_message`). The test then
asserted the scrub removed it — a green test defending a phantom. The skeptic found this by
checking the real write path (`packages/core/payments/service.py:243` writes `phone` into the
**outbox payload**, not `metadata_json`). Lesson: **when fixing a leak, prove the leak existed
first (red against real production write-path shape), then green.** Recorded as U51.

**EXEMPLAR — Fix agent's honest "left alone with justification" list.**
The fix agent (commit `e7170d2`) explicitly documented three items it deliberately did not touch,
with reasoning:
- `payments` `paid_at` invariant (protected zone, out of F3 scope)
- `stock.py` `created_by=reference_id` quirk (ledger is delicate, flagged as U57 not hacked)
- CRM LTV `_is_paid` counting refunded orders (different metric from revenue-today, product
  decision needed → U56)
This transparency lets the human triage without re-investigating.

**EXEMPLAR — Red→green evidence required and delivered per finding.**
Both F3 (revenue excludes refunds) and F5 (restock replay-dedupe) have the failing test output
recorded in the commit message `e7170d2`, then the fix, then the passing run. The gate log
(`makecheck-w2-145015.log` line 163: `764 passed`) confirms the full suite green. This is the
constitutional pattern: no claim without evidence.

**EXEMPLAR — ONE-RUNNER policy held all wave (serial gates; skeptic got exclusive DB).**
The 2026-10-17 DB collision (skeptic `pytest` concurrent with CRM agent) taught us that the
shared test DB (`socialerp-pg-test`) cannot support parallel runners. Wave-2 enforced strict
serialization: one `make check` at a time. The gate log shows a clean 18:41 run with 764 passed.
Recorded as process win in STATE.md and UNCERTAINTY.md.

---

### Failures (what didn't)

**FAILURE — 8b agent wrote docs claiming "unwired/unverified" for code it had itself wired and that the gate had already proven.**
The 8b agent's early STATE.md/UNCERTAINTY.md edits (committed in `cbfd762`) stated Phase 8b was
"UNCOMMITTED, UNVERIFIED, routers not wired" — but `8dbc120` had already committed the code,
wired the routers at `apps/api/main.py:165-166`, and the wave-2 gate ran 37+38 tests green
(log lines 51–55, 82–85). The agent wrote status against its **early session snapshot**, not
against verified HEAD. Lesson: **write status docs against verified HEAD (`git log` + gate log),
not against your session's early snapshot; status claims are load-bearing (AGENTS.md §0).**

**FAILURE — Fix-pass test manufactured the `phone` metadata key (self-fulfilling test).**
As noted above, the U30 test created a `phone` key in `metadata_json` that doesn't exist in
production, then asserted its removal. The test passed but the real leak (outbox payload) was
untouched. Root cause: the test author assumed the metadata shape without verifying the actual
write path. Fixed by skeptic finding → U51 filed.

**FAILURE — Phase 10 plan A1 cited ADR 0017 for TikTok evidence.**
The Phase 10 plan (`docs/plans/2026-10-18-phase-10-tiktok-billing-hardening.md`) had
`docs/adr/0017-tiktok-assisted-mode.md` in its "Docs to update" section, but ADR 0017 is CRM
memory. TikTok has no ADR yet. Fixed in this scribe pass (now points to plan/ADR TBD).

**FAILURE — ADR 0017 status still said "Proposed"/"uncommitted" after commit `713a135`.**
The ADR was written before the code landed and never updated to "Accepted". Fixed in this
scribe pass (Status: **Accepted**, implemented in `713a135`, hardening in `229fd48`,
human-approved 2026-10-18).

---

### Metrics (wave-2 measured)

| Metric | Wave-1 | Wave-2 | Delta | Evidence |
|--------|--------|--------|-------|----------|
| Tests passed | 626 | 764 | +138 | `makecheck-112818.log` → `makecheck-w2-145015.log` line 163 |
| Evals passed | 8/8 | 8/8 | 0 | Gate log line 326 |
| Wrong-price/stock hallucinations | 0 | 0 | 0 | Gate log line 326 |
| Ruff | clean | clean | — | Gate log line 3 |
| Mypy strict | success | success | — | Gate log |
| Test breakdown (new) | — | fix-pass 11, segments 37, insights 38, owner console 52 | +138 | Gate log lines 29, 51–55, 82–85 |

---

## 2026-10-17 — Phase 7 Remainder + U29 + Owner Alerts

### Rewards

- **Public tracking with signed tokens (U29):** The fail-closed design (HMAC over tenant+order,
  constant-time verify, same 404 for all failures) was proven by 25 dedicated tests
  (`tests/delivery/test_tracking_token.py`). No PII leakage in JSON or HTML responses.

- **Durable owner alerts (U15 partially resolved):** Moving from log-only to persisted
  `owner_alerts` rows with dedupe + RLS + acknowledge API means money-on-terminal-order events
  survive log rotation. Cross-tenant RLS test passes.

- **POD concurrency fix (U19):** Delivery-row `FOR UPDATE` lock serializes concurrent arrivals;
  barriered test proves exactly one STK push. The post-commit `trigger_pod_collection` pattern
  (idempotency key `delivery:{id}:arrived`) prevents double-prompt on replay.

### Failures

- **Rider router false-positive on "done"/"done for today" (U28):** The bare keyword "done"
  matches `delivered`, so a rider ending their shift with "done for today" would be read as
  having delivered an order. Accepted as LOW residual (negation list would trade recall).

- **Rider flow media gap (U27):** Worker drops non-text inbound; no photo/PIN ingestion path.
  Blocks in-chat POD closure for cash-on-delivery. MED priority, needs media pipeline.

- **POD callback URL silent divergence (U25):** Worker `trigger_pod_collection` needs
  `DARAJA_CALLBACK_URL`; API route falls back to `request.base_url`. If only fallback fires,
  rider path silently does not prompt. MED — needs shared builder or mandatory setting.

---

## 2026-10-16 — Money-Path Hardening (commit `ef95e4a`)

### Rewards

- **Atomic paid-check + write via row locks:** The `is_order_paid` predicate + `FOR UPDATE`
  on callback paths eliminates the stale-callback downgrade race. 9 mutation-verified tests
  (each fails without its fix).

- **Deferred cancel design:** A retry's cancel cannot kill the order or strand a late success
  on the other attempt. Audit trail (`payment.cancel_deferred`, `payment.stale_failure_ignored`)
  makes every decision traceable.

- **Collect-after-terminal capture:** Success on a non-collectable order no longer 500s; money
  captured on Payment, order untouched, durable alert raised. Fail-safe by design.

### Residuals (filed as uncertainties)

- U13: Deferred cancel leaves liveness hole (no auto-expiry bound).
- U14: Concurrent initiate with same idempotency key → raw `IntegrityError` 500.
- U15: Alert is durable but passive (no push channel).
- U16/U17: Fail-safe POD policy blocks delivery; no SELL recorded for captured money — human
  reconciliation required.
- U18: Single-completion DB backstop (partial unique index) deferred.

---

## 2026-10-15 — Build-Gate Code Fixes + Namespace Decision (ADR 0008)

### Rewards

- **Namespace decision (ADR 0008 Option D):** `packages.core` canonical, `socialerp` facade.
  Eliminated the dual-identity mypy hazard (`socialerp.settings` vs `packages.core.socialerp.settings`
  — two module objects, two `lru_cache` singletons, silent disagreement). Root `pyproject.toml`
  now drives all gates.

- **Makefile fixed:** Commands now run from repo root against root config. `make check` =
  ruff + mypy strict + pytest + evals (AGENTS.md §14).

- **Empty dir shadowing fixed:** `packages/core/events/whatsapp/` (dir) was shadowing
  `whatsapp.py` (module) for mypy.

### Failures

- **Settings singleton hazard (recorded in STATE.md):** The dual-import identity caused a
  mid-refactor test break (callback-token test). Rule going forward: never mix the two
  identities in feature + test code.

---

## 2026-10-14 — Blockers Resolved

### Rewards

- **Money.from_kes truncation bug fixed** (property test `test_money_from_kes_truncation`).
- **RLS tests now run and pass** (5 tests, cross-tenant isolation exercised, not just asserted).
- **Full suite green** (493 passed, 0 failed, 0 skipped).

---

## 2026-10-19 — Stale green gate discovered (Scribe re-measurement)

### Failures
- **FAILURE — a green gate claim without a SHA + log after the last code commit is not evidence.**
  STATE.md reported "764 passed, evals 8/8, ruff clean" from a measurement taken at `e7170d2`,
  but Phase 9 commits `c4db565..d1344fd`, docs commit `c955fbd` and a dirty Slice-A fragment
  landed afterwards without any full re-run; CI on `main` was red or absent. Measured
  2026-10-19 at HEAD `c955fbd` + dirty tree: ruff → 34 errors / 15 files; `pytest tests/contract`
  → 10 failed / 1 passed; full suite → 788 passed / 15 failed / 22 errors; Makefile references
  deleted files (lines 109–170). Report: `docs/failures/2026-10-19-stale-green-gate.md`.

### Process fix
- The scribe re-measures the gate after every wave and records SHA + raw command output in
  STATE.md; a claim older than the newest code commit is marked stale, not current.
- CI branch protection on `main` proposed (red gate cannot merge) — pending human approval as
  a protected zone under `docs/plans/2026-10-19-trial-spine-first-clients.md` (M0).

### Business lesson
- Projection tables must foot against their own assumptions: PRICING_STRATEGY.md's
  transaction-MRR projections overstate by ~4–7× vs its own pricing/usage inputs. Corrected,
  footed figures live in `docs/BUSINESS_PLAN.md` §12 (10 tenants ≈ KES 179K/mo; 50 ≈ 844K;
  200 ≈ 3.38M; 1000 ≈ 16.9M). PRICING_STRATEGY.md carries a correction banner; its original
  table is preserved pending owner sign-off. Filed as U59-adjacent uncertainty in UNCERTAINTY.md.

---

## 2026-10-21 — M1.5: the sales surface, the invite gate, and the column drift

### Rewards

**EXEMPLAR — A real HTTP proof against a migrated database found what 915 green tests could not.**
`scripts/prove_sales_spine.py` registers a tenant over HTTP against a database built by
`alembic upgrade head`. It failed with `column users.full_name does not exist` — the migration
chain had never created ten ORM columns (including all seven agent-action `audit_log` fields).
The suite was green because **the test schema is built from ORM metadata**, so the tests exercise
the ORM's idea of the schema, never the migrations' idea of it. The lesson is not "the tests were
bad": the tests were green and correct. **The gate had no assertion about the thing that was
broken.** Fix (`338d8d6`): migration `0018_missing_columns` + a column-drift guard proven red on
0017 and green on 0018. Record: `docs/failures/2026-10-21-column-drift-green-suite.md`.

**EXEMPLAR — Red → green for the drift, shown with the database, not asserted.**
The drift was demonstrated on the live dev database (the RED listing of the ten missing columns at
`0017_invite_codes`) and then re-measured after `alembic upgrade head` (GREEN, no missing
columns). The guard test was run against both revisions.

**EXEMPLAR — The proof script caught a second, different bug in the same flow.**
`User.role` used `Enum(UserRole)` without `values_callable`, so the ORM wrote `OWNER` into a
Postgres enum that only accepts `owner`. A first fix would have stopped at the first error; the
proof ran the whole spine to the login.

**EXEMPLAR — A protected-zone change was approved BEFORE the edit, with the approval recorded.**
The invite gate (B4) touched auth, the migration chain and the production boot guard. The plan
named the protected zone, the human approved it in the same bundle as F3/CI/U60, and the ADR
(`0023`) records the design decisions. The F3 plan's approval box is now ticked with the
human's answers, not with a retrospective claim.

**EXEMPLAR — The billing guard is in the gate, not in a reviewer's head.**
`tests/architecture/test_pricing_page_matches_code.py` compares `pricing.json` with `_PRICE_KES`
and the entitlement matrix: a price change in the backend fails the gate until the page is
updated. The xfail markers on the link guard are gone, so the debt is now paid and the
assertions are unconditional.

### Failures

**FAILURE — "The gate is green" was read as "the platform works".**
915 passing tests, ruff clean, mypy clean, evals 8/8 — and a production database that could not
register a user. The belief was not unreasonable; the measurement was simply measuring the wrong
thing. Root cause: the test schema is generated from the code under test, so a whole class of
bugs (schema drift) is invisible to it. Change: a migrated database is now a first-class test
target, and the guard lives in the gate. Filed as U71 (the guard is a floor: defaults, indexes,
policies and triggers are still unchecked).

**FAILURE — A local dev default that lies.**
`Settings.POSTGRES_PASSWORD` defaults to `socialerp_dev_password` while compose defaults to
`socialerp`; the API's "cannot connect" error reads like a broken database. Filed as U72 (LOW).

**FAILURE — An acceptance criterion quietly downgraded to "deferred".**
B8 (`make seed-demo`) was not built. The reason is defensible (there is no owner console, so a
seeded tenant has no surface to demo) but it would have been easy to mark the criterion done.
It is recorded as deferred **with the reason** in the plan, MAP and STATE.

### Metrics

| Metric | Before | After | Delta | Evidence |
|--------|--------|-------|-------|----------|
| ORM columns missing from the migrated schema | 10 (unmeasured) | 0 | -10 | `alembic upgrade head` + `information_schema` comparison, red on 0017 / green on 0018 |
| Columns the gate now compares | 0 (tables only) | all ORM columns (name, type family, nullability) | +all | `tests/integration/test_migration_column_drift.py` |
| Dead internal links on the public pages | 14 | 0 | -14 | `tests/architecture/test_dashboard_links.py` (xfails removed) |
| xfail(strict) markers in the architecture guard | 3 | 0 | -3 | same file |
| Absolute marketing claims on the public pages | 5 ("30 min", "watch demo", "100% isolation", …) | 0 | -5 | `apps/dashboard/src/app/page.tsx`, `tests/architecture/test_pricing_page_matches_code.py` |
| Invited registrations without a valid code | accepted | 403 with one uniform body | closed | `scripts/prove_sales_spine.py` |
| Rate limit on the auth endpoints | none (the setting existed, nothing read it) | 100/min per IP, per process | closed (first layer) | `tests/integration/test_rate_limit.py` |

---

## Template for future entries

### YYYY-MM-DD — <Phase/Increment name>

#### Rewards
- **EXEMPLAR — <title>.** <What worked, evidence link, lesson>.

#### Failures
- **FAILURE — <title>.** <What didn't, root cause, lesson, uncertainty row if filed>.

#### Metrics
| Metric | Before | After | Delta | Evidence |
|--------|--------|-------|-------|----------|

(End of file)