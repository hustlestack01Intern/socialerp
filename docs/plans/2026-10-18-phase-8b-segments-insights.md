# PLAN: Phase 8b — Segments + owner insights

Status: **SHIPPED, COMMITTED** (`8dbc120`, 2026-10-18). Gate-verified: 37 segments + 38
insights tests green in the wave-2 `make check` (764 passed, `makecheck-w2-145015.log`
lines 51–55 and 82–85). Routers wired at `apps/api/main.py:165-166`. ADR 0018.
Line references below were corrected post-ship to match the actual shipped code (the
fix-pass `229fd48` shifted some line numbers in `crm/service.py`).

Date: 2026-10-18. Author: Chief Engineer (planning scribe output). Classification
(@taxonomist):
- type = feature delivery (deterministic derivation; uses existing CRM)
- risk = MEDIUM (read-only analytics, tenant-scoped, no new PII). Any write-path for
  segments/insights must not touch payments/refunds/stock mutations.
- complexity = MEDIUM (pure evaluators + report builder + simple anomaly detection)

## Goal / non-goals

**Goal:** Per parent plan (docs/plans/2026-10-17-phase-8-crm.md, Slice 8b), add
deterministic **customer segments** (evaluated from traits) and **owner insights**
(daily/weekly reports + anomaly detection), with all numbers traceable to the DB
(orders, ledger, catalog) and no new sensitive data. Every output must either win
or protect a sale, save the owner time, or make the business smarter about its
customers (AGENTS.md §1). No LLM inference for numbers.

**Non-goals:** Creating new inferred sensitive attributes (forbidden: health,
religion, ethnicity, sexual orientation, gender, political affiliation, disability
— never added to segment predicates). Real customer data in tests. Live messaging
endpoints. LLM arithmetic. Segment mutation beyond definition (read-only
evaluation at read time) unless explicitly needed later (not proposed now).

One-paragraph summary: 8b makes the CRM’s trait graph actionable (segments) and
produces owner-facing numbers (insights) that trace to orders/ledger, with simple
anomaly detection; both are deterministic, tenant-scoped, RLS-enforced, and
evidence-oriented.

## Invariants

1. **Determinism.** Segment membership is pure code (no LLM). Predicates read only
   `CustomerTrait` values (versioned, allow-listed). Given the same traits at time
   T, membership is identical.
2. **Traceability.** Every insight figure (orders, revenue, AOV, top items, counts)
   is derivable from concrete DB rows (e.g. paid orders) with the query/derivation
   recorded in tests and service docstrings. Revenue = sum of paid orders
   (financial guard).
3. **Tenant isolation.** All reads are tenant-scoped; segments/insights never cross
   tenants. RLS remains effective (no new wide tables without RLS). Cross-tenant
   access attempts fail closed (release blocker).
4. **No new PII.** Segment definitions and insights never store customer phone/name
   beyond what is already exposed via existing CRM/customer read paths. No derived
   sensitive attributes (AGENTS.md §10).
5. **Evidence-aligned.** Segment predicates prefer stable, evidenced traits
   (e.g. `order_count`, `rfm_score`, `reorder_cycle_days`, `typical_order_hour`,
   `preferred_size/color`, `lifetime_value_cents`, `churn_risk`) as produced by
   `CRMService.derive_traits` (see `packages/core/crm/service.py:189-358`).
6. **Safe defaults.** Uncertain values don’t fabricate membership (treat missing
   trait as “unknown”, segment doesn’t match unless predicate explicitly allows).
7. **Anomalies are simple and derived.** No magic numbers. Thresholds and
   computation are explicit (e.g. today’s count vs trailing 14-day median ± factor
   or IQR; see derivation below). No black-box scoring.
8. **Read-only first.** No mutation of orders/stock/payments via segments/insights.
   Insights do not create side effects (separate from console actions).

## Derivation

