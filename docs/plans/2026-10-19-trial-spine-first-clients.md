# PLAN: Trial spine + gate truth + first-client readiness (2026-10-19)

> Written by the Chief Engineer. Decision inputs: taxonomist (classification),
> decision-scientist (7 candidates scored, consensus C8), incentives (trial abuse
> review). Supersedes the assumption that only "Phase 10 remains".

## Goal and non-goals

**Goal**: repair the gate to real green, ship the minimum trial→subscription spine
(billing/entitlement + tenancy wiring), expose a sellable client-facing surface,
and produce the business/audience/marketing plan — so the human can recruit first
clients on a 14-day trial and convert them to subscriptions.

**Non-goals (deferred with written acceptance)**:
- Phase 10 slices C (money-path residuals), E (TikTok), F (load/fairness), full G.
- Stripe integration (no keys, no charge path — manual plan changes only).
- Full dashboard UI (only landing/pricing/signup/login/trial-banner pages).
- Embedded Signup self-serve onboarding (white-glove for first cohort).
- **Day-7/day-11 value/proof card** (the conversion machine, skeptic G9): the
  owner-console summary that shows orders handled, KES value and response time.
  Named follow-up milestone **M3 (pre-pilot-week-4)** — the trial can launch
  without it (manual value review), but not scale. Added so it cannot be lost.

## Classification
- Type: engineering completion + business documentation (mixed).
- Risk: **HIGH** (touches billing = money-adjacent protected zone, tenancy =
  tenant-isolation, CI gate definitions) — every protected edit needs human
  approval *before* the edit (CONSTITUTION §5).
- Complexity: HIGH (multi-milestone); mitigated by small per-commit scope.

## Assumptions with confidence (copied to UNCERTAINTY.md as U58–U61)
| # | Assumption | Conf | Verify |
|---|---|---|---|
| U58 | Phase 9 commits + dirty tree have never passed a full solo `make check` (STATE.md's 764-green is stale at `e7170d2`) | 0.95 | Measured 2026-10-19: 34 ruff errors, contract 10/11 failed, subagent run 788/15/22 |
| U59 | Trial spine (B1 + D-min) ≈ 7–10 dev days; gate repair ≈ 1–2 days | 0.55 | Calibration log in LEARNINGS.md after each milestone; experiment E1. **Note: below the 0.6 §4 threshold by design** — it is a schedule estimate, not a money/security/privacy assumption; the stop-rule therefore triggers a review (not a halt) at M0.5 if measured actuals diverge |
| U60 | Trial = 14 days from channel connect, Professional entitlement, no grace, one human-approved 7-day extension | 0.7 | Human decision this session |
| U61 | The phone test can be run against Meta + Daraja **sandbox** (no real money) with the human's number | 0.6 | Human decision this session (scope question) |

## Derivation (first principles)
1. **Revenue requires three things that don't exist yet**: (a) a working trial
   clock + entitlement gate — impossible without billing code; (b) a surface a
   prospect can sign up on; (c) the external Meta/Safaricom approvals, whose lead
   time is outside our control → applications start immediately, in parallel.
2. **Selling on a red gate is dishonest and risky**: the gate is the only executable
   proof of tenant isolation and money safety; without it we have no evidence
   — the constitution forbids claiming success without evidence.
3. **Invariants that must hold throughout**:
   - I1: cross-tenant access fails closed, at every commit (RLS + tests).
   - I2: the LLM never does money/stock arithmetic; a denied tenant causes
     **zero** LLM calls (deterministic holding reply only).
   - I3: entitlement gates *agent spend only* — never data export, safety
     flags, fraud flags, rider flow, or audit logging.
   - I4: trial expiry never leaves a customer in silence (holding reply + handoff)
     and never auto-charges (explicit accept → M-Pesa STK).
   - I5: `make check` output pasted here is green **at a named SHA with a clean
     tree**, or the milestone is not done.
4. **Why billing before hardening C/E/F**: value proof (order → STK → paid) already
   works and is tested; the missing piece for trial→subscription is billing itself.

## Options considered (decision-scientist scoring, V1 time-revenue / V2 safety /
V3 demo-ability / V4 reversibility; Σ of +1/0/−1)
- C1 Full Phase 10 A→G first: safest, slowest → Σ+1. Rejected: months to revenue.
- C2 Full billing incl. Stripe webhook then rest → Σ+1. Rejected: Stripe untestable
  without keys; adds unexercised money code.
