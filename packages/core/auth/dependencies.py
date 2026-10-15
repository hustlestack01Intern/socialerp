"""
FastAPI Authentication Dependencies

Dependency injection for authentication and authorization.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.api_keys import ApiKeyManager
from packages.core.auth.jwt import decode_access_token
from packages.core.auth.tokens import TokenManager
from packages.core.database import get_db_session
from packages.core.domain.auth import TokenPayload
from packages.core.domain.user import User, UserRole
from packages.core.models import ApiKey as ApiKeyModel
from packages.core.repositories import UserRepository

# Security schemes
bearer_scheme = HTTPBearer(auto_error=False)


# =============================================================================
# Database Session Dependency
# =============================================================================


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    async for session in get_db_session():
        yield session


# =============================================================================
# Token Manager Dependency
# =============================================================================


def get_token_manager(db: AsyncSession = Depends(get_db)) -> TokenManager:
    """Get token manager instance."""
    return TokenManager(db)


def get_api_key_manager(db: AsyncSession = Depends(get_db)) -> ApiKeyManager:
    """Get API key manager instance."""
    from packages.core.auth.api_keys import ApiKeyManager

    return ApiKeyManager(db)


# =============================================================================
# Current User Dependencies
# =============================================================================


async def get_current_user_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> TokenPayload:
    """Extract and validate JWT token from Authorization header."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    return payload


async def get_current_user(
    payload: Annotated[TokenPayload, Depends(get_current_user_payload)],
    db: AsyncSession = Depends(get_db),
) -> User:
    """Get current authenticated user."""
    from packages.core.repositories import UserRepository

    user_repo = UserRepository(db)
    user = await user_repo.get(UUID(payload.sub))

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    return user


async def get_current_active_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get current active user (alias for clarity)."""
    return user


# =============================================================================
# Role-Based Access Control
# =============================================================================


def require_role(*allowed_roles: UserRole) -> Callable[[User], User]:
    """Dependency that requires specific role(s)."""

    def role_checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {[r.value for r in allowed_roles]}",
            )
        return user

    return role_checker


require_owner = require_role(UserRole.OWNER)
require_staff = require_role(UserRole.OWNER, UserRole.STAFF)
require_rider = require_role(UserRole.OWNER, UserRole.STAFF, UserRole.RIDER)


# =============================================================================
# Tenant Context Dependencies
# =============================================================================


async def get_current_tenant_id(
    payload: Annotated[TokenPayload, Depends(get_current_user_payload)],
) -> UUID:
    """Extract tenant_id from token payload."""
    return UUID(payload.tenant_id)


# =============================================================================
# API Key Authentication
# =============================================================================


async def get_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: AsyncSession = Depends(get_db),
) -> ApiKeyModel | None:
    """Validate API key from Authorization header."""
    if not credentials:
        return None

    # Check if it's an API key (prefix-based)
    if not credentials.credentials.startswith("sk_"):
        return None

    from packages.core.auth.api_keys import ApiKeyManager

    manager = ApiKeyManager(db)
    api_key = await manager.validate(credentials.credentials)

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return api_key


async def require_api_key(
    api_key: Annotated[ApiKeyModel | None, Depends(get_api_key)],
) -> ApiKeyModel:
    """Require valid API key."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid API key required",
        )
    return api_key


def require_api_key_scopes(*required_scopes: str) -> Callable[[ApiKeyModel], ApiKeyModel]:
    """Dependency that requires specific API key scopes."""

    def scope_checker(api_key: Annotated[ApiKeyModel, Depends(require_api_key)]) -> ApiKeyModel:
        if not all(scope in api_key.scopes for scope in required_scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires scopes: {required_scopes}",
            )
        return api_key

    return scope_checker


# =============================================================================
# Optional Authentication (for public endpoints with optional auth)
# =============================================================================


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Get current user if authenticated, None otherwise."""
    if not credentials:
        return None

    try:
        payload = decode_access_token(credentials.credentials)
        user_repo = UserRepository(db)
        user = await user_repo.get(UUID(payload.sub))
        if user and user.is_active:
            return user
    except ValueError:
        pass

    return None