- **Segments (predicate model):** A segment is a named rule over traits (all/any
  boolean composition). Evaluation uses the **latest** trait version per
  (customer, name) (as written by `record_trait`/`derive_traits`:
  `packages/core/crm/service.py:189-214, 217-341`). Missing required trait =>
  no match. Example predicates:
  - `first_time`: `order_count == 1` (evidence-based)
  - `repeat_buyers`: `order_count >= 2`
  - `high_value`: `lifetime_value_cents >= T` (KES-aware; store cents)
  - `at_risk`: `churn_risk >= t` (from `churn_risk` derivation at
    `packages/core/crm/service.py:312-341`)
  - `reorder_due`: uses `reorder_cycle_days` and last order (via evidence)
  - `size_preference_*`, `color_preference_*` map to modal attributes
- **Insights (owner, time-bounded):** Use `packages/core/orders/` and
  `packages/core/models/` (orders, order items) as sources of truth. Definitions:
  - `orders_count(period)`: count of orders created in [start,end)
  - `revenue_cents(period)`: sum of `total_cents` over **paid** orders in period
    (never sum unpaid)
  - `aov_cents(period)`: revenue_cents/paid_orders_count (guard division by zero)
  - `new_customers(period)`: customers whose first order created_at in period
  - `returning_customers(period)`: customers with >=1 order in period and first
    order < start
  - `top_items(period, k)`: by quantity sold from paid/order items in period
  - `stock_alerts`: items where stock < reorder_level (from inventory/catalog
    models; read-only)
- **Anomaly detection (simple, derived):** For a metric M (e.g. orders today),
  compare to trailing window W (default 14 days, excluding today): let m be median
  of M over W; define threshold factor f (e.g. 2.0x) and minimum baseline n (e.g.
  >= 5 days) to avoid noise. Fire anomaly if `M_today > m * f and m >= baseline`
  OR `M_today < m / f` (symmetric) with stated derivation and units. No magic
  numbers chosen blindly; tests will pin concrete values. Rationale: median robust
  vs outliers; simple to explain to owner (AGENTS.md §1: save time, smarter).

## Options (>=3) for segment rule representation

1. **JSON predicate tree (chosen baseline)**: e.g.
   `{op:'and', preds:[{field:'order_count', op:'>=', value:2}, ...]}`.
   - Pros: serializable, tenant-configurable later (owner can define), testable,
     no DB schema churn for arbitrary rules, human-readable. Deterministic pure
     eval.
   - Cons: not SQL-expressible for arbitrary joins unless we flatten; fine for
     trait-based predicates.
   - Fit: matches existing JSON fields (evidence_json, data_json) and “rules” idea
     (AGENTS.md §8 rules/DSL). Simpler than full DSL parser.

2. **SQL expression builder (repository-level):** build WHERE clauses against trait
   latest-version view/materialized view.
   - Pros: single query to get segment members (scale later), DB can index.
   - Cons: harder to unit test in pure Python, versioning of latest trait needs a
     view, migration + RLS details, risk of SQL injection if string-built carelessly.
   - Complexity higher than (1).

3. **Fixed enum segments (code-defined):** hardcode a small set (e.g.
   FIRST_TIME, REPEAT, HIGH_VALUE, AT_RISK, REORDER_DUE) in Python; no runtime
   config.
   - Pros: simplest, zero config surface, fastest to ship and test, impossible to
     create unsafe dynamic predicates now.
   - Cons: not owner-configurable, expansion requires code change. But meets 8b
     acceptance without scope creep.

**Chosen:** **(3) Fixed enum segments first (baseline)** + **(1) JSON predicate
tree as internal representation** for the evaluator (keep rules data-driven even
if enum-registered). Rationale: smallest blast radius, deterministic, easy to unit
test, can later promote selected rules to owner-configurable without rewriting
evaluation core. Avoid (2) for this slice.

## Ordered steps (small commits, tests first)

