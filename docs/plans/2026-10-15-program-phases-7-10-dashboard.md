# PLAN: Program completion — green gate, Phases 7–10, owner dashboard

Date: 2026-10-15. Author: Chief Engineer. Classification (via @taxonomist):
type = mixed (refactor + multi-phase feature delivery + hardening); risk = HIGH
(money-adjacent protected zones, tenant isolation release blocker, Kenya DPA
privacy zone); complexity = HIGH — split into the separately-planned sub-efforts
below, each with tests, ADRs where needed, and a real `make check` report.

## Overall goal and non-goals

Goal: finish SocialERP to pilot readiness per AGENTS.md §16 — Phases 7–10 plus a
functional owner dashboard, on a tree where `make check` is green at every step.
Non-goals: real Meta/Daraja/production credentials, live customer data, actual
Stripe charges, non-Kenyan markets, quantum hardware, roleplay expertise.

## Slice 0 (precondition): restore the green gate

The tree is mid-refactor (aggregator provider + per-tenant provider selection):
`get_tenant_payment_provider` is referenced but undefined (ruff F821 ×2),
ruff reports 27 errors, and pytest collection aborts on the payments API test.

- Design: define `get_tenant_payment_provider(tenant_id, db)` in
  `apps/api/routes/payments.py`; it reads the tenant's `payment_providers`
  setting and builds the first enabled provider (daraja → aggregator → manual),
  falling back to direct Daraja (historical default); result cached per
  (tenant_id, provider list). Restore lazy `get_settings()` usage (no module-level
  import-time env dependency). Add unit tests for the aggregator provider and
  the selection function.
- Protected zone: payment provider code — this slice finishes already-started
  reviewed work (the provider client was written in a prior session); no new
  money logic.
- Acceptance: `uv run ruff check .` 0 errors; `uv run mypy packages apps`
  clean; `uv run pytest` all green; `uv run python -m evals.runner` 8/8.
- Docs: STATE.md updated with real output.

## Phase 7 — Delivery (zones, fees, rider WhatsApp flow, pay-on-delivery, tracking)

- Plan: `docs/plans/2026-10-15-phase-7-delivery.md`.
- Domain: `DeliveryZone` (tenant, name, fee_cents), `Rider` (tenant, phone,
  name, active), `Delivery` (order, rider, zone, fee_cents, status, POD
  trigger). Delivery FSM: `assigned → picked_up → out_for_delivery → arrived →
  delivered | failed_delivery → (re)assigned`; mirrors order FSM transitions in
  ADR 0006. Fee arithmetic lives ONLY in deterministic service code (the agent
  may never quote an uncomputed fee — LLM grounding rule).
- Pay-on-delivery: rider marks `arrived` → agent triggers STK Push via the
  protected payment service (reuse `PaymentService.initiate_stk_payment`);
  callback → paid → rider marks delivered. Cash fallback only with rider
  confirmation + reconciliation flag.
- Rider WhatsApp flow: quick-reply keywords (picked up / arrived / delivered)
  handled in the inbound pipeline; photo/location proof stored as metadata.
- Tracking: public read-only page `/tracking/{order_id}` (no auth, no PII beyond
  order id; rider location only with tenant flag + customer consent).
- Fraud: risk flags (new/secluded address, repeated failed STK) attached to the
  delivery record and surfaced before dispatch.
- Acceptance: end-to-end tests with fakes (happy path + failed delivery +
  POD payment path); property tests for the delivery FSM; RLS on all new tables;
  cross-tenant tracking test fails closed.

## Phase 8 — CRM memory, segments, owner insights, owner WhatsApp console

- Plan: `docs/plans/2026-10-15-phase-8-crm.md`.
- `CustomerProfile` (phone identity across channels), immutable `CustomerEvent`
  log, versioned derived traits WITH stored evidence, segments, insights jobs
  (daily/weekly reports, anomaly detection). Owner WhatsApp console: owner
  intents (order counts, mark delivered, restock) routed through the same
  agent + policy engine; every action audited; refunds/discounts/price changes
  stay human-approved (protected zone).
- Privacy: consent records (source/time), opt-out, export, deletion; no inferred
  sensitive attributes; retention limits configurable (Kenya DPA readiness;
  lawyer/regulator items stay flagged in UNCERTAINTY.md).
