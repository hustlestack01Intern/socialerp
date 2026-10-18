"""
Tenant Context Middleware

FastAPI middleware that extracts JWT from Authorization header,
validates it, and sets the PostgreSQL session variable
`app.current_tenant_id` for Row-Level Security enforcement.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from packages.core.auth.jwt import decode_access_token
from packages.core.config import get_settings

logger = logging.getLogger(__name__)

# Paths that don't require authentication
PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/auth/login",
    "/auth/refresh",
    "/api/v1/webhooks/whatsapp",
    "/api/v1/webhooks/instagram",
    "/api/v1/webhooks/daraja",
    "/api/v1/webhooks/aggregator",
}


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware that sets tenant context for RLS.

    Flow:
    1. Extract JWT from Authorization header
    2. Validate token and extract tenant_id, user_id, roles
    3. Set `SET LOCAL app.current_tenant_id = <tenant_id>` on database session
    4. Attach tenant/user info to request.state for downstream use
    """

    def __init__(self, app: Any, exclude_paths: set[str] | None = None) -> None:
        super().__init__(app)
        self.exclude_paths = exclude_paths or PUBLIC_PATHS
        self.settings = get_settings()

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Skip authentication for public paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Skip for paths starting with public prefixes
        for prefix in ("/docs", "/redoc", "/openapi", "/health"):
            if request.url.path.startswith(prefix):
                return await call_next(request)

        # Extract Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing or invalid Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:]  # Remove "Bearer " prefix

        # Validate JWT and extract claims
        try:
            payload = decode_access_token(token)
        except ValueError as e:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": str(e)},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Extract tenant and user info
        tenant_id = payload.tenant_id
        user_id = payload.sub
        roles = payload.roles

        # Attach to request state for downstream dependencies
        request.state.tenant_id = tenant_id
        request.state.user_id = user_id
        request.state.roles = roles
        request.state.token_payload = payload

        # Set PostgreSQL session variable for RLS
        # This must be done within the database session
        # The actual SET LOCAL is executed by the database session dependency
        # We store the tenant_id in request.state for the session to pick up

        # Log for debugging (in development)
        if self.settings.app_debug:
            logger.debug(
                f"Tenant context set: tenant_id={tenant_id}, user_id={user_id}, roles={roles}",
            )

        # Continue to next middleware/route
        response = await call_next(request)
        return response


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


class DatabaseSessionMiddleware(BaseHTTPMiddleware):
    """
    Middleware that manages database session and sets RLS context.

    This middleware works with the TenantContextMiddleware to:
    1. Create a database session for each request
    2. Set the tenant context (SET LOCAL app.current_tenant_id)
    3. Commit/rollback transaction
    4. Close session
    """

    def __init__(self, app: Any) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Get tenant_id from request state (set by TenantContextMiddleware)
        tenant_id = getattr(request.state, "tenant_id", None)

        # Get database session from app state (set by lifespan)
        from packages.core.database import get_session_factory

        session_factory = get_session_factory()
        async with session_factory() as session:
            try:
                # Set RLS context if tenant_id is available
                if tenant_id:
                    await session.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")

                # Attach session to request state
                request.state.db = session

                # Process request
                response = await call_next(request)

                # Commit transaction
                await session.commit()

                return response

            except HTTPException:
                # Re-raise HTTP exceptions (don't rollback for client errors)
                await session.rollback()
                raise

            except Exception:
                # Rollback on any other exception
                await session.rollback()
                logger.exception("Database error")
                raise

            finally:
                await session.close()


# Dependency for getting database session in route handlers
async def get_db_session_from_request(request: Request) -> Any:
    """Get database session from request state."""
    return request.state.db


# Alternative: Session dependency that uses middleware context
async def get_db_with_context(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Get database session with tenant context already set."""
    from packages.core.database import get_session_factory

    session_factory = get_session_factory()
    async with session_factory() as session:
        # Get tenant_id from request state
        tenant_id = getattr(request.state, "tenant_id", None)

        if tenant_id:
            await session.execute(f"SET LOCAL app.current_tenant_id = '{tenant_id}'")

        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
