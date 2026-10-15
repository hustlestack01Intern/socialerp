# PLAN: M1 - Multi-tenant Core, Auth, RLS, Audit Log, Tenant Isolation Tests

## Goal and Non-Goals
**Goal:** Implement strict multi-tenant isolation with PostgreSQL Row-Level Security, authentication/authorization, and append-only audit logging. All cross-tenant access tests must fail closed.

**Non-Goals:**
- No business logic (orders, payments, inventory, agents)
- No WhatsApp/Daraja integration (handled in M2/M4)
- No owner dashboard UI
- No onboarding wizard

---

## Classification
- **Type:** Infrastructure
- **Risk:** High
- **Complexity:** High

---

## Assumptions with Confidence (copied to /docs/UNCERTAINTY.md)

| Assumption | Confidence | Why | How to Verify |
|------------|------------|-----|---------------|
| PostgreSQL 16 RLS policies work as documented | 0.95 | Well-tested feature | Run RLS test suite |
| `asyncpg` + SQLAlchemy 2.0 async support RLS `SET LOCAL` | 0.9 | Standard pattern | Test `SET LOCAL app.current_tenant_id` |
| JWT with short expiry + refresh tokens sufficient for auth | 0.85 | Industry standard | Integration tests |
| `tenant_id` as UUID (not composite key) works for RLS | 0.9 | Simpler than composite | RLS policy tests |
| Alembic migrations can create RLS policies | 0.85 | Standard practice | Migration test |
| Tenant context propagation via middleware works | 0.9 | FastAPI middleware pattern | Unit + integration tests |

---

## Derivation: First-Principles Reasoning and Invariants

### Invariant 1: Tenant Isolation is Absolute
- **Every** table with tenant data MUST have RLS policy
- **Every** query MUST include `tenant_id` (enforced by RLS, not application)
- Cross-tenant access returns **zero rows** (fail-closed), never errors
- `tenant_id` is immutable once set on a row

### Invariant 2: Authentication is Stateless and Scoped
- JWT access tokens: 15 min expiry, contain `tenant_id`, `user_id`, `roles`
- Refresh tokens: 7 day expiry, rotated on use, stored hashed
- All endpoints require valid access token (except `/health`, `/auth/login`)
- Roles: `owner`, `staff` (rider added later)

### Invariant 3: Audit Log is Append-Only and Complete
- Every agent action logs `AgentActionLogged` event (from M0)
- Every mutating API call logs audit entry
- Audit entries include: `tenant_id`, `user_id`, `action`, `resource`, `before`, `after`, `timestamp`
- Audit table has **no UPDATE/DELETE** permissions for application role

### Invariant 4: Tenant Context is Propagated Automatically
- Middleware extracts JWT → sets `SET LOCAL app.current_tenant_id = '...'`
- All database sessions use this context for RLS
- Background jobs receive `tenant_id` explicitly in job payload

---

## Options Considered (at least 3 candidates)

### Option A: RLS + Middleware Context (Chosen)
- PostgreSQL RLS policies on all tenant tables
- FastAPI middleware sets `SET LOCAL app.current_tenant_id` per request
- `tenant_id` column on every table, indexed, part of composite PKs where needed
- Application role has `SELECT/INSERT/UPDATE/DELETE` but **no BYPASSRLS**

**Why:** Enforces isolation at database level (defense in depth); matches SPEC §2 "Postgres row-level security on every tenant table plus tenant_id in every query"; constitution requires "tests that attempt cross-tenant access"

### Option B: Application-Level Filtering Only
- Add `tenant_id` filter to every query in code
- No RLS policies

**Why rejected:** Single point of failure; missed filter = cross-tenant leak; violates SPEC §2

### Option C: Schema-Per-Tenant
- Separate PostgreSQL schema per tenant
- Connection pool per tenant

**Why rejected:** Operational complexity (migrations, backups, connection pooling); doesn't scale to 100+ tenants; violates "modular monolith" architecture

### Option D: Row-Level Security with Session Variables + Row-Level Triggers
- Use `SET LOCAL` + triggers to auto-populate `tenant_id` on INSERT

**Why rejected:** Triggers add complexity and can be bypassed; explicit `tenant_id` in INSERT is clearer and testable

---

## Steps (ordered, each a small commit with tests)

