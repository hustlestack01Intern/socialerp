# Project State

> State of record. Updated 2026-10-17 by the scribe after the **rider WhatsApp quick-reply flow +
> U19 money-path concurrency fix** increment was COMMITTED (slices `b3e8571`/`aec992b`/`7102d1c`)
> plus a router negation-guard fix found in review. It sits on top of the Phase 7b delivery-API
> increment and the same-day money-path hardening session. If this contradicts MAP.md or the
> filesystem, check `git log` and `docs/plans/`.

## Current Phase
**Rider WhatsApp quick-reply flow + U19 arrival concurrency fix — COMMITTED 2026-10-17**
(slices `b3e8571` provider extraction, `aec992b` rider flow + worker wiring, `7102d1c` U19 lock).
A review pass before commit found and fixed a router negation bug: "not arrived" parsed as
`arrived` and "not picked up" as `picked_up` — on a POD order a false `arrived` fires an M-Pesa
prompt to the customer. A negation guard now suppresses those (explicit failure reports like
"not delivered"/"not done"/"undelivered"/"no answer" still parse as failed); 3 new tests.
**Lesson recorded in LEARNINGS.md:** the @skeptic report was largely hallucinated code —
subagent claims are hypotheses, verify every file:line against the file.
`packages/conversation/rider_flow.py` handles a rider's inbound text with a deterministic
EN/SW keyword router (`picked up`/`arrived`/`delivered`/`failed <reason>`/`status`) driving the
delivery FSM — no LLM. The worker (`apps/worker/main.py::handle_inbound_message`) tries the rider
flow first and falls through to the customer agent for non-riders; a rider with nothing in flight
who sends plain text also falls through. Replies are persisted as outbound `WhatsAppMessage` rows
(`metadata_json.rider_flow`). **POD collection is deferred**: an `arrived` on a POD+unpaid order
only sets `collection_due`; the worker commits, then calls `trigger_pod_collection` in a *fresh*
transaction using idempotency key `delivery:{delivery_id}:arrived` — so an at-least-once retry
cannot double-prompt the customer (skeptic B2). A rider's `delivered` on a POD order can't attach
the required proof photo over text, so it is answered with `POD_NEEDS_SHOP` (skeptic B1;
UNCERTAINTY U26/U27). Provider resolution moved to `packages/integrations/provider_resolver.py`,
shared by the API and the worker.

**U19 (money-path concurrency) RESOLVED.** `DeliveryRepository.get_delivery(..., for_update=True)`
holds the delivery row `FOR UPDATE`; `DeliveryService.mark_arrived(..., lock=True)` (used by both
the API arrival route and the rider flow) serializes concurrent arrivals so exactly one STK push
fires; the POD STK itself is fired post-commit in a fresh transaction so the lock is never held
across the network call. `latest_active_for_rider` orders deterministically
(`created_at DESC, id DESC`). `create_rider` normalizes the phone (U21); duplicate zone/rider now
return 409 not 500 (U20); a cross-tenant delivery test fails closed (U23, reduced).

This sits on the same-day **money-path hardening pass (2026-10-16, committed `ef95e4a`)** and the
**Phase 7b delivery API + POD arrival→STK trigger (committed `0bb16be`)**: `apps/api/routes/delivery.py`
is registered in `apps/api/main.py` and exposes tenant-scoped zone/rider CRUD, dispatch, delivery
transitions and list/get. `DeliveryService.mark_failed` guards the ORDER transition on the
PREVIOUS delivery status. The FSM edges it depends on are recorded in ADR 0015 + ADR 0006.
Deficiencies are tracked honestly in UNCERTAINTY U13–U27 (U19–U21 resolved this session; U25–U27
are the new rider-flow items). Phase 7 remainder (tracking page, owner alerting) + Phases 8–10 +
owner dashboard are planned in `docs/plans/2026-10-15-program-phases-7-10-dashboard.md`.

