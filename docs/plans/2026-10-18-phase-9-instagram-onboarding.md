# PLAN: Phase 9 — Instagram channel + 30-minute instrumented onboarding wizard
Date: 2026-10-18. Author: Planning Scribe (Phase 9). Classification (@taxonomist):
- type = feature delivery (channels + onboarding instrumentation)
- risk = MEDIUM–HIGH (channel compliance: Instagram Messaging API rules; onboarding affects time-to-first-order)
- complexity = MEDIUM (extends existing channel abstractions, adds wizard state + timers)

## 1) Goal and non-goals
Goal:
- Add Instagram as a second channel behind the same normalized-message abstraction as WhatsApp. Reuse the outbox→worker→agent pipeline. Enforce Instagram Messaging API rules (Graph API, Business/Creator connected to FB Page, user-initiated conversations, 24h reply window, comment-to-DM “private reply” is time-limited and rate-limited). (AGENTS.md §3)
- Implement the 30-minute instrumented onboarding wizard per AGENTS.md §11: 6 steps with per-step timers, fallback tracks, zero-interference invariant, and instrumented metrics (time to complete each step and total). Track against north-star “time to first order < 48h”.

Non-goals:
- TikTok (Phase 10, flagged). No owner dashboard UI (separate). No LLM arithmetic. No changes to payments/refunds/stock mutations beyond what onboarding configures. No real customer data in tests/dev.

One-paragraph summary: Extend the Meta channel surface (`packages/integrations/meta/`, webhook→normalizer, sender, window_tracker) to Instagram using the same normalized event shape and pipeline, add comment-to-DM eligibility tracking with strict enforcement, add fake Meta Instagram shapes for replay, and add an instrumented onboarding wizard (DB-backed progress + timers, fallback tracks, zero-interference) to hit the 30-minute target.

## 2) Invariants
1. **Same pipeline, not a second pipeline.** Instagram webhooks produce normalized events that flow through the same outbox→worker→agent pipeline as WhatsApp (reuse `MessageReceived` family, conversation service, agent). (mirrors ADR 0010/0014)
2. **Tenant isolation (fail-closed).** All Instagram tables/queries are tenant-scoped with RLS. Cross-tenant Instagram access must fail-closed.
3. **Signature verification.** Every Instagram webhook verifies HMAC-SHA256 (app secret) before processing (same verification approach as WhatsApp). Invalid → 401, no processing, audit.
4. **User-initiated + 24h window.** Instagram is user-initiated; enforce 24h reply window semantics. Track per (tenant_id, contact_identity) and block out-of-window free-form sends with explicit error (templates allowed per channel policy). Record assumptions with confidence.
5. **Comment-to-DM enforcement.** “Private reply” eligibility is time-limited and rate-limited. Persist eligibility (tenant/customer/thread) with expiry; a single reply window/attempt constraint is enforced; eligibility consumed on successful reply or expires. Tests prove enforcement.
6. **Dedupe by provider message id.** Idempotency via provider message id (Instagram Graph message id). Duplicate webhooks return 200 and do not reprocess.
7. **Feature flag per-tenant.** Instagram channel behind per-tenant flag; disabled => webhooks rejected/ignored and sender no-ops with clear reason.
8. **Zero-interference.** No customer messaging until owner approves launch message. Wizard does not send messages to existing customers; no chat history alteration. (AGENTS.md §11)
9. **Determinism for onboarding timing.** Timers are monotonic (start/stop, per-step) stored in DB; reporting is deterministic from stored events.
10. **Fallback tracks non-blocking.** Missing verified Meta assets or Safaricom Till => run in “manual payment confirmation” mode with async guidance, no block on go-live. (AGENTS.md §11)

