"""
Tests for idempotency keys.
"""

import pytest
from socialerp.idempotency import (
    IdempotencyRecord,
    check_idempotency,
    cleanup_expired_idempotency_keys,
    complete_idempotency,
    compute_request_hash,
    fail_idempotency,
    idempotent,
)
from socialerp.tenancy import reset_current_tenant_id, set_current_tenant_id


class TestIdempotency:
    """Tests for idempotency keys."""

    @pytest.mark.asyncio
    async def test_check_idempotency_first_request(self, db_session, tenant):
        """First request is not a duplicate."""
        is_dup, cached = await check_idempotency(
            key="test_key_1",
            scope="test_scope",
            payload={"amount": 100},
            tenant_id=tenant.id,
        )
        assert is_dup is False
        assert cached is None

    @pytest.mark.asyncio
    async def test_check_idempotency_duplicate_key(self, db_session, tenant):
        """Duplicate key returns duplicate."""
        # First request
        await check_idempotency("key_1", "scope", {"amount": 100}, tenant_id=tenant.id)

        # Second request with same key
        is_dup, cached = await check_idempotency(
            "key_1", "scope", {"amount": 100}, tenant_id=tenant.id
        )
        assert is_dup is True
        assert cached is None  # Still pending

    @pytest.mark.asyncio
    async def test_check_idempotency_duplicate_by_hash(self, db_session, tenant):
        """Duplicate by request hash returns duplicate."""
        await check_idempotency("key_1", "scope", {"amount": 100}, tenant_id=tenant.id)

        # Different key, same payload
        is_dup, cached = await check_idempotency(
            "key_2", "scope", {"amount": 100}, tenant_id=tenant.id
        )
        assert is_dup is True
        assert cached is None

    @pytest.mark.asyncio
    async def test_complete_idempotency_stores_response(self, db_session, tenant):
        """Complete stores response for future duplicates."""
        await check_idempotency("key_1", "scope", {"amount": 100}, tenant_id=tenant.id)

        await complete_idempotency(
            key="key_1",
            scope="scope",
            response={"status": "success", "order_id": "123"},
            tenant_id=tenant.id,
        )

        # Next duplicate should get cached response
        is_dup, cached = await check_idempotency(
            "key_1", "scope", {"amount": 100}, tenant_id=tenant.id
        )
        assert is_dup is True
        assert cached == {"status": "success", "order_id": "123"}

    @pytest.mark.asyncio
    async def test_fail_idempotency_allows_retry(self, db_session, tenant):
        """Fail marks key as failed, allowing retry."""
        await check_idempotency("key_1", "scope", {"amount": 100}, tenant_id=tenant.id)

        await fail_idempotency("key_1", "scope", tenant_id=tenant.id)

        # Should be able to reserve again
        is_dup, cached = await check_idempotency(
            "key_1", "scope", {"amount": 100}, tenant_id=tenant.id
        )
        assert is_dup is False
        assert cached is None

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, test_engine):
        """Idempotency keys are isolated by tenant."""
        from uuid import uuid4

        from socialerp.tenancy import Tenant
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        # First create and commit tenants with unique slugs
        async with async_session() as session, session.begin():
            slug_a = f"tenant-a-{uuid4().hex[:8]}"
            slug_b = f"tenant-b-{uuid4().hex[:8]}"
            tenant_a = Tenant(
                id=uuid4(),
                name="Tenant A",
                slug=slug_a,
                owner_phone="254712345678",
                owner_email="a@test.com",
                is_active=True,
                settings={},
            )
            tenant_b = Tenant(
                id=uuid4(),
                name="Tenant B",
                slug=slug_b,
                owner_phone="254712345679",
                owner_email="b@test.com",
                is_active=True,
                settings={},
            )
            session.add_all([tenant_a, tenant_b])
            await session.flush()
            tenant_a_id = tenant_a.id
            tenant_b_id = tenant_b.id

        # Now test isolation with committed tenants
        # Reserve in tenant_a
        await check_idempotency("key_1", "scope", {"amount": 100}, tenant_id=tenant_a_id)

        # Same key in tenant_b should not be duplicate
        is_dup, _ = await check_idempotency(
            "key_1", "scope", {"amount": 100}, tenant_id=tenant_b_id
        )
        assert is_dup is False

    @pytest.mark.asyncio
    async def test_scope_isolation(self, db_session, tenant):
        """Idempotency keys are isolated by scope."""
        await check_idempotency("key_1", "scope_a", {"amount": 100}, tenant_id=tenant.id)

        is_dup, _ = await check_idempotency(
            "key_1", "scope_b", {"amount": 100}, tenant_id=tenant.id
        )
        assert is_dup is False

    @pytest.mark.asyncio
    async def test_compute_request_hash_deterministic(self):
        """Request hash is deterministic."""
        payload = {"b": 2, "a": 1, "nested": {"c": 3}}
        hash1 = compute_request_hash(payload)
        hash2 = compute_request_hash(payload)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 truncated

    @pytest.mark.asyncio
    async def test_compute_request_hash_order_independent(self):
        """Request hash is order-independent (JSON sort_keys)."""
        payload1 = {"a": 1, "b": 2}
        payload2 = {"b": 2, "a": 1}
        assert compute_request_hash(payload1) == compute_request_hash(payload2)

    @pytest.mark.asyncio
    async def test_cleanup_expired_keys(self, test_engine, tenant):
        """Cleanup removes expired keys."""
        from datetime import datetime, timedelta

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

        # Add expired key directly
        async with async_session() as session, session.begin():
            expired = IdempotencyRecord(
                tenant_id=tenant.id,
                key="expired_key",
                scope="test",
                request_hash="abc123",
                status="pending",
                expires_at=datetime.utcnow() - timedelta(hours=1),
            )
            session.add(expired)
            await session.flush()

        # Add valid key
        await check_idempotency("valid_key", "test", {"data": 1}, tenant_id=tenant.id)

        # Cleanup
        deleted = await cleanup_expired_idempotency_keys()
        assert deleted >= 1

        # Valid key should still exist
        is_dup, _ = await check_idempotency("valid_key", "test", {"data": 1}, tenant_id=tenant.id)
        assert is_dup is True


