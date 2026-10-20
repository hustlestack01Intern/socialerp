"""
API-path Row-Level Security test (plan M1.1 acceptance, U23).

This proves the *whole* chain works together:

1. ``TenantContextMiddleware`` decodes the bearer JWT and publishes the tenant
   on the ``socialerp.tenancy`` contextvar.
2. ``get_db_session`` reads that contextvar and runs a parameterized
   ``set_config('app.current_tenant_id', ...)`` on the request's session.
3. Postgres RLS (the ``socialerp_app`` non-owner role) then scopes every query.

We point the API's DB engine at the non-owner ``socialerp_app`` role so RLS is
actually enforced (the default test engine is the superuser, which bypasses it).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

RLS_DB_URL_ASYNC = "postgresql+asyncpg://socialerp_app:socialerp_app@localhost:5432/socialerp_test"


@pytest.fixture
async def rls_api_client(rls_engine, session_factory):
    """API client whose DB engine is the non-owner RLS role, flag on.

    We monkeypatch the module-level engine/session-factory singletons in
    ``packages.core.database`` so ``get_db_session`` (used by routes) runs as
    ``socialerp_app`` and the RLS policies bite. Restored on teardown.
    """
    from socialerp.settings import get_settings
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    import packages.core.database as db_mod

    settings = get_settings()
    original_flag = settings.TENANCY_MIDDLEWARE_ENABLED
    settings.TENANCY_MIDDLEWARE_ENABLED = True

    saved_engine, saved_factory = db_mod._engine, db_mod._session_factory
    engine = db_mod.create_async_engine(RLS_DB_URL_ASYNC, echo=False, pool_pre_ping=True)
    db_mod._engine = engine
    db_mod._session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    from apps.api.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        settings.TENANCY_MIDDLEWARE_ENABLED = original_flag
        db_mod._engine, db_mod._session_factory = saved_engine, saved_factory
        await engine.dispose()


async def _seed_tenant_with_order(session_factory, name: str):
    """Seed a tenant + one customer + one order as the superuser (bypasses RLS)."""
    from packages.core.models import Customer, Order

    tenant_id = uuid4()
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO tenants "
                "(id, name, slug, owner_phone, owner_email, is_active, settings) "
                "VALUES (:id, :name, :slug, :phone, :email, true, '{}'::jsonb)"
            ),
            {
                "id": str(tenant_id),
                "name": name,
                "slug": f"{name.lower().replace(' ', '-')}-{uuid4().hex[:8]}",
                "phone": "254712345678",
                "email": f"{uuid4().hex[:8]}@example.com",
            },
        )
        customer = Customer(
            tenant_id=tenant_id,
            phone="254700000001",
            name="Test Customer",
        )
        session.add(customer)
        await session.flush()

        order = Order(
            tenant_id=tenant_id,
            customer_id=customer.id,
            customer_phone="254700000001",
            status="draft",
            total_cents=10000,
        )
        session.add(order)
        await session.flush()
        order_id = order.id
    return tenant_id, order_id


def _token_for(tenant_id, *, role: str = "owner"):
    from packages.core.auth.jwt import create_access_token

    return create_access_token(str(uuid4()), str(tenant_id), [role])


class TestRlsOnApiPath:
    @pytest.mark.asyncio
    async def test_cross_tenant_order_read_is_empty(self, rls_api_client, session_factory):
        """Tenant B must not see tenant A's order, with RLS active on the path."""
        tenant_a, order_a = await _seed_tenant_with_order(session_factory, "Tenant A")
        tenant_b, order_b = await _seed_tenant_with_order(session_factory, "Tenant B")

        # Tenant A sees its order.
        token_a = _token_for(tenant_a)
        resp_a = await rls_api_client.get(
            "/api/v1/orders", headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a.status_code == 200, resp_a.text
        ids_a = [o["id"] for o in resp_a.json()["items"]]
        assert str(order_a) in ids_a
        assert str(order_b) not in ids_a

        # Tenant B sees its OWN order and NOT tenant A's.
        token_b = _token_for(tenant_b)
        resp_b = await rls_api_client.get(
            "/api/v1/orders", headers={"Authorization": f"Bearer {token_b}"}
        )
        assert resp_b.status_code == 200, resp_b.text
        ids_b = [o["id"] for o in resp_b.json()["items"]]
        assert str(order_a) not in ids_b
        assert str(order_b) in ids_b

    @pytest.mark.asyncio
    async def test_guc_is_what_unlocks_the_read(self, rls_api_client, session_factory):
        """Prove the GUC (not the explicit filter alone) is active.

        With no tenant context (public path), RLS returns no rows. We assert the
        GUC is set on the request transaction by reading it inside the request
        session via a probe route-free check: query ``current_setting`` through
        the same engine after setting it.
        """
        tenant_a, _ = await _seed_tenant_with_order(session_factory, "Tenant A")

        # Directly exercise get_db_session with the contextvar set, as the
        # middleware does, and confirm the GUC is visible to the session.
        from socialerp.tenancy import reset_current_tenant_id, set_current_tenant_id

        import packages.core.database as db_mod

        token = set_current_tenant_id(tenant_a)
        try:
            async for session in db_mod.get_db_session():
                val = (
                    await session.execute(
                        text("SELECT current_setting('app.current_tenant_id', true)")
                    )
                ).scalar_one()
                assert val == str(tenant_a)
                count = (await session.execute(text("SELECT count(*) FROM orders"))).scalar_one()
                assert count == 1
                break
        finally:
            reset_current_tenant_id(token)

    @pytest.mark.asyncio
    async def test_no_tenant_context_sees_nothing(self, rls_api_client, session_factory):
        """Without a tenant context (GUC unset), RLS hides every row."""
        await _seed_tenant_with_order(session_factory, "Tenant A")

        import packages.core.database as db_mod

        assert db_mod._session_factory is not None
        async with db_mod._session_factory() as session:
            count = (await session.execute(text("SELECT count(*) FROM orders"))).scalar_one()
            assert count == 0