- Acceptance: trait evidence traces in logs; consent/export/deletion tested;
  owner console actions audited; injection attempts ("ignore the rules") blocked;
  cross-tenant customer data never leaks.

## Phase 9 — Instagram channel + 30-minute instrumented onboarding wizard

- Plan: `docs/plans/2026-10-15-phase-9-instagram-onboarding.md`.
- Instagram via official Graph API: webhook receiver (signature verification,
  dedupe by provider message id), 24h window tracker per customer, private-reply
  limits, human handoff reuse. TikTok stays Phase 10/flagged.
- Onboarding wizard (dashboard + measured instrumentation): 6-step flow from
  SPEC §11; per-step timers; go-live in L0 shadow mode; fallback tracks for
  missing Meta assets / Safaricom Till.
- Acceptance: scripted onboarding run < 30 min on sandbox fixtures; window
  rules tested; wizard step timings recorded.

## Phase 10 — TikTok assisted mode, billing, hardening, load, security review

- Plan: `docs/plans/2026-10-15-phase-10-tiktok-billing-hardening.md`.
- TikTok: feature-flagged assisted mode only (draft replies for owner, move to
  WhatsApp); no direct sends; Kenya availability unverified (UNCERTAINTY).
- Billing: SaaS subscription — the ONLY monetization. Stripe placeholders
  already in settings; implement billing service (tenant subscription record,
  plan tiers Starter/Professional/Enterprise), webhook handling for Stripe
  events, subscription-gated feature flags. Money-adjacent: human-approved design
  review before any real charge; no customer-payment co-mingling (SPEC §3).
- Hardening: dependency scanning, secret scanning in CI, rate limits, PII
  redaction verification, RLS audit of every tenant table, tenancy middleware
  wired into main.py, docker-compose reconciliation, load test (100 tenants /
  50 conv/s burst target), security review vs THREAT_MODEL.md.
- Acceptance: load test report; security review checklist; billing creates a
  subscription without charging (sandbox mode); gates green.

## Owner dashboard

- Real mobile-first dashboard over the existing APIs: login (JWT), inbox
  conversations, orders board (state transitions), catalog/stock management,
  payments (initiate STK, refund approval), delivery board, CRM insights,
  onboarding wizard UI, settings (rules, providers, autonomy level).
- Acceptance: key owner flows usable end-to-end against the test DB with fakes;
  TypeScript type-check passes; mobile viewport layouts.

## Program-wide invariants (conservation laws)

1. The LLM never computes money/stock/delivery arithmetic; deterministic code
   does, and every number a customer sees traces to the ledger or catalog.
2. Every tenant table has RLS; every query carries tenant_id; cross-tenant
   tests fail closed (release blocker).
3. Idempotency on every inbound webhook, payment request/callback and outbound
   message; zero double-credit under replayed callbacks.
4. `make check` (ruff + mypy --strict + pytest + evals) green at every commit;
   real output reported; no test/gate weakened to pass (STOP rule).
5. Evals wrong-price/stock rate stays 0; new capabilities (delivery quotes,
   owner console actions) get labeled eval cases.

## Failure modes and detection (program level)

- Cross-tenant escape via new tables → RLS tests + dependency-rule tests.
- Payment pathologies on POD → reuse shared `_apply_result`, dedupe by
  CheckoutRequestId, status-query fallback; tests for replay/late/partial.
- Prompt injection via customer DMs and owner console → injection detector +
  policy escalation; eval cases.
- Fee hallucination → eval invariant + service-layer computation only.
- Self-weakening under schedule pressure → STOP protocol, failure reports in
  /docs/failures/, skeptic review before each phase sign-off.

## Program sequence

1. Slice 0 green gate (this session) → commit.
2. Phase 7 plan → implement → commit.
3. Phase 8 plan → implemented → commit.
4. Phase 9 plan → implemented → commit.
5. Phase 10 plan → implemented → commit.
6. Dashboard (API-aligned slices interleaved after Phase 7 APIs exist).
7. Final: full `make check`, skeptic program review, scribe updates
   STATE/MAP/UNCERTAINTY, pilot readiness checklist.
