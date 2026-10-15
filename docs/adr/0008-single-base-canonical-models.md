# ADR 0008: One SQLAlchemy `Base`; `packages.core` is the canonical model layer

- Status: Accepted (2026-10-14)
- Deciders: Chief Engineer (human approved Option D)
- Supersedes: informal "two generations" split

## Context

The repo contains two independently-built model generations:

1. **Generation 1 — `packages.core`** (`packages/core/models/*.py`, `packages/core/database.py`):
   full ERP domain (tenants, users, audit_log, api_keys, outbox_events, catalog, orders,
   stock, reservations, whatsapp). Seven Alembic migrations, Postgres `app` schema with
   `app.current_tenant_id()`, and a non-owner `socialerp_app` role for RLS. Uses
   `packages.core.models.base.Base` (no naming convention).
2. **Generation 2 — `socialerp` facade** (`packages/core/socialerp/*.py`): infrastructure-only
   duplicate (tenants, audit_log, outbox, idempotency_keys) with its own `Base` and a single
   Alembic migration. Also contains re-export shims for models/security/auth/repositories.

Problems this caused:

- **The app was broken end-to-end.** `apps/api/main.py` ran `socialerp.database.init_db`
  (created only the 4 gen-2 tables) while `apps/api/routes/{auth,catalog}.py` queried gen-1
  tables that were never created.
- **Two `Base` metadata objects** with colliding `tenants` / `audit_log` table names but
  different columns.
- **`apps/api/routes/auth.py` referenced a third shape** (`User.full_name`/`phone`,
  `Tenant.slug`) that matches neither generation → 201 mypy errors.
- **Tenant isolation was unproven:** the 5 RLS tests in `tests/tenancy/test_rls.py` were
  skipped ("RLS policy not working in test environment").

Evidence for the canonical direction: `tests/property/test_rls_support.py` and the root
migrations `alembic/versions/0001_initial.py` + `0007_metadata_json_outbox_rls.py` all target
the gen-1 schema (`tenants(owner_phone, owner_email)`, `users`, `audit_log(user_id, action,
resource, ...)`, `app` schema, `socialerp_app` role). The decision-scientist 7+1 analysis also
selected this option (weighted +3.45; disagreement HIGH, so reviewed by the human).

## Decision

There is **exactly one `Base`**: `packages.core.models.base.Base`.

1. `socialerp.database.Base` re-exports the canonical `Base` (single metadata).
2. `socialerp.tenancy.Tenant` and `socialerp.audit.AuditLog` become re-exports of the
   canonical `packages.core.models.core` classes; their duplicate ORM definitions are removed.
3. The canonical `AuditLog` is extended with the fields the constitution requires for agent
   audit (`actor_type, actor_id, decision, rule_ref, confidence, correlation_id`) in addition
   to the existing CRUD fields — a union, not a replacement.
4. `User` gains nullable `full_name` and `phone` (product intent: staff/owner profiles).
5. `Tenant` gains a nullable, unique `slug` (product intent: onboarding/URLs); existing
   `owner_phone`/`owner_email` are the primary contact fields.
6. Gen-2-only tables `outbox` (distinct from gen-1 `outbox_events`) and `idempotency_keys`
   remain, but register on the single canonical `Base`.
7. The duplicate Alembic tree at `packages/core/alembic/` is retired; `alembic/` is the one
   migration tree. A new revision adds the reconciled columns/tables and the RLS role.

## Consequences

- One `init_db` creates the whole schema; the app boots and serves auth + catalog.
- A single metadata means `create_all` and migrations agree.
- Cost: some nullable/redundant columns (union approach) to avoid a destructive migration.
  Tightening (dropping `outbox_events` or the duplicated audit columns) is deferred to a
  later ADR once no caller depends on them.

## Follow-ups

- Tighten the audit/outbox schema (remove the union) in a dedicated migration.
- Add `FORCE ROW LEVEL SECURITY` / `socialerp_app` role wiring to the local test harness so
  the RLS tests run instead of skipping.
- Delete the `packages/core/alembic/` tree after the next migration is validated.
