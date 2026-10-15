"""
Idempotency keys for deduplication.

Ensures exactly-once semantics for webhooks, payment requests, and API calls.
Keys are scoped by tenant and operation type, with TTL-based expiration.
"""

import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from socialerp.database import Base, get_session
from socialerp.tenancy import TenantMixin, get_current_tenant_id
from sqlalchemy import CursorResult, Index, String, delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column


class IdempotencyKey(BaseModel):
    """Schema for idempotency key records."""

    key: str
    tenant_id: UUID
    scope: str  # e.g., "webhook:meta", "payment:stk_push", "api:order_create"
    request_hash: str  # Hash of request payload for content-based dedup
    response: dict[str, Any] | None = None
    status: str = "pending"  # pending, completed, failed
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


class IdempotencyRecord(Base, TenantMixin):
    """
    Idempotency key storage.

    Deduplicates requests by (tenant_id, scope, key) or (tenant_id, scope, request_hash).
    Automatically expires after TTL.
    """

    __tablename__ = "idempotency_keys"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_idempotency_tenant_scope_key", "tenant_id", "scope", "key", unique=True),
        Index(
            "ix_idempotency_tenant_scope_hash", "tenant_id", "scope", "request_hash", unique=True
        ),
        Index("ix_idempotency_expires", "expires_at"),
    )


DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24 hours


def compute_request_hash(payload: dict[str, Any]) -> str:
    """Compute deterministic hash of request payload."""
    import json

    content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()[:64]


async def check_idempotency(
    key: str,
    scope: str,
    payload: dict[str, Any],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    tenant_id: UUID | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """
    Check if request has been processed before.

    Returns (is_duplicate, cached_response).
    If not duplicate, reserves the key for this request.
    """
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise RuntimeError("No tenant context for idempotency check")

    request_hash = compute_request_hash(payload)
    expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds)

    async with get_session() as session:
        # Try to find existing key (by key or by request hash)
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            if existing.status == "completed":
                return True, existing.response
            elif existing.status == "pending":
                # Request is in progress (rare race condition)
                return True, None
            else:
                # Failed or any other status - allow retry by deleting old record
                await session.delete(existing)
                await session.flush()

        # Also check by request hash (content-based dedup)
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.request_hash == request_hash,
        )
        result = await session.execute(stmt)
        existing_by_hash = result.scalar_one_or_none()

        if existing_by_hash:
            if existing_by_hash.status == "completed":
                return True, existing_by_hash.response
            elif existing_by_hash.status == "pending":
                return True, None
            else:
                await session.delete(existing_by_hash)
                await session.flush()

        # Reserve the key
        record = IdempotencyRecord(
            tenant_id=tenant_id,
            key=key,
            scope=scope,
            request_hash=request_hash,
            status="pending",
            expires_at=expires_at,
        )
        session.add(record)
        await session.flush()

    return False, None


async def complete_idempotency(
    key: str,
    scope: str,
    response: dict[str, Any],
    tenant_id: UUID | None = None,
) -> None:
    """Mark idempotency key as completed with response."""
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise RuntimeError("No tenant context for idempotency completion")

    async with get_session() as session:
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
            IdempotencyRecord.status == "pending",
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.status = "completed"
            record.response = response
            record.completed_at = datetime.utcnow()


async def fail_idempotency(
    key: str,
    scope: str,
    tenant_id: UUID | None = None,
) -> None:
    """Mark idempotency key as failed (allows retry by deleting the record)."""
    if tenant_id is None:
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise RuntimeError("No tenant context for idempotency failure")

    async with get_session() as session:
        stmt = select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            await session.delete(record)


async def cleanup_expired_idempotency_keys() -> int:
    """Clean up expired idempotency keys (run as scheduled job)."""
    async with get_session() as session:
        result = await session.execute(
            delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < datetime.utcnow())
        )
        return cast("CursorResult[Any]", result).rowcount


# Decorator for automatic idempotency handling
def idempotent(
    scope: str, key_param: str = "idempotency_key", ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """
    Decorator to make an async function idempotent.

    Usage:
        @idempotent(scope="api:order_create", key_param="idempotency_key")
        async def create_order(idempotency_key: str, payload: dict):
            ...
    """

    def decorator(
        func: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Extract idempotency key from kwargs
            idempotency_key = kwargs.get(key_param)
            if not idempotency_key:
                raise ValueError(f"Missing idempotency key parameter: {key_param}")

            # Extract payload (all other kwargs)
            payload = {k: v for k, v in kwargs.items() if k != key_param}

            is_dup, cached = await check_idempotency(
                key=idempotency_key,
                scope=scope,
                payload=payload,
                ttl_seconds=ttl_seconds,
            )

            if is_dup:
                if cached is not None:
                    return cached
                # In progress - wait and retry? For now raise
                raise RuntimeError("Request already in progress")

            try:
                result = await func(*args, **kwargs)
                await complete_idempotency(key=idempotency_key, scope=scope, response=result)
                return result
            except Exception:
                await fail_idempotency(key=idempotency_key, scope=scope)
                raise

        return wrapper

    return decorator
