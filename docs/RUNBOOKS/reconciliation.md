# Runbook: Payment reconciliation

Two independent safety nets. Neither auto-credits money it cannot verify.

## 1. Status-query fallback (automatic, every 5 minutes)

Callbacks can be **late, duplicated or missing**. A worker cron
(`reconcile_stale_payments`, `*/5 * * * *`) re-queries the provider for payments still
`initiated`/`pending` past `DEFAULT_STALE_SECONDS` (15 min).

- **Failure / cancellation** reported by the query is applied (payment → `failed` /
  `cancelled`; order → `cancelled` when still `awaiting_payment`).
- **Success** reported by the query is **not** credited. The Daraja status query carries
  no amount, so it cannot be verified against the attempt. Instead an append-only
  `payment.reconciliation_review_required` audit entry is written and the payment is left
  `pending` for a human to confirm against the provider statement.
- A provider error during the query leaves the payment stale for the next run.

Operator action on `payment.reconciliation_review_required`: check the M-Pesa statement;
if the money is genuinely there, complete the order manually (or re-drive the callback);
if not, let the attempt expire.

Code: `packages/core/payments/reconciliation.py` (`reconcile_all`, `reconcile_tenant`,
`stale_payments`), wired in `apps/worker/main.py`.

## 2. Statement reconciliation (on demand / scheduled)

`reconcile_statement(session, tenant_id, statement, since=…, until=…)` compares our
**completed** payments against a provider settlement statement — a list of
`StatementEntry(receipt, amount_cents)` lines from a Daraja portal export, an aggregator
settlement file, or an operator upload.

It reports mismatches and writes one `payment.reconciliation_mismatch` audit entry per
discrepancy (these are the alert source):

| kind | meaning |
|---|---|
| `missing_in_ledger` | provider paid us but we have no completed payment for that receipt |
| `missing_at_provider` | we recorded a completed payment but the provider statement has no such receipt |
| `amount_mismatch` | receipt present in both, amounts differ |
| `no_receipt` | a completed payment with no receipt number (should not happen) |

Nothing is auto-adjusted — mismatches are surfaced for a human.

The statement source must be supplied by an integration (aggregator API) or operator
upload; there is deliberately **no** fabricated statement fetch. Wiring the aggregator
statement feed is part of the aggregator-provider slice.