- C3 D-min + B only, no UI → Σ+2. Rejected: nothing for a prospect to see.
- C4 Sales surface first → Σ+2. Rejected: sells an entitlement system that
  doesn't exist yet.
- C5 B + D-min + parallel surface, G-lite → Σ+3.
- C6 Money residuals first → Σ+2. Rejected: protected-zone heavy, not user-visible.
- C7 D-min only, concierge → Σ+1. Rejected: skips tenant-isolation wiring.
- **C8 consensus (CHOSEN, Σ+4)**: M0 gate truth → M1 (B1 + D-min) → M1.5 sales
  surface (serial, not parallel — one developer) → M2 pre-go-live gate (G-lite).
  Money-path pulls C1/U25 only if human approves; otherwise runbook items.

## Steps (ordered; each a small commit with its tests)

### M0 — Gate truth + commit Slice A remainder (1–2 days)
1. **M0.0 Python version decision via ADR** (skeptic G8): the dirty tree already
   moves CI/Docker/lockfile to 3.13 while AGENTS.md/root pyproject say 3.12.
   Recommendation: complete the 3.13 move (Dockerfile, root pyproject, AGENTS.md,
   ADR) — a partial bump is a broken image. Human picks 3.12 (revert) or 3.13
   (complete); record the ADR before the lint sweep.
2. **M0.1 Makefile repair**: point `db-up/db-down/fake-meta/fake-daraja/clean` at
   root `docker-compose.yml`; `migrate/migrate-down/seed` at root `alembic.ini`
   (no `cd packages/core`). Test: shell-level smoke + a guard test asserting the
   Makefile references no deleted path.
3. **M0.2 Lint sweep (34 errors)**: fix contract-test imports (`hmac`,
   `hashlib`), onboarding/instagram/webhook/outbox/crm/property-test issues.
   Note: one fix target is `tests/property/*` — property tests are NOT protected;
   fixing imports/syntax in them is not weakening (no assertion changes).
4. **M0.3 Test-support repairs**: hoist `fake_meta_server` fixture to
   `tests/conftest.py` scope, add missing `app` schema for RLS property tests.
   **Onboarding-E2E 401s: the tests hardcode `Authorization: Bearer test-token`
   while `get_current_tenant_id` validates real JWTs.** Fix by minting real
   tokens via `create_access_token` (pattern already in
   `tests/crm/test_crm_api.py`) — **dependency override/stubbing is forbidden**
   (it would silently weaken an auth-adjacent test). Never weaken assertions.
5. **M0.4 Committed Slice A pieces**: `tests/architecture/test_alembic_single_tree.py`,
   fake-server pyproject packaging, contract replay tests; docs reconciliation
   (STATE/MAP/UNCERTAINTY U7/U8 rows, `.env.example` `TRACKING_TOKEN_SECRET`).
   ⚠ A5 CI scanners (gitleaks/pip-audit) = protected CI gate → **human approval
   before edit** (bundle with the go question; if not approved, defer).
6. **M0.5 CI truth check** (skeptic F1): inspect CI status for the Phase 9
   commits; if CI was red on main pushes, propose branch protection / required
   checks (⚠ CI gate = protected → approval). Then **solo `make check`** on a
   clean tree → paste real output (SHA + counts) into STATE.md.
   Commits: `infra: single compose tree + root alembic (U7/U8)` then
   `gate: repair lint + test support, real-green measured`.

