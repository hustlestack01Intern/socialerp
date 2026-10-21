# 2026-10-21 — The green suite could not see a broken database (column drift)

**Protocol**: CONSTITUTION §4 (reasoning-failure) — plan prediction vs tool result disagree.

## What was expected
M1.5 shipped the invite gate and the sales surface on top of a gate that was
measured green on 2026-10-21: **915 passed**, ruff clean, mypy clean, evals
8/8. The plan assumed the platform underneath was sound, and the new pages
were expected to be the only thing that could be wrong.

## What happened (measured this session)
The dashboard is browser code, so the only honest check of `/signup` is a
real HTTP call against a real database. `scripts/prove_sales_spine.py` did
exactly that, against a database built by `alembic upgrade head`:

```
registration without a code -> HTTP 403     (the gate works)
registration with the code  -> HTTP 500     "column users.full_name does not exist"
```

The migrated database was missing **ten columns the ORM has always had**:

| Table | Missing |
|---|---|
| `users` | `full_name`, `phone` |
| `tenants` | `slug` |
| `audit_log` | `actor_type`, `actor_id`, `payload`, `decision`, `rule_ref`, `confidence`, `correlation_id` |

and `audit_log.resource` was `NOT NULL` in the migration while the ORM
allows NULL. So on a migrated database:

- **registration could not work at all** (it writes `tenants.slug`,
  `users.full_name`, `users.phone`);
- **the agent-action audit trail could not be written** - the fields that
  record *who* acted, *what was decided* and *which rule allowed it*
  (ADR 0008, and a constitution requirement) did not exist;
- a second crash followed: `User.role` used `Enum(UserRole)` without
  `values_callable`, so the ORM wrote `OWNER` while the migration's
  Postgres enum only accepts `owner`.

## What I believed (and why it was wrong)
1. "The gate is green, so the platform works." The gate was green **and the
   production database was broken**. The reason: the test schema is built
   from **ORM metadata** (`Base.metadata.create_all`), so the tests exercise
   the ORM's idea of the schema, never the migrations' idea of it. The
   migration chain was verified only for *table existence* and *RLS*
   (`tests/integration/test_migration_schema_drift.py`), never for columns.
2. "The ORM is the schema." It is the schema **for the tests** and a
   different schema **for production**. The two had diverged for months
   (the missing columns predate this session's work).

The green gate was not lying: it measured exactly what it measures. It was
measuring the wrong thing. The failure is not "the tests were bad" - the
tests were green and correct - it is that **the gate had no assertion about
the thing that was broken**.

## What the fix is
- `alembic/versions/0018_missing_columns.py` adds the ten columns, the five
  indexes and relaxes `audit_log.resource`.
- `User.role` now persists the lowercase value (`values_callable=enum_values`),
  matching the migration's enum labels and its own `server_default`.
- `tests/integration/test_migration_column_drift.py` migrates a scratch
  database and compares every ORM column (name, type family, nullability)
  with the schema. Proven **red** on 0017, **green** on 0018.

## Next different strategy (adopted)
- **A migrated database is a first-class test target.** Any change that
  touches the schema gets proven against `alembic upgrade head`, not only
  against the metadata-built test schema.
- **A browser page is proven by a real request.** The sales surface is
  checked by `scripts/prove_sales_spine.py` against a running API and a
  migrated database, because "the page compiles" is not evidence that the
  flow works.
- **The gate is only as good as its assertions.** Where a class of bug is
  invisible to the current gate, the gate gets a new assertion (here: column
  drift) rather than a discipline note.
