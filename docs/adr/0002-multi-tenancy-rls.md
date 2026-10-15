# ADR 0002: Multi-Tenancy with Row-Level Security

**Status**: Accepted
**Date**: 2024-01-15
**Deciders**: Chief Engineer

---

## Context

SocialERP is a multi-tenant SaaS where each tenant is a Kenyan business. Cross-tenant data leaks are a release blocker. We need bulletproof isolation at every layer.

---

## Decision

**Primary**: PostgreSQL Row-Level Security (RLS) on every tenant-scoped table.

**Defense in Depth**: Application-level `TenantMixin` with context variable.

---

## Implementation

### Database Layer (Enforced)

```sql
-- Function to get current tenant from session variable
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid AS $$
    SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

-- Enable RLS on table
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

-- Policy: users only see their tenant's data
CREATE POLICY tenant_isolation ON orders
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());
```

### Application Layer (Defense in Depth)

```python
# Context variable for current tenant
current_tenant_id: contextvars.ContextVar[Optional[UUID]] = ContextVar(
    "current_tenant_id", default=None
)


# TenantAwareSession sets PostgreSQL session variable
class TenantAwareSession:
    async def __aenter__(self):
        self._token = set_current_tenant_id(self.tenant_id)
        await self._session.execute(f"SET LOCAL app.current_tenant_id = '{self.tenant_id}'")
        return self._session
```

### TenantMixin for Models

```python
class TenantMixin:
    @declared_attr.directive
    def tenant_id(cls) -> Mapped[UUID]:
        return mapped_column(PG_UUID, ForeignKey("tenants.id"), nullable=False, index=True)
```

---

## Rules

1. **Every tenant-scoped table MUST have `tenant_id` column with FK to `tenants.id`**
2. **RLS policy MUST be created in migration for every tenant-scoped table**
3. **All queries MUST use `TenantAwareSession` or explicit `tenant_id` filter**
4. **Cross-tenant queries are FORBIDDEN except for platform admin (separate auth domain)**
5. **Integration tests MUST verify isolation** (see `tests/tenancy/test_rls.py`)

---

## Consequences

### Positive
- **Mathematically impossible** to leak data via SQL (enforced by PostgreSQL)
- Works even with raw SQL, ORM bugs, or future developers forgetting filters
- Performance: RLS policies use indexes efficiently
- Auditability: Every row tied to tenant

### Negative
- Slight migration complexity (RLS policies per table)
- Platform admin needs separate mechanism (bypass RLS or separate connection)
- Session variable must be set on every connection (handled by `TenantAwareSession`)

---

## Testing Requirements

```python
# tests/tenancy/test_rls.py
async def test_cross_tenant_isolation(db_session, tenant_a, tenant_b):
    # Create data as tenant_a
    async with TenantAwareSession(tenant_a.id, db_session) as s:
        s.add(Order(tenant_id=tenant_a.id, ...))
        await s.commit()

    # Query as tenant_b - MUST return 0 rows
    async with TenantAwareSession(tenant_b.id, db_session) as s:
        result = await s.execute(select(Order))
        assert result.scalars().all() == []  # RLS blocks access
```

---

## Related
- ADR 0001: Stack and Architecture
- ADR 0006: Order State Machine (tenant-scoped)