1. **Model/segment types (commit 1)** — `packages/core/crm/segments.py` (or
   `packages/core/segments/` if preferred; colocate with crm per MAP later): enum
   `SegmentKey`, `SegmentRule` (pure dataclass), predicate evaluator reading
   latest traits (via repo/service accessor). **Tests:** truth tables for each
   predicate (including missing trait cases).
2. **Repository support (commit 2)** — extend `CRMRepository` to get latest traits
   per customer (or use existing `list_traits` filtered to latest version). Avoid
   N+1; fetch for tenant in batch. **Tests:** latest-version selection.
3. **Segment service (commit 3)** — `packages/core/crm/segment_service.py`:
   `evaluate_segment(tenant_id, segment_key, customer_id)` and
   `list_members(tenant_id, segment_key, limit)` (read-only). Tenant-scoped.
   **Tests:** determinism (same inputs => same result), cross-tenant isolation
   (members only from tenant).
4. **Insights service (commit 4)** — `packages/core/insights/service.py`:
   `daily_report(tenant_id, date)`, `weekly_report(tenant_id, week_start)`,
   `stock_alerts(tenant_id)`, `detect_anomaly(...)` with explicit params
   (window_days=14, factor=2.0, min_baseline=5). Numbers computed from
   orders/items/ledger (read-only). **Tests:** report totals match DB for synthetic
   data; AOV division-by-zero guard; anomaly fires on synthetic spike; no unpaid
   revenue counted.
5. **API surface (commit 5, owner-only)** — `apps/api/routes/segments.py` and
   `apps/api/routes/insights.py` (or extend crm routes): list segments, get
   members (paginated), get daily/weekly report, list alerts. AuthZ: owner/staff
   per tenant (owner-only for “insights” read as per console/dashboard ownership;
   follow existing owner guard). Audited minimally (read paths may use existing
   audit policy; writes none). **Tests:** authZ (non-owner denied), tenant
   scoping.
6. **Docs + ADR-lite (commit 6)** — update docs (STATE, MAP if needed), add short
   ADR in `/docs/adr/` (segment predicate model, anomaly derivation, no-new-PII).
   No code changes.

**TDD:** each step writes failing tests first (per constitution §4 plan steps say
“each a small commit with its tests”).

## Blast radius and rollback

- **Blast radius:** `packages/core/crm/segments.py`,
  `packages/core/crm/segment_service.py` (new), `packages/core/insights/` (new),
  `apps/api/routes/segments.py`, `apps/api/routes/insights.py` (new). Minor
  extension to `CRMRepository` (read-only helpers). No changes to
  `orders/service.py`, payments, stock ledger, policy, agent tools.
- **Isolation:** pure functions + read-only services. No schema migration required
  for 8b (segments evaluated at read time from traits; no `segments` table
  proposed now — avoids churn; can add later if persisted). Insights are computed
  on-the-fly.
- **Rollback:** revert the 6 commits (or feature-flag routes behind a flag not
  proposed; simplest: revert files). No destructive migrations. No data loss risk
  (read-only).

## Incentive review (AGENTS.md §6; constitution §1.6)

Consider actors: **owner**, **customer**, **staff**, **rider**, **fraudster**,
**Meta/Safaricom/regulator/competitor**.

- **Owner:** wants time saved + smarter decisions (good). Could be tempted to
  over-segment for spam; but marketing requires consent (existing). Insights help
  prevent stockouts (win/protect sale).
- **Customer:** benefits from relevant, consented offers; harmed if segments leak
  across tenants or infer sensitive traits — blocked by invariants (allow-list,
  tenant isolation, no sensitive inference).
- **Staff/rider:** read-only access scope; no elevation via segments/insights.
- **Fraudster:** could try to game segments (create many orders to appear
  high-value) — that’s legitimate business activity, not a privacy leak. Or try
  to access other tenant’s insights — blocked by RLS + authZ. No new attack
  surface for injection via UI (read-only).
- **Regulator (Kenya DPA):** export/erasure unchanged (CRM DSR). No new PII;
  traceability + versioned traits aid explainability. No profiling of sensitive
  categories.
