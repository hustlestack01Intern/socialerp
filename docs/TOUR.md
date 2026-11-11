# The Guided Tour

A 45-minute reading path through SocialERP. No code required. If you want the floor plan
instead, see the [museum index](README.md).

Bring one question: *"Could I trust this thing with someone's money and stock?"* By the end
you should be able to answer it with evidence, including the parts that are not ready.

---

## Stop 1 — The problem and the promise (5 min)

Read the top of the [README](../README.md): the problem, the product loop, and the
[status board](../README.md#what-is-real-today).

What to notice:

- The launch position is deliberately narrow: **help a WhatsApp seller respond, capture an
  accurate order, and keep the owner informed.** Not "an AI ERP for everyone".
- The channel fit is backed by a source (Communications Authority of Kenya). The willingness
  to pay is explicitly a hypothesis.

## Stop 2 — The rules of the house (5 min)

Read [`CONSTITUTION.md`](CONSTITUTION.md).

What to notice:

- The LLM is never allowed to do money or stock arithmetic.
- Protected zones need human approval *before* an edit.
- Nothing is done until `make check` passes and the real output is reported.

## Stop 3 — Where we actually are (5 min)

Read [`STATE.md`](STATE.md) and the top banner of [`UNCERTAINTY.md`](UNCERTAINTY.md).

What to notice:

- The measured baseline and the exact commit.
- The difference between "implemented" and "proven in production".
- The open blockers: database-enforced tenant isolation (M1), provider-verified subscription
  payment (M3), trial policy, data protection (M4).

## Stop 4 — The architecture in one sitting (10 min)

Read [`ARCHITECTURE.md`](../ARCHITECTURE.md).

Then trace the money path in your head:

1. A customer orders in WhatsApp.
2. The model extracts intent; deterministic code validates catalog, stock and total.
3. An order is created, stock is reserved, an STK Push is sent.
4. A signed callback is matched against the expected order before anything is marked paid.
5. A sale is recorded idempotently; delivery and tracking follow.

Ask: *where could this lose money?* Then read the failure-handling list and the
[postmortem on late money](adr/0016-money-path-concurrency-and-late-collection.md).

## Stop 5 — The decisions (5 min)

Skim the ADR titles in [`adr/`](adr/). Open two:

- [`0005-event-sourced-stock-ledger.md`](adr/0005-event-sourced-stock-ledger.md) — why stock
  is a sum of movements, not a number.
- [`0017-crm-memory.md`](adr/0017-crm-memory.md) — why traits need evidence and consent is
  default deny.

What to notice: each ADR records consequences and trade-offs, not just the choice.

## Stop 6 — The failures (5 min)

Read [`failures/2026-10-21-column-drift-green-suite.md`](failures/2026-10-21-column-drift-green-suite.md).

This is the heart of the museum. The test suite was green while the migrated database could not
register a user. The tests built the schema from ORM metadata, so they never exercised the
migrations. The fix was a migration plus a new guard that compares every ORM column and enum
value against a migrated database.

What to notice: the project treats a green suite as evidence with a scope, not as truth.

## Stop 7 — The plan to the first paid merchant (7 min)

Read the executive recommendation, the ordered steps, and the acceptance criteria in
[`plans/2026-10-23-production-pilot-launch.md`](plans/2026-10-23-production-pilot-launch.md).

What to notice:

- The plan refuses to rewrite or to wait for every feature.
- It puts hard safety gates between the merchant and live data.
- It defines what would make the team stop, not only what would make them scale.

## Stop 8 — Your verdict (3 min)

You now have enough to judge the project. A good final question is not "does it have AI?" but:

> Can the team onboard a merchant safely, demonstrate a benefit the owner recognises, collect
> the disclosed subscription with verified payment, and keep contribution positive after
> support and provider costs?

If you want to contribute, go to [`CONTRIBUTING.md`](../CONTRIBUTING.md). If you found a
security issue, read [`SECURITY.md`](../SECURITY.md) first.

---

## If you have another hour

- Read [`SPEC.md`](SPEC.md) end to end, then compare it with [`MAP.md`](MAP.md).
- Read the [threat model](THREAT_MODEL.md) and note which mitigations are marked partial.
- Read the [`BUSINESS_PLAN.md`](BUSINESS_PLAN.md) and mark every number that is a hypothesis.
- Run `make check` yourself on a clean database and compare the output with `STATE.md`.