## Measured Results (real output, 2026-10-17, after commit of rider-flow + U19 + negation-guard fix)
| Gate | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | **All checks passed!** |
| Format | `uv run ruff format --check .` | **248 files already formatted** |
| Types | `uv run mypy packages apps` (strict) | **Success: no issues found in 93 source files** |
| Tests | `uv run pytest` (via `make check`) | **529 passed** (529 collected; 0 failed, 0 skipped) |
| Evals | `uv run python -m evals.runner` | **8/8 cases pass; wrong-price/stock hallucinations: 0** |
| Full gate | `make check` | **✅ All checks passed!** (all four deps green) |

New tests this session: `tests/delivery/test_delivery_api.py` (U19 lock proof + barriered concurrent
arrival; duplicate zone/rider → 409; invalid rider phone → 422; cross-tenant → 404/empty list),
`tests/conversation/test_rider_flow.py` (router), `tests/conversation/test_rider_flow_service.py`
(picked_up/arrived/failed/inactive/fall-through/POD flag/second-arrival rejection/local-format
lookup/transcript), `tests/conversation/test_pod_collection.py` (post-commit STK, idempotent replay,
paid/prepay/missing-callback-URL/missing-delivery guards), `tests/conversation/test_worker_inbound.py`
(rider bypasses agent; POD arrival triggers collection only after commit; replayed arrival prompts
once).

Prior measurement (2026-10-16, Phase 7b + money-path hardening): ruff clean, mypy 91 files,
`make check` all green. Prior: Phase 4 remainder (aggregator + provider selection): 448 tests, evals 8/8.

Prior measurement (2026-10-15, after Slice 0 — aggregator + provider selection): ruff clean,
227 files formatted, mypy 84 files, 448 tests, evals 8/8 / 0 hallucinations.

Slice 0 tests: **20 new** — `tests/integrations/aggregator/test_aggregator_provider.py` (16:
STK normalize/reject/sub-shilling/HTTP-wrap, status mapping incl. 404→pending, HMAC
pass/fail/missing, IP allowlist, parse normalization, refund, factory) and
`tests/payments/test_payments_api.py` +4 (default→daraja, manual selection, aggregator
selection, unknown-skip + caching). The payments API callback route now injects the
per-tenant provider (`Depends(get_tenant_payment_provider)`) instead of constructing
Daraja directly — callbacks for aggregator/manual tenants route correctly and the
FakeProvider test injection is preserved.

**Settings singleton hazard (found 2026-10-15, must not regress):** `socialerp.settings`
(typings-symlink identity) and `packages.core.socialerp.settings` (workspace identity)
are TWO module objects over the SAME file with TWO lru_cache singletons. Code that
imports one and tests that patch the other silently disagree (this broke the
callback-token test mid-refactor). App code standardizes on `socialerp.settings`
except the aggregator factory (`packages.core.config.Settings`, env-driven).
Rule going forward: never mix the two identities in feature + test code.

Phase 6 tests: **53 in `tests/orders` + `tests/agent`** — `tests/orders/test_order_service.py`
(16: reservation + totals, catalog-price authority, price mismatch, unknown/inactive variant,
insufficient stock writes nothing, idempotency, duplicate-line merge, customer reuse,
cancel/release, cannot-cancel-paid, idempotent sale, TTL expiry, concurrent last-item race),
`tests/orders/test_orders_api.py` (6), `tests/orders/test_orders_rls.py` (1),
`tests/orders/test_agent_order_tool.py` (3: agent → `ServiceOrderCreator` → real
`OrderService`, out-of-stock writes no order, cross-tenant SKU fails closed),
`tests/agent/test_l2_order_tool.py` (8: L2 create via `FakeOrderCreator`, no-phone guard,
unknown item, out-of-stock truth, tool crash fail-safe, L1 still drafts, injection blocked,
idempotency), plus 5 policy tests for the L2→`EXECUTE_ORDER` decision.

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

## Fixed 2026-10-16 (money-path hardening, committed `ef95e4a`)
7 modified files (+539/−18): `packages/core/payments/service.py`,
`packages/core/orders/service.py`, `packages/core/delivery/service.py`,
`apps/worker/main.py`, plus 3 test files. Goal: a late, stale or mis-timed STK callback can
never downgrade, lose or double-spend an order. Guards in the protected payment service
(surgical, tests-first):

