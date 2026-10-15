"""
API Key Management

Generation, validation, and management of API keys for programmatic access.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain.auth import ApiKeyCreate, ApiKeyList, ApiKeyResponse
from packages.core.models import ApiKey as ApiKeyModel
from packages.core.repositories import ApiKeyRepository
from packages.core.security import hash_password, verify_password


class ApiKeyManager:
    """Manages API key lifecycle."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = ApiKeyRepository(session)

    @staticmethod
    def generate_key(prefix: str = "sk") -> tuple[str, str, str]:
        """Generate a new API key.

        Returns:
            tuple: (full_key, key_hash, key_prefix)
        """
        # Generate random part
        random_part = secrets.token_urlsafe(32)
        full_key = f"{prefix}_{random_part}"

        # Hash for storage (using bcrypt via password hashing)
        key_hash = hash_password(full_key)

        # Prefix for identification (first 8 chars of hash)
        key_prefix = f"{prefix}_{secrets.token_urlsafe(4)}"

        return full_key, key_hash, key_prefix

    @staticmethod
    def verify_key(full_key: str, key_hash: str) -> bool:
        """Verify an API key against its hash."""
        return verify_password(full_key, key_hash)

    async def create(self, tenant_id: UUID, data: ApiKeyCreate) -> ApiKeyResponse:
        """Create a new API key for a tenant."""
        full_key, key_hash, key_prefix = self.generate_key()

        expires_at = None
        if data.expires_in_days:
            expires_at = datetime.now(UTC) + timedelta(days=data.expires_in_days)

        model = ApiKeyModel(
            tenant_id=tenant_id,
            name=data.name,
            key_hash=key_hash,
            scopes=data.scopes,
            expires_at=expires_at,
        )

        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)

        return ApiKeyResponse(
            id=model.id,
            tenant_id=model.tenant_id,
            name=model.name,
            key=full_key,  # Only returned on creation
            key_prefix=key_prefix,
            scopes=model.scopes,
            expires_at=model.expires_at,
            created_at=model.created_at,
            revoked_at=model.revoked_at,
        )

    async def validate(self, full_key: str) -> ApiKeyModel | None:
        """Validate an API key and return the model if valid."""
        # Extract prefix to find candidate keys
        # Since we store bcrypt hash, we need to check all active keys
        # In production, use a prefix index for performance

        from packages.core.models import ApiKey as ApiKeyModel

        result = await self.session.execute(
            select(ApiKeyModel)
            .where(ApiKeyModel.revoked_at.is_(None))
            .where(ApiKeyModel.expires_at.is_(None) | (ApiKeyModel.expires_at > datetime.now(UTC)))
        )
        keys = result.scalars().all()

        for key in keys:
            if verify_password(full_key, key.key_hash):
                # Update last used timestamp
                await self.session.execute(
                    update(ApiKeyModel)
                    .where(ApiKeyModel.id == key.id)
                    .values(last_used_at=datetime.now(UTC))
                )
                return key

        return None

    async def revoke(self, tenant_id: UUID, key_id: UUID) -> bool:
        """Revoke an API key."""
        from packages.core.models import ApiKey as ApiKeyModel

        result = await self.session.execute(
            update(ApiKeyModel)
            .where(ApiKeyModel.id == key_id)
            .where(ApiKeyModel.tenant_id == tenant_id)
            .values(revoked_at=datetime.now(UTC))
        )
        rowcount = getattr(result, "rowcount", 0) or 0
        return bool(rowcount > 0)

    async def revoke_all(self, tenant_id: UUID) -> int:
        """Revoke all API keys for a tenant."""
        from packages.core.models import ApiKey as ApiKeyModel

        result = await self.session.execute(
            update(ApiKeyModel)
            .where(ApiKeyModel.tenant_id == tenant_id)
            .where(ApiKeyModel.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        rowcount = getattr(result, "rowcount", 0) or 0
        return int(rowcount)

    async def list_keys(self, tenant_id: UUID, page: int = 1, page_size: int = 20) -> ApiKeyList:
        """List API keys for a tenant."""
        return await self.repo.list(tenant_id, page, page_size)

    async def get_key(self, tenant_id: UUID, key_id: UUID) -> Any | None:
        """Get API key details (without the full key)."""
        return await self.repo.get(key_id)


# Need to import timedelta
