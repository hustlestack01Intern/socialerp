"""
Tenant Context Middleware

FastAPI middleware that extracts the JWT from the ``Authorization`` header,
validates it, and records which tenant is acting.

Design (ADR 0023, plan M1.1 / U62):

* The middleware decides *whether* a tenant is acting at the HTTP edge. When
  ``TENANCY_MIDDLEWARE_ENABLED`` is on it fails closed (401) on non-public paths
  without a valid bearer token.
* The middleware never owns a database session. It publishes the tenant id via
  the ``socialerp.tenancy.current_tenant_id`` contextvar and on ``request.state``;
  ``packages.core.database.get_db_session`` reads the contextvar and runs a
  **parameterized** ``set_config('app.current_tenant_id', :tid, true)`` on the
  session the route actually uses, so Row-Level Security is active (U23).

Path matching is prefix-based (see ``PUBLIC_EXACT_PATHS`` / ``PUBLIC_PATH_PREFIXES``).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response, status
from socialerp.settings import get_settings
from socialerp.tenancy import reset_current_tenant_id, set_current_tenant_id
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from packages.core.auth.jwt import decode_access_token

logger = logging.getLogger(__name__)

# Paths matched exactly (never as a prefix — ``/`` as a prefix would match
# the whole API).
PUBLIC_EXACT_PATHS: frozenset[str] = frozenset({
    "/",
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
})

# Prefixes matched with ``str.startswith``. Every entry ends in ``/`` so a
# sibling route (e.g. ``/api/v1/tracking-admin``) can never sneak in.
PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/api/v1/webhooks/",
    "/api/v1/payments/stk/callback/",
    "/api/v1/tracking/",
    "/tracking/",
)


def is_public_path(path: str) -> bool:
    """Return True if ``path`` is public (no bearer token required)."""
    if path in PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Set tenant context for RLS at the HTTP edge (Fails closed when enabled)."""

    def __init__(self, app: Any, exclude_paths: set[str] | None = None) -> None:
        super().__init__(app)
        # Kept for backwards-compatibility with callers that passed a custom set.
        self.exclude_paths = exclude_paths
        self.settings = get_settings()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path

        if self.exclude_paths and path in self.exclude_paths:
            return await call_next(request)

        if is_public_path(path):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:]
        try:
            payload = decode_access_token(token)
        except ValueError as e:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": str(e)},
                headers={"WWW-Authenticate": "Bearer"},
            )

        from uuid import UUID

        tenant_id: UUID | None = None
        try:
            tenant_id = UUID(payload.tenant_id)
        except (ValueError, AttributeError, TypeError):
            tenant_id = None

        if tenant_id is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Token is missing a valid tenant"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Publish tenant context for downstream (contextvar is read by
        # get_db_session; request.state is read by handlers).
        request.state.tenant_id = tenant_id
        request.state.user_id = payload.sub
        request.state.roles = payload.roles
        request.state.token_payload = payload

        token_ctx = set_current_tenant_id(tenant_id)
        try:
            return await call_next(request)
        finally:
            reset_current_tenant_id(token_ctx)


def get_tenant_id_from_request(request: Request) -> str | None:
    """Extract tenant_id from request state."""
    return getattr(request.state, "tenant_id", None)


def get_user_id_from_request(request: Request) -> str | None:
    """Extract user_id from request state."""
    return getattr(request.state, "user_id", None)


def get_roles_from_request(request: Request) -> list[str] | None:
    """Extract roles from request state."""
    return getattr(request.state, "roles", None)


def get_token_payload_from_request(request: Request) -> dict[str, Any] | None:
    """Extract token payload from request state."""
    return getattr(request.state, "token_payload", None)


__all__ = [
    "PUBLIC_EXACT_PATHS",
    "PUBLIC_PATH_PREFIXES",
    "TenantContextMiddleware",
    "get_roles_from_request",
    "get_tenant_id_from_request",
    "get_token_payload_from_request",
    "get_user_id_from_request",
    "is_public_path",
]
