# ADR 0019: Owner WhatsApp console — separate owner-keyed surface, deterministic commands, policy-gated writes

- Status: **Proposed** (implementation exists but is uncommitted/unreviewed as of 2026-10-18)
- Date: 2026-10-18
- Phase: 8c (owner console)
- Relates to: AGENTS.md §2 (owner WhatsApp console) and §6 (autonomy policy, injection
  defense, fraud awareness), ADR 0012 (autonomy policy), ADR 0008 (users/roles),
  plan `docs/plans/2026-10-18-phase-8c-owner-console.md`, UNCERTAINTY U15 (durable
  alerts need a push channel).

## Context

Phase 8c lets the owner run the business by chatting: "how many orders today?",
"mark order <ref> delivered", "restock 20 of SKU X", plus reading/acknowledging durable
owner alerts. Three forces shape the design:

1. **A separate surface.** Owner commands must never be parsed as customer messages and
   owner identity must never be assumed from a customer channel. The plan requires a
   distinct handler keyed to the owner's phone number, with non-owner senders falling
   through to the untouched customer agent.
2. **No second path to money/stock.** Writes must reuse the existing protected services
   (delivery FSM, alert service, event-sourced stock ledger); the console must not
   duplicate validation or bypass policy (AGENTS.md §5).
3. **The owner phone can be compromised.** A stolen phone or an impersonating staff
   member is a real threat, so the owner channel is treated as untrusted input:
   injection defense applies and risky verbs stay human-approved.

The chosen approach (plan options 2 + 3) is a dedicated `OwnerConsoleService` with a
deterministic-first command parser and no LLM on any command.

## Decision

1. **Identity is phone-keyed and tenant-scoped.**
   `OwnerConsoleService._resolve_owner` resolves the sender to an **active `OWNER`
   user of the tenant carried by the inbound outbox event**, matching `users.phone`
   under Kenyan normalization (`254…`/`+254…`/`07…`). The console operates strictly
   inside that tenant (plan invariant 7). A phone that is only an owner of *another*
   tenant — or a staff/inactive user, or a customer — returns `handled=False` and the
   customer path runs unchanged. This differs from the plan's illustrative "match
   owners across every tenant and ask which business" branch: the inbound channel
   already fixes exactly one tenant and there is no per-phone session state to resolve
   an ambiguity on a later message, so cross-tenant matching would dead-lock
   multi-tenant owners rather than protect them. Tenant-scoping is the safer and
   functional reading of invariant 7.

2. **Deterministic parser, no model.** `parse_owner_command` maps owner text to one of
   `orders_today | revenue_today | status | alerts | ack | mark_delivered | restock |
   always_human | help`, or `None`. Command arguments are validated by pydantic models
   with `extra="forbid"` (`AckAlertArgs`, `MarkDeliveredArgs`, `RestockArgs`, …); a
   regex match whose arguments fail validation becomes `None` → the help menu, never an
   improvised action. Unparsed text also yields the help menu (an owner will never
   silently fall through to the customer agent, because doing so could let a
   phone-thief place customer orders under the owner's identity).

3. **Every command goes through the shared `PolicyEngine`.** Reads and acknowledgements
   map to `Intent.FAQ`; writes map to `Intent.CREATE_ORDER` (so `may_execute` is
   `EXECUTE_ORDER` only at L2+); always-human keywords (`refund`, `discount`, `price`,
   `bulk`, `delete`) are passed as `sensitive_action` and hit
   `ALWAYS_HUMAN_ACTIONS`, escalating at every level. The console verbs
   `order_mark_delivered` / `inventory_adjustment` are **not** in
   `ALWAYS_HUMAN_ACTIONS` (the plan gates them rather than banning them) and the
   console adds an extra L3 gate on restock on top of the policy's L2 gate.

4. **Writes reuse protected services; stock goes through the ledger.**
   `mark_delivered` resolves the order (full UUID or hex prefix) and the in-flight
   delivery, then calls `DeliveryService.mark_delivered`, relaying
   `PaymentNotCollectedError`, `ProofOfDeliveryRequiredError` and other `DeliveryError`
   honestly — it never bypasses the POD/proof gates. `restock` resolves the variant,
   takes the same `FOR UPDATE` variant lock `OrderService` uses, loads the event-sourced
   ledger and appends a `receive` movement (`reference_type="owner_console"`,
   reason `OTHER`); no quantity is ever overwritten. Alerts use
   `AlertService.list_alerts` / `acknowledge` (idempotent, tenant-scoped).

5. **Money/stock arithmetic stays in SQL/domain code.** `revenue_today` is a
   `SUM(total_cents)` over orders whose payment is confirmed (`payment_status =
   completed` OR `paid_at IS NOT NULL`); reads never compute in the model. The console
   never touches payments.

6. **Owner-channel injection is escalated.** `detect_injection` runs on the raw owner
   text before parsing; a hit escalates to a human (`requires_human`, conversation set
   to `handoff_requested`), executes nothing and is audited.

7. **Reads and acknowledgements are always sent; the policy decision is still
   recorded.** For the console the requester, the approver and the sole audience are
   the same person, and U15 makes the console the missing alert push channel, so
   applying the L0 draft step to a read would only hide the answer from its only
   consumer. The `PolicyDecision` is still computed and audited. Writes are **not**
   exempt: below their gate they change nothing and reply honestly.

8. **Every handled message is audited** as `console.action_executed` with the command,
   validated args, policy decision/risk/reason, autonomy, gate, outcome, injection flag
   and the **actor phone** (plan invariant 6). The phone appears only in the audit row,
   never in `logger.*` output (AGENTS.md §12).

9. **Wiring is one lazy hook.** `ConversationAgentService.respond_to_message` calls
   `_try_owner_console` first; it returns `None` for non-owners, so the customer path
   is byte-for-byte unchanged. The hook lazy-imports the console (the console imports
   `MessageSender`/`autonomy_from_settings` from the agent service), shares the
   service's sender, and never closes it — the caller owns the sender lifecycle.

## Consequences

- **Positive:** a clear surface boundary; deterministic, testable commands; a single
  policy engine and a single path to delivery/stock; the customer channel is provably
  untouched (the hook returns `None`).
- **Deviation to track:** unlike the customer channel, L0 does not force answers to
  reads into drafts. This is intentional (see decision 7) and must be revisited if the
  console ever gets an audience other than the owner.
- **Known gaps (open items):**
  - No API router reaches an inbound message path today, so the console is
    worker-path-only; activation is the single agent-service hook. A future API/dev
    simulator would need to route through the same hook.
  - `register` writes the same value to `tenant.owner_phone` and the owner `User.phone`,
    so a business whose channel number equals its personal number cannot receive
    owner-console messages from that number; onboarding/profile should separate the
    two. Recorded for the onboarding phase.
  - `StockLedger.record_receive` currently drops its `created_by` argument (it stores
    `reference_id` instead); the console passes `created_by=owner.id` for forward
    compatibility and relies on the audit row for attribution. Fix belongs to the
    ledger's owner, not this slice.
  - `ProductVariant.quantity` is a display column never updated by `OrderService`
    either; the ledger is the source of truth. The console reads availability from the
    ledger and does not touch the column.
