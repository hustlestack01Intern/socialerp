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
- No CI/Makefile edits (CI gate definitions = protected zone; reported, not
  self-edited).
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