### M1 — Trial spine (7–10 dev days)
7. **M1.1 B1 tenancy middleware** (U23): `TenantContextMiddleware` wired in
   `apps/api/main.py` with parameterized `set_config('app.current_tenant_id')`,
   public allow-list (webhooks, callbacks, tracking), feature flag
   `TENANCY_MIDDLEWARE_ENABLED` with a **production startup guard** (settings
   validation refuses to boot prod with the flag off — skeptic G10). Tests:
   cross-tenant API reads 404/empty under RLS, allow-listed routes still work.
   ⚠ touches security/tenant-isolation tests (protected) → approval before edit.
8. **M1.2 D1 billing domain + migration `0013_billing`**: `subscriptions`
   (tenant_id unique; plan `trial|starter|professional|enterprise`; status
   `active|past_due|cancelled|expired`; `period_end`; RLS) + `usage_counters`
   (tenant, metric, period, integer). Backfill existing tenants → `trial/active`
   so no pilot tenant is bricked. Dependency test: `packages.core.billing` must
   not import `packages.core.payments`. ⚠ billing = money-adjacent protected
   zone → approval before edit.
9. **M1.3 Trial start hook**: the subscription row is created at **channel
   connect** (onboarding step 2 event), not at registration, with
   `period_end = connect_time + 14d`; a tenant that signs up but never connects
   stays `pending` (no trial burned) — and the never-connected path expires to
   a dormant/cancelled state by rule, so a verified number is not permanently
   blocked. One rule, audit-logged (skeptic G5).
10. **M1.4 D2 entitlement gate**: an **entitlements-per-plan feature matrix**
    (plan × feature: channels, agent level, insights, API, seats) with a test
    over all plans × features — NOT an entitled/denied boolean (skeptic G2).
    Enumerate **all LLM-gateway call sites** and gate agent spend at every one,
    including the **owner WhatsApp console** (Phase 8c), not only the customer
    path (skeptic G1) — but never gate data access, safety/fraud flags, rider
    flow, or audit logging (I3). Denied/capped → persist inbound, deterministic
    holding reply, handoff, audit `billing.entitlement_denied`, `record_usage` —
    **zero LLM calls** (test asserts ScriptedGateway call list empty).
    **Cap enforcement is part of this step**: when the daily message or LLM
    spend cap is hit, deny exactly like an expired plan, with a boundary test
    (skeptic G3). Premium API endpoints → 402. Daily cron flips
    `trial → expired` past `period_end` (lazy check also on read). Owner alert
    at T−3d and at expiry.
11. **M1.5 D4 billing API + providers**: `GET /api/v1/billing/subscription`,
    `GET /api/v1/billing/usage`, `POST /api/v1/billing/subscription` (owner-only,
    audited `apply_plan_change`); `NoopBillingProvider`/`ManualBillingProvider`
    mirroring `PaymentProvider`; **no Stripe webhook** (deferred). Pilot
    collection path is explicit: manual M-Pesa transfer + **founder confirms the
    plan change** (audited `apply_plan_change`), with a billing-ops runbook
    (skeptic G4). Tests: cross-tenant isolation, forged plan change rejected,
    usage increments once under webhook replay.
12. **M1.6 E1 vertical experiment**: register → invite-gated signup → connect →
    trial → clock-forward → denied inbound (0 gateway calls) → holding reply →
    manual plan change → L1 resumes. Record predicted vs actual in
    `docs/LEARNINGS.md`.

### M1.5 — Sales surface (2–3 days, serial)
13. Fix landing CTAs (no 404s); add `/pricing` (from PRICING_STRATEGY.md),
    **invite-code-gated** `/signup` (→ `POST /auth/register` → pending trial)
    (skeptic G6), `/login`, trial banner with days remaining; static
    Terms/Privacy/DPA pages (flag: Kenyan lawyer review); demo script + seeded
    demo tenant. pnpm lockfile + `lint-js` wired into CI is a separate opt-in
    commit (CI = protected → approval).

