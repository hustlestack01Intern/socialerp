# PLAN: Phase 7 — Delivery (zones, fees, rider WhatsApp flow, pay-on-delivery, tracking)

Date: 2026-10-15. Classification: type=feature; risk=MED-HIGH (pay-on-delivery
touches the protected payment path; rider/customer fraud surface); complexity=high.

## Goal and non-goals

Goal: an order can be dispatched to a rider, tracked by the customer, and
completed — including pay-on-delivery (STK Push at rider arrival) — with the
rider working entirely over WhatsApp quick replies and no app.
Non-gives: live GPS location pipeline, rider apps, route optimization,
auto-dispatch/batching (owner assigns manually first, per SPEC §9).

## Assumptions with confidence (also UNCERTAINTY.md candidates)

- A1 (0.9): Rider identity = phone number in a `riders` table per tenant; the
  inbound pipeline intercepts rider phones before the customer agent.
- A2 (0.85): Delivery fees are zone-based flat fees from tenant-configured
  zones; free above a threshold from tenant settings. No dynamic distance
  pricing in Phase 7.
- A3 (0.9): POD orders pay the order total (goods) on arrival; delivery fee
  is bundled into the quoted total at order time by deterministic service code
  (the LLM never quotes fees).
- A4 (0.75): Tracking page exposes order status only; rider identity/location
  never exposed publicly (rider safety).

## Derivation (invariants that must hold)

1. The LLM/agent never computes or quotes a fee: fee arithmetic lives only in
   `DeliveryService` (deterministic, tested). Every customer-facing fee message
   traces to a stored zone fee.
2. Order FSM stays strict: extend LEGAL_TRANSITIONS with `paid → out_for_delivery`
   (POD only: goods already moving when payment completes) — property-tested;
   all other transitions unchanged.
3. Delivery FSM: assigned → picked_up → out_for_delivery → arrived → delivered
   | failed; failed → out_for_delivery (retry). Illegal transitions raise
   (mirror `IllegalTransitionError`).
4. POD payment uses the PROTECTED existing path: `PaymentService.initiate_stk_payment`
   (order must be `awaiting_payment`), dedupe by idempotency key
   `delivery:{id}:arrived`, zero double-credit under replay (existing guarantee).
5. Stock: sale already recorded at payment (prepay). POD order stock is
   reserved at creation; reservation expiry must SKIP orders with an active
   delivery (else stock vanishes mid-delivery).
6. Every new table (zones, riders, deliveries) has Postgres RLS + tenant_id in
   every query; cross-tenant tests fail closed (release blocker).

## Options considered

- O1: Delivery as a separate service table vs order-embedded status. Chosen:
  separate `delivery` row (one active delivery per order) + order status
  mirror — order FSM stays the authority for money/stock; delivery row carries
  rider/zone/fee/proof.
- O2: Rider flow via agent LLM vs deterministic keyword router. Chosen:
  deterministic quick-reply router in `DeliveryFlowService` (fail-safe; the
  LLM never moves stock/money state). Owner console (Phase 8) reuses the same
  service.
- O3: Tracking as Next.js page vs FastAPI-served minimal HTML. Chosen:
  FastAPI endpoint `/tracking/{order_id}` returning a small status page (no
  PII, no auth, no order-data enumeration surface beyond the UUID).

## Steps (each a small commit, tests first)

1. **Domain + models + migration**: `DeliveryZone`, `Rider`, `Delivery` models
   (RLS), `DeliveryStatus` FSM + `is_legal_delivery_transition`, alembic
   `0010_delivery` (tables + RLS policies + grants, mirroring `0008_payments`).
2. **DeliveryService** (`packages/core/delivery/`): fee computation
   (zone fee / free threshold), dispatch (packed→out_for_delivery, creates
   delivery), transitions (picked_up/arrived/delivered/failed), order-side
   transitions via `OrderService` extension (paid→out_for_delivery for POD),
   reservation-expiry skip for active deliveries.
3. **DeliveryFlowService** (`packages/conversation/delivery_flow.py`): quick
   reply router (en/sw keywords), rider interception in worker
   `handle_inbound_message` (rider phone check first), customer status
   messages via the existing `MessageSender` abstraction, POD trigger (STK at
   arrival; delivered gated on payment for POD), failure reasons + owner alert.
4. **APIs**: `apps/api/routes/delivery.py` — zone CRUD, rider CRUD,
   `POST /orders/{id}/dispatch`, `GET /deliveries`; public
   `GET /tracking/{order_id}` (HTML, no PII). Wire into main.py. Tenant-scoped,
   RLS, idempotency on dispatch.
5. **Tests**: service FSM property tests, fee tests, flow tests (fake sender +
   fake payment service), API/RLS tests, worker interception test, tracking
   fail-closed test.

## Blast radius and rollback

New tables + one order-FSM edge addition (`paid→out_for_delivery`); new routes;
worker handler branch. Rollback: drop migration, remove routes — additive only,
no existing behavior changed except the FSM edge (guarded to POD path).

## Incentive review (who gains, who abuses)

- Rider: gains by falsely marking delivered → mitigated: POD delivery requires
  completed payment before "delivered" is accepted; cash fallback needs rider
  confirmation + reconciliation flag; all transitions audited.
- Customer: gains by claiming non-delivery → mitigated: payment already
  captured (prepay); POD requires payment at arrival before completion.
- Owner: gains by dispatching to secluded/new addresses → fraud flags
  (from `detect_personal_number_payment`-style heuristics) stored on the
  delivery record before dispatch.
- SocialERP: no fee skimming; fees are the tenant's revenue.

## Failure modes and detection

- POD double-charge → idempotency key + existing callback dedupe; test replays.
- Reservation expiry mid-delivery → expiry skip rule; test.
- Rider keyword injection (customer text is data) → router matches fixed
  keywords only; anything else falls back to a clarifying rider message.
- Cross-tenant rider/order access → RLS + scoped queries; tests.
- Lost "arrived" callback → status-query fallback job (existing) covers POD STK.

## Acceptance criteria (measurable)

- `make check` green (ruff, mypy strict, pytest, evals) with real output.
- End-to-end test: packed order → dispatch → rider picked up → arrived → POD
  STK → callback → paid → delivered; customer messages asserted.
- Failed delivery path: arrived → failed(reason) → owner alerted → retry →
- delivered.
- Property tests: delivery FSM + order FSM edge.
- Cross-tenant: rider A cannot see/act on tenant B orders (fail closed);
  tracking page 404s for another tenant's order.
  **AMENDED 2026-10-17:** the public tracking page is deliberately a
  capability URL (unguessable order UUID) and does NOT fail closed for a
  foreign order — it returns 200. This is a documented deviation from the AC
  above, tracked as **UNCERTAINTY U29**, with the fix (signed tracking token or
  SECURITY DEFINER minimal lookup) pending. Do not treat this AC as met.
- RLS present on zones/riders/deliveries in migration + verified by tests.

## Docs to update

STATE.md, MAP.md, ADR 0015 (delivery FSM + POD trigger), RUNBOOKS/delivery.md,
UNCERTAINTY.md (A1–A4 confidence), LEARNINGS if patterns emerge.
