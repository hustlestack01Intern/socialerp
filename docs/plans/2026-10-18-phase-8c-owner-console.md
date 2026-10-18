# PLAN: Phase 8c — Owner WhatsApp console

Date: 2026-10-18. Author: Chief Engineer (planning scribe output). Classification
(@taxonomist):
- type = agent surface (owner channel) + action gating
- risk = HIGH (write actions: orders/delivery/stock; injection surface on owner
  phone). Refunds/discounts/price changes/bulk sends remain human-approved.
- complexity = MEDIUM-HIGH (deterministic intent parsing + policy gating + reuse
  of existing protected services; no second code path to money/stock)

## Goal / non-goals

**Goal:** Implement the Owner WhatsApp console per parent plan (Slice 8c) and
AGENTS.md §2 and §6. The owner can run the business by chatting:
"how many orders today?", "mark order 1042 delivered", "restock 20 of SKU X".
Design as a **SEPARATE agent surface keyed to the owner's phone number** (never a
customer identity). All write actions route through the policy engine and existing
protected services (never a second code path to money/stock). Every action
audited. Injection defense applies to the owner channel.

**Non-goals:** Modifying payments/refunds core logic (refunds/discounts/price
changes/bulk sends stay human-approved). Creating a parallel order service.
Changing customer-channel behavior. Live provider calls (synthetic/fakes only).

One-paragraph summary: 8c adds an owner-scoped console path (separate from
customer conversations) that parses owner intents deterministically where possible,
uses the same policy engine and service tools (e.g. OrderService via
ServiceOrderCreator or direct services), enforces “always human” for risky writes,
audits everything, and treats owner messages as potentially compromised (injection
defense + identity gating).

## Invariants

1. **Separate surface, owner identity only.** Console is keyed to the owner’s
   phone number (tenant-scoped). It must never be used to impersonate/act as a
   customer identity. Non-owner phone numbers never get console privileges.
2. **No second code path to money/stock.** All write actions call **existing
   protected services** (`packages/core/orders/OrderService`,
   `packages/core/delivery/` if present, catalog/inventory services, or
   `packages/agent/service_tools.py:1-122` patterns). Do not bypass policy or
   duplicate validation (AGENTS.md §5: LLM never does money/stock arithmetic).
3. **Deterministic intent parsing where possible.** Read/write intents
   ("how many orders today?", "mark order X delivered", "restock 20 of SKU X")
   parsed deterministically (regex/structured patterns) for high-confidence owner
   commands; fall back to LLM extractor only if ambiguous, still validated against
   strict schema. Never trust free-form.
4. **Policy engine gates everything.** Every action passes through
   `packages/agent/policy.py` (PolicyEngine). **Always human-approved** retained
   for: refunds, discounts beyond configured limit, price changes, bulk marketing
   sends, deletion of customer data, high-risk/low-confidence, injection detected.
5. **Owner-channel injection defense.** Apply injection detection (see
   `packages/agent/intent.py:69-86, 196-210`) to owner messages; owner phone
   compromise is treated as a risk (fraudster who steals owner’s phone; staff
   pretending to be owner). High-disagreement/uncertain cases escalate.
6. **Every action audited.** Write intents produce audit entries (action, resource,
   params, tenant, actor id/phone, policy decision). Append-only audit (reuse
   `packages/core/audit/logger.py`).
7. **Tenant-scoped + RLS enforced.** Owner console operates only within the
   tenant associated with that owner phone; cross-tenant operations impossible.
8. **Durable alerts (U15).** Console surfaces/acknowledges durable owner alerts
   (`packages/core/alerts/`): the console IS part of the answer for “missing push
   channel”. Read alerts, acknowledge via existing `AlertService` (idempotent).
9. **Fail-safe.** On extraction/policy error, draft or handoff (never auto-exec
   risky writes). If unsure, clarify or handoff.

## Derivation

- **Owner identity mapping.** Identify tenant(s) for owner phone (separate from
  customer `customers.phone`). Use existing auth/tenant membership (owner role).
  Console path rejects messages from non-owner numbers with safe handoff/log.
- **Intent taxonomy (owner console):** Extend/branch on owner vs customer. Owner
  intents (examples from parent):
  - Read: `orders_today`, `orders_count`, `revenue_today`, `open_orders`,
    `stock_low`, `list_alerts`
  - Write: `order_mark_delivered` (order X), `restock_sku` (20 of SKU X),
    `order_cancel` (with reason/rules), `alert_acknowledge`
  Prefer **deterministic parsers** first (e.g. regex for “mark order (\d+)
  delivered”, “restock (\d+) of ([A-Z0-9\-]+)”, “how many orders today?”). Keep
  LLM extractor as fallback but schema-constrained; never let model invent IDs
  without validation.
