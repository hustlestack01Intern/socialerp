# ADR 0011: Payments — STK Push, callbacks and the payment-attempt ledger

- Status: Accepted
- Date: 2026-10-15
- Phase: 4 (payments)
- Supersedes/relates to: ADR `0004-payment-provider-interface` (interface contract)

## Context

Phase 4 must take money via M-Pesa. The hard parts are not "call STK Push" but the
trust and consistency edges: a callback is untrusted input, may be duplicated, late,
missing or for the wrong amount; the LLM must never do money arithmetic; and every
tenant row must stay behind Postgres RLS. This ADR records the decisions made for the
first working slice (4a provider + 4b persistence/service/API).

## Decisions

1. **Provider interface is provider-neutral and lives in core.**
   `packages.core.domain.payment` defines `PaymentProvider` (ABC), `STKPushRequest`,
   `STKPushResponse`, `ProviderPaymentResult` (the ADR 0004 `PaymentStatus`, renamed to
   avoid clashing with the existing order `PaymentStatus` enum), `FailureReason`,
   `classify_result_code`, and provider errors. Concrete providers live in
   `packages.integrations`; `DirectDarajaProvider` talks to Daraja over `httpx` with
   injectable transport (unit-tested with `MockTransport`).

2. **`payments` is an attempt ledger; the `orders` row stays the fulfilment truth.**
   One order may have several attempts (retry after insufficient funds, etc.). The
   service moves the order through the **existing** order state machine rather than
   inventing a second status field.

3. **Callback tenant resolution: tenant id + shared secret in the URL path.**
   `POST /api/v1/payments/stk/callback/{tenant_id}/{callback_token}`. The handler
   validates the path token against `DARAJA_CALLBACK_TOKEN`, sets
   `app.current_tenant_id` for the request, then looks the payment up **inside the
   tenant** under RLS. This avoids an un-scoped `checkout_request_id → tenant` lookup
   table (which would itself be an isolation weak point). Optional header token
   (`x-callback-token`) and IP allowlist are enforced by the provider's
   `validate_callback`.

4. **Never trust the callback amount.** On a success callback the service requires
   `result.amount_cents == payment.amount_cents` (which equals `order.total_cents`).
   A mismatch marks the attempt `FAILED` with `failure_reason=wrong_amount`, writes an
   append-only `payment.amount_mismatch` audit entry, does **not** credit the order,
   and still returns HTTP 200 so Daraja does not retry. (Rationale: raising past the
   commit would discard evidence; a 2xx stops the retry loop.)

5. **Idempotency is explicit.** `(tenant_id, checkout_request_id)` is unique (NULLs are
   distinct, so unattempted rows are fine). Re-initiating with the same
   `Idempotency-Key` returns the in-flight attempt. A callback for a payment already in
   a terminal state (`COMPLETED/FAILED/CANCELLED/PARTIAL/REFUNDED`) is a no-op, so
   replayed callbacks cannot double-credit.

6. **Enum storage matches the schema.** `orders`/`payments` use
   `Enum(..., native_enum=False, values_callable=…)` so SQLAlchemy persists lowercase
   **values** (`paid`, `initiated`) that match the Alembic `String` columns and their
   `server_default`s. (Previously `Enum` defaulted to persisting member *names*, which
   did not match the migrations.)

7. **Order transitions on payment.** Success: `awaiting_payment → paid` (and order
   payment fields/receipt set). Customer cancel: `awaiting_payment → cancelled`.
   Insufficient funds / wrong PIN / timeout: the attempt fails but the order stays
   `awaiting_payment` so the customer can retry. Stock release on cancellation is
   owned by the reservation service and is **not** duplicated here.

8. **Status-query fallback never auto-credits on an unverified success.** Callbacks can
   be late, duplicated or missing, so a background job
   (`packages.core.payments.reconciliation.reconcile_all`, wired to the worker's
   `reconcile_stale_payments` cron every 5 minutes) re-queries the provider for payments
   still `initiated`/`pending` past a timeout (`DEFAULT_STALE_SECONDS`, 15 min). Applied
   via `PaymentService.apply_query_result`, which shares `_apply_result` with the
   callback path. The Daraja **status query does not return the amount**, so a
   `COMPLETED` query result cannot be verified against the attempt: it is **not**
   credited and instead writes an append-only `payment.reconciliation_review_required`
   audit entry (status left `pending`). Failures and user cancellations are applied
   directly. A provider error during a query leaves the payment stale for the next run.

9. **Deferred to later Phase 4 slices (tracked, not faked).** Statement-based daily
   reconciliation vs provider settlement files, refunds (provider raises
   `NotSupportedError`; human-approved runbook), aggregator providers, and the
   pay-on-delivery trigger. The `httpx` packaging gap (runtime import but dev-only
   dependency) is now **fixed** — `httpx` is a runtime dependency in `pyproject.toml`.

## Consequences

- The payments path is money-safe and test-covered: success, cancel, insufficient
  funds, pending, wrong amount, duplicate success, invalid signature, unknown checkout,
  RLS cross-tenant read, blocked cross-tenant insert, and the status-query fallback
  (failure applied, unverified success flagged for review, cancellation applied).
- Because callbacks are acknowledged on unknown checkout ids and uncredited on amount
  mismatch without retry, the reconciliation job is the backstop for genuinely missing
  money; success-without-callback remains a human-review case rather than an auto-credit.
- The callback URL must carry the shared token; rotating it requires updating the URL
  registered with Daraja. Documented in `.env.example`.
