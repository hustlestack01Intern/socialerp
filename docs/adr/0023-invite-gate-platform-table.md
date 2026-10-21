# ADR 0023: The invite gate lives in a platform-scoped table (no RLS)

## Status
Accepted (2026-10-21, human-approved as part of the M1.5 wave)

## Context
M1.5 adds an invite-code gate on self-serve registration
(`POST /api/v1/auth/register`): the founder issues one code per prospect, the
code is consumed on use, and self-serve signup without a code is refused
(403) when `INVITE_CODES_REQUIRED` is on. Without it, anybody on the internet
can start a trial (trial stacking, U68) and the "first cohort" is not a cohort.

The design question is where the code table lives. The obvious answer — a
tenant-scoped table with a `tenant_id` column and row-level security — does
not work: the code must be read **before** a tenant exists (that is the whole
point of the gate), and the redemption happens inside the registration
transaction for a tenant that was created milliseconds earlier.

The repository already faces this exact fork once, for the payment-reference
ledger (`billing_payment_references`), and settled it with a narrow,
compensating carve-out documented in
`docs/failures/2026-10-21-platform-ledger-vs-rls-guard.md`: a platform-scoped
table, no RLS, app-role grants narrowed, and a test that keeps the carve-out
from silently widening.

## Decision
`invite_codes` is a **platform-scoped** table:
- **no `tenant_id` column** — the code is not a tenant's row; it is a
  platform secret that predates the tenant. (The redemption is recorded in
  `redeemed_by_tenant_id`, a plain column, not a tenancy key: it is not used
  for isolation and is not covered by RLS.)
- **no row-level security** — RLS is a *tenant* isolation mechanism; there is no
  tenant to isolate from while a code is unredeemed.
- **no customer PII** — a code, a note ("Amina - cohort 1"), a timestamp and
  the redeeming tenant id. No phone number, no name, no message content.
- app-role grants: `SELECT, INSERT, UPDATE` — the app may issue a code and
  consume a use (the redemption is a conditional `UPDATE`, so it must be able
  to write), but it may not `DELETE` a code: a code is never silently
  destroyed, only deactivated.

The redemption is a **single atomic statement**:
```sql
UPDATE invite_codes
   SET uses = uses + 1, ...
 WHERE code = :code AND is_active AND uses < max_uses
   AND (expires_at IS NULL OR expires_at > now())
RETURNING *
```
One statement decides the winner, so two concurrent registrations cannot both
take the last use. The expiry is part of the same statement, not a pre-check,
so an expired code can never be redeemed even in a race.

**All failures are the same failure.** Unknown, expired, exhausted and disabled
codes raise one error with one message, and the HTTP layer answers every case
with the same 403 body. The endpoint must not be an oracle for "does this code
exist" (that would leak whether a code was ever issued).

The redemption is audited against the tenant that used it
(`auth.invite_redeemed` in the tenant-scoped `audit_log`), so the founder can
reconstruct which code paid for which tenant.

`INVITE_CODES_REQUIRED` must be **on in production**: the API refuses to boot
otherwise (same shape as the `TENANCY_MIDDLEWARE_ENABLED` guard, ADR 0023's
predecessor guard in `apps/api/main.py`).

## Consequences
- The first cohort is genuinely invited: a prospect cannot register without a
  code, and a code cannot be reused.
- The carve-out stays narrow and reviewed: one new platform table, with its
  own migration, its own grants and its own compensating tests
  (`tests/auth/test_invite_codes.py`).
- `tests/integration/test_migration_schema_drift.py` needs **no** change: its
  `_PLATFORM_TABLES` allowlist covers tables that *carry* a `tenant_id` column
  without RLS, and `invite_codes` has no such column, so it is not a tenant
  table and needs no RLS. The allowlist stays exactly one entry.
- A future table that wants a `tenant_id` column *and* no RLS must go through
  the same stop-rule (documented reasoning + compensating test) as
  `billing_payment_references`.

## Alternatives rejected
- **Tenant-scoped table with RLS**: impossible — the code must be readable
  before a tenant exists, and RLS would hide every code from the registration
  handler.
- **Codes in the tenants table** (`invite_code` column): one code per tenant,
  so a code cannot exist before the tenant; it also pollutes the tenant row
  with a secret.
- **Codes in Redis** (or a cache): the gate must be durable and auditable; a
  cache eviction would silently open registration to the internet.
- **Codes in the audit log**: the log is append-only and tenant-scoped, and it
  would make the code unqueryable in practice.
- **Allow enumeration (different 403 bodies for unknown vs exhausted)**: it
  turns the endpoint into a code oracle, which is a reconnaissance tool for
  anyone who guesses the alphabet.
- **Charge the code only after the tenant is committed** (two-phase): leaves a
  window in which N registrations can all pass the check for one use.