1. **Shared predicate `is_order_paid(order)`** (`packages/core/orders/service.py`): True iff
   `payment_status == "completed"` OR `paid_at` is set. Used by `initiate_stk_payment`,
   `_complete_payment`, `_cancel_payment`, `_fail_payment` (payments). Delivery deliberately
   does NOT use it (see 6).
2. **Order row lock on the guard+write paths**: `_get_order(..., for_update=True)` uses
   `with_for_update(of=Order)` inside `handle_callback` and `apply_query_result`, so the
   paid-check and the write are atomic. `initiate_stk_payment` deliberately does NOT lock —
   it must not hold a row lock across the provider network call (that would block a
   concurrent callback); the callback paths do lock.
3. **Stale/deferred cancels** (`_cancel_payment`): a cancel for an order already paid is
   ignored (audit `payment.stale_failure_ignored`, attempt outcome `cancelled`); a cancel is
   DEFERRED (audit `payment.cancel_deferred`) if another payment attempt for the order is
   still INITIATED/PENDING — a retry's cancel can neither kill the order nor strand a late
   success on the other attempt.
4. **Stale failures** (`_fail_payment`): same paid-order guard, same
   `payment.stale_failure_ignored` audit, failure not applied to the order.
5. **Collect-after-terminal capture** (`_complete_payment`): a success callback whose order
   is neither `awaiting_payment` (prepay) nor POD `out_for_delivery` no longer raises
   `IllegalTransitionError` (previously 500 + rollback + stranded money). The collection is
   captured on the Payment (status COMPLETED, receipt, `metadata_json["collected_after_terminal"] = True`),
   the ORDER is left untouched (NOT marked collected), outbox `payment.collected_after_terminal`
   + audit emitted, return. Refunded/delivered orders keep `paid_at`, so they take the
   pre-existing duplicate-payment branch instead.
6. **POD delivery gate tightened** (`DeliveryService.mark_delivered`): requires
   `payment_status == completed` (was `is_order_paid`), so a refunded order — which keeps
   `paid_at` — cannot be delivered.
7. **Reservation expiry hardening** (`OrderService.expire_stale_reservations`): re-reads
   `Reservation.status` under `with_for_update()` after the ACTIVE scan and skips rows no
   longer ACTIVE (no stale EXPIRED overwrite / miscount).
8. **Worker alert handler** (`apps/worker/main.py`): registers
   `handle_payment_collected_after_terminal` — logs a WARNING (money on a non-collectable
   order needs a refund-vs-manual-honour decision), no automatic stock/order side effect,
   and the event is not marked FAILED.

Tests added this session (9 cases across 8 new functions, all passing; each mutation-verified
to fail without its fix): `tests/payments/test_payment_service.py` —
`test_stale_cancel_callback_does_not_downgrade_paid_order`,
`test_stale_failure_callback_does_not_downgrade_paid_order`,
`test_cancel_of_retry_attempt_does_not_cancel_order_with_other_in_flight`,
`test_concurrent_success_and_cancel_callbacks_cannot_downgrade_paid_order`,
`test_success_callback_on_terminal_order_is_captured_not_lost` (parametrized
cancelled/failed_delivery), `test_pod_packed_late_success_is_captured_and_not_collected`;
`tests/orders/test_order_service.py` — `test_expire_skips_reservation_fulfilled_concurrently`;
`tests/delivery/test_delivery_service.py` — `test_pod_delivered_for_refunded_order_raises`.

Remaining gaps from this pass are recorded in `docs/UNCERTAINTY.md` (U13–U17): deferred-cancel
liveness bound, idempotent initiate replay, alert paging, the fail-safe "captured but not
collectable" POD policy, and no SELL recorded for captured-after-non-collectable money.