class TestIdempotentDecorator:
    """Tests for @idempotent decorator."""

    @pytest.mark.asyncio
    async def test_decorator_caches_response(self, test_engine, tenant):
        """Decorator caches successful response."""
        call_count = 0

        @idempotent(scope="test_decorator", key_param="idempotency_key")
        async def create_order(idempotency_key: str, amount: int):
            nonlocal call_count
            call_count += 1
            return {"order_id": f"order_{call_count}", "amount": amount}

        # Set tenant context
        token = set_current_tenant_id(tenant.id)
        try:
            # First call
            result1 = await create_order(idempotency_key="key_1", amount=1000)
            assert result1 == {"order_id": "order_1", "amount": 1000}
            assert call_count == 1

            # Second call with same key - should return cached
            result2 = await create_order(idempotency_key="key_1", amount=1000)
            assert result2 == {"order_id": "order_1", "amount": 1000}
            assert call_count == 1  # Function not called again
        finally:
            reset_current_tenant_id(token)

    @pytest.mark.asyncio
    async def test_decorator_propagates_exception(self, test_engine, tenant):
        """Decorator propagates exception and marks as failed."""

        @idempotent(scope="test_decorator", key_param="idempotency_key")
        async def failing_func(idempotency_key: str):
            raise ValueError("Simulated error")

        token = set_current_tenant_id(tenant.id)
        try:
            with pytest.raises(ValueError):
                await failing_func(idempotency_key="key_1")

            # Should be able to retry
            with pytest.raises(ValueError):
                await failing_func(idempotency_key="key_1")
        finally:
            reset_current_tenant_id(token)
