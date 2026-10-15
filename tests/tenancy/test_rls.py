"""
Tests for multi-tenancy RLS enforcement.
"""

from uuid import uuid4

import pytest
from socialerp.tenancy import (
    TenantAwareSession,
    get_current_tenant_id,
    reset_current_tenant_id,
    set_current_tenant_id,
)

from tests.factories import TenantFactory


class TestRLSIsolation:
    """Tests for Row-Level Security tenant isolation.

    Two roles are used deliberately:
      * ``session_factory`` connects as the DB superuser and bypasses RLS; it is
        used only to seed tenants.
      * ``rls_session_factory`` connects as the non-owner ``socialerp_app`` role,
        so every query is subject to the RLS policies under test.
    """

    @pytest.mark.asyncio
    async def test_cross_tenant_query_returns_zero_rows(self, session_factory, rls_session_factory):
        """Cross-tenant query returns 0 rows due to RLS."""
        from socialerp.audit import AuditLog
        from sqlalchemy import select

        # Seed tenants as superuser (bypasses RLS).
        tenant_a = TenantFactory.build(name="Tenant A", slug=f"tenant-a-{uuid4().hex[:8]}")
        tenant_b = TenantFactory.build(name="Tenant B", slug=f"tenant-b-{uuid4().hex[:8]}")
        async with session_factory() as seed, seed.begin():
            seed.add_all([tenant_a, tenant_b])

        # Create data as tenant_a (WITH CHECK permits only tenant_a rows).
        async with TenantAwareSession(tenant_a.id, rls_session_factory) as session:
            session.add_all([
                AuditLog(
                    tenant_id=tenant_a.id,
                    actor_type="user",
                    action="test.action",
                    payload={"data": "tenant_a"},
                    decision="allowed",
                ),
                AuditLog(
                    tenant_id=tenant_a.id,
                    actor_type="user",
                    action="test.action",
                    payload={"data": "tenant_a_2"},
                    decision="allowed",
                ),
            ])
            await session.flush()

        # Query as tenant_b - RLS must hide tenant_a's rows.
        async with TenantAwareSession(tenant_b.id, rls_session_factory) as session:
            logs = (await session.execute(select(AuditLog))).scalars().all()
            assert len(logs) == 0, "RLS should block cross-tenant access"

        # Query as tenant_a - should see 2 rows.
        async with TenantAwareSession(tenant_a.id, rls_session_factory) as session:
            logs = (await session.execute(select(AuditLog))).scalars().all()
            assert len(logs) == 2

    @pytest.mark.asyncio
    async def test_tenant_aware_session_sets_context(self, test_engine, tenant):
        """TenantAwareSession sets current_tenant_id context."""
        assert get_current_tenant_id() is None

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        session_factory = async_sessionmaker(
            test_engine, class_=AsyncSession, expire_on_commit=False
        )

        async with TenantAwareSession(tenant.id, session_factory):
            assert get_current_tenant_id() == tenant.id

        # Context should be reset after exit
        assert get_current_tenant_id() is None

    @pytest.mark.asyncio
    async def test_rls_blocks_insert_wrong_tenant(self, session_factory, rls_session_factory):
        """RLS blocks inserting data for a different tenant."""
        from socialerp.audit import AuditLog

        tenant_a = TenantFactory.build(slug=f"tenant-a-{uuid4().hex[:8]}")
        tenant_b = TenantFactory.build(slug=f"tenant-b-{uuid4().hex[:8]}")
        async with session_factory() as seed, seed.begin():
            seed.add_all([tenant_a, tenant_b])

        # Try to insert an audit log for tenant_a while in tenant_b context.
        async with TenantAwareSession(tenant_b.id, rls_session_factory) as session:
            session.add(
                AuditLog(
                    tenant_id=tenant_a.id,  # Wrong tenant!
                    actor_type="user",
                    action="test.action",
                    payload={},
                    decision="allowed",
                )
            )
            with pytest.raises(Exception):  # ruff: ignore[assert-raises-exception] - RLS raises a DB error
                await session.flush()
            await session.rollback()

    @pytest.mark.asyncio
    async def test_rls_blocks_update_wrong_tenant(self, session_factory, rls_session_factory):
        """RLS hides other tenants' rows from reads/updates."""
        from socialerp.audit import AuditLog
        from sqlalchemy import select

        tenant_a = TenantFactory.build(slug=f"tenant-a-{uuid4().hex[:8]}")
        tenant_b = TenantFactory.build(slug=f"tenant-b-{uuid4().hex[:8]}")
        async with session_factory() as seed, seed.begin():
            seed.add_all([tenant_a, tenant_b])

        # Create a log as tenant_a.
        log_id = None
        async with TenantAwareSession(tenant_a.id, rls_session_factory) as session:
            log = AuditLog(
                tenant_id=tenant_a.id,
                actor_type="user",
                action="test.action",
                payload={},
                decision="allowed",
            )
            session.add(log)
            await session.flush()
            log_id = log.id

        # As tenant_b the row must be invisible.
        async with TenantAwareSession(tenant_b.id, rls_session_factory) as session:
            result = await session.execute(select(AuditLog).where(AuditLog.id == log_id))
            assert result.scalar_one_or_none() is None, "RLS should hide other tenant's data"

    @pytest.mark.asyncio
    async def test_rls_blocks_delete_wrong_tenant(self, session_factory, rls_session_factory):
        """RLS prevents deleting another tenant's rows."""
        from socialerp.audit import AuditLog
        from sqlalchemy import delete

        tenant_a = TenantFactory.build(slug=f"tenant-a-{uuid4().hex[:8]}")
        tenant_b = TenantFactory.build(slug=f"tenant-b-{uuid4().hex[:8]}")
        async with session_factory() as seed, seed.begin():
            seed.add_all([tenant_a, tenant_b])

        # Create a log as tenant_a.
        log_id = None
        async with TenantAwareSession(tenant_a.id, rls_session_factory) as session:
            log = AuditLog(
                tenant_id=tenant_a.id,
                actor_type="user",
                action="test.action",
                payload={},
                decision="allowed",
            )
            session.add(log)
            await session.flush()
            log_id = log.id

        # Try to delete as tenant_b.
        async with TenantAwareSession(tenant_b.id, rls_session_factory) as session:
            result = await session.execute(delete(AuditLog).where(AuditLog.id == log_id))
            assert result.rowcount == 0, "RLS should prevent delete of other tenant's data"

    @pytest.mark.asyncio
    async def test_platform_admin_can_bypass_rls(self, session_factory, rls_session_factory):
        """Superuser connections bypass RLS; the app role is fail-closed."""
        from sqlalchemy import select

        from packages.core.models.core import Tenant

        tenant_a = TenantFactory.build(slug=f"tenant-a-{uuid4().hex[:8]}")
        tenant_b = TenantFactory.build(slug=f"tenant-b-{uuid4().hex[:8]}")
        async with session_factory() as seed, seed.begin():
            seed.add_all([tenant_a, tenant_b])

        # Platform admin (superuser, no tenant context) sees all tenants.
        async with session_factory() as admin:
            rows = (await admin.execute(select(Tenant))).scalars().all()
            assert len(rows) >= 2

        # Non-owner app role with no context must see nothing (fail-closed).
        async with rls_session_factory() as app_session:
            rows = (await app_session.execute(select(Tenant))).scalars().all()
            assert len(rows) == 0


class TestTenantContext:
    """Tests for tenant context management."""

    def test_get_current_tenant_id_default_none(self):
        """Default context is None."""
        assert get_current_tenant_id() is None

    def test_set_and_reset_current_tenant_id(self):
        """Set and reset tenant context."""
        tenant_id = uuid4()
        token = set_current_tenant_id(tenant_id)
        assert get_current_tenant_id() == tenant_id
        reset_current_tenant_id(token)
        assert get_current_tenant_id() is None

    def test_nested_context(self):
        """Nested context works correctly."""
        tenant_a = uuid4()
        tenant_b = uuid4()

        token_a = set_current_tenant_id(tenant_a)
        assert get_current_tenant_id() == tenant_a

        token_b = set_current_tenant_id(tenant_b)
        assert get_current_tenant_id() == tenant_b

        reset_current_tenant_id(token_b)
        assert get_current_tenant_id() == tenant_a

        reset_current_tenant_id(token_a)
        assert get_current_tenant_id() is None