## Fixed 2026-10-15 (build-gate code fixes)
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
- **Phase 6 agent L2 tool** (`packages/agent/tools.py`, `packages/agent/service_tools.py`):
  the `OrderCreator` boundary (`OrderCreateRequest`/`OrderCreateResult`, stable error codes,
  customer-safe messages) with a deterministic `FakeOrderCreator` (tests/evals) and
  `ServiceOrderCreator` (backed by the real `OrderService`; agent → core dependency direction).
  Policy now returns `EXECUTE_ORDER` for `CREATE_ORDER` at L2/L3 (still `ESCALATE_HUMAN` on
  injection/risk/low-confidence, still `DRAFT_FOR_APPROVAL` below L2). The agent resolves items
  to catalog SKUs, passes the catalog price as a *guard*, calls the tool and reports the tool's
  authoritative total — it never does arithmetic; a tool crash fails safe to a human.
- **Phase 6 evals promoted**: new `order_transaction` capability (L2, `FakeOrderCreator`) is
  **active**, checking `order_created` and exact `total_cents`; the harness now reports every
  expected-but-unchecked assertion as deferred (no silent drops). 8 cases / 6 capabilities.
- **Phase 7a delivery + POD stabilization (committed, 2026-10-16, `3b52fc3`…`22fafc6`)**:
  delivery domain/models + migration `0010_delivery` (RLS); `DeliveryService`
  (`packages/core/delivery/service.py`) with zones, riders, dispatch, picked-up, arrived,
  delivered (payment+proof gated), failed and retry-after-failure; B1–B7 fixed per
  `docs/plans/2026-10-16-stabilize-phase7-pod.md` (import cycle broken via the shared
  `orders/transitions.py::apply_order_transition` helper + injectable `active_delivery_lookup`,
  SELL requires an open reservation, single-completion/already-paid guards, canonical
  `domain.order.OrderStatus` identity, `Reservation.reference_id` in expiry); ADR 0015 new,
  ADR 0006 amended (guarded `awaiting_payment → packed` POD edge).

- **Phase 7b delivery API + POD trigger (committed `0bb16be`, 2026-10-16)**: `apps/api/routes/delivery.py`
  — tenant-scoped FastAPI routes over `DeliveryService`, registered in `apps/api/main.py`
  (`/api/v1/delivery/*` including zones, riders, dispatch, delivery transitions, list/get).
  `POST /deliveries/{id}/arrived` fires an STK Push for the order total when the order is
  `mpesa_pod` and unpaid (idempotency key `delivery:{delivery_id}:arrived`); the provider is
  resolved lazily inside the handler so prepay arrival never needs a configured provider and
  provider errors are reported as `stk_error` while arrival still commits. `DeliveryService.mark_failed`
  checks the previous delivery status: if it was `assigned` (goods never picked up), the order
  is not touched (order stays `packed`); for `picked_up`/`arrived` failures the order transitions
  to `failed_delivery`. `retry_after_failure` moves delivery `failed→picked_up` and order
  `packed→out_for_delivery`. Tests: `tests/delivery/test_delivery_api.py` (8 passed) cover zone/rider
  CRUD, dispatch-requires-packed, full prepay lifecycle, illegal transition 409, POD arrival triggers
  exactly one STK + delivered blocked until paid, failed-on-assigned keeps order packed + retry moves
  to out_for_delivery, prepay arrival works with no provider override; `tests/property/test_delivery_fsm.py`
  (4 passed) validate delivery FSM invariants.

## Corrected facts vs. earlier docs
- Namespace/generation target decided and implemented: **ADR 0008** — `packages.core` is the
  system of record; `socialerp` is a thin facade; one `Base` (`packages.core.models.base`).
- The 2026-10-14 "Remaining Blockers" list is resolved: namespace unified, `Money` fixed,
  RLS tests run, `routes/auth.py` type-checks, Makefile fixed.
- Duplicate ADR numbers exist on disk (two each of 0001–0004, 0008); harmless but should be
  renumbered in a docs pass.

