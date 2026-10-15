# Project State

> State of record. Updated 2026-10-15 by the Chief Engineer after driving the build
> gates green. If this contradicts MAP.md or the filesystem, check `git log` and `docs/plans/`.

## Current Phase
**M3 (catalog/stock/orders) complete. Phase 4 (payments) 4a + 4b + status-query fallback
done — direct Daraja STK Push provider, payments ledger, callback handling, API routes and
a reconciliation job. Statement-based reconciliation, refunds and aggregator providers remain.
Phases 5–10 not started.**

## Measured Results (real output, 2026-10-15, after Phase 4 status-query fallback)
| Gate | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | **All checks passed!** |
| Format | `uv run ruff format --check .` | **183 files already formatted** |
| Types | `uv run mypy packages apps` (strict) | **Success: no issues found in 65 source files** |
| Tests | `uv run pytest` | **294 passed, 78 warnings** (~6:11) |
| Full gate | `make check` | **✅ All checks passed!** (ruff + mypy + 294 tests + eval stub) |

Phase 4 tests: **25 new** — `tests/integrations/daraja/test_daraja_provider.py` (18 unit,
Phase 4a) plus the 4b/fallback suite: `tests/payments/test_payment_service.py` (13),
`tests/payments/test_payments_api.py` (6), `tests/payments/test_payments_rls.py` (2),
`tests/payments/test_reconciliation.py` (4: stale selection, failure applied, unverified
success flagged for review, cancellation applied).

No skips. The 5 RLS tests that previously skipped themselves ("RLS policy not working in test
environment") now run and pass, so the cross-tenant isolation guarantee is exercised (not just
asserted). The `Money.from_kes` truncation bug is fixed and covered by
`tests/property/test_domain_primitives.py`.

The earlier blockers recorded on 2026-10-14 (namespace fork, `Money.from_kes` truncation,
skipped RLS tests, non-hermetic suite) are **resolved in tree**.

## Fixed this session (2026-10-15)
Build-gate code fixes (all under the root `pyproject.toml` configuration):

1. **`packages/core/events/whatsapp/`** — an empty directory was shadowing the
   `packages/core/events/whatsapp.py` module for mypy (namespace package resolved ahead of
   the module), producing bogus "module has no attribute MessageDelivered" errors. Removed the
   empty dir. Verified no other empty dir shadows a sibling `.py`.
2. **`packages/core/repositories/__init__.py`** — completed the canonical-model reconciliation:
   `UserRole` domain import, `_affected_rows()` for `rowcount` reads, `ProductRepository.create`
   /`add_variant` take explicit `tenant_id`, variant attributes (de)serialized via helpers,
   `TenantId(...)` wrapping in `_to_domain`, and `ProductRepository.update` no longer calls the
   non-existent `model_dump` on the frozen `UpdateProductRequest` dataclass.
3. **`packages/core/auth/`** — `jose` typed via a `[[tool.mypy.overrides]]` `ignore_missing_imports`
   section (no matching stub ships on PyPI); `UserRole(...)` conversion in `tokens.py`; return-type
   annotations on the role/scope dependency factories.
4. **`packages/conversation/handoff.py`** — replaced calls to the non-existent
   `AuditLogger.log_agent_action(...)` with the real `AuditLogger.log_action(...)` (via a small
   `_audit_handoff` helper); fixed `col is not None` SQL predicates to `col.is_not(None)`;
   fixed async `scalar()` usage and the nullable `first_response_due_at` handling.
5. **`packages/conversation/manager.py`** — `conversation.state` is a `str` column, not the enum;
   corrected `old_state.value` → `old_state`; audit `metadata` made JSON-serializable (was a raw
   `DomainEvent` object, which would fail on the JSONB flush).
6. **`packages/integrations/meta/`** — `sender.py` session typed as `AsyncSession` (was `Any`);
   `window_tracker.py` cache value type admits a `None` last-message timestamp; `webhook.py`
   return annotations, `TenantId(UUID(...))` for the tenant lookup, guarded `request.client`,
   and removal of two dead (unused, non-serializable) event constructions.
7. **`apps/worker/main.py`** — typed arq job signatures (`dict[str, Any]`), typed `functions` /
   `cron_jobs` lists, `-> None` on relay/main.
8. **`Makefile`** — the Python gates (`lint-python`, `typecheck-python`, `format-python`, `test`,
   `test-unit/integration/contract`, `eval`) ran `cd <pkg> && uv run …`, which discarded the root
   config (mypy re-reported 129 errors; `--strict` was applied on top). Rewrote them to run from
   the repo root against the root `pyproject.toml`. `check` now = ruff + `mypy packages apps` +
   `pytest` + evals, matching AGENTS.md §14.

