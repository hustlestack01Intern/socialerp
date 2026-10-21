# PLAN: Money path F0→F2 — docs honesty, gate green, payment-verified plan changes (2026-10-21)

> Approved by the human 2026-10-21: "Yes Please Proceed" — includes pre-approval
> of the F2 billing protected-zone edit once the gate is green at a named SHA
> (recorded in this file and in each F2 commit body, CONSTITUTION §5).

## Goal and non-goals

**Goal**: (F0) make every client-facing doc tell the truth as of HEAD; (F1)
reach a measured-green `make check` at a named SHA with the in-flight M1.5
billing API committed; (F2) close the fraudulent-upgrade hole — a tenant owner
must not be able to self-upgrade a plan without a verified payment reference.

**Non-goals**: sales-surface pages (`/pricing /signup /login`, M1.5 of the
trial-spine plan = next milestone), Stripe, sandbox phone test (M2), trial
policy changes (U60 stays a human decision), any change to customer-payment
code (`packages/core/payments/` — untouched).

## Classification
- Type: engineering completion (docs + lint + billing API hardening).
- Risk: **HIGH** for F2 (billing = money-adjacent protected zone, human-approved
  this session); LOW for F0/F1.
- Complexity: MEDIUM (three small concerns, one commit each).

## Assumptions with confidence
| # | Assumption | Conf | Verify |
|---|---|---|---|
| A1 | The 7 ruff errors are confined to the untracked billing files + `apps/api/main.py` diff | 0.9 | `uv run ruff check .` re-run after fixes |
| A2 | `ManualBillingProvider`/`NoopBillingProvider` remain the pilot rails; verification = a human/founder-confirmed M-Pesa reference passed through the API and durably recorded | 0.8 | F2 tests: plan change without reference → 402; with reference → audited + deduped |
| A3 | No tenant is currently paying/self-upgraded in the dev DB (backfill-safe change) | 0.9 | query dev subscriptions before migration-free schema change; F2 needs **no migration** (validation lives in the service/route) |

## Derivation (invariants)
- I1: cross-tenant access fails closed (existing RLS tests must stay green).
- I2: the LLM never does money arithmetic (untouched here).
- I3: **entitlement must never be granted without a recorded payment reference
  (trial excepted)** — today `POST /billing/subscription` applies any plan for
  any owner with no payment check (`apps/api/routes/billing.py:140-171`); the
  approver is the tenant owner themselves, so "founder confirms" is not
  enforced anywhere. F2 makes the confirm step real: non-trial plan changes
  require `payment_reference` (M-Pesa code) that is (a) required, (b) at least
  minimally validated (shape), (c) durably deduped per tenant so one code
  cannot pay for two upgrades, (d) audited.
- I4: `make check` output is real, solo, at a named SHA with a clean tree.

## Options (chosen + why)
- O1 (chosen): keep the owner-callable route but require `payment_reference`
  for non-trial plans; service-level dedupe on the reference; founder still
  verifies the M-Pesa statement out-of-band (billing-ops runbook already
  planned). Smallest change that closes the hole; reversible.
- O2: restrict the route to a platform-operator role. Rejected: no such role
  exists yet (schema/role churn) and the owner legitimately needs to *accept*
  an upgrade offer; the missing piece is payment proof, not routing.
- O3: wire `ManualBillingProvider.confirm()` as the only path. Rejected: it is
  an in-process set (non-durable) — worse than an audited reference; fixing
  its durability is a bigger change than the hole requires today.

## Steps (each = one commit, tests first where code changes)
1. **F0 commit** `docs: client-facing honesty pass (SHIPPING_GUIDE vs STATE)` —
   rewrite SHIPPING_GUIDE final-status + header to pilot-candidate status;
   refresh STATE.md/MAP.md headers to HEAD truth (M0–M1.4 committed, M1.5
   in-flight, gate measured <date>).
2. **F1a commit** `billing: fix lint/type debt in the M1.5 API` — ruff fixes in
   `apps/api/routes/billing.py`, `packages/core/billing/provider.py`,
   `apps/api/main.py` (no behaviour change).
3. **F1b commit** `billing: M1.5 API — subscription/usage/collection routes`
   — commit the previously untracked files + any `main.py` wiring + the
   existing `tests/billing/` suite staying green.
4. **F2a commit** (tests first, red→green evidence in commit body)
   `billing: non-trial plan change requires a unique payment reference` —
   tests: no reference → error; duplicate reference → error; cross-tenant
   reference reuse → error; trial plan → still allowed; owner-only + audit row
   present; then the implementation.
5. **Gate**: solo `uv run ruff check . && uv run mypy packages apps && uv run pytest && make eval`
   (one runner on the shared test DB); paste SHA + real counts into STATE.md.

## Blast radius and rollback
- F0/F1: docs + lint; rollback = revert commits.
- F2: touches `packages/core/billing/` + `apps/api/routes/billing.py` only;
  additive validation. A too-strict check would block pilot plan changes →
  rollback = revert the two commits; no migration involved (I3).

## Incentive review
- Tenant owner: tempted to self-upgrade for free → F2 blocks it (reference
  required + deduped + audited).
- Founder: must verify one M-Pesa code per upgrade (out-of-band, ~1 min); the
  audit row carries the reference.
- Fraudster with a stolen code: dedupe prevents reuse; shape validation is a
  pre-filter, not proof — statement reconciliation stays a human duty (U68
  residual, documented).

## Failure modes and detection
| Failure | Detection |
|---|---|
| Lint gate still red | solo ruff/mypy run; counts pasted in STATE |
| F2 blocks a legitimate upgrade | clear 4xx detail + tests for the happy path with a valid ref |
| Reference leak into logs | reference is not PII (M-Pesa codes are merchant-facing); no phone number stored |
| Route unauthenticated | existing `require_owner` dependency + a no-JWT 401 test |

## Acceptance criteria
1. `uv run ruff check .` → 0 errors; `mypy packages apps` → success; full
   pytest + evals green (real output, named SHA, clean tree).
2. SHIPPING_GUIDE no longer claims production readiness; STATE/MAP headers
   match `git log`.
3. `POST /api/v1/billing/subscription` with a non-trial plan and no
   `payment_reference` → 4xx; with a duplicate reference → 4xx; with a fresh
   reference → 2xx + `billing.apply_plan_change` audit + reference recorded;
   trial remains reachable without a reference; staff role → 403; no JWT → 401.

## Docs to update
`docs/STATE.md`, `docs/MAP.md` (phase line), `docs/SHIPPING_GUIDE.md`,
`docs/UNCERTAINTY.md` (U68 status note), this plan's measured results section.
