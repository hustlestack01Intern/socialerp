# Project State

> State of record. Updated 2026-10-15 by the Chief Engineer after driving the build
> gates green. If this contradicts MAP.md or the filesystem, check `git log` and `docs/plans/`.

## Current Phase
**M3 (catalog/stock/orders) complete. Phase 4 (payments) done except the aggregator provider.
Phase 5 (agent) L0/L1 core complete. Phase 6 (orders) first slice landed: deterministic
`OrderService` (catalog-price authority, reservation on creation, release on cancel/expiry, the
`payment.completed` sale side effect, idempotency, concurrent last-item race), tenant-scoped
orders API, migration `0009` (`metadata` → `metadata_json`) and enum storage alignment.
L2 transact tools, conversation integration, and Phase 7–10 not started.**

## Measured Results (real output, 2026-10-15, after Phase 6 first slice)
| Gate | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | **All checks passed!** |
| Format | `uv run ruff format --check .` | **211 files already formatted** |
| Types | `uv run mypy packages apps` (strict) | **Success: no issues found in 77 source files** |
| Tests | `uv run pytest` | **401 passed, 99 warnings** (~8:26 in `make check`, 8:49 standalone) |
| Evals | `uv run python -m evals.runner` | **6/6 cases pass, wrong-price/stock hallucinations: 0** |
| Full gate | `make check` | **✅ All checks passed!** (ruff + mypy + 401 tests + real evals) |

Phase 6 tests: **37 in `tests/orders/`** — `test_order_service.py` (16: reservation + totals,
catalog-price authority, price mismatch, unknown/inactive variant, insufficient stock writes
nothing, idempotency, duplicate-line merge, customer reuse, cancel/release, cannot-cancel-paid,
idempotent sale, TTL expiry, concurrent last-item race), `test_orders_api.py` (6: create/get,
idempotent header, 409 insufficient, 400 unknown variant, cancel/pack, pack-paid), and
`test_orders_rls.py` (1 cross-tenant isolation). `tests/conftest.py` now installs RLS on the
orders/customers/order_items/reservations/stock_movements/products/product_variants/categories
tables so isolation is actually exercised.

Phase 4 tests: **46 new** — `tests/integrations/daraja/test_daraja_provider.py` (18, 4a),
`tests/payments/test_payment_service.py` (13), `tests/payments/test_payments_api.py` (6),
`tests/payments/test_payments_rls.py` (2), `tests/payments/test_reconciliation.py` (8:
status-query fallback + statement reconciliation), `tests/payments/test_refunds.py` (7),
`tests/integrations/manual/test_manual_provider.py` (6),
`tests/payments/test_manual_flow.py` (3, manual provider end-to-end).

Phase 5 tests: **65 new** — `tests/agent/test_gateway.py` (14) + `tests/agent/test_openai_gateway.py`
(6) for the gateway, `tests/agent/test_intent.py` (24: schema strictness, injection, fraud-number,
language, heuristic), `tests/agent/test_policy.py` (10), `tests/agent/test_agent.py` (11:
grounding, shadow vs answer, fail-safe). Eval harness (`evals/runner.py`) covers 5 capabilities:
faq_answering, order_capture, handoff_decisions, fraud_flagging, swahili_sheng.

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
- **Phase 4 refunds** (human-approved): `PaymentService.refund_payment` (+ `RefundOutcome`/
  `RefundResult`), `POST /api/v1/payments/{payment_id}/refund`, audit + outbox, 7 tests,
  runbook `docs/RUNBOOKS/refunds.md`.
- **Phase 4 statement reconciliation**: `reconcile_statement` (our ledger vs a provider
  settlement statement) with `StatementEntry`/`StatementMismatch`, audit-per-mismatch,
  4 tests, runbook `docs/RUNBOOKS/reconciliation.md`.
- **Phase 4 manual provider**: `packages/integrations/manual/ManualPaymentProvider`
  (pay-to-Till and confirm, fail-closed token), end-to-end through the payment service,
  9 tests. New settings `MANUAL_PAYMENT_TOKEN`/`MANUAL_PAYMENT_TILL`.
- **Phase 5 plan + ADR**: `docs/plans/2026-10-15-phase-5-agent.md`,
  `docs/adr/0012-agent-gateway-autonomy-policy.md` (gateway, autonomy L0–L3, policy,
  `/evals` protected).
- **Phase 5 LLM gateway (first slice)**: `packages/agent/` — model-agnostic `LLMGateway`
  interface + `ScriptedGateway` (CI/tests) + `OpenAICompatibleGateway` (Ollama/OpenAI, mock
  transport tests) + cross-cutting `RetryingGateway` (backoff), `SpendCapGateway`
  (per-tenant/per-request, fails closed) and `CachingGateway`; `build_default_gateway` from
  settings. 18 tests, no network.
- **Phase 5 intent extraction** (`packages/agent/intent.py`): strict `IntentResult` schema
  (`extra="forbid"`), `extract_intent` (untrusted-output validation → `IntentParseError`),
  `LLMIntentExtractor` adapter, and a deterministic offline `HeuristicIntentExtractor`.
  Customer text is data: `detect_injection` (prompt-injection), `detect_personal_number_payment`
  (fraud risk flag) and `detect_language` (en/sw/sheng) drive safety decisions.
- **Phase 5 policy engine** (`packages/agent/policy.py`): `AutonomyLevel` (L0–L3), `RiskLevel`,
  `PolicyAction`, fixed always-human set (complaints, talk-to-human, refunds/discounts/price
  changes/bulk sends/data deletion), confidence gating, injection/risk escalation. Default L0.