## Done
- M0 scaffold, fake Meta/Daraja servers, docs, ADRs, threat model
- M1 multi-tenant core: RLS, audit, outbox, idempotency, auth/api-keys/JWT, observability
- M2 WhatsApp channel domain + conversation manager/handoff + webhook receiver
- M3 catalog/stock/order/reservation domain + models
- 2026-10-15: all quality gates driven green (see above)
- **Phase 4a** (commit `35f0d62`): `PaymentProvider` interface + value objects
  (`packages/core/domain/payment.py`) and `DirectDarajaProvider`
  (`packages/integrations/daraja/client.py`), 18 unit tests.
- **Phase 4b** (commit `d715853`): `Payment` model + migration `0008_payments` (RLS + grants),
  `PaymentService` (`packages/core/payments/`), `apps/api/routes/payments.py` wired into
  `apps/api/main.py`; ADR `0011-payments-stk-and-callbacks`. Fixed the doubled router
  prefix in `main.py` (catalog/stock/orders) and aligned order/payment enum storage with
  the migrations via `values_callable`.
- **Phase 4 status-query fallback**: `packages/core/payments/reconciliation.py`
  (`stale_payments`/`reconcile_tenant`/`reconcile_all`), `PaymentService.apply_query_result`
  (shared `_apply_result` with the callback path; unverified success → review, not credit),
  provider factory `packages/integrations/daraja/factory.py`, worker job
  `reconcile_stale_payments` (cron every 5 min), 4 reconciliation tests. Moved `httpx` to
  runtime dependencies in `pyproject.toml`.

## Corrected facts vs. earlier docs
- Namespace/generation target decided and implemented: **ADR 0008** — `packages.core` is the
  system of record; `socialerp` is a thin facade; one `Base` (`packages.core.models.base`).
- The 2026-10-14 "Remaining Blockers" list is resolved: namespace unified, `Money` fixed,
  RLS tests run, `routes/auth.py` type-checks, Makefile fixed.
- Duplicate ADR numbers exist on disk (two each of 0001–0004, 0008); harmless but should be
  renumbered in a docs pass.

## Known Gaps / Not Yet Done
- **Phases 5–10 are unimplemented**: agent L0–L3 + policy engine + evals, L2 end-to-end
  transact, delivery/rider flow, CRM memory/segments/insights, Instagram, onboarding
  wizard, TikTok assisted mode, billing, hardening/load/security review.
- **Phase 4 remainder**: statement-based daily reconciliation vs provider settlement files,
  refunds (provider currently raises `NotSupportedError`; human-approved runbook), aggregator
  payment providers, and the pay-on-delivery trigger (Phase 7). The status-query fallback and
  the `httpx` runtime-dependency fix are **done**.
- **`evals/runner.py` is a stub** — `make eval` exits 0 but runs no assertions. The eval gate is
  therefore not yet meaningful; must be built with Phase 5.
- **`apps/dashboard`** has no `node_modules` (not installed); `lint-js`/`typecheck-js` are not
  part of `check` and have not been verified.
- **Makefile db/run targets** (`migrate`, `seed`, `run-api`, `run-worker`) still `cd` into
  subdirectories and were not re-verified against the root `alembic.ini`/module paths.
- **Infra is started ad hoc** (colima + `socialerp-pg` :5432, `socialerp-pg-test` :5433, redis
  :6379), not via `docker compose`; `infra/docker-compose.yml` and root `docker-compose.yml`
  disagree on credentials/ports and should be reconciled.
- **Request-scoped RLS context is not wired into the app.** `TenantContextMiddleware` /
  `DatabaseSessionMiddleware` exist but are not added in `main.py`; the payments callback
  route sets `app.current_tenant_id` manually. Authenticated routes still rely on the DB
  connection role. To be addressed with a tenancy middleware pass.
- The whole tree is still largely untracked beyond the few phase commits.

## Risks
- Two-generation duplication can silently ship wrong tenant/schema behaviour until fully retired
  (ADR 0008 chose the direction; the duplicate Alembic tree still exists under
  `packages/core/alembic/`).
- Tests are green on the current DB state; hermeticity (per-test reset) was improved but any
  future DB pollution could reintroduce flakiness. Treat "290 green" as measured, not guaranteed.
- Callbacks are acknowledged on unknown checkout ids and uncredited on amount mismatch without
  a retry. The status-query fallback now backstops missing callbacks for failures and
  cancellations, but a provider-reported **success** without a callback has no verifiable
  amount and is routed to human review rather than auto-credited — so genuinely missing money
  still needs operator attention.
- Full suite wall-clock is ~6 minutes; the shell/tooling default timeout can truncate a run
  (use ≥10 min when running `make check` locally).

## Next Steps
1. Phase 4 finish: statement-based reconciliation + refunds runbook + aggregator provider.
2. Build a real eval harness (Phase 5) so the eval gate asserts something.
3. Reconcile docker-compose files; verify `make migrate`/`make run-api` from root; wire tenancy
   middleware into `main.py`.
