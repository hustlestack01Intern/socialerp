# PLAN: M2 - WhatsApp Channel (Webhooks, Signature Verification, 24h Window, Inbox, Human Handoff)

## Goal and Non-Goals
**Goal:** Implement complete WhatsApp Cloud API integration using fake Meta server (M0) for development/testing, with production-ready code that can switch to Meta sandbox via configuration. Includes webhook receiver, signature verification, 24h customer service window tracking, rate limiting, normalized message events, unified inbox, and human handoff workflow.

**Non-Goals:**
- No Instagram/TikTok channels (M9+)
- No agent L0/L1 logic (M5)
- No order/payment flows (M3/M4)
- No delivery/rider flows (M7)
- No owner dashboard UI

---

## Classification
- **Type:** Feature
- **Risk:** High (security-critical: signature verification, 24h window compliance)
- **Complexity:** High (6+ integrated components, stateful per-customer windows)

---

## Assumptions with Confidence

| Assumption | Confidence | Why | How to Verify |
|------------|------------|-----|---------------|
| Fake Meta server (M0) correctly simulates WhatsApp Cloud API webhooks | 0.85 | Built to spec; needs parity testing | Record real sandbox webhooks; compare shapes |
| `X-Hub-Signature-256` HMAC-SHA256 verification works with Meta's algorithm | 0.9 | Standard Meta webhook auth | Unit tests with known vectors; fake server challenge |
| 24h window tracked per `tenant_id` + `customer_phone` survives restarts | 0.9 | Stored in DB with timestamp; TTL cleanup | Integration tests with time manipulation |
| Rate limiting at 20 msg/s per tenant (Coexistence limit) | 0.9 | Meta documented limit | Load test; fake server enforcement |
| Message dedupe by `message_id` (WhatsApp's `id` field) works | 0.95 | Standard idempotency pattern | Property tests with duplicate webhooks |
| Cross-tenant isolation enforced by RLS on all WhatsApp tables | 0.95 | RLS from M1 extends to new tables | Isolation tests with 2+ tenants |

---

## Derivation: First-Principles Reasoning and Invariants

### Invariant 1: Webhook Security is Non-Negotiable
- **Every** inbound webhook **must** verify `X-Hub-Signature-256` using tenant's `meta_app_secret`
- Invalid signature → **401 immediately**, no processing, audit logged
- Signature verification uses **constant-time comparison** (timing attack prevention)

### Invariant 2: 24h Customer Service Window is Absolute
- Window starts on **first customer message** (inbound)
- Free-form messages allowed **only** within 24h of last customer message
- Outside window: **only template messages** allowed (enforced at send time)
- Window tracked per `(tenant_id, customer_phone)` in database with `last_customer_message_at`
- Clock skew handled: use **server time** for window calculation; log discrepancies

### Invariant 3: Idempotency via Provider Message ID
- WhatsApp provides `id` field on each message → use as **idempotency key**
- Dedupe table: `(tenant_id, provider_message_id)` unique constraint
- Duplicate webhooks → return 200 OK, no re-processing, audit logged

### Invariant 4: Tenant Isolation at Every Layer
- All WhatsApp tables have `tenant_id` + RLS policies
- Webhook verification uses **tenant's** `meta_app_secret`
- Inbox queries filtered by RLS automatically
- Cross-tenant message access → **0 rows returned**

### Invariant 5: Outbox Pattern for All Side Effects
- Inbound message → `MessageReceived` domain event → outbox
- Outbound send → `MessageSent` domain event → outbox
- Human handoff → `HandoffRequested` domain event → outbox
- All events processed by worker with at-least-once delivery

---

## Options Considered (3+ candidates)

### Option A: Full WhatsApp Integration with Fake Server First (Chosen)
- Use M0 fake Meta server for all development/testing
- Production switches to real Meta via config (`META_BASE_URL`)
- Single codebase, environment-driven
- Fake server enforces same validation as real API

### Option B: Direct Sandbox Integration from Start
- Develop against Meta sandbox directly
- **Rejected**: Requires network, credentials, rate limits; flaky CI; no replay

### Option C: Third-party WhatsApp Library (e.g., whatsapp-cloud-api)
- Use existing Python wrapper
- **Rejected**: Adds dependency; less control over validation; security audit needed

### Option D: Separate Service for WhatsApp
- Extract WhatsApp handling to separate microservice
- **Rejected**: Premature splitting; modular monolith per SPEC §4

---

## Steps (ordered, each a small commit with tests)

### Step M2.1: WhatsApp Domain Models & Events
- `packages/core/domain/whatsapp.py`: `WhatsAppMessage`, `WhatsAppContact`, `WhatsAppTemplate`, `MessageDirection`, `MessageType`
- `packages/core/events/whatsapp.py`: `MessageReceived`, `MessageSent`, `MessageDelivered`, `MessageRead`, `HandoffRequested`, `TemplateSent`
- Database tables: `whatsapp_messages`, `whatsapp_conversations`, `whatsapp_contacts`, `whatsapp_templates`, `whatsapp_dedupe`
- RLS policies on all tables

**Tests:** Property tests for message normalization; RLS isolation tests

### Step M2.2: Webhook Receiver + Signature Verification
- `packages/integrations/meta/webhook.py`: FastAPI endpoint `/api/v1/webhooks/whatsapp`
- Signature verification: `verify_meta_signature(payload, signature, app_secret)`
- Dedupe: check `whatsapp_dedupe` table before processing
- Normalize inbound to `MessageReceived` event
- Return 200 OK within 100ms (Meta requirement)

**Tests:** Unit tests with valid/invalid signatures; dedupe tests; timing attack tests

### Step M2.3: 24h Window Tracker + Rate Limiter
- `packages/integrations/meta/window_tracker.py`: `WindowTracker` class
- `get_window_state(tenant_id, phone)` → `OPEN` / `CLOSED` / `UNKNOWN`
- `update_on_customer_message(tenant_id, phone)` → sets `last_customer_message_at`
- `can_send_freeform(tenant_id, phone)` → boolean
- Rate limiter: token bucket at 20 req/s per tenant (Redis or in-memory)

**Tests:** Property tests for window boundaries; rate limit burst tests; clock skew tests

### Step M2.4: Outbound Sender + Template Enforcement
- `packages/integrations/meta/sender.py`: `WhatsAppSender` class
- `send_text(tenant_id, to, body)` → checks window, calls Meta `/messages`
- `send_template(tenant_id, to, template_name, language, components)` → allowed outside window
- Uses tenant's `meta_access_token` and `phone_number_id`
- Returns `message_id` for tracking

**Tests:** Window enforcement tests; template vs freeform tests; rate limit tests

### Step M2.5: Unified Inbox + Conversation State
- `packages/conversation/`: `Conversation`, `Message`, `ConversationState` models
- Conversation threading by `customer_phone` + `tenant_id`
- State machine: `open` → `awaiting_response` → `handoff` → `closed`
- Human handoff flag + escalation rules
- API: `GET /api/v1/conversations`, `GET /api/v1/conversations/{id}/messages`

**Tests:** Conversation state transitions; threading tests; RLS isolation

### Step M2.6: Human Handoff Workflow
- `packages/conversation/handoff.py`: `HandoffManager`
- Triggers: low confidence, high-risk intent, owner command, customer request
- Notifies owner via WhatsApp (template) + in-app
- SLA: owner response within 5 min → escalation
- Customer sees "Connecting you to a human..." message

**Tests:** Handoff trigger tests; notification tests; SLA tests

### Step M2.7: Integration with Fake Meta Server
- Configure fake Meta server URL from settings (`FAKE_META_BASE_URL`)
- End-to-end tests: inbound → process → outbound
- Verify fake server receives/sends correct payloads
- Record/replay for regression testing

**Tests:** E2E flow tests; record/replay tests; fake server contract tests

### Step M2.8: Documentation & ADRs
- ADR: WhatsApp webhook signature verification design
- ADR: 24h window tracking implementation
- Update MAP.md, STATE.md, UNCERTAINTY.md
- API docs for webhook endpoints

---

## Blast Radius and Rollback
- **Blast radius:** WhatsApp integration tables, webhook endpoints, conversation module. No existing M0/M1 code broken.
- **Rollback:** `alembic downgrade` for WhatsApp tables; git revert for code; feature flag `FEATURE_WHATSAPP_ENABLED=false`
- **Verification:** Each step's tests pass; M2 acceptance criteria met before next step

---

## Incentive Review

| Stakeholder | Gains | Abuse Risk | Mitigation |
|-------------|-------|------------|------------|
| **Shop owner** | 24/7 inbound orders, centralized inbox | Spam messages | Rate limiting; template enforcement |
| **Customer** | Familiar WhatsApp UX | Spam to shop | 24h window; opt-in for marketing |
| **Meta** | Platform compliance | Policy violations | Signature verification; window enforcement |
| **Attacker** | Forge webhooks, bypass window | Data injection, spam | HMAC verification; dedupe; rate limits |
| **Engineering** | Testable with fake server | Skipping signature check | CI enforces `make check` |

---

## Failure Modes and Detection

| Failure Mode | Detection | Response |
|--------------|-----------|----------|
| Invalid signature accepted | Unit test with forged payload | Reject → 401; alert |
| 24h window miscalculated | Property test with time boundaries | Log discrepancy; fallback to template |
| Rate limit exceeded | Fake server returns 429 | Backoff + retry with jitter |
| Dedupe fails (duplicate processed) | Unique constraint violation | Idempotent handler; log duplicate |
| Cross-tenant message leak | RLS isolation test | RLS blocks → 0 rows |
| Fake server drifts from real API | Contract test on sandbox | Update fake; alert team |

---

## Acceptance Criteria (Measurable)

1. **Webhook Receiver**: `POST /webhook` returns 200 for valid signature, 401 for invalid, 400 for malformed JSON
2. **Signature Verification**: All 5 test vectors pass (valid, wrong secret, wrong payload, missing prefix, empty)
3. **Dedupe**: Same `provider_message_id` sent 10x → processed once, audit logged 10x
3. **24h Window**: 
   - Customer msg at T=0 → free-form allowed until T+24h
   - Free-form at T+24h+1s → rejected (template required)
   - Customer msg at T+25h → window resets, free-form allowed
4. **Rate Limit**: 21st request in 1s → 429 with `Retry-After` header
5. **Outbound Send**: 
   - Free-form within window → success
   - Free-form outside window → 400 error, template required
   - Template outside window → success
6. **Inbox API**: 
   - `GET /conversations` returns only tenant's conversations
   - `GET /conversations/{id}/messages` returns threaded messages
   - Human handoff flag visible
7. **Human Handoff**: 
   - Triggered by `/handoff` command or low confidence
   - Owner notified via WhatsApp template
   - Customer sees handoff message
8. **Cross-Tenant Isolation**: All RLS tests pass (0 rows for other tenant)
9. **Fake Server E2E**: Inbound → process → outbound completes with fake server
10. **CI**: `make check` passes including all M2 tests

---

## Docs to Update
- `/docs/STATE.md` — M2 done, next = M3
- `/docs/MAP.md` — Add `packages/integrations/meta`, `packages/conversation`
- `/docs/UNCERTAINTY.md` — M2 assumptions
- `/docs/adr/0007-whatsapp-webhook-design.md`
- `/docs/adr/0008-24h-window-tracking.md`
- `/docs/adr/0009-conversation-handoff-design.md`

---

## Open Questions for Human (max 5)

1. **Window Persistence**: Store `last_customer_message_at` in `whatsapp_contacts` table or separate `whatsapp_windows` table? (Default: `whatsapp_contacts`)
2. **Template Management**: Who creates/manages templates — owner via dashboard, or via API? (Default: API + owner console in M8)
3. **Media Messages**: Support images/documents in M2 or defer? (Default: M2 — store media URLs, no processing)
4. **Group Chats**: WhatsApp group chats don't sync in Coexistence (SPEC §3). Ignore or log? (Default: log + ignore)
5. **Business-Initiated Messages**: Outside 24h window, only templates allowed. How to handle "proactive" messages? (Default: template-only; owner creates templates via console)

---

**Ready for your "go" to begin M2 implementation.**