- **Gating via policy:** Treat owner writes as sensitive actions where appropriate.
  Map console verbs to `sensitive_action` strings checked by
  `PolicyEngine.decide(..., sensitive_action=...)` (see
  `policy.py:174-252`: `refund`, `price_change`, `discount_beyond_limit`,
  `bulk_marketing_send`, `delete_customer_data` are always human). Add console
  verbs carefully: `restock_sku` (inventory adjustment) — low/med risk per rules?
  Must still go through existing inventory service with validation; consider as
  “inventory_adjustment” and require approval if outside bounds (tenant rules).
  `order_mark_delivered` affects state machine (delivery) — use existing delivery
  flow; policy: L2/L3 transacts only where permitted, else draft/handoff.
- **Reuse existing services (no second path):**
  - Order state transitions: `packages/core/orders/OrderService` (or existing
    methods). Never write to orders table directly.
  - Inventory/restock: use catalog/inventory service (ledger-based). Event-sourced
    (AGENTS.md §5).
  - Alerts: `packages/core/alerts/AlertService.acknowledge()` / `list_alerts()`.
  - Agent tools: prefer `ServiceOrderCreator` for order creation if needed; for
    delivery/stock, add small service-backed tools in `packages/agent/service_tools.py`
    (or new owner tools) that call core services — same boundary (agent -> core).
- **Channel separation:** Route owner messages via a distinct handler (e.g.
  `ConversationAgentService` variant or owner console service) keyed by owner
  phone. Do not mix customer conversation state with owner console session in a
  way that leaks identity. Console replies are outbound to owner number.

## Options (>=3)

1. **Extend ConversationAgentService with owner mode (flag)**: detect if sender
   is owner phone => use OwnerIntentExtractor + owner-allowed tools + stricter
   policy.
   - Pros: reuse wiring, audit, sender/outbox, tests harness.
   - Cons: mixes surfaces; risk of accidental cross-path.
2. **Separate OwnerConsoleService (new)**: dedicated service that loads owner
   context, deterministic parser, calls core services directly via policy-gated
   commands, audits. No LLM path by default for writes.
   - Pros: clear boundary (separate surface), easier to harden, explicit identity
     check. Matches “separate agent surface”.
   - Cons: some duplication of wiring (sender, audit).

3. **Owner console as “tools only” + deterministic router (no LLM for writes):**
   For owner, parse with pure regex/DSL first; execute via whitelisted service
   calls gated by PolicyEngine with `sensitive_action` set; only reads may use
   LLM-free summarisation. Writes never hit LLM extractor.
   - Pros: strongest injection defense for writes; deterministic.
   - Cons: less flexible for natural phrasing initially.

**Chosen:** **(2) Separate OwnerConsoleService** with **(3)’s deterministic-first**
approach: owner writes parsed deterministically; reads allow minimal grounded
summaries. Keep identity check strict (owner phone mapping required). Reuse
existing sender/audit patterns.

## Ordered steps (tests first, small commits)

1. **Owner identity + gating (commit 1)** — helper to resolve tenant + verify
   sender is owner (by phone). Add tests: owner allowed, non-owner denied,
   cross-tenant impossible. No behavior change to customer path.
2. **Owner intent schema + deterministic parser (commit 2)** — add owner intent
   types (read/write) and `DeterministicOwnerIntentParser` covering:
   - "how many orders today?" / "orders today"
   - "mark order <id> delivered"
   - "restock <n> of <sku>"
   - "list alerts", "ack alert <id>"
   **Tests:** parsing, variants (Swahili/Sheng light), invalid forms rejected.
3. **Policy mapping for owner writes (commit 3)** — extend policy usage: set
   `sensitive_action` for inventory adjustments/delivery state changes as
   appropriate; ensure refunds/discounts/price changes/bulk sends remain in
   `ALWAYS_HUMAN_ACTIONS`. **Tests:** write intents gated (L0/L1 => draft/handoff
   or escalate per rules), injection forces handoff.
4. **OwnerConsoleService (commit 4)** — new service
   `packages/conversation/owner_console.py` (or `packages/agent/owner_console.py`):
   - validate owner identity (tenant-scoped)
   - parse intent (deterministic-first)
   - authorize via PolicyEngine
   - execute via core services (OrderService, AlertService, inventory) — no direct
     DB writes outside services; map errors to safe replies
   - audit every action (`console.action_executed`, decision trace)
   - send reply via `MessageSender` (draft/sent per policy)
   **Tests:** read intents answered from DB; write intents execute only when
   policy allows; non-owner phone rejected; injection blocked; audit present.
5. **Channel integration (commit 5)** — wire inbound messages: when sender is
   owner phone, route to OwnerConsoleService instead of customer
   ConversationAgentService (or dispatch layer). Preserve existing customer path
   unchanged. **Tests:** routing decision correct; no cross-leakage.
6. **Alerts integration + docs/ADR (commit 6)** — console can list/ack alerts
   (uses `AlertService`), document owner-console surface, ADR covering
   separation, identity gating, injection defense for owner channel, incentive
   review. **Tests:** ack is idempotent, tenant-scoped.

**TDD:** each step failing tests first.

## Blast radius and rollback

- **Blast radius:** new `packages/conversation/owner_console.py` (or agent
  module), owner intent/parser, dispatch hook in channel/agent wiring. No changes
  to `OrderService`, `PolicyEngine` core sets (only how we call), no schema
  changes. No modification of payment/refund protected paths.