## 3) Derivation (first principles)
- **Reuse over duplicate.** WhatsApp abstraction in `packages/integrations/meta/webhook.py` (normalize + parse + dedupe) and `sender.py` (window enforcement + template policy) separates transport from normalized events. Instagram differs in webhook shape/entry points (DMs) and has comment-to-DM semantics; both map to same normalized `MessageReceived` fields and direction. So extend normalizer/parser per platform (shared pipeline).
- **Window semantics (Instagram).** AGENTS.md states 24h reply window; treat same class of constraint as WhatsApp but persist separately (different channel). Record confidence on edge cases (IG-specific policy nuances) in assumptions.
- **Comment-to-DM.** Private reply is time-limited and rate-limited — model as an eligibility record (expires_at, remaining/consumed, source: comment_id, media_id, thread) tied to tenant+customer/channel. Enforcement is at send time (and eligibility checked before reply) to avoid violating limits.
- **Onboarding state.** 6 steps with times: (1) business profile 3m, (2) connect WhatsApp + Instagram via OAuth/Embedded path 8m, (3) catalog 8m, (4) payments 5m, (5) rules/tone 4m, (6) go-live shadow test 2m = 30m. Need per-step `started_at`, `completed_at`, `duration_ms`, `status`, `notes/failures` and wizard `state` (NOT_STARTED/IN_PROGRESS/COMPLETED/FAILED/ABORTED). Choose **DB table** (`tenant_onboarding_wizard` or `onboarding_progress`) over session/localStorage: persists across devices/restarts, supports audit, testable, RLS/tenant-scoped. Simpler baseline than distributed session store; no client-side trust. (reject localStorage for server-instrumented metric integrity)

## 4) Options (≥3)
1. **Extend existing Meta integration (shared normalizer + channel discriminator) — chosen baseline.** Detect channel from webhook payload (`object`/entry) (WhatsApp vs Instagram) in `meta/webhook.py`, branch to Instagram normalizer/parser. Reuse `window_tracker` (adapted per channel identity), sender logic via channel-aware policy. Pros: unified pipeline, less duplication, easy to test against fakes. Cons: meta package becomes cross-channel (acceptable boundary).
2. **Separate `packages/integrations/instagram/` duplicating logic.** Pros: clean separation. Cons: high duplication, risk of pipeline drift vs WhatsApp; harder to keep normalization consistent (ADR 0010 pattern). Higher complexity.
3. **Model Instagram as different channel type in conversation, keep webhook separate but emit same events.** Hybrid. Pros: flexible. Cons: needs clear interface. Baseline (1) is simplest + matches existing code.

Onboarding state store:
1. **DB table (chosen).** `onboarding_wizard_progress` (tenant_id PK or composite, state, current_step, timers JSON, flags, started_at/completed_at). RLS tenant-scoped. Auditable.
2. **In-memory + API session.** Lost on restart, hard to measure across devices.
3. **Redis session.** adds infra dependency; overkill for wizard.

Chosen: (1) for both.

## 5) Real modules/functions to extend (from tree read)
- `packages/integrations/meta/webhook.py` — webhook receiver, `parse_whatsapp_webhook`, `normalize_message`, signature path. Extend to parse/normalize Instagram DM webhooks; extract tenant via metadata/phone_number_id equivalent or mapping; same HMAC verification path.
- `packages/integrations/meta/sender.py` — `WhatsAppSender` enforces 24h window via `limits`/`window_tracker`, template enforcement. Create/extend channel-aware sender behavior for Instagram (respect user-initiated + 24h window; enforce comment-to-DM eligibility). Keep protocol compatible with pipeline.
- `packages/integrations/meta/window_tracker.py` — `WindowTracker`, `WindowState`, cache + DB persistence. Adapt identity (Instagram thread/contact handle/id) and semantics; record assumptions.
- `packages/integrations/fake_meta/main.py` — fake Meta server (WhatsApp shapes). Extend to emit Instagram webhook shapes (DMs, message types), record/replay support, and signature verification consistent with tests. Also note `meta/fakes/main.py` exists as alternative location (read both) — use existing fake_meta as canonical for replay/CI per §14.
- `packages/core/events/whatsapp.py` — events. Reuse for Instagram-normalized inbound (channel tag if needed, but normalized shape shared). Pipeline consumes same types.
- `packages/conversation/` (per ADR 0014) — outbox→worker→agent pipeline. No changes to core flow; channel identity carried in normalized event.
- `apps/api/routes/auth.py` — registration/onboarding entry (step 1). May extend onboarding endpoints for wizard steps.
- Fakes: `packages/integrations/fake_meta/` and `packages/integrations/meta/fakes/` present; prefer `fake_meta` for recorded replay contract (AGENTS.md §14 requires fake Meta/Daraja for CI).