### Step M1.1: Database Schema & Alembic Setup
- Create initial migration with:
  - `tenants` table (id, name, owner_phone, owner_email, settings, created_at)
  - `users` table (id, tenant_id, email, password_hash, role, is_active, created_at)
  - `audit_log` table (id, tenant_id, user_id, action, resource, resource_id, before_json, after_json, ip_address, user_agent, created_at)
  - `api_keys` table (id, tenant_id, name, key_hash, scopes, expires_at, created_at)
- Enable RLS on all tables
- Create RLS policies:
  - `tenants`: `tenant_id = current_setting('app.current_tenant_id')::uuid` (owners see own)
  - `users`: same
  - `audit_log`: same (append-only via policy)
  - `api_keys`: same
- Create `app` schema for `current_tenant_id` setting
- Grant `USAGE` on `app` schema to application role

**Tests:** Migration applies cleanly; RLS policies exist; `SHOW rls` returns on

### Step M1.2: Tenant & User Domain Models
- `packages/core/domain/tenant.py`: `Tenant`, `TenantSettings` (pydantic models)
- `packages/core/domain/user.py`: `User`, `UserRole` enum
- `packages/core/domain/auth.py`: `TokenPair`, `TokenPayload`, `ApiKey`
- SQLAlchemy models in `packages/core/models/` (new folder)
- Repository pattern: `TenantRepository`, `UserRepository`

**Tests:** Property tests for model validation; repository CRUD with tenant isolation

### Step M1.3: Authentication Module
- `packages/core/auth/`:
  - `password.py`: bcrypt hash/verify (from M0 security)
  - `jwt.py`: encode/decode access + refresh tokens
  - `tokens.py`: `TokenManager` (issue, refresh, revoke, blacklist)
  - `api_keys.py`: generate, hash, validate API keys
  - `dependencies.py`: FastAPI `Depends` for `get_current_user`, `get_current_tenant`, `require_role`
- Login endpoint: `POST /auth/login` → returns access + refresh tokens
- Refresh endpoint: `POST /auth/refresh` → rotates refresh token
- Logout endpoint: `POST /auth/logout` → blacklists access token

**Tests:** Unit tests for token encoding/decoding; integration tests for login flow; token expiry; refresh rotation; role enforcement

### Step M1.4: Tenant Context Middleware
- `packages/core/middleware/tenant_context.py`:
  - Extracts JWT from `Authorization: Bearer <token>`
  - Validates token, extracts `tenant_id`, `user_id`, `roles`
  - Executes `SET LOCAL app.current_tenant_id = '<tenant_id>'` on db session
  - Sets `request.state.tenant_id`, `request.state.user_id`, `request.state.roles`
  - Handles missing/invalid/expired tokens → 401
- Register middleware in FastAPI app

**Tests:** Middleware sets context correctly; RLS blocks cross-tenant queries; expired token returns 401

### Step M1.5: Audit Logging
- `packages/core/audit/`:
  - `logger.py`: `AuditLogger` with `log_action(tenant_id, user_id, action, resource, before, after)`
  - Uses `OutboxEvent` pattern from M0 (writes to `audit_log` table in same transaction)
  - `models.py`: SQLAlchemy `AuditLog` model
  - Middleware integration: auto-logs all mutating requests (POST/PATCH/DELETE)
- `AgentActionLogged` events from M0 also write to audit log via outbox

**Tests:** Audit entries created for API calls; `AgentActionLogged` persists; immutable (no UPDATE/DELETE)

### Step M1.6: Tenant Isolation Tests (Critical)
- `tests/integration/test_tenant_isolation.py`:
  - Create 2 tenants, users, data
  - Authenticate as tenant A → verify can read/write own data
  - Authenticate as tenant A → attempt to read tenant B data → **assert zero rows returned**
  - Test all tables: `tenants`, `users`, `audit_log`, `api_keys`
  - Test concurrent access: 10 parallel requests from tenant A, tenant B
  - Test raw SQL injection attempts with different `tenant_id`
  - Test background job context propagation
- Property-based tests for RLS policies (hypothesis)

**Tests:** All cross-tenant access tests **must fail closed** (return 0 rows, not error)

