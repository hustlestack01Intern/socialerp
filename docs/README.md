# The SocialERP Museum

This repository keeps its thinking in public. This index is the floor plan. Every document
is an exhibit, and each one is honest about its date and its confidence.

If you only have 45 minutes, take the **[guided tour](TOUR.md)** instead.

---

## How to read this place

The documents fall into seven wings. Read in this order if you are new:

```
Foundations → Architecture decisions → Plans → Security & truth → Operations → Business → Learning
```

Nothing here is marketing copy. Where a document was superseded, it says so at the top. Where
a claim was aspirational, it has been corrected rather than deleted, because the correction is
part of the record.

---

## Wing 1 — Foundations

| Document | What it is | Read it if |
|---|---|---|
| [`CONSTITUTION.md`](CONSTITUTION.md) | The non-negotiable rules of engineering and safety | You are about to contribute |
| [`SPEC.md`](SPEC.md) | The product specification and roadmap | You want the whole intent |
| [`STATE.md`](STATE.md) | The measured state of record: HEAD, gate output, what is done | You want to know where we actually are |
| [`MAP.md`](MAP.md) | Modules, boundaries, data flows and external integrations | You want the map before the territory |
| [`GLOSSARY.md`](GLOSSARY.md) | Domain language: tenant, ledger, reservation, STK, POD, autonomy levels | A term is unfamiliar |

## Wing 2 — Architecture decisions

Every significant decision is an ADR in [`adr/`](adr/). Consequences are written down, not
just choices. Some decision numbers appear twice because parallel workstreams allocated the
same number; they are preserved rather than renumbered, and the pilot plan notes a future
docs pass.

Highlights:

- [`0001-lean-mvp.md`](adr/0001-lean-mvp.md) — build the narrow thing first.
- [`0002-multi-tenancy-rls.md`](adr/0002-multi-tenancy-rls.md) — tenancy from day one.
- [`0005-event-sourced-stock-ledger.md`](adr/0005-event-sourced-stock-ledger.md) — stock as
  immutable movements.
- [`0006-order-state-machine.md`](adr/0006-order-state-machine.md) — legal transitions only.
- [`0011-payments-stk-and-callbacks.md`](adr/0011-payments-stk-and-callbacks.md) — the money path.
- [`0012-agent-gateway-autonomy-policy.md`](adr/0012-agent-gateway-autonomy-policy.md) — the
  autonomy ladder and policy engine.
- [`0015-order-transition-boundary-and-pod-fsm-edge.md`](adr/0015-order-transition-boundary-and-pod-fsm-edge.md)
  — the boundary that keeps orders and delivery decoupled.
- [`0016-money-path-concurrency-and-late-collection.md`](adr/0016-money-path-concurrency-and-late-collection.md)
  — late callbacks and captured-but-not-collectable money.
- [`0017-crm-memory.md`](adr/0017-crm-memory.md) — consent, evidence-backed traits, erasure.
- [`0023-invite-gate-platform-table.md`](adr/0023-invite-gate-platform-table.md) — why the
  invite table is platform-scoped, not tenant-scoped.

## Wing 3 — Plans

[`plans/`](plans/) holds the ordered work, each with acceptance tests, risk notes and
rollback. They are plans, not claims of completion.

Start with:

- [`2026-10-23-production-pilot-launch.md`](plans/2026-10-23-production-pilot-launch.md) —
  the current path to the first paid Kenyan pilot: M0 to M8, acceptance criteria, blast radius,
  incentive review and failure modes. **This is the live plan.**
- [`2026-10-19-trial-spine-first-clients.md`](plans/2026-10-19-trial-spine-first-clients.md) —
  the earlier trial-spine plan that the pilot plan builds on.
- [`2026-10-21-security-hardening-plan.md`](plans/2026-10-21-security-hardening-plan.md) —
  the security wave that produced the invite gate, rate limiting and cookie work.

The rest are phase plans kept for provenance: agent, orders, delivery, CRM, segments,
owner console, Instagram, TikTok, sales surface, money path.

## Wing 4 — Truth, security and failure

This is the most important wing. It is what separates this project from a demo.

| Document | What it is |
|---|---|
| [`UNCERTAINTY.md`](UNCERTAINTY.md) | The live ledger of assumptions, confidence and status. Anything open that touches money, stock, security or privacy is a stop condition. |
| [`THREAT_MODEL.md`](THREAT_MODEL.md) | Assets, actors, threats and mitigations — corrected on 2026-11-01 to separate design intent from deployed control. |
| [`failures/`](failures/) | Postmortems. Each one names what broke, why the green suite could not see it, and what changed. |

The 2026-10-21 column-drift postmortem is required reading for anyone who trusts a green test:
the suite was green while the migrated production database could not register a user, because
the test schema was built from ORM metadata rather than the migrations.

## Wing 5 — Operations

| Document | What it is |
|---|---|
| [`RUNBOOKS/reconciliation.md`](RUNBOOKS/reconciliation.md) | Daily provider-vs-ledger reconciliation. |
| [`RUNBOOKS/refunds.md`](RUNBOOKS/refunds.md) | Human-approved refund procedure. |
| [`SHIPPING_GUIDE.md`](SHIPPING_GUIDE.md) | The technical and delivery guide (check its date; state docs win). |
| [`CLIENT_ONBOARDING_CHECKLIST.md`](CLIENT_ONBOARDING_CHECKLIST.md) | What a merchant needs before the 30-minute onboarding. |

## Wing 6 — Business

| Document | What it is |
|---|---|
| [`BUSINESS_PLAN.md`](BUSINESS_PLAN.md) | Market, motion and economics. Treat projections as hypotheses. |
| [`PRICING_STRATEGY.md`](PRICING_STRATEGY.md) | The price book and reasoning. The live source of truth for the page is `apps/dashboard/src/lib/pricing.json`. |

## Wing 7 — Learning

| Document | What it is |
|---|---|
| [`LEARNINGS.md`](LEARNINGS.md) | Predicted-versus-actual calibration and process lessons. |
| [`PROJECT_FINDINGS_AND_IDEAS.md`](PROJECT_FINDINGS_AND_IDEAS.md) | Strategic review memo: what to test first, and what not to claim. |
| [`FINAL_DELIVERY_SUMMARY.md`](FINAL_DELIVERY_SUMMARY.md) | An archived summary that was **corrected** because it overstated readiness. Kept as a lesson. |
| [`PHASE_0_PLAN.md`](PHASE_0_PLAN.md) | The original scaffold plan. |

---

## A note on honesty

Three habits are visible throughout this museum:

1. **Every claim has a date and a measurement.** If it was not run, it says so.
2. **Corrections stay next to the original.** Superseded text is struck through or labelled,
   not quietly deleted.
3. **Open problems are first-class.** The uncertainty ledger and the postmortems are as
   important as the specifications.

If you find a place where the docs and the code disagree, the code wins — and the disagreement
is a bug worth fixing in both.
