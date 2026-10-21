# PLAN: M1.5 — Sales surface (pricing, signup, login, legal pages, trial banner) (2026-10-21)

> Parent plan: `docs/plans/2026-10-19-trial-spine-first-clients.md` (step 13, M1.5).
> Predecessor: `docs/plans/2026-10-21-money-path-f0-f2.md` (F0→F2 committed).
> Classification from @taxonomist (this session): composite `T_UI` + `T_AUTHZ` +
> `T_ENTITLE` + `T_LEGAL_DOC` + `T_FIXTURE`; risk MED overall with **one HIGH
> sub-item** (the invite-code gate on `POST /auth/register` = "permission
> configs" = protected zone → **human approval required before the edit**).

## Goal and non-goals

**Goal**: give a prospect a surface they can trust and use — a pricing page
that only claims what the system enforces, a legal surface that is honest about
its DRAFT status, a login, and (once approved) an invite-gated signup that
starts a pending trial. Plus an in-gate **link-integrity guard** so the landing
page can never point at a 404 again (today 14 of its links are dead).

**Non-goals**:
- No change to the entitlement matrix, the billing service or the trial policy
  (U60 stays a human pricing decision).
- No Stripe, no self-serve Embedded Signup (white-glove for cohort 1).
- No owner-dashboard UI work (that is a later milestone).
- No CI/Makefile edits *in the original plan* — the CI gate definitions are a
  protected zone, so the JS-toolchain finding was **reported** and the human
  **approved** the CI wiring separately on 2026-10-21 (see "Measured result").
- No `SalesIntent` taxonomy inside `packages/agent/intent.py` and nothing added
  to `/evals` (held-out).

## Assumptions with confidence

| # | Assumption | Conf | Verify |
|---|---|---|---|
| A1 | The public surface must live in `apps/dashboard` (Next.js), because the landing page with its CTAs already lives there and the API is a JSON API | 0.9 | The landing page's `href`s are the dead links we must fix |
| A2 | The JS toolchain can be made verifiable locally (pnpm + lockfile + `pnpm lint` / `pnpm typecheck` / `next build`) | 0.85 | Run all three and paste the output; if the toolchain cannot be installed, the frontend work is **not shippable** and the milestone is cut to the in-gate Python guard only |
| A3 | `PRICING_STRATEGY.md` prices (KES 2,999 / 7,999 / 19,999) match the code (`_PRICE_KES` in `apps/api/routes/billing.py`) | 0.95 | Compare the three numbers in both places; they must agree, else the code is the truth and the doc is corrected |
| A4 | The only plan features the system **enforces** are the five in the entitlement matrix (channels, agent level, insights, API access, seats) | 0.95 | `packages/core/billing/entitlements.py` is the matrix; everything else in the pricing table is sales copy, not enforced |
| A5 | The trial is 14 days from **channel connect** and grants the L2 agent on WhatsApp, while paid **Starter** only grants L1 | 0.95 | `entitlements.py` (TRIAL = L2, STARTER = L1) + `BillingService.start_trial`. The generosity gap is **U60** — shown honestly on the page, **not decided in code** |
| A6 | The dead-link set is exactly the 14 hrefs in `page.tsx` that have no `page.tsx` under `src/app/` | 0.9 | The link-integrity guard test computes it; the test must fail on today's tree (red → green evidence) |

## Derivation (first principles and the invariants that must hold)

1. **A sale needs a surface, but a lying surface is worse than no surface.**
   Every claim on `/pricing` must map to something the code enforces, or be
   labelled as coming-soon. Therefore the page is generated from the same
   numbers as the code (`_PRICE_KES`) and the same feature list as the matrix.
2. **Trust is the product.** A privacy page that claims "we delete your data on
   request" is false while U51 (MSISDN survives in the outbox) and U52
   (`whatsapp_contacts` / `whatsapp_messages` survive erasure) are open. So the
   legal pages are DRAFT-marked, state what the system actually does, and
   **omit** the deletion claim rather than hedge it.
3. **A signup form is a promise.** A waitlist form that `alert()`s "Thanks for
   joining the waitlist!" while recording nothing is a fake success → it is
   removed in the same commit that fixes the legal pages.
4. **Dead links are the cheapest credibility killer.** The guard turns "no 404
   CTAs" from a promise into an **in-gate invariant** (a Python test that runs
   in `make check`), so the class of bug cannot come back silently.