## 6) Ordered steps (small commits, tests first) — docs-only plan
1. **Instagram types + normalization (commit 1, tests first)** — add Instagram webhook parsing/normalization in `meta/webhook.py` (Instagram DM shapes), preserve existing WhatsApp behavior. Unit tests: valid/invalid signature (Instagram), dedupe by provider message id, basic normalize cases. Include time-limited comment-to-DM shape handling (eligibility fields).
2. **Window + eligibility tracking (commit 2, tests first)** — extend `window_tracker.py` for Instagram identity (tenant + thread/contact). Add comment-to-DM eligibility model (persisted: `instagram_dm_eligibility` or fields on contact/conversation) with expiry, time-limited single-reply constraint, rate-limit counters. Enforce at send time. Tests: eligibility consumed on send, expires, rate-limited, cannot reuse after consumption.
3. **Sender + policy (commit 3, tests first)** — extend `sender.py`/policy for Instagram (user-initiated, 24h window, template constraints). Per-tenant feature flag gates sends. Tests: window-violating send blocked; allowed within window; template path where applicable.
4. **Webhook integration + fake server (commit 4, tests first)** — wire Instagram path in webhook, add Instagram shapes to `fake_meta/main.py` (+record/replay), verify signature paths. Integration tests: inbound Instagram → normalized event → outbox. Contract tests against fake.
5. **Instagram channel enablement + isolation (commit 5, tests first)** — feature flag per tenant, RLS on new tables (eligibility), cross-tenant isolation test, dedupe test. 
6. **Onboarding wizard schema + timers (commit 6, tests first)** — DB model `onboarding_wizard_progress` (tenant_id PK/composite, current_step, state, step_timings JSON, flags, timestamps). Service with start/stop step, compute totals, enforce zero-interference (no customer sends). Fallback tracks: record missing assets/Till state. Tests: timers monotonic, < 30m path, fallback doesn’t block go-live.
7. **API surface for wizard (commit 7, tests first)** — endpoints to get/update progress, start/complete step, record fallback, mark launch approved (enables channel sends). AuthZ owner/staff per tenant. Tests: authZ, zero-interference invariant (cannot send to customers until launch approved).
8. **E2E + eval hooks (commit 8, tests first)** — scripted onboarding run on fakes < 30 min; Instagram E2E (inbound→pipeline) with fakes. Update docs (STATE/MAP/UNCERTAINTY/ADR). ADR for Instagram channel semantics + comment-to-DM + onboarding wizard instrumentation.

## 7) Blast radius and rollback
Blast radius: `packages/integrations/meta/webhook.py`, `sender.py`, `window_tracker.py`, `packages/integrations/fake_meta/main.py`, new tables/service for eligibility + onboarding wizard, API routes for wizard. No changes to payments/refunds/stock ledger/order state machine/policy beyond config flags.
Rollback: revert commits; no destructive schema if we add nullable/isolated tables; feature flags off. Read-only pipeline extensions safe.

## 8) Incentive review
Actors: owner (time saved, faster go-live), customer (consent, no spam), staff, fraudster, Meta/regulator. 
- Owner: wants <30m onboarding and Instagram channel. Mitigate by timers + deterministic reporting.
- Customer: user-initiated + window enforcement + comment-to-DM limits protect against abuse; zero-interference prevents premature messaging.
- Regulator (Kenya DPA): consent unchanged, data minimization; no new sensitive attributes; auditability via timers/events.
- Fraudster: cannot forge webhooks (signature), cannot cross-tenant. 
Mitigations: allow-list fields, RLS, per-tenant flag, eligibility consumption, launch approval gate.

