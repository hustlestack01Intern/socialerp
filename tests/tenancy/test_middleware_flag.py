"""
Tests for the tenant-context middleware HTTP edge (plan M1.1, U62/U63).

These tests exercise the *edge* behaviour of ``TenantContextMiddleware`` when
``TENANCY_MIDDLEWARE_ENABLED`` is on:

* non-public paths without a valid bearer JWT fail closed (401);
* public/allow-listed paths (health, root, auth login/register/refresh, all
  webhook paths, the Daraja STK callback, public tracking) still pass through;
* with the flag off (the default), behaviour is exactly as before — the
  middleware is not registered at all.

The middleware sets the ``socialerp.tenancy.current_tenant_id`` contextvar; the
DB-level GUC enforcement is covered by ``test_rls_api.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app(*, enabled: bool):
    """Build a fresh app with the tenant flag forced to ``enabled``.

    ``get_settings()`` is an ``lru_cache`` singleton, so we patch the cached
    instance's attribute directly and restore it afterwards.
    """
    from socialerp.settings import get_settings

    settings = get_settings()
    original = settings.TENANCY_MIDDLEWARE_ENABLED
    settings.TENANCY_MIDDLEWARE_ENABLED = enabled

    # Import in a way that re-reads the flag at create_app() time.
    from apps.api.main import create_app

    app = create_app()
    return app, settings, original


@pytest.fixture
async def flag_on_client():
    """Client against an app where the tenant middleware is enabled."""
    app, settings, original = _build_app(enabled=True)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        settings.TENANCY_MIDDLEWARE_ENABLED = original


@pytest.fixture
async def flag_off_client():
    """Client against an app where the tenant middleware is disabled (default)."""
    app, settings, original = _build_app(enabled=False)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        settings.TENANCY_MIDDLEWARE_ENABLED = original


# ---------------------------------------------------------------------------
# Flag OFF — no behaviour change
# ---------------------------------------------------------------------------


class TestFlagOff:
    @pytest.mark.asyncio
    async def test_unauthenticated_protected_path_not_401_from_middleware(self, flag_off_client):
        """With the flag off, the middleware never intercepts — the route decides.

        Today's routes authenticate via ``Depends`` and return 401/403
        themselves; the point is that the *middleware* adds nothing. We assert
        the response is not the middleware's specific body.
        """
        resp = await flag_off_client.get("/api/v1/orders")
        # Route-level auth responds 401/403/404/200 — never the middleware body.
        detail = (
            resp.json().get("detail")
            if resp.headers.get("content-type", "").startswith("application/json")
            else None
        )
        assert detail != "Missing or invalid Authorization header"

    @pytest.mark.asyncio
    async def test_health_open(self, flag_off_client):
        resp = await flag_off_client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Flag ON — fail closed on protected, pass through on public
# ---------------------------------------------------------------------------

PUBLIC_OK_PATHS = [
    "/health",
    "/",
    "/metrics",
    "/docs",
    "/openapi.json",
]

PROTECTED_401_PATHS = [
    "/api/v1/orders",
    "/api/v1/catalog/products",
    "/api/v1/crm/customers",
    "/api/v1/billing/subscription",
    "/api/v1/tracking/00000000-0000-0000-0000-000000000000",  # NO: tracking is public
]


class TestFlagOnFailsClosed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/api/v1/orders", "/api/v1/catalog/products"])
    async def test_missing_bearer_is_401(self, flag_on_client, path):
        resp = await flag_on_client.get(path)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Missing or invalid Authorization header"
        assert resp.headers.get("www-authenticate") == "Bearer"

    @pytest.mark.asyncio
    async def test_invalid_bearer_is_401(self, flag_on_client):
        resp = await flag_on_client.get(
            "/api/v1/orders", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PUBLIC_OK_PATHS)
    async def test_public_paths_pass_through(self, flag_on_client, path):
        resp = await flag_on_client.get(path)
        # Should never be the middleware 401.
        assert resp.status_code != 401


class TestFlagOnAllowList:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/auth/login",
            "/api/v1/auth/register",
            "/api/v1/auth/refresh",
        ],
    )
    async def test_auth_paths_not_blocked_by_middleware(self, flag_on_client, path):
        """Public auth endpoints must reach the route (POST)."""
        resp = await flag_on_client.post(path, json={})
        # Middleware did not 401 with its specific body; route validates body.
        detail = None
        if resp.headers.get("content-type", "").startswith("application/json"):
            detail = resp.json().get("detail")
        assert detail != "Missing or invalid Authorization header"

    @pytest.mark.asyncio
    async def test_whatsapp_verify_get_passes_through(self, flag_on_client):
        resp = await flag_on_client.get("/api/v1/webhooks/whatsapp")
        detail = None
        if resp.headers.get("content-type", "").startswith("application/json"):
            detail = resp.json().get("detail")
        assert detail != "Missing or invalid Authorization header"

    @pytest.mark.asyncio
    async def test_aggregator_webhook_post_passes_through(self, flag_on_client, monkeypatch):
        # Provide dummy provider creds so the route's dependency constructs and
        # the request is handled by the route (not the middleware). Reaching the
        # route at all proves the middleware allowed the path through.
        from packages.core.config import get_settings as legacy_settings

        s = legacy_settings()
        monkeypatch.setattr(s, "aggregator_api_key", "test_key", raising=False)
        monkeypatch.setattr(s, "aggregator_api_secret", "test_secret", raising=False)

        tid = str(uuid4())
        resp = await flag_on_client.post(f"/api/v1/webhooks/aggregator/{tid}/tok", json={})
        # The middleware must not 401; the route may 4xx/5xx on its own.
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_daraja_stk_callback_passes_through(self, flag_on_client):
        tid = str(uuid4())
        resp = await flag_on_client.post(f"/api/v1/payments/stk/callback/{tid}/tok", json={})
        detail = None
        if resp.headers.get("content-type", "").startswith("application/json"):
            detail = resp.json().get("detail")
        assert detail != "Missing or invalid Authorization header"

    @pytest.mark.asyncio
    async def test_public_tracking_passes_through(self, flag_on_client):
        oid = str(uuid4())
        resp = await flag_on_client.get(f"/api/v1/tracking/{oid}")
        detail = None
        if resp.headers.get("content-type", "").startswith("application/json"):
            detail = resp.json().get("detail")
        assert detail != "Missing or invalid Authorization header"

    @pytest.mark.asyncio
    async def test_tracking_admin_prefix_not_public(self, flag_on_client):
        """A sibling path must NOT match the ``/tracking/`` prefix allow-list."""
        resp = await flag_on_client.get("/api/v1/tracking-admin")
        assert resp.status_code == 401


class TestPathMatcher:
    """Unit tests for the allow-list matcher itself."""

    def test_root_is_exact_only(self):
        from packages.core.middleware.tenant_context import is_public_path

        assert is_public_path("/") is True
        assert is_public_path("/api/v1/orders") is False

    def test_tracking_prefix_boundary(self):
        from packages.core.middleware.tenant_context import is_public_path

        assert is_public_path("/tracking/abc") is True
        assert is_public_path("/tracking-admin") is False
        assert is_public_path("/api/v1/tracking/abc") is True

    def test_webhooks_public(self):
        from packages.core.middleware.tenant_context import is_public_path

        assert is_public_path("/api/v1/webhooks/whatsapp") is True
        assert is_public_path("/api/v1/webhooks/meta") is True

    def test_stk_callback_public(self):
        from packages.core.middleware.tenant_context import is_public_path

        assert is_public_path("/api/v1/payments/stk/callback/t/tok") is True

    def test_auth_exact_public(self):
        from packages.core.middleware.tenant_context import is_public_path

        assert is_public_path("/api/v1/auth/login") is True
        assert is_public_path("/api/v1/auth/me") is False