### M2 — Pre-go-live gate (1–2 days)
14. A6 backup/restore drill + runbook; G-lite (threat-model delta rows for
    billing/trial abuse, feature-flag inventory, `DARAJA_CALLBACK_URL`/
    `PUBLIC_BASE_URL` verified); timed 30-min onboarding dry-run on fakes;
    **sandbox smoke test with the human's phone** (Meta WhatsApp test number +
    Daraja sandbox STK — no real money, subject to the human's scope answer);
    skeptic sign-off.

## Blast radius and rollback
- M0: infra/test-only; rollback = `git revert` of 2 commits; dev DB rebuildable.
- M1: new tables + new gate in the worker path — highest blast radius. Rollback:
  `TENANCY_MIDDLEWARE_ENABLED=off` and entitlement gate behind
  `BILLING_ENTITLEMENT_ENABLED` flag (default ON in prod, OFF is an emergency
  escape); migration is additive (new tables only, backfill is idempotent) so
  downgrade drops only the two new tables.
- Every protected-zone edit ships as its own commit with the human's approval
  recorded in the commit body.

## Incentive review (summary; full analysis from @incentives)
Top abuses: free-LLM farming/trial stacking → spend caps + one trial per
verified WhatsApp number (concierge-verified for cohort 1); trial-clock gaming →
clock starts at channel connect, extensions audit-logged; fake manual payment
confirmation → human-verified during trial; trial bulk sends → plan-gated off +
zero-interference gate; customer ghosting at expiry → holding reply + handoff.
Dark patterns explicitly avoided: no silent auto-charge, no data hostage, no
degraded quality for trial replies, safety features never entitlement-gated.

## Failure modes and detection
| Failure | Detection |
|---|---|
| Gate stays red / regresses | M0.5 solo `make check` + CI; scribe records SHA |
| Entitlement gate kills an entitled tenant | matrix test (all plans × states) + alert on `billing.entitlement_denied` volume |
| LLM spend on denied tenant | ScriptedGateway call-count = 0 test; spend-cap metric |
| Cross-tenant leak via new middleware | RLS + middleware cross-tenant tests (protected, run in CI) |
| Trial never converts (value not shown) | trial metrics dashboard days 3/7/11/14; E2 timed demo |
| Meta/Safaricom approval delays | start applications now; white-glove fallback |
| Makefile/compose drift again | compose-singleton + Makefile guard tests |

## Acceptance criteria (measurable)
1. Clean tree at a named SHA: `make check` EXIT=0 with real pasted output
   (ruff 0, mypy success, pytest 0 fail / 0 **unexpected** skips, evals 8/8,
   wrong-price/stock 0).
2. `make db-up && make migrate && make seed` work from repo root on a fresh clone.
3. Invite-gated signup → channel connect creates the trial row (`period_end =
   connect+14d`); never-connected signup burns no trial; existing tenants
   backfilled entitled.
4. Expired **or cap-exceeded** tenant inbound → holding reply + handoff + audit,
   ScriptedGateway calls = 0; owner-console LLM spend also gated; entitled
   tenant unaffected; rider flow unaffected.
5. `POST /billing/subscription` owner-only + audited; per-plan feature matrix
   test (all plans × features) green; cross-tenant billing reads fail closed;
   billing↛payments dependency test green; no Stripe key required.
6. `/pricing`, `/signup`, `/login` load with zero 404 CTAs; signup requires an
   invite code and produces a pending (then active) trial.
7. Phone test: inbound WhatsApp (sandbox) → agent reply; sandbox STK callback →
   order paid — recorded transcript/log.
8. Skeptic sign-off + scribe updated STATE/MAP/UNCERTAINTY/LEARNINGS.

## Docs to update
`docs/STATE.md`, `docs/MAP.md`, `docs/UNCERTAINTY.md` (U58–U61 + status flips),
`docs/LEARNINGS.md` (calibration), `docs/BUSINESS_PLAN.md` (new, this session),
`docs/THREAT_MODEL.md` (billing/trial delta at M2), ADR for billing domain
(`0022-billing-entitlement.md`), `.env.example` (billing/tenancy flags).