## 9) Failure modes and detection
| Failure | Detection | Response |
|---|---|---|
| Signature forgery | Invalid signature tests (Instagram) | 401, audit, no processing |
| Cross-tenant leakage | Isolation test (tenant B context) | Fail-closed (RLS + filters) |
| Comment-to-DM reused after expiry/consumption | Eligibility tests | Block send, audit |
| Window violation | Sender test | Block free-form, require template or handoff per policy |
| Dedupe missed | Duplicate webhook test | Return 200, no reprocess |
| Onboarding > 30m | Timer test | Record failure reason, don’t block async fallback but surface metric |
| Launch approved early | Zero-interference test | Cannot send to existing customers until approved (gate enforced) |

## 10) Acceptance criteria (measurable)
- [ ] `make check` passes (ruff, mypy --strict, pytest unit+integration+property, evals no regression). Real output reported.
- [ ] Instagram webhook signature: valid/invalid cases pass; timing-safe comparison.
- [ ] Instagram normalization + dedupe by provider message id; duplicate webhook returns 200, processed once.
- [ ] Comment-to-DM eligibility: time-limited, rate-limited, consumed on send, expires => blocked.
- [ ] 24h window enforced for Instagram (free-form blocked outside). Tests pass.
- [ ] Cross-tenant Instagram isolation test fails-closed.
- [ ] Fake Meta supports Instagram shapes + recorded replay (CI contract).
- [ ] Onboarding wizard: 6 steps instrumented with per-step/completed timers; scripted onboarding < 30 minutes on fakes.
- [ ] Fallback tracks: missing assets/Till => manual payment mode, async guidance, no block on go-live (verified by test).
- [ ] Zero-interference: no customer messaging until owner approves launch message (enforced by gate tests).
- [ ] ADR(s) added for Instagram semantics + comment-to-DM + onboarding instrumentation. Docs updated (MAP/STATE/UNCERTAINTY as needed).

## 11) Docs to update
- `docs/STATE.md` (Phase 9 slice)
- `docs/MAP.md` (new/updated modules: eligibility, onboarding wizard)
- `docs/UNCERTAINTY.md` (assumptions with confidence below)
- `docs/adr/XXXX-instagram-channel-and-onboarding.md` (new ADR covering Instagram semantics + wizard)

## 12) Assumptions with confidence (copy to UNCERTAINTY.md)
| Assumption | Confidence (0-1) | Evidence/way to reduce |
|---|---|---|
| Instagram DM webhooks map to same normalized message shape (reuse pipeline). Differences are entry points (DMs) and comment-to-DM. | 0.90 | Reuse of `MessageReceived` family (ADR 0014); test against fake shapes. |
| 24h reply window applies to Instagram DMs as stated; edge cases recorded. | 0.85 | AGENTS.md §3 states 24h; confirm against Graph API notes in tests/fakes. |
| Comment-to-DM private reply is time-limited and rate-limited per Meta limits; eligibility model matches enforcement points. | 0.80 | AGENTS.md notes limits; model as expiry + consumption + counters; tests enforce. |
| Wizard timers monotonic and server-controlled (DB) give accurate < 30m measurement on fakes. | 0.95 | Deterministic fakes, no wall-clock skew in tests. |
| DB-backed wizard progress is simplest baseline (tenant-scoped, auditable). | 0.95 | Matches existing multi-tenant model, no client trust. |
| Missing Meta assets/Safaricom Till => manual payment mode is non-blocking (AGENTS.md §11). | 0.90 | Wizard records fallback state and go-live allowed with manual mode. |
| Instagram channel behind per-tenant flag; disabled => no cross-channel leakage. | 0.95 | Flag checked on webhook receive + sender. |

(End of file - 270 lines)