## Known Gaps / Not Yet Done
- **Phase 7b (committed `0bb16be`):** delivery API routes + POD trigger (lazy provider resolution,
  idempotent arrived→STK) are implemented. **Resolved in the 2026-10-17 commit (`7102d1c`)**:
  (was MED) two concurrent arrivals double-pushing STK — now serialized by the delivery-row
  `FOR UPDATE` (U19, barriered test proves one push); (was MED) duplicate zone name/rider phone
  500 → now 409 (U20); (was MED) rider phone not normalized → now normalized to
  2547/2541 + 422 on invalid (U21). **Still open (LOW):** (U22) re-arrival after a failed attempt
  reuses the same idempotency key (if first still in-flight, no new prompt); (U23) no cross-tenant
  test for zone/rider/dispatch routes and `TenantContextMiddleware` not registered in main.py so RLS
  is inert on the API path (delivery queries are tenant_id-scoped); (U24) duplicate/invalid path
  UUIDs and unauthenticated access are not explicitly tested for these routes. (See UNCERTAINTY.md)
- **Phases 7–10 remainder:** rider WhatsApp quick-reply flow + POD trigger **DONE** (committed
  2026-10-16/17); remaining in Phase 7: rider location/photo proof, tracking page, owner alerting;
  then CRM memory/segments/insights, Instagram, onboarding wizard, TikTok assisted mode,
  billing, hardening/load/security review. (POD trigger is DONE in 7b; pay-on-delivery completion gates
  still rely on existing payment callback path.)
- **Phase 6 remainder — conversation pipeline: DONE** (Phase 6b landed 2026-10-15, commit
  `782c04e`; see the Phase 6b section below). L2 order creation is idempotent end-to-end from
  the webhook to the tool.
- **Known gap from the payment flow**: when a customer cancels the STK push, the protected
  payment service cancels the order directly and does **not** release the stock reservation.
  The reservation TTL job backstops it (released after expiry) rather than immediately; a
  direct release hook from the payment-cancel path would be cleaner but touches the protected
  zone.
- **Phase 4 remainder**: the aggregator provider + per-tenant provider selection are **done**
  (commit `38d0bdc`); the pay-on-delivery trigger is **done in Phase 7b (committed `0bb16be`)**; the
  aggregator's settlement-statement feed is still open. Status-query fallback, refunds, statement reconciliation
  and the manual provider are **done**.
- **Money-path hardening gaps (2026-10-16, details in `docs/UNCERTAINTY.md` U13–U17)**: no
  auto-cancel/expiry bound when a deferred cancel leaves an order `awaiting_payment` with
  `payment_status == failed` indefinitely; a concurrent same-idempotency-key initiate can
  surface a raw `IntegrityError` 500 (correctness saved by the unique index, but it should be
  an idempotent replay); the `payment.collected_after_terminal` alert is a log WARNING only
  (no paging); POD money captured while the order is packed/failed_delivery blocks delivery
  until a human resolves refund-vs-manual-honour (fail-safe by design) and records **no SELL**
  (human must reconcile).
- **`evals/runner.py`** now runs the real agent and enforces the zero-hallucination invariant
  across **6 active capabilities** (8 cases) including `order_transaction`. Assertions for
  not-yet-built features (delivery area/fee, `payment_initiated`, …) are still reported as
  deferred and must be promoted when those features land.
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
- Working tree as of 2026-10-17: the rider-flow + U19 increment is **COMMITTED** (`b3e8571`,
  `aec992b`, `7102d1c`) and the router negation guard + 3 tests are included in `aec992b`.
  Only docs (this file, MAP, UNCERTAINTY, LEARNINGS) were pending at the time of the gate run
  and are committed alongside.

## Risks
- Two-generation duplication can silently ship wrong tenant/schema behaviour until fully retired
  (ADR 0008 chose the direction; the duplicate Alembic tree still exists under
  `packages/core/alembic/`).
- Tests are green on the current DB state; hermeticity (per-test reset) was improved but any
  future DB pollution could reintroduce flakiness. Treat "green" as measured on the day, not
  guaranteed.
- Callbacks are acknowledged on unknown checkout ids and uncredited on amount mismatch without
  a retry. The status-query fallback now backstops missing callbacks for failures and
  cancellations, but a provider-reported **success** without a callback has no verifiable
  amount and is routed to human review rather than auto-credited — so genuinely missing money
  still needs operator attention.