- **Phase 5 agent loop** (`packages/agent/agent.py`): grounded L0/L1 `Agent` composed from
  tenant `BusinessFacts` + a `Catalog` tool, so it cannot invent prices/stock/hours/areas;
  fail-safe to a human on extractor/gateway failure.
- **Phase 5 real eval harness** (`evals/runner.py`): runs the labeled `evals/conversation_sets`
  through the real agent, enforces the zero-wrong-price/stock invariant as a hard fail, and
  reports Phase-6+ assertions (order creation, payments, totals) explicitly as **deferred**
  rather than silently passing. `evals/run.py` (duplicate stub) removed.
- **Phase 6 plan + ADR**: `docs/plans/2026-10-15-phase-6-orders.md`,
  `docs/adr/0013-order-creation-reservation-and-sale.md` (ledger is source of truth, reserve at
  creation, sale via the `payment.completed` outbox handler so the protected payment service is
  untouched, enum storage alignment, catalog-price authority).
- **Phase 6 order service** (`packages/core/orders/`): `OrderService.create_order`
  (idempotency → catalog-validated, price-from-catalog lines → integer-cents totals →
  `RESERVE` movement + `Reservation` row per variant → `draft → awaiting_payment` →
  `order.created` outbox + audit), `cancel_order` (release reservation, `draft`/
  `awaiting_payment` only), `record_sale_for_order` (idempotent `SELL` + fulfil reservations),
  `mark_packed`, `expire_stale_reservations`. Stock pre-check happens under a `FOR UPDATE`
  variant lock before any row is written (out-of-stock leaves no partial order).
- **Phase 6 repositories**: `CustomerRepository` (`get_or_create` by normalized phone) and
  `OrderRepository` (`add`/`get`/`get_by_idempotency_key`/`items_for`/`list`) in
  `packages/core/repositories/`.
- **Phase 6 enum alignment + migration 0009**: shared `values_callable=enum_values` helper in
  `models/base.py`, applied to catalog/reservation/stock enum columns (orders/payments already
  had one); `alembic/versions/0009_metadata_rename.py` renames the drifted `metadata` column
  to `metadata_json` on 13 tables (guarded, symmetric downgrade). Applied to the dev DB.
- **Phase 6 payment→sale wiring**: worker outbox handler `handle_payment_completed` calls
  `OrderService.record_sale_for_order`; no-op handlers registered for `order.created`,
  `order.cancelled`, `payment.initiated/failed/refunded`; new `expire_reservations` cron job.
- **Phase 6 orders API**: `apps/api/routes/orders.py` — `POST /api/v1/orders` (idempotency
  header), `GET /api/v1/orders`, `GET /api/v1/orders/{id}`, `POST /{id}/cancel`,
  `POST /{id}/pack`.

## Corrected facts vs. earlier docs
- Namespace/generation target decided and implemented: **ADR 0008** — `packages.core` is the
  system of record; `socialerp` is a thin facade; one `Base` (`packages.core.models.base`).
- The 2026-10-14 "Remaining Blockers" list is resolved: namespace unified, `Money` fixed,
  RLS tests run, `routes/auth.py` type-checks, Makefile fixed.
- Duplicate ADR numbers exist on disk (two each of 0001–0004, 0008); harmless but should be
  renumbered in a docs pass.

## Known Gaps / Not Yet Done
- **Phases 7–10 are unimplemented**: delivery/rider flow, CRM memory/segments/insights,
  Instagram, onboarding wizard, TikTok assisted mode, billing, hardening/load/security review.
  **Phase 6 core is done**; the L2 agent wiring is not (see below).
- **Phase 6 remainder**: wire the agent's L2 `create_order` tool (policy L2) into the
  conversation/channel pipeline with full audit logging, and promote the deferred Phase-6
  eval assertions (order_created, price_accurate, total_cents, payment_initiated) to hard
  checks now that the deterministic service exists. The `/evals` sets cover 5 capabilities
  with one or two cases each; broaden before promoting any prompt/model.
- **Known gap from the payment flow**: when a customer cancels the STK push, the protected
  payment service cancels the order directly and does **not** release the stock reservation.
  The reservation TTL job backstops it (released after expiry) rather than immediately; a
  direct release hook from the payment-cancel path would be cleaner but touches the protected
  zone.
- **Phase 4 remainder**: a licensed **aggregator provider** (third `PaymentProvider`
  implementation) plus its settlement-statement feed, and the pay-on-delivery trigger
  (Phase 7). Status-query fallback, refunds, statement reconciliation, the manual
  provider and the `httpx` runtime-dependency fix are **done**.
- **`evals/runner.py`** now runs the real Phase-5 agent and enforces the zero-hallucination
  invariant. It still scores only Phase-5 capabilities; order/payment assertions are reported
  as deferred and must be promoted to hard checks when Phase 6 lands.
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
1. Phase 6 finish: agent L2 `create_order` tool + policy L2 rules wired into the conversation
   pipeline (audit every agent action); promote deferred Phase-6 eval assertions to hard checks.
2. Phase 4 finish: aggregator provider + its statement feed.
3. Phase 7: delivery zones/fees, rider WhatsApp flow, pay-on-delivery trigger, tracking page.
4. Reconcile docker-compose files; verify `make migrate`/`make run-api` from root; wire tenancy
   middleware into `main.py`.
