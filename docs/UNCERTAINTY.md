# UNCERTAINTY LEDGER

Updated 2026-10-16 by the scribe after the money-path hardening session (7 files, uncommitted,
`make check` green — see STATE.md). Anything marked OPEN is a STOP condition if it affects
money, stock, security or privacy. Older rows (U1–U12) are kept for the record; statuses were
re-verified against the tree on 2026-10-16 where noted.

| # | Assumption | Confidence 0-1 | Why | How to Verify | Status |
|---|---|---|---|---|---|
| U1 | Order FSM semantics match ADR 0006 | 0.95 | Code table is `paid->{packed,refunded}`, no cancel-after-paid, `failed_delivery->{out_for_delivery,refunded}`, DELIVERED terminal; 149 property tests green | Re-verified in-process 2026-10-14; ADR 0015 amended the table once (`awaiting_payment -> packed`, POD-only, service-guarded) | **RESOLVED** (was OPEN) |
| U2 | Two import namespaces (`packages.core.*` + `socialerp.*`) can co-exist in ONE venv | 0.9 | Workspace membership added; `pytest --collect-only` 0 errors | `import socialerp`, `import packages.core` both succeed | RESOLVED (packaging); naming target still OPEN (ADR 0008 cleanup) |
| U3 | `socialerp.domain.*` re-exports are broken | 0.05 | FALSE: all 65 re-export targets resolve; only issue was the package not installed + `Iterator` bug | verified in-process | **REFUTED** (stale doc) |
| U4 | M3 domain code is buggy (OrderItem/Money/over-release/FSM) | 0.1 | REFUTED: Money rejects floats, OrderItem constructible, over-release raises, timestamps apply, FSM matches ADR | in-process probes + 149 tests | **REFUTED** (stale report) |
| U5 | `make check` can run the real suite | 0.1 | Was FALSE on 2026-10-14; Makefile fixed 2026-10-15 (root-config commands), verified again 2026-10-16: ruff + mypy(90) + pytest(493) + evals(8/8) all green | `make check` (or the four commands individually) | **RESOLVED** (was OPEN) |
| U6 | Root venv can import + collect the suite | 0.95 | `pytest --collect-only` → 241 collected, 0 errors (now 493 collected, 0 errors) | re-run anytime | RESOLVED |
| U7 | Runtime interpreter: pyproject says >=3.12/mypy 3.13, venv is 3.13.15 | 0.8 | Single venv now used by root; CI pins 3.12 | align pyproject `requires-python`/targets to 3.13 (or pin CI to 3.13) | OPEN (low risk) |
| U8 | M3 tables get Alembic migration + RLS before M3 done | 0.5 | Migrations `0003`–`0006` with RLS now exist (plus `0007`–`0010`); however TWO Alembic trees still exist | `alembic/versions/*` all have `ENABLE ROW LEVEL SECURITY`; collapse the duplicate tree under `packages/core/alembic/` | **PARTIALLY RESOLVED** — M3/RLS done, tree collapse still open |
| U9 | Fake Meta/Daraja servers simulate provider behaviour faithfully | 0.8 | Fake Daraja now starts (SQL bug fixed); 12 fake-server tests green; no recorded sandbox webhooks yet | record real sandbox webhooks | Pending |
| U10 | `socialerp` vs `packages.core` as system of record | 0.55 | DECIDED: ADR 0008 (option D) — `packages.core` canonical, `socialerp` facade; implemented and mypy/pytest green | ADR 0008 + working tree | **RESOLVED** (decision made; duplicate-tree cleanup still open, see U8) |
| U11 | `routes/auth.py` targets canonical `User`/`ApiKeyManager` | 0.05 | Was FALSE (201 mypy errors on 2026-10-14); now `uv run mypy packages apps` → Success, 90 files | re-run mypy | **RESOLVED** (was REFUTED — fixed) |
| U12 | Full test suite is green | 0.9 | 2026-10-16 measured: **493 passed, 0 failed, 0 skipped**; the 2026-10-14 event-loop flake did not recur | re-run `uv run pytest` | **RESOLVED** (measured; treat as day-of, not guaranteed) |
| U13 | A deferred cancel leaves a liveness hole: if attempt A's cancel is deferred because attempt B is in flight, and B then fails/cancels too, the order stays `awaiting_payment` with `payment_status == failed` indefinitely | 0.8 | The 2026-10-16 guard chose money-safety over liveness (a retry's cancel can no longer kill the order); no auto-cancel/expiry bound was implemented | Add an expiry bound: when no attempt is INITIATED/PENDING and the order is `awaiting_payment`+failed, cancel it; test the sequence | **OPEN — MEDIUM (liveness, not money loss)** |
| U14 | Concurrent `initiate_stk_payment` with the same idempotency key can surface a raw `IntegrityError` 500 instead of an idempotent replay | 0.9 | The unique index preserves correctness (no second charge); only the replay/error-mapping path is missing | Two concurrent initiates in a test must return the first result, not 500 | OPEN (LOW) |
| U15 | `payment.collected_after_terminal` reaches a human: worker handler logs a WARNING only | 0.9 | Handler verified in `apps/worker/main.py`; no pager/notification integration exists, so the event can be missed in log rotation | Wire the alert to a notification channel and confirm receipt end-to-end | OPEN (LOW) |
| U16 | Chosen fail-safe policy: POD money captured while the order is `packed`/`failed_delivery` is recorded on the Payment and flagged, but the ORDER is not marked collected, so the B4 delivery gate BLOCKS delivery until a human resolves refund vs manual honour | 0.95 | Deliberate decision in the 2026-10-16 pass — block on ambiguity rather than auto-mark paid; may strand a deliverable order until reviewed | Owner policy review; test that `mark_delivered` raises while the payment is captured-not-collectable | **DECISION (fail-safe) — revisit with owner's policy** |
| U17 | No stock SELL is recorded for captured-after-non-collectable money (by design) | 0.95 | The sale side effect hangs off `payment.completed`, which is deliberately not emitted in that path; a human must reconcile the sale manually | Daily reconciliation (or an operator runbook step) must flag captured payments with no matching SELL | NOTE — human reconciliation required |
| U18 | The B5 single-completion guard is application-level only: a DB partial unique index `UNIQUE(order_id) WHERE status='completed'` was deferred to avoid a migration in the stabilization pass | 0.7 | Plan `docs/plans/2026-10-16-stabilize-phase7-pod.md` assumption 5 (copied here as the plan required, at C6); sequential + single-transaction cases are tested, concurrency proof deferred | Add the partial unique index migration + a true concurrent-double-complete test | OPEN (deferred by plan; correctness currently rests on the service guard + row lock) |

## Action required
- U7: align `requires-python`/mypy target with the CI pin (human choice: 3.12 or 3.13).
- U8: collapse the duplicate Alembic tree under `packages/core/alembic/`.
- U9: record real sandbox webhooks for the fakes.
- U13 (MEDIUM): decide the auto-cancel/expiry bound for deferred-cancel orders — needs an
  owner-visible rule, not just a code fix.
- U14/U15 (LOW): idempotent initiate replay; page a human on `payment.collected_after_terminal`.
- U16/U17 (design decision): owner to confirm the fail-safe POD policy (block delivery vs
  auto-honour) and who reconciles captured-but-unsold money.
- U18: add the partial unique index migration + concurrency proof (deferred by plan C3).
