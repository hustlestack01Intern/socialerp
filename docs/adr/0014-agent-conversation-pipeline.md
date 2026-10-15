# ADR 0014: Agent conversation pipeline — outbox→worker, drafts at L0

Date: 2026-10-15 · Status: accepted

## Context
The inbound WhatsApp pipeline persisted messages and conversation state but never ran the agent.
Phase 6 gives the agent a deterministic L2 order tool. We need the agent to actually run on
customer messages without slowing the webhook or losing work on model latency/failure.

## Decision
1. **Transport**: the webhook persists the inbound message (as today) then enqueues a single
   `inbound_message` outbox event. The **worker** relay runs the agent. The webhook never calls
   the model. Outbox `idempotency_key = inbound_message:{provider_message_id}` makes a retried
   event a no-op (the webhook dedupe already covers duplicate deliveries).
2. **Orchestration**: a new `ConversationAgentService` (in `packages/conversation/`) loads
   business facts from `tenant.settings`, builds a catalog from the tenant's active products,
   constructs the `Agent` (injected `IntentExtractor` + `PolicyEngine` + auto-resolved
   `ServiceOrderCreator`), runs one message, and:
   - audits every action (`agent.message_handled`) with intent/confidence/decision/tools/order;
   - on `requires_human`, moves the conversation to `handoff_requested`;
   - persists the reply as an outbound message: `sent` when the policy allows auto-send (L1
     replies, L2 orders) and the message was sent within the 24h window; otherwise **`draft`**
     for owner approval (the L0 default). A failed/closed-window send degrades to a draft.
3. **Sender injection**: sending goes through a `MessageSender` protocol. Production uses a thin
   `WhatsAppSenderAdapter` over the existing `WhatsAppSender` (which enforces the 24h window and
   rate limits); tests use a recording fake. The service (not the sender) persists the outbound
   row, so persistence has one owner.
4. **New status value** `WhatsAppMessageStatus.DRAFT`. The column is a non-native enum
   (`native_enum=False`, no CHECK constraint), so no migration is required; drafts are simply
   outbound rows the owner has not approved.
5. **Autonomy** is read from `tenant.settings["agent_autonomy"]` (L0–L3), default **L0**.

## Consequences
- The webhook is fast; model failures are retried and observable; no customer reply is lost
  (drafts).
- The owner-facing draft approve/edit UI and the "send draft" endpoint are Phase 8 work; for now
  drafts are visible as outbound `draft` messages on the conversation.
- Loading prior message history into the prompt is deferred; each message is handled standalone.
- `WhatsAppSender` still resolves the business phone number id from global settings
  (single-WABA assumption) — multi-WABA routing is a later phase.