5. **The client never computes entitlement.** The trial banner renders the
   server's decision (`GET /api/v1/billing/subscription`: `status`,
   `days_remaining`, `entitled`, `entitlement_reason`) and never derives days
   itself; on an API error it renders nothing (never blocks the page, never
   invents a number).
6. **Invariants that must hold throughout**: I1 cross-tenant access still fails
   closed; I2 the LLM never does money arithmetic (untouched); I3 the gate
   denies only agent spend; I4 no new endpoint may be added without a tenant
   scope; I5 the public surface never reveals another tenant's data.

## Options (candidates, chosen + why)

- **S1 (chosen): Next.js pages in `apps/dashboard`, verified with the real
  toolchain (pnpm lint + tsc + next build), plus a Python link-integrity guard
  inside `make check`.** One surface, one style system, the guard runs in the
  gate the team already runs.
- S2: Serve the sales pages from FastAPI (Jinja2/static HTML). Cheapest to
  verify (pytest only), but it forks the design system and leaves the existing
  Next.js landing page as a second, unaudited surface — rejected.
- S3: Keep the marketing site static (no forms) and do onboarding by hand with
  every prospect. Zero new code, zero risk, but it is not sellable at all —
  rejected (it is the fallback if A2 fails).
- S4 (rejected sub-option for the invite gate): a client-side-only invite-code
  check on the signup form. Fake security: anyone can call the API directly.
  Rejected by the taxonomist; the gate must be server-side.

## Steps (ordered; one concern per commit; tests first where code changes)

1. **B0 — `chore(dashboard): pnpm toolchain + lockfile`.** Install pnpm, create
   `pnpm-lock.yaml`, install dependencies, paste `pnpm lint` / `pnpm typecheck`
   / `next build` output. No Makefile/CI edit.
2. **B1 — `test(dashboard): CTA link-integrity guard (in-gate)`.** A Python test
   in `tests/architecture/` parses every `href=` under `apps/dashboard/src/app`
   and asserts each target resolves to a real route (a `page.tsx`) or is listed
   in an explicit pending manifest. Red on today's tree (14 dead links),
   green as the later commits land.
3. **B2 — `feat(dashboard): public legal pages (/terms /privacy /dpa) —
   DRAFT-marked`.** Plus **remove the fake-success waitlist form** (same
   concern: the public surface must not claim what the system does not do).
4. **B3 — `feat(dashboard): /pricing from the enforced matrix`.** Prices from
   the code; features only from the matrix; transaction fees and onboarding
   fees labelled "sold separately / not enforced yet"; the trial-vs-Starter
   generosity shown honestly, U60 flagged, not decided.
5. **B4 — `feat(auth): invite-code gate on registration` (PROTECTED → needs
   human approval).** New `invite_codes` table + migration, service in
   `packages/core/auth/`, `invite_code` field on `RegisterRequest`, uniform
   403 for absent/invalid/expired/redeemed (no enumeration oracle), race-safe
   single redemption, prod-startup guard (`INVITE_CODES_REQUIRED`).
6. **B5 — `feat(dashboard): /login`** — posts to `/api/v1/auth/login`; handles
   401 and the 409 multi-tenant-email case.
7. **B6 — `feat(dashboard): /signup`** — invite field, exact API contract
   (`^\\+?254[0-9]{9}$` phone, ≥12-char password), 409/422 error states,
   success copy that says the trial starts when a channel is connected (not
   at signup).
