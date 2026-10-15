# PLAN: Phase 6 — conversation pipeline wiring (agent on inbound WhatsApp)

Date: 2026-10-15 · Author: Chief Engineer · Status: **in progress**.
User decision (2026-10-15): wire via **outbox → worker** (not inline in the webhook).

## Goal and non-goals
- Goal: when a customer sends a WhatsApp message, the **agent runs it** and the outcome is
  persisted: the reply is sent (within the 24h window) at L1+/L2, or stored as a **draft** for
  owner approval at L0 (the default), and a human handoff is requested when policy says so.
- Goal: every agent action is **audited** (intent, confidence, decision, tools, order id).
- Goal: the webhook stays **fast**: it persists the inbound message and enqueues an
  `inbound_message` outbox event; the worker does the model/tool work (AGENTS.md §4/§13).
- Non-goal: the owner-facing draft inbox UI (Phase 8), media/voice handling, Instagram/TikTok,
  loading full message history into the prompt, per-tenant model routing.

## Classification (taxonomist)
- Type: new application service + a worker handler + a webhook outbox emission.
- Risk: **high** (customer-facing, money via the L2 tool, tenant isolation, 24h-window rules).
- Complexity: **high**.

## Derivation and invariants
- One agent run per inbound provider message: webhook dedupe already drops repeats; the outbox
  `idempotency_key = inbound_message:{provider_message_id}` makes a retried event a no-op.
- The agent **only reads** its inputs (business facts from `tenant.settings`, catalog from the
  DB). It **never** computes money/stock; order creation goes through the L2 tool → `OrderService`.
- Sending is guarded: free-form only inside the 24h window (the sender enforces this); an
  inbound message opens the window, so an immediate reply is normally allowed. If sending fails
  or the window is closed, the reply is stored as a **draft**, never lost.
- Below L1 (default L0) nothing is auto-sent: the reply is persisted with status `draft`.
- `requires_human` ⇒ the conversation moves to `handoff_requested`; the reply is a draft.

## Options considered
1. **Outbox → worker** (chosen): webhook returns immediately; model/tool work is retried and
   observable; matches the codebase's at-least-once design.
2. Inline in the webhook: rejected — slow webhook, Meta retries, model latency in the request path.
3. Direct background task in the API process: rejected — no retry/backoff, lost on restart.

## Steps (each a small commit)
1. Plan + ADR 0014.
2. `WhatsAppMessageStatus.DRAFT` (native_enum=False ⇒ no migration needed).
3. `packages/conversation/agent_service.py`: `ConversationAgentService` + `MessageSender`
   protocol + `WhatsAppSenderAdapter`; loads facts/catalog, builds the `Agent` (injected
   extractor/fake in tests), runs it, audits, persists sent/draft outbound + handoff.
4. Webhook: enqueue `inbound_message` outbox event with message/contact/conversation/phone.
5. Worker: `build_conversation_agent_service` factory + `handle_inbound_message` handler,
   registered on the relay.
6. Tests; docs; gates.

## Acceptance criteria
- L2 tenant (settings `agent_autonomy=L2`): inbound text → a real order is created, the reply
  is sent (fake sender records it), an outbound `sent` message is persisted, audit row exists.
- L0 tenant (default): same message → owner draft persisted, nothing sent, no order.
- Handoff intent → conversation `handoff_requested`, draft persisted, nothing sent.
- Webhook processing enqueues exactly one `inbound_message` outbox event per message.
- Worker handler runs the service (session per run) and is idempotent on replay.
- `ruff`, `mypy`, `pytest`, evals all green.

## Blast radius and rollback
- Touches `packages/integrations/meta/webhook.py` (one enqueue call), `apps/worker/main.py`
  (factory + handler + registration), `packages/core/models/whatsapp.py` (one enum member); new
  service module + tests. Rollback: revert the commit (no migration).

## Failure modes and detection
- Model down/slow → worker retries with backoff; the webhook already returned 200.
- Window closed / send fails → store a draft (never drop); test.
- Cross-tenant → catalog + order tool are tenant-scoped; RLS; test.
- Double reply on outbox replay → idempotency key on the event; test.