### Step M1.7: RLS Policy Tests
- `tests/property/test_rls_policies.py`:
  - Verify RLS enabled on all tenant tables
  - Verify policies use `current_setting('app.current_tenant_id')`
  - Test `INSERT` sets `tenant_id` correctly
  - Test `UPDATE` cannot change `tenant_id`
  - Test `DELETE` only affects own tenant
  - Test `SELECT` only returns own tenant

**Tests:** Property tests with multiple tenants, concurrent sessions

### Step M1.8: Documentation & ADRs
- Create `/docs/adr/0005-rls-policies.md` (RLS policy design)
- Create `/docs/adr/0006-auth-design.md` (JWT, refresh, API keys)
- Update `/docs/MAP.md` with new modules
- Update `/docs/UNCERTAINTY.md` with M1 assumptions
- Update `/docs/STATE.md` with M1 done

---

## Blast Radius and Rollback
- **Blast radius:** Database schema, auth, all future queries. No existing business logic to break.
- **Rollback:** `alembic downgrade -1` for migration; git revert for code.
- **Verification:** Each step's tests pass before next; M1.6 isolation tests are gate for M1 completion.

---

## Incentive Review

| Stakeholder | Gains | Abuse Risk | Mitigation |
|-------------|-------|------------|------------|
| **Tenants (owners)** | Data isolation guarantee | None | RLS at DB level |
| **Attackers** | Cross-tenant data access | High | RLS + tests prove fail-closed |
| **Engineering** | Clear isolation boundary | Skipping middleware | Integration tests require middleware |
| **Compliance (DPA)** | Audit trail | Incomplete logs | Auto-logging middleware + agent events |

---

## Failure Modes and Detection

| Failure Mode | Detection | Response |
|--------------|-----------|----------|
| RLS policy missing on new table | CI test: `information_schema.tables` vs `pg_policies` | Add policy in same migration as table |
| Middleware not setting context | Integration test: cross-tenant query returns data | Fix middleware registration order |
| JWT `tenant_id` tampered | Token signature verification + RLS mismatch | Token invalid → 401; RLS blocks anyway |
| Audit log missing entries | Test: count audit entries vs mutating requests | Fix middleware/logger |
| Migration fails on RLS | CI runs `alembic upgrade head` on clean DB | Fix migration SQL |
| Performance: RLS overhead | Load test in M6 | Index `tenant_id`; monitor query plans |

---

## Acceptance Criteria (Measurable)
1. **Database**: `alembic upgrade head` creates all tables with RLS enabled
2. **RLS**: `SELECT * FROM users` returns 0 rows when `app.current_tenant_id` not set
3. **Auth**: `POST /auth/login` returns valid JWT pair; `GET /api/test` with JWT returns 200
4. **Roles**: `require_role("owner")` returns 403 for `staff` user
5. **Audit**: Every POST/PATCH/DELETE creates `audit_log` entry with correct `tenant_id`, `user_id`
6. **Agent Events**: `AgentActionLogged` persists to `audit_log` via outbox
7. **Isolation Tests**: All cross-tenant access tests return 0 rows (fail-closed)
8. **Property Tests**: RLS policy tests pass with 100+ generated scenarios
9. **CI**: `make check` passes including new tests

---

## Docs to Update
- `/docs/STATE.md` — M1 done, next = M2
- `/docs/MAP.md` — Add `packages/core/models`, `packages/core/auth`, `packages/core/audit`, `packages/core/middleware`
- `/docs/UNCERTAINTY.md` — M1 assumptions
- `/docs/adr/0004-m1-multi-tenant-core.md` (this plan)
- `/docs/adr/0005-rls-policies.md`, `/docs/adr/0006-auth-design.md`

---

## Open Questions for Human (max 5)

1. **Initial Admin User**: Should M1 include a bootstrap script to create the first tenant + owner user, or defer to onboarding (M9)?
2. **Password Policy**: Minimum length, complexity requirements? (Default: 12 chars, no complexity)
3. **API Key Scopes**: What scopes needed for M1? (Default: `read`, `write`, `admin`)
4. **Session Storage**: Blacklisted tokens in Redis (needs Redis) or in Postgres? (Postgres for M1, Redis later)
5. **RLS Performance Baseline**: Run EXPLAIN ANALYZE on sample queries to establish baseline for M6?

---

**Ready for your "go" to begin M1 implementation.**