8. **B7 — `feat(dashboard): trial banner`** — renders the server's decision
   only: `pending` → "starts when you connect your channel"; `active` → days
   remaining; `expired`/`past_due` → upgrade CTA; API error → render nothing.
9. **B8 — `feat(seed): demo tenant`** — `scripts/seed_demo.py` + `make
   seed-demo` (env-guarded, **not** `make seed`), demo-only credentials
   documented.
10. **Gate** — one solo `make check` at the named SHA + the JS evidence pasted
    (the Python gate does not cover the frontend).

## Blast radius and rollback

- B0/B1: tooling + one test. Rollback = revert.
- B2/B3/B5/B6/B7/B8: additive public pages + one demo seed script. Rollback =
  revert the commit; no schema, no tenant data, no money path.
- B4: **the only schema change** (`invite_codes`) plus a check inside the
  register handler. Blast radius = every signup. Rollback = revert the
  migration + the handler change; the flag makes it switchable without a
  redeploy.

## Incentive review

- **Prospect**: gets a price they can check and a signup that cannot be
  silently abused; the trial copy tells the truth about when the clock starts.
- **Tenant owner**: an invite code means only the founder's prospects can
  register → no stranger can consume the tenant's WhatsApp number.
- **Founder**: issues one invite per prospect (and sees the trial start at
  connect, so an invite that never lands does not burn a trial); must still
  verify each M-Pesa code out of band (F2).
- **Abuser**: the obvious abuse is trial stacking — the invite code plus the
  one-trial-per-number process rule raise the cost from "one HTTP call" to
  "one human conversation". A stolen code is single-use (redeemed atomically).
- **Us (the platform)**: every claim we publish is a promise we must keep; the
  DRAFT banner on the legal pages is the price of not having a lawyer yet.

## Failure modes and detection

| Failure | Detection |
|---|---|
| A page ships with a broken link | the link-integrity guard (in `make check`) |
| The pricing page drifts from the code | the page renders prices imported from the same source as the API; a test asserts the numbers match `_PRICE_KES` |
| The legal page claims something untrue | DRAFT banner + every sentence checked against the code before publishing (no "we delete on request" while U51/U52 are open) |
| The invite gate can be bypassed | the check lives in the handler (server-side), the middleware allow-list for `/api/v1/auth/register` is untouched, and a test asserts a missing code is refused |
| A denied tenant sees nothing at all | the banner renders nothing on an API error and never computes a fallback |
| The JS toolchain cannot be verified | if `pnpm lint` / `tsc` / `next build` cannot run, the frontend commits are NOT made (S3 fallback) |

## Acceptance criteria (measurable)

1. `make check` EXIT=0 at a named SHA with a clean tree (real pasted output),
   plus `pnpm lint`, `pnpm typecheck` and `next build` output pasted (the
   Python gate does not cover the frontend).
2. The link-integrity guard is green and **no href on any public page is dead**.
3. `/pricing` shows the three code prices, only matrix-enforced features, and
   labels the unenforced ones.
4. `/terms`, `/privacy`, `/dpa` load and carry a visible "DRAFT — pending
   Kenyan data-protection lawyer review" banner.
5. `/login` posts to the real endpoint and renders the real error states.
6. `/signup` (only after B4 is approved) creates a `pending` trial and says
   the clock starts at channel connect.
7. The trial banner renders the server's decision only; on API error it renders
   nothing.
8. `make seed-demo` creates a demo tenant that is clearly marked demo.

## Docs to update

`docs/STATE.md`, `docs/MAP.md`, `docs/UNCERTAINTY.md` (U60 note, CI-JS finding,
link-guard note), `docs/LEARNINGS.md` (M1.6 + M1.5 calibration), this plan's
measured-results section.

## Measured result (2026-10-21)

**Human approval**: 2026-10-21, bundled with F3, the CI wiring and the U60
trial decision — the invite gate (B4, protected zone) was approved.

### Commits

