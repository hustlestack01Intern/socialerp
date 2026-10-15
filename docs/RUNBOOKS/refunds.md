# Runbook: Refunds (human-approved)

Refunds are **never automatic**. The agent may *propose* a refund, but a human must
approve every one. The system records who approved, why, and what happened.

## When a refund is allowed

- The payment is `completed` and the order can legally reach `refunded`
  (`paid → refunded` or `failed_delivery → refunded`, per ADR 0006).
- A refund is **not** allowed from `delivered`, `cancelled`, `draft`, `awaiting_payment`,
  `packed` or `out_for_delivery` (the state machine blocks it).
- An already-`refunded` payment is a safe no-op (idempotent).

## How an operator issues a refund

`POST /api/v1/payments/{payment_id}/refund` (authenticated; owner/staff JWT)

```json
{ "reason": "damaged on arrival", "amount_cents": 25000 }
```

- `reason` is required. `amount_cents` is optional (defaults to the full paid amount);
  it must be positive and not exceed the paid amount.
- The endpoint is thin: it calls `PaymentService.refund_payment(...)` with
  `approved_by = <current user id>` (audit trail).

Responses:

| Outcome | Meaning |
|---|---|
| `refunded` | Provider accepted the refund. Payment → `refunded`, order → `refunded`. |
| `manual_required` | Provider cannot refund (Daraja has no self-serve refund API). **A human must complete the reversal offline** (see below). Ledger is left unchanged on purpose. |
| `409` | Not refundable (wrong status / order state). |
| `403` | No approver / not permitted. |
| `502` | Provider declined (`payment.refund_failed`) or a refund error occurred. |

## Provider reality (M-Pesa / Daraja)

`DirectDarajaProvider.refund` raises `NotSupportedError`. Daraja exposes no self-serve
refund API; reversals are done via B2C (business → customer) manually or through a
licensed aggregator. Therefore the common path today is `manual_required`:

1. The attempt is written to the audit log as `payment.refund_manual_required` with the
   receipt, amount, reason and approver.
2. An operator sends the money back (M-Pesa B2C / Till reversal) using the receipt.
3. Reconcile: confirm the reversal against the provider statement (statement
   reconciliation, see `docs/RUNBOOKS/reconciliation.md`).

## Audit trail

Every refund writes an append-only audit entry:

- `payment.refund_manual_required` — provider cannot refund; manual step needed.
- `payment.refund_failed` — provider declined.
- `payment.refunded` — reversal completed; also emits a `payment.refunded` outbox event.

Refunds are idempotent: replaying the same call after success is a no-op.
