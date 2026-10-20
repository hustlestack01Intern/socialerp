"""
Multi-tenancy support with Row-Level Security (RLS).

Provides TenantMixin for models, tenant context management, and RLS policy helpers.
All tenant-scoped queries MUST go through the tenant-aware session.
"""

import contextvars
import types
from typing import Any
from uuid import UUID

from socialerp.settings import get_settings
from sqlalchemy import Connection, ForeignKey, event, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, Mapper, declared_attr, mapped_column, relationship

from packages.core.models.core import Tenant

settings = get_settings()

__all__ = [
    "Tenant",
    "TenantAwareSession",
    "TenantMixin",
    "get_current_tenant_id",
    "reset_current_tenant_id",
    "set_current_tenant_id",
]

# Context variable for current tenant (set per request/task)
current_tenant_id: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "current_tenant_id", default=None,
)


def get_current_tenant_id() -> UUID | None:
    """Get the current tenant ID from context."""
    return current_tenant_id.get()


def set_current_tenant_id(tenant_id: UUID) -> contextvars.Token[UUID | None]:
    """Set the current tenant ID for the context."""
    return current_tenant_id.set(tenant_id)


def reset_current_tenant_id(token: contextvars.Token[UUID | None]) -> None:
    """Reset the current tenant ID to previous value."""
    current_tenant_id.reset(token)


class TenantMixin:
    """
    Mixin for tenant-scoped models.

    Adds tenant_id column and automatically applies RLS policies.
    All queries on models using this mixin MUST be executed within a tenant context.
    """

    @declared_attr.directive
    def tenant_id(cls) -> Mapped[UUID]:  # ruff: ignore[invalid-first-argument-name-for-method]
        return mapped_column(
            PG_UUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
            doc="Tenant this record belongs to",
        )

    @declared_attr.directive
    def tenant(cls) -> Mapped["Tenant"]:  # ruff: ignore[invalid-first-argument-name-for-method]
        return relationship("Tenant", lazy="raise")


# NOTE: `Tenant` is the canonical model from `packages.core.models.core`
# (imported above and re-exported) — see ADR 0008. Do not define a second
# `tenants` table here.


# RLS Policy SQL (run via Alembic migration)
RLS_POLICIES_SQL = """
-- Enable RLS on all tenant-scoped tables
-- This must be run after tables are created

-- Function to get current tenant_id from session variable
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid AS $$
    SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid;
$$ LANGUAGE sql STABLE;

-- Policy: users can only see their tenant's data
CREATE POLICY tenant_isolation_policy ON tenants
    USING (id = current_tenant_id());

-- This policy template will be applied to each tenant-scoped table via migration
-- Example for a table 'orders':
-- ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY tenant_isolation_policy ON orders
--     USING (tenant_id = current_tenant_id())
--     WITH CHECK (tenant_id = current_tenant_id());
"""


def generate_rls_policy(table_name: str) -> str:
    """Generate RLS policy SQL for a tenant-scoped table."""
    return f"""
ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_policy ON {table_name}
    USING (tenant_id = current_tenant_id())
    WITH CHECK (tenant_id = current_tenant_id());
"""


# Event listener to automatically set tenant_id on insert
@event.listens_for(TenantMixin, "before_insert", propagate=True)
def set_tenant_id_on_insert(mapper: Mapper[Any], connection: Connection, target: Any) -> None:
    """Automatically set tenant_id from context on insert."""
    tenant_id = get_current_tenant_id()
    if tenant_id is not None and not hasattr(target, "tenant_id"):
        target.tenant_id = tenant_id


class TenantAwareSession:
    """
    Wrapper for async session that enforces tenant context.

    Usage:
        async with TenantAwareSession(tenant_id) as session:
            # All queries automatically scoped to tenant
            result = await session.execute(select(Order))
    """

    def __init__(
        self,
        tenant_id: UUID,
        session_or_factory: AsyncSession | async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self._session_or_factory = session_or_factory
        self._session: AsyncSession | None = None
        self._token: contextvars.Token[UUID | None] | None = None

    async def __aenter__(self) -> AsyncSession:
        from socialerp.database import get_session_factory
        from sqlalchemy.ext.asyncio import AsyncSession

        if self._session_or_factory is None:
            factory = get_session_factory()
            session = factory()
        elif isinstance(self._session_or_factory, AsyncSession):
            # Session provided directly
            session = self._session_or_factory
        else:
            # Factory provided
            factory = self._session_or_factory
            session = factory()

        self._session = session
        self._token = set_current_tenant_id(self.tenant_id)

        # Set PostgreSQL session variable for RLS (don't start transaction if already in one)
        if not session.in_transaction():
            await session.begin()
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(self.tenant_id)},
        )

        return session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        session = self._session
        if session is not None:
            if exc_type:
                await session.rollback()
            else:
                await session.commit()
        token = self._token
        if token is not None:
            reset_current_tenant_id(token)