| Commit | What |
|---|---|
| `c1d1c5f` | B0 — pnpm toolchain (`pnpm-lock.yaml`, `.eslintrc.json`) |
| `3953ab7` | B1 — the link-integrity guard (in `make check`) |
| `81a50a4` | B4 — the invite gate (table, service, route, script, 16 tests, ADR 0023) |
| `d475ee9` | the CI wiring (pnpm + Node in CI, honest timeouts) — approved |
| `7fc25d8` | B2/B3/B5/B6/B7 — the sales surface (pricing, legal, login, signup, banner) + the landing-page repair |
| `338d8d6` | the column-drift fix the sales-spine proof found (migration `0018`, the column guard, the `User.role` enum fix) |
| `921f2de` | the failure record for the column drift |

### What landed

- **B2** `/terms`, `/privacy`, `/dpa`: DRAFT-bannered, and they state the two
  open erasure gaps (U51, U52) instead of claiming a deletion the system
  cannot do. `/security` (not in the plan) lists what is enforced and what is
  not done yet.
- **B3** `/pricing` renders `apps/dashboard/src/lib/pricing.json`; a new
  in-gate test (`tests/architecture/test_pricing_page_matches_code.py`)
  compares that file with `_PRICE_KES` and the entitlement matrix, so a price
  or a feature change in the backend fails the gate until the page is
  updated. The unenforced costs are listed as sold separately.
- **B4** the invite gate: `invite_codes` is a platform-scoped table (no
  `tenant_id`, no RLS, no PII — ADR 0023), the redemption is one atomic
  `UPDATE` (two racing registrations cannot both take the last use), every
  failure is the same 403 (no oracle), the redemption is audited per tenant,
  and `INVITE_CODES_REQUIRED` must be on in production (the API refuses to
  boot otherwise).
- **B5/B6** `/login` and `/signup` post the real contracts (OAuth2 form
  login; `RegisterRequest` with the Kenyan phone format, a 12+ character
  password and the invite code) and render the real 403/409/422 states.
- **B7** the trial banner renders the server's decision only and renders
  **nothing** when the API errors.
