"""
Tests for outbox pattern.
"""

from uuid import uuid4

import pytest
from socialerp.outbox import (
    Outbox,
    OutboxEventStatus,
    OutboxRelay,
    add_outbox_event,
    publish_order_created,
)
from socialerp.tenancy import TenantAwareSession


class TestOutbox:
    """Tests for outbox pattern."""

    @pytest.mark.asyncio
    async def test_add_outbox_event(self, db_session, tenant):
        """Add event to outbox within transaction."""
        async with TenantAwareSession(tenant.id, db_session) as session:
            event_id = await add_outbox_event(
                event_type="test.event",
                payload={"key": "value"},
                idempotency_key="test_key_123",
                tenant_id=tenant.id,
                session=session,
            )
            assert event_id is not None

            # Verify event was stored
            from sqlalchemy import select

            result = await session.execute(select(Outbox).where(Outbox.id == event_id))
            event = result.scalar_one()
            assert event.event_type == "test.event"
            assert event.payload == {"key": "value"}
            assert event.idempotency_key == "test_key_123"
            assert event.status == OutboxEventStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_idempotency_key_prevents_duplicates(self, db_session, tenant):
        """Duplicate idempotency key returns existing event."""
        async with TenantAwareSession(tenant.id, db_session) as session:
            event_id1 = await add_outbox_event(
                event_type="test.event",
                payload={"key": "value"},
                idempotency_key="same_key",
                tenant_id=tenant.id,
                session=session,
            )

            event_id2 = await add_outbox_event(
                event_type="test.event",
                payload={"key": "different"},  # Different payload
                idempotency_key="same_key",  # Same key
                tenant_id=tenant.id,
                session=session,
            )

            assert event_id1 == event_id2

    @pytest.mark.asyncio
    async def test_outbox_relay_processes_events(self, test_engine, tenant):
        """OutboxRelay processes pending events."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session, session.begin():
            await add_outbox_event("event.1", {"data": 1}, tenant_id=tenant.id, session=session)
            await add_outbox_event("event.2", {"data": 2}, tenant_id=tenant.id, session=session)

        # Track handler calls
        handled = []

        async def handler(payload, tenant_id):
            handled.append((payload, tenant_id))

        relay = OutboxRelay(batch_size=10)
        relay.register_handler("event.1", handler)
        relay.register_handler("event.2", handler)

        # Process one batch using relay's own session (like production)
        await relay._process_batch()

        # Verify both events handled
        assert len(handled) == 2
        assert handled[0][0] == {"data": 1}
        assert handled[1][0] == {"data": 2}

        # Verify events marked as published using fresh session
        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(select(Outbox).where(Outbox.tenant_id == tenant.id))
            events = result.scalars().all()
            for event in events:
                assert event.status == OutboxEventStatus.PUBLISHED.value
                assert event.published_at is not None

    @pytest.mark.asyncio
    async def test_outbox_relay_retries_on_failure(self, test_engine, tenant):
        """OutboxRelay retries failed events up to max_retries."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session, session.begin():
            await add_outbox_event(
                "event.fail", {"data": "fail"}, tenant_id=tenant.id, session=session
            )

        call_count = 0

        async def failing_handler(payload, tenant_id):
            nonlocal call_count
            call_count += 1
            raise Exception("Simulated failure")  # ruff: ignore[raise-vanilla-class]

        relay = OutboxRelay(batch_size=10, max_retries=3)
        relay.register_handler("event.fail", failing_handler)

        # Process batch - should retry (using relay's own session like production)
        for _ in range(3):
            await relay._process_batch()

        # Should have been retried max_retries times
        assert call_count == 3

        # Event should be marked as failed
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(select(Outbox).where(Outbox.event_type == "event.fail"))
            event = result.scalar_one()
            assert event.status == OutboxEventStatus.FAILED.value
            assert event.retry_count == 3

    @pytest.mark.asyncio
    async def test_outbox_relay_skips_unknown_event_types(self, test_engine, tenant):
        """OutboxRelay marks events with no handler as failed."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session, session.begin():
            await add_outbox_event("unknown.event", {}, tenant_id=tenant.id, session=session)

        relay = OutboxRelay(batch_size=10)
        await relay._process_batch()

        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(Outbox).where(Outbox.event_type == "unknown.event")
            )
            event = result.scalar_one()
            assert event.status == OutboxEventStatus.FAILED.value
            assert "No handler" in event.last_error

    @pytest.mark.asyncio
    async def test_publish_order_created(self, test_engine, tenant):
        """publish_order_created helper works."""
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        order_id = uuid4()
        async with async_session() as session, session.begin():
            event_id = await publish_order_created(
                order_id=order_id,
                order_data={"total": 5000, "items": 2},
                tenant_id=tenant.id,
                session=session,
            )

        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(select(Outbox).where(Outbox.id == event_id))
            event = result.scalar_one()
            assert event.event_type == "order.created"
            assert event.payload["order_id"] == str(order_id)
            assert event.payload["total"] == 5000
            assert event.idempotency_key == f"order_created:{order_id}"