- **Safaricom/Meta/competitor:** no new integration secrets; no external calls.

**Mitigations:** forbid sensitive predicates in evaluator (allow-list of trait
names used), tenant-scoped on every query, cross-tenant tests, read-only API,
evidence requirement unchanged.

## Failure modes and detection

| Failure mode | How detected | Response |
|---|---|---|
| Segment matches on fabricated/missing trait | Unit tests (missing trait => no match); type checks (mypy). | Treat missing as unknown; require evidence-backed traits. |
| Cross-tenant leakage | Integration test: list members for tenant A using tenant B context => empty/forbidden. | RLS + tenant_id filter in repo/service; test fails closed. |
| Revenue includes unpaid | Report test: unpaid order excluded from `revenue_cents`. | Filter to paid (payment_status completed or paid_at set) per `_is_paid` logic (mirror `crm/service.py:81-83`). |
| AOV division by zero | Test with no paid orders => AOV 0 or omitted with explanation. | Guard: if paid_count==0 return 0 or skip. |
| Anomaly false positive (small baseline) | Test with < min_baseline days => no anomaly. | Enforce `min_baseline` (default 5). |
| Non-determinism (LLM used) | Code review + grep (no LLM imports in segments/insights). | Pure code only; evaluator has no gateway calls. |
| Sensitive trait predicate attempted | Static check + unit test refusing sensitive names. | Evaluator allow-list of trait names it may read. |
| N+1 on member listing | Test with many customers; count queries. | Batch fetch latest traits. |

## Acceptance criteria (measurable)

- [ ] `make check` passes: ruff, mypy --strict, pytest (unit+integration+property
  where added), evals (no regressions). Real output reported.
- [ ] Segment evaluator: all predicates covered by truth tables; missing trait =>
  no match; determinism holds across runs.
- [ ] Segment membership is tenant-scoped; cross-tenant test fails closed.
- [ ] Insights daily/weekly reports: totals match synthetic DB (orders_count,
  revenue_cents from paid only, AOV correct, new/returning, top items).
- [ ] Anomaly detection: explicit derivation; fires on synthetic spike; doesn’t
  fire with < min_baseline; threshold factor configurable in tests.
- [ ] No sensitive trait derivation/usage introduced (negative test).
- [ ] No new PII stored by segments/insights (code review + test).
- [ ] API authZ: owner-only for insights/segment membership (or per existing
  policy); non-owner denied.
- [ ] ADR added for segment predicate model + anomaly derivation.

## Docs to update

- `docs/STATE.md` (Phase 8b slice status)
- `docs/MAP.md` (if new modules: `packages/core/crm/segments.py`,
  `packages/core/crm/segment_service.py`, `packages/core/insights/`)
- `docs/UNCERTAINTY.md` (new assumptions with confidence; e.g. anomaly defaults
  14d/2.0x/min_baseline=5 — confidence TBD, but derived)
- `docs/adr/XXXX-segments-insights.md` (numbered per existing ADRs)

## Assumptions with confidence (copy to UNCERTAINTY.md)

| Assumption | Confidence (0-1) | Evidence/way to reduce |
|---|---|---|
| Traits from `derive_traits` are sufficient for initial segments (order_count, ltv, churn_risk, rfm, cycle, prefs). | 0.95 | Read `packages/core/crm/service.py:217-358`; matches AGENTS.md §10. |
| “Paid” = completed or paid_at set (mirror `_is_paid`). | 0.95 | Consistent with CRM LTV (`service.py:81-83`). |
| Trailing median (14d) + factor 2.0 + min_baseline 5 is acceptable baseline for owner alerts/insights (simple). | 0.7 | Synthetic tests + owner feedback later; must be stated derivation. |
| Read-time segment evaluation (no persisted segments table) is sufficient for 8b acceptance. | 0.9 | Avoids migration; can persist later if performance needed. |
| Insights are read-only; no side effects (separates from console). | 0.95 | Matches slice boundary (8c is console). |
