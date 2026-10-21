# PLAN: the security wave (U71, U77/U82, U78, U79)

The enforcement wave is done and measured (`7c28a79`: 1018 passed, evals 8/8).
This plan closes the four honest-but-unsafe items the ledger still carries, in
the order of blast radius: smallest and safest first, the database's security
posture last (it is the one that can break everything if it is wrong).

- **Goal**: the rate limiter is shared across workers, the refresh token is no
  longer in `localStorage`, the migration guard sees the things it could not
  see (indexes, server defaults, and policies that do not actually use the
  tenant setting), and the database - not just the application - enforces
  tenant isolation.
- **Non-goals**: no change to the agent's behaviour, the pricing, the payment
  path, or the product surface. No new dependencies (Redis and `redis-py` are
  already in `pyproject.toml` and the compose file).

## Classification
- Type: hardening (security) + tooling.
- Risk: **high** for U77/U82 (it changes how the application connects to the
  database and can break every request), **medium** for U78 and U79, **low**
  for U71.
- Complexity: four steps, each with its own tests and one gate run at the end.

## Assumptions (confidence in brackets)
- A1 The API and the worker run as one logical application but may be more than
  one process, so a per-process limiter is not a limiter. [0.95]
- A2 Redis is already a dependency and already running in the compose stack
  (`redis` + `redis-test` services; `redis` was reachable on localhost:6379
  during this session). [0.95 — checked]
- A3 The dashboard calls the API through the Next.js rewrite on its own
  origin, so a `SameSite=Lax`, `HttpOnly` cookie is sent on the refresh call
  without any CORS change. [0.9 — checked in `apps/dashboard/src/lib/api.ts`]
- A4 Postgres 16 is the deployed version, so `BYPASSRLS` and
  `SECURITY DEFINER` exist. [0.9 — compose image]
- A5 The application connects as the table owner today, which is why RLS is
  advisory rather than enforced. [0.95 — checked: `DATABASE_URL` uses
  `socialerp:socialerp`, the owner of every table]

## Derivation
1. **A rate limit that lives in one process is not a limit** (U78). The
   counter must live where all the workers can see it: Redis. The in-process
   counter stays as the fallback when Redis is unreachable, and the fallback
   must *say* it is the fallback (no pretending).
2. **A token in `localStorage` is a token any script on the page can read**
   (U79). The refresh token moves into an `HttpOnly` cookie that JavaScript
   cannot touch; the access token stays where it is (short-lived, and the
   residual risk is recorded).
3. **A guard that cannot see a drift is not a guard** (U71). The migration
   guard compares columns; it does not compare indexes, server defaults, or
   whether a policy actually uses the tenant setting. A policy that exists but
   does not reference `app.current_tenant_id()` isolates nothing.
4. **RLS that the table owner can switch off is not isolation** (U77/U82).
   The application must connect as a role that is *not* the owner, and the
   tables must be `FORCE ROW LEVEL SECURITY`, so the policies apply to the
   application's own writes. The platform-level paths (login by email,
   registration, webhook lookups, the tracking page) need a narrow, explicit
   carve-out - a `SECURITY DEFINER` function owned by the owner, never a
   blanket `BYPASSRLS` for the application role.

## Options considered
- **U78** — (a) keep the in-process counter; (b) a Postgres table as the
  counter; (c) Redis (chosen). (b) puts a write on the hot path of the
  database and adds a table to the RLS story; Redis is already deployed and
  is the standard answer.
- **U79** — (a) keep both tokens in `localStorage`; (b) move the refresh
  token to an `HttpOnly` cookie, keep the access token in `localStorage`
  (chosen); (c) move both to cookies and make the API cookie-authenticated.
  (c) is the strongest, but it changes every route's dependency and the
  dashboard's fetch layer - too much blast radius for this wave, and it is
  recorded as the next step.
- **U77/U82** — (a) `FORCE RLS` only, with the app still connecting as the
  owner: the policies then apply to the app's own writes, but the platform
  paths need `SECURITY DEFINER` helpers anyway, so this is half the work with
  all the risk; (b) a separate application role with `FORCE RLS` on the
  tenant tables and `SECURITY DEFINER` functions for the platform paths
  (chosen); (c) `BYPASSRLS` for the application role: rejected outright, that
  is the hole in a wider costume.

## Steps
1. **U78** — the limiter gets a store: `InMemoryRateLimitStore` (today's
   behaviour) and `RedisRateLimitStore` (a Lua script: `INCR` + `EXPIRE` +
   `TTL`, atomic). The app builds the Redis store when `REDIS_URL` is set and
   Redis answers, and the in-process one otherwise, logging which it is.
2. **U79** — the auth routes set an `HttpOnly`, `SameSite=Lax` cookie scoped
   to `/api/v1/auth`; the refresh route reads the cookie when the body has no
   token; the logout route clears it; the dashboard stops writing the refresh
   token to `localStorage`.
3. **U71** — a new drift guard (a new file; the existing guards are not
   touched): every ORM index exists in the migrated database with the same
   columns and uniqueness; every column with a `server_default` has it in the
   database; every tenant table's policy *uses* `app.current_tenant_id()`; the
   `app.current_tenant_id()` function returns the setting that is set.
4. **U77/U82** — the application role, `FORCE RLS` on the tenant tables, and
   the `SECURITY DEFINER` platform functions. The application connects as the
   new role; the platform paths call the functions.

## Blast radius
- U78: the auth endpoints' behaviour (429 after the limit) - unchanged, except
  that the limit is now global rather than per process.
- U79: the login, register, refresh and logout responses gain a `Set-Cookie`
  header; the dashboard's session helper changes.
- U71: a new test file; it fails only if the migrations and the ORM disagree.
- U77/U82: **every** database session in the application. A wrong grant means
  a 500 on every route; a missing `SECURITY DEFINER` function means the login
  cannot find its user. This is the step that must not be rushed.

## Failure modes
- Redis is down at startup → the limiter falls back to the in-process counter
  and says so. The limiter must never take the API down.
- The cookie is not sent by the browser → the refresh fails and the owner is
  asked to log in again. The body-token path stays for non-browser clients.
- The new role lacks a grant → a 500 on one route, caught by the gate.
- `FORCE RLS` blocks the platform paths → the login breaks, caught by the
  auth tests and the sales-spine proof.

## Acceptance criteria
- `make check` green (ruff, eslint, mypy --strict, tsc, pytest, evals).
- The rate limiter's counters are shared: two clients, one Redis, one budget.
- The refresh token is not in `localStorage` and is not readable by script.
- The drift guard fails when a policy stops using the tenant setting (proven
  by a deliberate break, then reverted).
- The application connects as a role that is not the table owner, and a
  cross-tenant read through that role returns nothing.
