"""
Token Manager

Manages token lifecycle: issuance, refresh, revocation, blacklisting.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from packages.core.domain.auth import TokenBlacklistEntry, TokenPair
from packages.core.domain.user import User, UserRole
from packages.core.models import User as UserModel
from packages.core.repositories import UserRepository
from packages.core.security import verify_password


class TokenManager:
    """Manages JWT token lifecycle."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.user_repo = UserRepository(session)
        self._blacklist: set[str] = set()  # In-memory blacklist (use Redis in production)

    async def authenticate_user(self, email: str, password: str) -> User | None:
        """Authenticate user by email and password."""
        # Note: In production, this would be cross-tenant for superuser
        # For tenant-scoped auth, tenant_id must be provided
        # This is a simplified version for the manager
        return None

    async def authenticate_user_in_tenant(
        self, tenant_id: UUID, email: str, password: str
    ) -> User | None:
        """Authenticate user within a specific tenant."""
        result = await self.session.execute(
            select(UserModel)
            .where(UserModel.tenant_id == tenant_id)
            .where(UserModel.email == email)
            .where(UserModel.is_active.is_(True))
        )
        user_model = result.scalar_one_or_none()

        if not user_model:
            return None

        if not verify_password(password, user_model.password_hash):
            return None

        return User(
            id=user_model.id,
            tenant_id=user_model.tenant_id,
            email=user_model.email,
            role=UserRole(user_model.role.value),
            is_active=user_model.is_active,
            last_login_at=user_model.last_login_at,
            created_at=user_model.created_at,
            updated_at=user_model.updated_at,
        )

    def create_token_pair(self, user: User) -> TokenPair:
        """Create access and refresh token pair for user."""
        access_token = create_access_token(
            user_id=str(user.id),
            tenant_id=str(user.tenant_id),
            roles=[user.role.value],
        )
        refresh_token = create_refresh_token(
            user_id=str(user.id),
            tenant_id=str(user.tenant_id),
            roles=[user.role.value],
        )

        # Calculate expiry
        from packages.core.auth.jwt import get_jwt_settings

        settings = get_jwt_settings()
        expires_in = settings.access_token_expire_minutes * 60

        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    async def refresh_tokens(self, refresh_token: str) -> TokenPair | None:
        """Refresh access token using refresh token."""
        try:
            payload = decode_refresh_token(refresh_token)
        except ValueError:
            return None

        # Check if token is blacklisted
        if payload.jti in self._blacklist:
            return None

        # Get user
        user = await self.user_repo.get(UUID(payload.sub))
        if not user or not user.is_active:
            return None

        # Blacklist the old refresh token
        self._blacklist.add(payload.jti)

        # Create new token pair
        return self.create_token_pair(user)

    def revoke_token(self, token: str, token_type: str = "access", reason: str = "logout") -> bool:
        """Revoke a token by adding to blacklist."""
        try:
            if token_type == "access":
                payload = decode_access_token(token)
            else:
                payload = decode_refresh_token(token)

            self._blacklist.add(payload.jti)
            return True
        except ValueError:
            return False

    def is_blacklisted(self, token: str, token_type: str = "access") -> bool:
        """Check if token is blacklisted."""
        try:
            if token_type == "access":
                payload = decode_access_token(token)
            else:
                payload = decode_refresh_token(token)
            return payload.jti in self._blacklist
        except ValueError:
            return True  # Treat invalid tokens as blacklisted

    def blacklist_token_id(self, jti: str) -> None:
        """Add token ID to blacklist."""
        self._blacklist.add(jti)

    def cleanup_blacklist(self, max_age: timedelta = timedelta(days=7)) -> int:
        """Clean up old blacklist entries (not implemented for in-memory).

        In production with Redis, this would remove expired entries.
        """
        return 0

    async def revoke_all_user_tokens(self, user_id: UUID) -> int:
        """Revoke all tokens for a user (by blacklisting their current refresh token).

        Note: This is a best-effort approach. In production, use a token store
        with user_id index for complete revocation.
        """
        # This would require storing user_id in the blacklist
        # For now, return 0 (not implemented)
        return 0


class TokenBlacklistRepository:
    """Repository for persistent token blacklist (stub for in-memory)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, entry: TokenBlacklistEntry) -> None:
        """Add entry to blacklist."""
        # In production, insert into database table

    async def is_blacklisted(self, jti: str) -> bool:
        """Check if token ID is blacklisted."""
        # In production, query database
        return False

    async def cleanup(self, max_age: timedelta = timedelta(days=7)) -> int:
        """Clean up old blacklist entries."""
        # In production, delete expired entries
        return 0


def get_token_manager(session: AsyncSession) -> TokenManager:
    """Build a TokenManager bound to the given session."""
    return TokenManager(session)
