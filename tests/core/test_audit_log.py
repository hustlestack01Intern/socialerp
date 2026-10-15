"""
Tests for audit log.
"""

from datetime import datetime
from uuid import uuid4

import pytest
from socialerp.audit import (
    AuditLog,
    AuditLogEntry,
    log_agent_action,
    log_event,
    log_payment_event,
    log_security_event,
    log_webhook_event,
)
from socialerp.tenancy import TenantAwareSession


class TestAuditLog:
    """Tests for audit log."""

    @pytest.mark.asyncio
    async def test_log_event_basic(self, session_factory, tenant):
        """Basic event logging."""
        event_id = await log_event(
            action="test.action",
            payload={"key": "value"},
            actor_type="user",
            actor_id="user_123",
            decision="allowed",
            rule_ref="policy:test",
            tenant_id=tenant.id,
        )
        assert event_id is not None

        # Verify stored
        async with TenantAwareSession(tenant.id, session_factory) as session:
            from sqlalchemy import select

            result = await session.execute(select(AuditLog).where(AuditLog.id == event_id))
            log = result.scalar_one()
            assert log.action == "test.action"
            assert log.payload == {"key": "value"}
            assert log.actor_type == "user"
            assert log.actor_id == "user_123"
            assert log.decision == "allowed"
            assert log.rule_ref == "policy:test"

    @pytest.mark.asyncio
    async def test_log_agent_action(self, session_factory, tenant):
        """Log agent action with full context."""
        from socialerp.tenancy import reset_current_tenant_id, set_current_tenant_id

        # Set tenant context
        token = set_current_tenant_id(tenant.id)
        try:
            event_id = await log_agent_action(
                action="order.create",
                payload={"items": [{"sku": "ABC", "qty": 2}], "total": 5000},
                agent_run_id="run_abc123",
                decision="allowed",
                rule_ref="policy:max_order:100000",
                confidence=0.97,
                correlation_id="conv_xyz",
            )
        finally:
            reset_current_tenant_id(token)

        assert event_id is not None

        async with TenantAwareSession(tenant.id, session_factory) as session:
            from sqlalchemy import select

            result = await session.execute(select(AuditLog).where(AuditLog.id == event_id))
            log = result.scalar_one()
            assert log.actor_type == "agent"
            assert log.actor_id == "run_abc123"
            assert log.confidence == pytest.approx(0.97)
            assert log.correlation_id == "conv_xyz"

    @pytest.mark.asyncio
    async def test_log_webhook_event(self, session_factory, tenant):
        """Log webhook event."""
        from socialerp.tenancy import reset_current_tenant_id, set_current_tenant_id

        token = set_current_tenant_id(tenant.id)
        try:
            event_id = await log_webhook_event(
                provider="meta",
                event_type="message",
                payload={"message": "hello"},
                webhook_id="wh_123",
                decision="processed",
            )
        finally:
            reset_current_tenant_id(token)

        assert event_id is not None

        async with TenantAwareSession(tenant.id, session_factory) as session:
            from sqlalchemy import select

            result = await session.execute(select(AuditLog).where(AuditLog.id == event_id))
            log = result.scalar_one()
            assert log.action == "webhook.meta.message"
            assert log.actor_type == "webhook"
            assert log.actor_id == "wh_123"
            assert log.correlation_id == "wh_123"

    @pytest.mark.asyncio
    async def test_log_payment_event(self, session_factory, tenant):
        """Log payment event."""
        from socialerp.tenancy import reset_current_tenant_id, set_current_tenant_id

        token = set_current_tenant_id(tenant.id)
        try:
            event_id = await log_payment_event(
                action="stk_push_callback",
                payload={"checkout_request_id": "ws_CO_123", "result_code": "0"},
                payment_id="pay_123",
                decision="confirmed",
                correlation_id="ws_CO_123",
            )
        finally:
            reset_current_tenant_id(token)

        assert event_id is not None

        async with TenantAwareSession(tenant.id, session_factory) as session:
            from sqlalchemy import select

            result = await session.execute(select(AuditLog).where(AuditLog.id == event_id))
            log = result.scalar_one()
            assert log.action == "payment.stk_push_callback"
            assert log.actor_type == "system"
            assert log.actor_id == "pay_123"
            assert log.decision == "confirmed"

    @pytest.mark.asyncio
    async def test_log_security_event(self, session_factory, tenant):
        """Log security event."""
        from socialerp.tenancy import reset_current_tenant_id, set_current_tenant_id

        token = set_current_tenant_id(tenant.id)
        try:
            event_id = await log_security_event(
                action="prompt_injection_attempt",
                payload={"customer_message": "Ignore previous instructions..."},
                actor_type="customer",
                actor_id="254712345678",
                decision="flagged",
                correlation_id="conv_123",
            )
        finally:
            reset_current_tenant_id(token)

        assert event_id is not None

        async with TenantAwareSession(tenant.id, session_factory) as session:
            from sqlalchemy import select

            result = await session.execute(select(AuditLog).where(AuditLog.id == event_id))
            log = result.scalar_one()
            assert log.action == "security.prompt_injection_attempt"
            assert log.actor_type == "customer"
            assert log.decision == "flagged"

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, session_factory, rls_session_factory):
        """Audit logs are isolated by tenant (RLS enforced)."""
        from sqlalchemy import select

        from tests.factories import TenantFactory

        # Seed tenants as superuser (bypasses RLS).
        tenant_a = TenantFactory.build(slug=f"audit-a-{uuid4().hex[:8]}")
        tenant_b = TenantFactory.build(slug=f"audit-b-{uuid4().hex[:8]}")
        async with session_factory() as seed, seed.begin():
            seed.add_all([tenant_a, tenant_b])

        # Write two logs as tenant_a (non-owner role, RLS WITH CHECK applies).
        async with TenantAwareSession(tenant_a.id, rls_session_factory) as session:
            session.add_all([
                AuditLog(
                    tenant_id=tenant_a.id,
                    actor_type="user",
                    action="test.action",
                    payload={"n": 1},
                    decision="allowed",
                ),
                AuditLog(
                    tenant_id=tenant_a.id,
                    actor_type="user",
                    action="test.action",
                    payload={"n": 2},
                    decision="allowed",
                ),
            ])
            await session.flush()

        # tenant_b must not see tenant_a's logs.
        async with TenantAwareSession(tenant_b.id, rls_session_factory) as session:
            logs = (await session.execute(select(AuditLog))).scalars().all()
            assert len(logs) == 0

        # tenant_a sees exactly its own two logs.
        async with TenantAwareSession(tenant_a.id, rls_session_factory) as session:
            logs = (await session.execute(select(AuditLog))).scalars().all()
            assert len(logs) == 2

    @pytest.mark.asyncio
    async def test_audit_log_entry_schema(self):
        """AuditLogEntry schema validation."""
        entry = AuditLogEntry(
            tenant_id=uuid4(),
            actor_type="agent",
            actor_id="run_123",
            action="order.create",
            payload={"total": 5000},
            decision="allowed",
            rule_ref="policy:test",
            confidence=0.95,
            correlation_id="corr_123",
        )
        assert entry.tenant_id is not None
        assert entry.created_at is not None
        assert isinstance(entry.created_at, datetime)
