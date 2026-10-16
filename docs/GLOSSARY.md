# GLOSSARY

Domain terms used across SocialERP. Add a term here when it appears in more than one
doc or is ambiguous.

- **Delivery FSM** — the physical-lifecycle state machine on a `Delivery` row:
  `assigned → picked_up → arrived → delivered | failed`, `failed → picked_up` (retry).
  `delivered` is terminal. `assigned → failed` is intentionally legal (a dispatch can be
  abandoned before pickup). It is the SERVICE (`DeliveryService.mark_failed`), not the FSM,
  that guards the linked ORDER transition: on an `assigned` failure the order is left
  `packed` (no `packed → failed_delivery` edge exists); on `picked_up`/`arrived` failures the
  order moves to `failed_delivery`.

- **Dispatch** — `POST /api/v1/delivery/orders/{order_id}/dispatch`. Idempotent; requires the
  order to be `packed`; creates the `Delivery` in `assigned` with a rider. One active delivery
  per order.

- **Pay-on-delivery (POD)** — an order whose `payment_method` is `mpesa_pay_on_delivery`:
  goods move first, money is collected at the door. `mark_delivered` is gated on the payment
  being collected (`payment_status == completed`) and evidenced.

- **Arrival STK trigger** — the POD pay-at-arrival flow: `POST
  /api/v1/delivery/deliveries/{id}/arrived` fires an STK Push for the order total when the
  order is POD and still unpaid. Key facts: the provider is resolved LAZILY inside the
  handler (prepay arrivals never need a configured provider); the idempotency key is
  `delivery:{delivery_id}:arrived`; a provider error is surfaced as `stk_error` while the
  arrival still commits.

- **Rider** — a per-tenant delivery agent identified by phone number; not a login user.
  Currently the phone is stored as provided (NOT yet normalized to `2547…`/`2541…` — see
  UNCERTAINTY U21).

- **Zone** — a per-tenant delivery area with a flat fee (and optional free-above threshold).
  Fee arithmetic lives only in `DeliveryService` (the LLM never quotes fees).
