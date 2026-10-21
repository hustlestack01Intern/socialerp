# PLAN: F3 — a paid plan change must open a paid period (2026-10-21)

> Parent: `docs/plans/2026-10-21-money-path-f0-f2.md` (F0→F2 done) and
> `docs/plans/2026-10-19-trial-spine-first-clients.md` (M1.6 vertical experiment).
> **PROTECTED ZONE:** this changes payment code (`packages/core/billing/service.py`
> and the entitlement decision). Per `docs/CONSTITUTION.md` §5, a human must
> approve before the edit. This plan exists so the approval is informed.

## Why (the failure, measured)

The M1.6 vertical experiment (`tests/e2e/test_trial_to_subscription_spine.py`)
ran the whole spine: signup → trial → expiry → denied inbound → the owner pays
with a unique M-Pesa reference → the sale is recorded.

**The last joint failed.** After the payment:

```
plan      = professional
status    = active
period_end = <14 days in the past>   ← still the trial's date
check_entitlement() -> entitled=False, reason="expired"
```

The money is taken, the plan is changed, the audit says `billing.apply_plan_change`
— and the agent still refuses to answer, because `check_entitlement` sees an
active subscription whose `period_end` is in the past.

Root cause (`packages/core/billing/service.py:433` `apply_plan_change`): the
method sets `plan` and `status` but **never touches `period_end`**, and **no
code path anywhere sets a period end for a paid plan** (`start_trial` is the
only writer, and it is for the trial). Two symmetric defects follow:

| Tenant state before the upgrade | `period_end` after | Consequence |
|---|---|---|
| Expired trial (the spine) | stale, in the past | **the sale does not unlock the product** — the customer pays and stays locked out |
| `pending` (never connected a channel) | `NULL` | an **eternal paid subscription** — it never expires, so it is never re-billed or re-decided |

Both are money-path defects and both were invisible: the unit tests asserted
"the plan changed" and never asserted "the customer can now use the product".

## The fix (proposed)

1. `packages/core/socialerp/settings.py`: add `BILLING_PAID_PERIOD_DAYS = 30`
   (monthly in advance, per `docs/PRICING_STRATEGY.md` "Billing: Monthly in advance").
2. `packages/core/billing/service.py::apply_plan_change`: when the target plan
   is a **paid** plan (not `trial`):
   - `period_end = max(existing period_end if it is in the future, now + BILLING_PAID_PERIOD_DAYS)`
     — a fresh 30-day window when there is no live period, and **never shorter**
     than a period that is already paid for (INV18, "never shortens a paid period");
   - when the target plan is `trial`, the trial semantics are kept untouched (the
     clock still starts at channel connect, U67).
3. The audit row `billing.apply_plan_change` records the new `period_end` in
   `after`, so the money trail shows what was bought and for how long.

Explicitly **not** in this change:
- no automatic expiry email, no renewal, no proration, no refunds (still
  human-approved only);
- no change to the entitlement matrix, the prices, the trial length (U60) or
  the payment-reference rules (F2/U68);
- no change to what the customer is *charged* — the amount is still the
  business's manual M-Pesa verification, not our code.

## Tests (test first, then the change)

| Test | Asserts |
|---|---|
| `test_paid_plan_change_opens_a_paid_period` | expired trial → professional: `period_end ≈ now + 30d`, `status=active`, `check_entitlement` → **entitled** |
| `test_paid_plan_change_never_shortens_a_live_period` | a plan with `period_end` 20 days out stays 20 days out (INV18) |
| `test_paid_plan_from_pending_gets_a_period` | `pending` → professional: `period_end` is set (no eternal subscription) |
| `test_plan_change_to_trial_does_not_open_a_paid_period` | the trial clock still starts at connect (U67) |
| `test_trial_to_subscription_spine` (e2e) | the xfail marker is removed: the payment now re-opens the agent (0 → 1 gateway call) |

## Blast radius, rollback, risk

- **Blast radius:** the billing service and the entitlement decision for paid
  plans. Every existing billing/tenant test must stay green.
- **Rollback:** revert the commit (and drop the setting). A migration is NOT
  needed: `period_end` already exists.
- **Risk:** a wrong period length either locks a paying customer out (too
  short) or gives free service (too long). Mitigations: the length is one
  named setting; the value is in the audit row; the entitlement decision is
  covered by the existing matrix tests.
- **Residual (not fixed here):** renewals, reminders and dunning are not
  implemented — a paid period that ends simply expires the entitlement and
  the customer is asked to pay again (the current manual model).

## Open questions for the human

1. **Is 30 days right?** Monthly in advance, per the pricing strategy. A
   31-day month would be awkward; 30 keeps it simple.
2. **Does the unused trial time roll over into the paid period?** Recommendation:
   **no** (keep the change small; the trial was free and its length is U60).
3. **Who decides the paid period for a plan bought mid-period?** Recommendation:
   the period always runs from the payment date, and an existing live period is
   never shortened (the simple, honest rule above).

## Approval

- [x] Human approves F3 (payment code, protected zone) — **YES, 2026-10-21**,
  bundled with the invite-code gate, the CI wiring and the U60 trial decision.
- [x] Human answers 1–3 above — **1: 30 days** (monthly in advance, per
  `docs/PRICING_STRATEGY.md`); **2: no rollover** of unused trial time; **3: the
  period runs from the payment date and a live period is never shortened**.

## Measured result (2026-10-21)

- Commit `3cba4e6` (F3), `10e8ee5` (this plan), `ba89d34` (the handoff fix
  the F3 test found).
- `packages/core/socialerp/settings.py`: `BILLING_PAID_PERIOD_DAYS = 30`.
- `packages/core/billing/service.py` `apply_plan_change`: a paid plan gets
  `period_end = max(live period_end, now + 30 days)`; trial-plan changes keep
  the trial semantics (the clock starts at channel connect, U67); the audit
  entry records `period_end` before and after.
- Tests: `tests/billing/test_paid_period.py` (6) — the expired-trial buyer is
  entitled after paying, the pending upgrade gets a real period, the live
  period is never shortened, the trial rollover is refused, the audit records
  the period.
- The M1.6 spine test's `xfail` marker was **removed** and both spine tests
  are green (`tests/e2e/test_trial_to_subscription_spine.py`).
- Follow-on defect found by the F3 test: the entitlement-denial path returned
  `requires_human=True` but never set `handoff_requested`, so a denied tenant
  was left in silence. Fixed in `ba89d34`
  (`ConversationManager.set_handoff(..., reason="entitlement_denied")`) with
  the test `tests/billing/test_entitlement_gate.py::test_denied_path_hands_off_to_a_human`.
