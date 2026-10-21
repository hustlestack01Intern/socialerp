# PLAN: enforce the plan (and close the rest of the open list)

Human instruction (2026-10-21): *"Enforce in code, and fix everything else, make
sure it's perfect."* — the answer to U80 (the entitlement matrix was published
but not enforced) plus the open items U71, U77, U78, U79, U81, U82.

- **Goal**: every cell of the plan × feature matrix is enforced in code, in one
  place, with the pricing page generated from that place; and the remaining
  honest-but-unsafe items (RLS not enforced by the database, the in-process
  rate limiter, the refresh token in `localStorage`, one trial per business not
  enforced, the drift guard's blind spots) are closed.
- **Non-goals**: no new product surface (no owner console, no demo seed); no
  change to prices; no change to the agent's behaviour on a plan that allows it.

## Classification
- Type: feature (enforcement) + hardening (security) + tooling.
- Risk: **high** — the enforcement touches the agent path, the auth routes and
  the database's security posture. Money is not touched (no payment/refund code
  changes), but stock and the customer-facing reply path are.
- Complexity: 6 steps, each a small commit with its own tests. Budget: one
  session, one gate run per step boundary.

## Assumptions (confidence in brackets)
- A1 The pilot tenants run on the **trial** plan: WhatsApp only, L2, 1 seat, no
  API access. So enforcing the matrix must not break the trial loop. [0.9]
- A2 "One trial per business" is best approximated by **one account per WhatsApp
  number** (`tenants.owner_phone`): the number is the business identity in this
  product (Coexistence connects the existing number). [0.75]
- A3 The dashboard calls the API on its own origin (Next rewrite), so an
  httpOnly cookie works without CORS changes. [0.9 — checked in `api.ts`]
- A4 Postgres 16 is the deployed version, so `BYPASSRLS` and `SECURITY DEFINER`
  are available. [0.9 — compose image]
- A5 The agent-level cell must **cap**, not deny: a Starter tenant that asks
  for L2 keeps answering (L1); it is not silenced. [0.85]

## Derivation
The matrix exists to make a promise the code can keep. A promise is kept when
the *same* function decides, and every caller asks that function. Therefore:

1. **One decision point.** `EntitlementEnforcer` maps (subscription state, plan
   row) → a `FeatureDecision(allowed, reason, plan, entitlements, feature)`.
   Every enforcement site calls it. No site re-derives "is Instagram in my
   plan".
2. **The plan state is the outer gate, the cell is the inner one.** A tenant
   whose subscription is expired/pending/cancelled is not entitled to *any*
   premium behaviour (the existing holding-reply gate, unchanged). Inside that,
   each cell decides its own feature.
3. **I3 is the limit on what may be denied.** The gate may deny agent LLM spend
   and premium API features. It may never gate inbound persistence, safety or
   fraud flags, the rider flow, the audit log, data export, or the holding
   reply. The channel cell therefore refuses *processing* of a channel the
   tenant did not buy, while the raw webhook is still recorded.
4. **Enforcement is observable.** Every denial is audited with the reason, so
   the owner can see why a thing did not happen.

## Options considered
- **A. Patch each call site with its own plan lookup** — rejected: five copies
  of the same mapping, five ways to drift, and the pricing page still needs a
  hand-written list.
- **B. One enforcer + call sites (chosen)** — one mapping, one testable
  decision, the pricing page's "enforced today" list derived from
  `ENFORCED_FEATURES` and checked by a test.
- **C. Enforce only in the agent path** (the cheapest) — rejected: it leaves
  API keys and seats unenforced, which is the promise the page makes.

## Steps (each a commit with tests)
1. `packages/core/billing/enforcer.py`: the enforcer + `ENFORCED_FEATURES`.
   Tests: unit tests for every cell, every plan, and the fail-closed behaviour
   for an unknown plan.
2. The agent-level ceiling: cap the effective autonomy, audit the cap.
   Test: a Starter tenant (L1) asking for L2 answers but cannot transact.
3. The channel cell: the Instagram webhook refuses to process a channel the
   plan does not include; the raw event is still recorded.
   Test: a trial tenant's Instagram webhook → no conversation, no agent, audit
   row; a professional tenant's → processed as before.
4. The `api_access` cell: creating an API key needs API access (402 otherwise).
   Test: trial tenant → 402 with the reason; professional → 201.
5. The `seats` cell: adding a user needs a free seat (402 otherwise).
   Test: trial tenant (1 seat, the owner) → 402; professional with 3 seats → ok.
6. The pricing page: move the four cells from "not enforced yet" to "enforced
   today", generated from the enforcer's list, and pin it with a test.
7. U81: one account per WhatsApp number (route check + unique index) and a
   re-runnable proof script.
8. U78: the rate limiter's counters move to Redis (shared, survives a restart),
   with the in-process counter as the fallback.
9. U71: the drift guard also compares server defaults, index sets, RLS
   enablement and triggers.
10. U79: the refresh token moves from `localStorage` to an httpOnly cookie
    (SameSite=Strict) and the API stops returning it in the body.
11. U77/U82: the database becomes the last line of defence — the API runs as
    `socialerp_app` (not the table owner) and the cross-tenant lookups move
    into `SECURITY DEFINER` functions owned by a narrow platform role.

## Blast radius and rollback
- Steps 1–6: the agent path, the auth routes, the pricing page. Rollback is
  one revert; the flag `BILLING_ENTITLEMENT_ENABLED` still governs the plan
  state gate, and the cell checks honour the same switch.
- Step 7: a new unique index → a rollback drops it.
- Steps 8–11: config/migration changes, each with a downgrade.

## Incentive review
- **Owner**: pays for a plan and expects the limits to be real; a plan that
  cannot refuse a second seat is worth less than the price.
- **Owner (abuse)**: buy one trial, run ten businesses. Answered by step 7
  (one account per WhatsApp number) plus the invite gate.
- **Customer**: a channel that is not in the plan must not silently drop their
  messages → the raw event is recorded and the owner is alerted.
- **Fraudster**: probe which plan cells are enforced → the denials are
  uniform per feature and rate limited.
- **Us (platform)**: the trial stays the real product (A1) — the trial is
  WhatsApp + L2 + 1 seat, and the agent's order loop is untouched.

## Failure modes and detection
| Failure | Detection |
|---|---|
| The enforcer denies the trial loop | the sales-spine proof + the agent tests |
| A cell is enforced in one place only | the pricing-page test compares the list of enforced cells with the enforcer |
| A denial is silent | every denial writes an audit row (asserted in the tests) |
| An unknown plan crashes a request path | `entitlements_for` fails closed to Starter (unit test) |

## Acceptance criteria
- Every matrix cell has a test that shows it enforced (denied) for a plan
  without it and allowed for a plan with it.
- The pricing page's "enforced today" list is generated from the enforcer and
  the "not enforced yet" list is empty of enforced cells.
- `make check` green; the sales-spine proof still passes.
- U71/U78/U79/U81 closed with their own tests; U77/U82 either closed with a
  proof under a restricted role, or recorded honestly as still open.

## Docs to update
`docs/STATE.md`, `docs/MAP.md`, `docs/UNCERTAINTY.md` (U71–U82),
`docs/LEARNINGS.md`, `docs/adr/0024-enforcement-one-decision-point.md`,
`docs/failures/` if anything breaks the way the plan did not predict.
