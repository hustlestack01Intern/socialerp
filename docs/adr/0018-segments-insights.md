# ADR 0018: Customer segments and owner insights — fixed enum rules, read-time evaluation

- Status: **Proposed** (implementation exists but is uncommitted/unreviewed as of 2026-10-18)
- Date: 2026-10-18
- Phase: 8b (segments + insights)
- Relates to: AGENTS.md §5 (LLM never computes), §8 (rule engine), §10 (customer
  memory), plan `docs/plans/2026-10-18-phase-8b-segments-insights.md`, ADR 0013
  (catalog/stock), ADR 0017 (CRM memory — the trait store segments read)

## Context

Phase 8a shipped evidence-backed, versioned customer traits. The plan for 8b asks
two owner-facing questions: "which customers are in this group?" (segments) and
"how did the business do, and is anything wrong?" (insights). Both must be
deterministic, tenant-scoped, read-only for this wave, and computed by code —
never the LLM (AGENTS.md §5). The plan leaves three design choices open: how
segments are stored, how they are evaluated, and whether insights raise durable
alerts. Wrong choices here either bolt persistence onto a pure derivation or
smuggle side effects into a slice the plan explicitly keeps read-only.

## Decision

1. **Fixed enum segments + JSON predicate trees (plan Options 3 + 1).**
   `packages/core/crm/segments.py` defines the closed `SegmentKey` set
   (`first_time`, `repeat_buyers`, `high_value`, `at_risk`, `reorder_due`).
   Each rule's predicate is a JSON tree (`and`/`or` composition, comparison
   leaves) over **allow-listed trait fields only** — the allow-lists are
   imported READ-only from `packages.core.crm.service` (`ALLOWED_TRAITS`,
   `SENSITIVE_TRAIT_NAMES`), so segments can never read a trait the CRM is not
   allowed to store. Registry validation runs at import time.

2. **No `not` operator.** Negating a missing field would fabricate membership
   from absence (`not (churn_risk >= 0.5)` would match every customer with no
   churn trait). Absence must mean "unknown, therefore not a member" (plan
   invariant 6). `!=` on a leaf is allowed; a missing field under `!=` is still
   `False`.

3. **Read-time evaluation; nothing persisted.** `SegmentService` resolves the
   latest trait version per `(customer, name)` in one batched SELECT for the
   whole tenant (the N+1 guard the plan's failure-mode table demands) and
   evaluates the predicate over that mapping. No `segments` table, no
   membership cache, no migration. Same DB state ⇒ same membership, always.
   Member pages are sorted by customer id for stable pagination.

4. **Insights are pure reports with pinned, derived thresholds.**
   `packages/core/insights/service.py` computes daily/weekly figures
   (orders, paid orders, revenue, AOV, new vs returning, top items) and a
   simple, explainable order-count anomaly (today vs trailing 14-day median;
   spike at >2x, drop at <1/2x, refused below a baseline median of 5).
   Every constant carries its derivation in code. Revenue and top-items count
   **paid** orders only, via the canonical `is_order_paid`
   (`packages/core/orders/service.py`) so insights and CRM LTV can never
   disagree.

5. **Insights do NOT raise durable alerts (plan invariant 8).** The
   `AlertService`/`owner_alerts` write path (Phase 7) is deliberately not
   reused: 8b's slice is read-only, and a computed stock/anomaly view must not
   leave rows behind. Stock alerts are a request-time snapshot of active
   variants below their owner-configured reorder level. A later phase can add
   a durable alerting surface with its own ADR if the owner needs push-style
   notifications.

6. **Owner-only, ids-only API.** `/api/v1/segments` (definitions, static
   predicates for explainability) and `/api/v1/segments/{key}/members`
   (paginated customer ids) plus `/api/v1/insights/{daily,weekly,stock-alerts}`
   all sit behind `require_owner`. Payloads carry no phone numbers or names —
   PII minimisation (plan invariant 4; Kenya DPA readiness, AGENTS.md §12).

## Deviations from the plan (reported, not silent)

- **Batch query lives in `segment_service.py`, not `CRMRepository`.** The plan
  suggested extending the repository; the repository module is under active
  concurrent edit (Phase 8a agent), so the batch loader was placed in the new
  service module with identical semantics to `latest_traits`. Consolidation
  can happen later without behaviour change.
- **Shared docs (`STATE.md`, roadmap map) not updated by this wave** —
  concurrent-edit conflict risk with the CRM agent; the Chief integrates them.

## Consequences

- Adding an owner-configurable segment later means adding an enum member +
  validated predicate (or a per-tenant predicate store) — the evaluator and
  service do not change.
- Anomaly detection is intentionally dumb and explainable; if owners want
  multi-metric or seasonal baselines, that is a new ADR, not a tweak here.
- Because membership is computed on read, a trait backfill/re-derivation
  (Phase 8a `derive_traits`) automatically corrects segment membership — no
  re-sync job exists because none is needed.
- Tests pin every constant, every truth-table edge (missing trait, type
  mismatch, boundary equality), tenant scoping (service-level and RLS), the
  single-query guard, read-only invariants, and owner-only authZ
  (`tests/segments/`, `tests/insights/`).