- A success callback on a terminal order (cancelled/failed_delivery) is captured on the
  Payment and alerted, but deliberately **not** applied to the order and **not** sold — if the
  `payment.collected_after_terminal` WARNING is missed (log-only alerting, see UNCERTAINTY),
  that money sits unreconciled until a human notices.
- Full suite wall-clock is ~13 minutes (769s measured 2026-10-16); the shell/tooling default
  timeout can truncate a run (use ≥15 min when running `make check` locally).

## Next Steps
1. ~~Agent + commit this increment~~ **DONE 2026-10-17** (3 slices + docs; negation-guard fix
   included; see LEARNINGS.md for the skeptic-hallucination lesson).
2. Phase 7 remainder: tracking page (public, no PII) + owner alerting; then Phases 8–10 per
   `docs/plans/2026-10-15-program-phases-7-10-dashboard.md` (delivery API + POD trigger + rider
   quick replies are DONE; address the rider-flow residual gaps U25–U27).
3. Money-path liveness/observability gaps: auto-cancel or expiry bound for orders left
   `awaiting_payment` by a deferred cancel (U13); idempotent replay for concurrent initiate (U14);
   page a human on `payment.collected_after_terminal` (U15); partial unique index for single
   completion (U18).
4. Reconcile docker-compose files; verify `make migrate`/`make run-api` from root; wire tenancy
   middleware into `main.py` (U23).
## Phase 6b — conversation pipeline wiring (agent on inbound WhatsApp) (2026-10-15)
- **ADR 0014** (`docs/adr/0014-agent-conversation-pipeline.md`): outbox→worker pipeline; webhook
  enqueues `inbound_message` event with `provider_message_id`, `conversation_id`, `contact_id`,
  `customer_phone`, `text_body`; worker runs the agent; outbound replies are persisted as `sent`
  when auto-send is allowed and sent succeeds, else `draft` for owner approval (L0 default).
- **DRAFT status**: added `WhatsAppMessageStatus.DRAFT` (non-native enum, no migration).
- **ConversationAgentService** (`packages/conversation/agent_service.py`): loads tenant
  settings/facts and builds a read-only catalog from active products; constructs `Agent` with
  injected `IntentExtractor`, real `ServiceOrderCreator(session, tenant_id)` and policy; audits
  `agent.message_handled` (intent/confidence/language/injection, autonomy, decision/risk/tools,
  `order_id`, grounded/sent/requires_human); on `requires_human` calls `ConversationManager.set_handoff`;
  persists outbound messages with status `SENT` or `DRAFT` (records `send_error` in `metadata_json`
  on degradation), updates `conversation.last_agent_message_at`. Sender abstraction
  `MessageSender` protocol + `WhatsAppSenderAdapter` over the Meta sender (closes resources).
- **Webhook** (`packages/integrations/meta/webhook.py`): after persisting the inbound message,
  enqueues an `inbound_message` outbox event with idempotency key `inbound_message:{provider_message_id}`
  (prevents duplicate agent runs on webhook retry). Fixed a latent bug: store WhatsApp message
  metadata under `metadata_json` (not `metadata`).
- **Worker** (`apps/worker/main.py`): factory `build_conversation_agent_service` creates service
  with the real sender adapter (tests monkeypatch it); new handler `handle_inbound_message` sets
  tenant context, runs the service, commits, and closes the sender. Registered
  `inbound_message` in the outbox relay.
- **Idempotency thread-through**: `Agent.handle_message(..., idempotency_key=...)` and
  `_execute_order` pass the provider message id into `OrderCreateRequest.idempotency_key`, so a
  retried event cannot create a second order (the tool/OrderService already dedupe by key).
- **Tests**: new `tests/conversation/test_agent_service.py` (L2→sent+real order, L0→draft+no order,
  L1 FAQ→sent, handoff→handoff_requested+draft, send failure→degrades to draft, tenant-scoped
  catalog). `tests/conversation/test_worker_inbound.py` (handler creates order, replayed event does
  not create a second order, non-text payload skipped). `tests/conversation/test_webhook_outbox.py`
  (webhook enqueues exactly one event with correct payload and idempotency key; duplicate webhook
  deduped). All 11 conversation tests pass; full suite **428 passed, 114 warnings**.