- **Rollback:** revert commits; feature can be disabled by not registering
  dispatch. No data mutation risk in rollback (services enforce invariants).
- **Safety:** write execution only via existing services; policy remains source of
  truth.

## Incentive review (AGENTS.md §6)

Actors: **owner**, **fraudster who steals owner’s phone**, **staff member
pretending to be owner**, **customer**, **rider**, **regulator**.

- **Owner:** primary beneficiary (run business by chat, save time). Legitimate.
- **Fraudster who steals owner’s phone:** high risk. Can attempt to mark orders
  delivered, restock, or issue commands. **Mitigations:** owner-channel injection
  defense, deterministic-first for writes, policy gating, every action audited,
  alerts on unusual activity (can raise alerts via `AlertService`), high-risk
  flagged. Also consider session binding, step-up not proposed now but noted.
- **Staff pretending to be owner:** privilege escalation via phone spoofing? Or
  shared device. **Mitigations:** identity is phone-based + tenant role mapping;
  audit includes actor phone/id; tenant policy controls who is “owner”. Treat as
  compromise risk flag case.
- **Customer/rider:** no impact; surface separated. No cross-tenant access.
- **Regulator:** audit trail completeness supports accountability; no new PII
  collected beyond existing.
- **Competitor/Meta/Safaricom:** no external secrets exposed.

**Key point (required):** injection defense applies to OWNER channel too
(compromise risk). Risk flags escalate to human; refunds/discounts/price
changes/bulk sends remain human-approved (never autonomously executed).

## Failure modes and detection

| Failure mode | How detected | Response |
|---|---|---|
| Non-owner executes console command | Identity test fails | Reject, audit, handoff/log; no execution. |
| Owner phone spoofed/compromised | Injection markers or unusual pattern detected | Set risk flags => PolicyAction.ESCALATE_HUMAN; audit; never auto-exec. |
| Deterministic parser misreads natural phrasing | Parser tests + fallback path | If ambiguous, treat as low confidence => handoff/draft (don’t guess). |
| Second code path to money/stock created | Code review (imports restricted) | Enforce: writes only via core services (OrderService/AlertService/inventory). |
| Write bypasses policy | Integration test asserting policy decision logged | Require policy.decision recorded in audit before service call. |
| Cross-tenant action | Tenant test | Tenant-scoped on every service call; identity => single tenant. |
| Out-of-order/double execution | Idempotency of core services (order create uses idempotency; state transitions validated) | Rely on existing service invariants; audit dedupe. |
| Alert ack leaks tenant | RLS test via AlertService | Tenant-scoped list/get/ack (existing). |

## Acceptance criteria (measurable)

- [ ] `make check` passes (ruff, mypy strict, pytest, evals), real output.
- [ ] Owner identity: owner allowed, non-owner denied, cross-tenant impossible
  (tests).
- [ ] Deterministic parser: read/write intents parse correctly; invalid rejected;
  Swahili/Sheng light cases covered.
- [ ] Write intents: ALL gated by policy; refunds/discounts/price changes/bulk
  sends remain human-approved (never auto-executed). L0 => draft/handoff as
  appropriate.
- [ ] Injection on owner channel: detected => escalated to human; never
  auto-executed (test).
- [ ] No second code path: console calls existing protected services only (code
  review + tests).
- [ ] Every owner action audited (decision + params + resource) — presence verified
  in tests.
- [ ] Alerts: list/ack via `AlertService`, idempotent, tenant-scoped.
- [ ] Customer path unchanged (regression tests).
- [ ] ADR added documenting separation + owner-channel risk.

## Docs to update

- `docs/STATE.md` (8c status)
- `docs/MAP.md` (owner console module)
- `docs/UNCERTAINTY.md` (assumptions: owner phone mapping, compromise risk
  handling, deterministic coverage)
- `docs/adr/XXXX-owner-console.md`

## Assumptions with confidence (copy to UNCERTAINTY.md)

| Assumption | Confidence (0-1) | Evidence/way to reduce |
|---|---|---|
| Owner identity is resolvable by phone number to a tenant with owner role. | 0.9 | Existing tenant/user model; tests will pin mapping. |
| Deterministic parsing covers common owner commands (“delivered”, “restock N of SKU”, reads). | 0.85 | Synthetic test set; fallback to LLM only if ambiguous but still gated. |
| Inventory “restock 20 of SKU X” maps to existing adjustment path (ledger) with bounds. | 0.9 | Use catalog/inventory services (event-sourced). Validate SKU exists/tenant. |
| “mark order X delivered” uses existing delivery/order state machine (no bypass). | 0.95 | Reuse OrderService/delivery flow. |
| Owner-channel compromise is a real risk; escalation is the correct default. | 0.95 | AGENTS.md §6 fraud awareness + injection defense. |
| Console surfaces durable alerts (U15) — read/ack via AlertService. | 0.95 | `packages/core/alerts/service.py` exists. |
