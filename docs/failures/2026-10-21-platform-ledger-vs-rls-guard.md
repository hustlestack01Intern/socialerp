# Failure: schema-drift RLS guard vs the approved platform-scoped billing ledger (2026-10-21)

**Expected**: full `uv run pytest` green after the F2 commit (`4c86c16`).
**What happened**: 2 failures in `tests/integration/test_migration_schema_drift.py`
— `test_tenant_tables_have_rls_enabled` and `test_every_tenant_table_has_a_policy`
report `billing_payment_references` as a tenant table without RLS/policy.

**What I believed**: the F2 design (human-approved 2026-10-21, plan
`docs/plans/2026-10-21-money-path-f0-f2.md` §Derivation/Options) makes the
M-Pesa reference ledger **platform-scoped on purpose** — cross-tenant UNIQUE
dedupe of payment references is the invariant, and the row carries no PII.
The drift guard derives "tenant table" as *any ORM table with a `tenant_id`
column*, so it flags the ledger.

**Which belief was wrong/unverified**: I did not check the drift guard's
heuristic before adding a table with `tenant_id` and no RLS. The guard is
correct for its own definition; the definition is over-broad for this one
deliberate exception.

**Why not silently edit the test**: CONSTITUTION §4 — editing a gate to make
something pass is a stop-rule; the drift tests are tenant-isolation tests
(protected zone). This document plus the explicit disclosure in the commit
body and to the human/skeptic is the required accounting.

**Next strategy (chosen)**: keep the guard's strength for every other table;
subtract a *narrow, commented allowlist* (`_PLATFORM_TABLES`) containing only
this ledger; add a compensating test asserting the ledger's own invariant
(`UNIQUE(reference)`, SELECT/INSERT-only grants, no RLS) so the exemption
cannot rot into a general loophole. Alternative designs rejected: permissive
RLS theater (`USING (true)`) is dishonest; SECURITY DEFINER dedupe functions
add complexity without benefit at pilot scale.