- The landing page: 14 dead CTAs removed (it now links only to pages that
  exist), the fake waitlist form deleted, the absolute claims ("100% tenant
  isolation", "30 min", "Watch Demo") replaced with what the system does.
- The link guard's `xfail(strict=True)` markers are **removed**: the debt is
  paid, so the assertions are unconditional.

### B8 (the demo seed) — deferred, with a reason

`make seed-demo` was **not** built: the owner console (the thing a demo
tenant would be shown in) does not exist yet, so a seeded tenant would have
no visible surface. Seeding demo data before there is something to look at
is work for a demo nobody can see. It moves to the owner-console milestone.

### The unplanned finding: the green suite could not see a broken database

`scripts/prove_sales_spine.py` (a real registration against a database built
by `alembic upgrade head`) failed with `column users.full_name does not
exist`. The migrations had never created ten columns the ORM has: `tenants.slug`,
`users.full_name`, `users.phone` and seven agent-action `audit_log` columns.
The 915-test suite was green throughout, because **the test schema is built
from ORM metadata** — the tests never see the migrations' schema.

Fixed in `338d8d6`: migration `0018_missing_columns` adds the columns and the
indexes, `User.role` now persists the lowercase value (it wrote `OWNER` into a
Postgres enum that only accepts `owner`), and the new guard
`tests/integration/test_migration_column_drift.py` compares every ORM column
(name, type family, nullability) with a migrated scratch database — proven
red on 0017, green on 0018.

The record is `docs/failures/2026-10-21-column-drift-green-suite.md`.

### Evidence

- `pnpm lint` → "✔ No ESLint warnings or errors"
- `pnpm typecheck` → clean (`tsc --noEmit`)
- `pnpm build` → "✓ Compiled successfully", 11 routes
- `uv run pytest tests/architecture tests/auth tests/integration tests/billing tests/e2e`
  → 120 passed
- `scripts/prove_sales_spine.py` → "SALES SPINE OK" (403 without a code, 201
  with one, `status=pending plan=trial entitled=False`, the code cannot be
  reused, login agrees with the same decision)

### Acceptance criteria

| # | Criterion | Result |
|---|---|---|
| 1 | `make check` EXIT=0 at a named SHA, JS output pasted | See the measured gate in `docs/STATE.md` |
| 2 | no dead internal link | done (the guard is unconditional) |
| 3 | `/pricing` shows the code prices and only matrix-enforced features | done (data-driven + tested) |
| 4 | legal pages carry a DRAFT banner | done (`/terms`, `/privacy`, `/dpa`) |
| 5 | `/login` posts to the real endpoint and renders the real errors | done |
| 6 | `/signup` creates a pending trial and says the clock starts at connect | done (proof script) |
| 7 | the banner renders the server's decision only; nothing on an API error | done |
| 8 | `make seed-demo` creates a demo tenant | **deferred** (no surface to demo yet — see B8) |

## The skeptic wave (2026-10-21, after the first gate)

The skeptic attacked the wave and found two real defects and three untrue
claims. All of it is fixed; the record is in `docs/LEARNINGS.md` and
`docs/UNCERTAINTY.md`.

### Fixed

| Finding | Fix | Commit |
|---|---|---|
| The registration committed mid-request: a failure after that left a tenant, a subscription and a **spent** invite code behind, and the retry hit the 409 | the route flushes; the dependency commits; a failed registration now rolls back completely and the code is reusable | `8faac57` |
| A stranger creating a tenant was not audited (only the code redemption was) | the registration writes `auth.tenant_registered` in the same transaction | `8faac57` |
| `/security` claimed "a query without the right tenant context returns nothing" - but the API connects as the table owner, and a table owner bypasses the policies | the page says what is true; the role hardening is listed under "not done yet" (U77) | `ac2d7ed` |
| `/pricing` claimed "each line below is what the system actually enforces" - only the plan-state gate is enforced; the matrix is published | the page separates "enforced today" from "recorded on your plan, not switched on yet" (U80) | `ac2d7ed` |
| "A uniform 403, so the endpoint is not a code oracle" - the body is uniform, the status code is not | ADR 0023, the route comment and the metrics table now say exactly what is hidden; the rate limiter makes probing expensive | `6c1c5d3`, `0b7e2a3` |
| The drift guard could not see the enum-value mismatch (`OWNER` vs `owner`) | the guard now compares the values an enum column accepts | `8faac57` |
| The app role had a table-wide `UPDATE` on `invite_codes` | migration `0019` narrows it to the four redemption columns | `8faac57` |

### Open (not fixed here, with the reasons)

- **U77/U82 (HIGH)** - the API connects as the table owner, so the row-level
  security policies do not bite, and the public write path sets no tenant
  context. This is the next hardening job: a restricted role, forced RLS, and
  a tenant context on the registration path. It is a deployment change with a
  wide blast radius, so it gets its own wave - not a drive-by edit.
- **U80 (human)** - enforce the entitlement cells (a channel check on the
  Instagram path, a seat count, an API-access gate, an agent-level ceiling),
  or keep selling them as plan contents until they are enforced. The page
  says the truth in the meantime.
- **U79** - the refresh token lives in `localStorage`; the fix is an httpOnly
  cookie plus a CSP, which is a design change for the auth flow.
- **U78** - the rate limiter is per process and covers the auth paths only.
- **U81** - "one trial per business" is not enforced (only one owner account
  per email address is).

### What the plan did not do, and why

- **B8 `make seed-demo`** - deferred: there is no owner console, so a seeded
  tenant has no visible surface to demo. It moves to the owner-console
  milestone.
- **The 30-minute onboarding claim** - the pages do not make it any more
  (U74): the target is still unmeasured, and M2 measures it.
