"""
Outbox pattern for reliable event publishing.

Events are written to the outbox table within the same transaction as the
business logic, then a relay worker publishes them to message brokers.
Guarantees at-least-once delivery with idempotent handlers.
"""

import json
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from socialerp.database import Base
from socialerp.tenancy import TenantMixin, get_current_tenant_id
from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column


class OutboxEventStatus(str, Enum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class OutboxEvent(BaseModel):
    """Schema for outbox events."""

    event_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    event_type: str  # e.g., "order.created", "payment.confirmed", "message.send"
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    status: OutboxEventStatus = OutboxEventStatus.PENDING
    retry_count: int = 0
    last_error: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    published_at: datetime | None = None


class Outbox(Base, TenantMixin):
    """
    Outbox table for reliable event publishing.

    Written within the same transaction as domain changes.
    Relay worker reads pending events and publishes them.
    """

    __tablename__ = "outbox"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OutboxEventStatus.PENDING.value, index=True
    )
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, default=datetime.utcnow, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_outbox_tenant_status_created", "tenant_id", "status", "created_at"),
        Index("ix_outbox_idempotency", "idempotency_key", unique=True),
    )


async def add_outbox_event(
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
    tenant_id: UUID | None = None,
    session: AsyncSession | None = None,
) -> UUID:
    """
    Add an event to the outbox within the current transaction.

    Must be called within a tenant context and active session.
    """
    from socialerp.database import get_session

    if tenant_id is None:
        tenant_id = get_current_tenant_id()
        if tenant_id is None:
            raise RuntimeError("No tenant context for outbox event")

    # Generate idempotency key if not provided
    if idempotency_key is None:
        import hashlib

        content = f"{tenant_id}:{event_type}:{json.dumps(payload, sort_keys=True)}"
        idempotency_key = hashlib.sha256(content.encode()).hexdigest()[:64]

    # Use provided session or create a new one
    if session is not None:
        # Use existing session (for tests)
        from sqlalchemy import select

        existing = await session.execute(
            select(Outbox).where(Outbox.idempotency_key == idempotency_key)
        )
        existing_event = existing.scalar_one_or_none()
        if existing_event:
            return existing_event.id

        event = Outbox(
            tenant_id=tenant_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        session.add(event)
        await session.flush()
        return event.id
    else:
        # Create new session (for production use)
        async with get_session() as new_session:
            from sqlalchemy import select

            existing = await new_session.execute(
                select(Outbox).where(Outbox.idempotency_key == idempotency_key)
            )
            if existing.scalar_one_or_none():
                return existing.scalar_one().id

            event = Outbox(
                tenant_id=tenant_id,
                event_type=event_type,
                payload=payload,
                idempotency_key=idempotency_key,
            )
            new_session.add(event)
            await new_session.flush()
            return event.id


# Convenience functions for common event types
async def publish_order_created(
    order_id: UUID, order_data: dict[str, Any], tenant_id: UUID, session: AsyncSession | None = None
) -> UUID:
    return await add_outbox_event(
        event_type="order.created",
        payload={"order_id": str(order_id), **order_data},
        idempotency_key=f"order_created:{order_id}",
        tenant_id=tenant_id,
        session=session,
    )


async def publish_payment_confirmed(
    payment_id: UUID,
    payment_data: dict[str, Any],
    tenant_id: UUID,
    session: AsyncSession | None = None,
) -> UUID:
    return await add_outbox_event(
        event_type="payment.confirmed",
        payload={"payment_id": str(payment_id), **payment_data},
        idempotency_key=f"payment_confirmed:{payment_id}",
        tenant_id=tenant_id,
        session=session,
    )


async def publish_message_send(
    channel: str,
    recipient: str,
    content: dict[str, Any],
    tenant_id: UUID,
    idempotency_key: str | None = None,
    session: AsyncSession | None = None,
) -> UUID:
    return await add_outbox_event(
        event_type="message.send",
        payload={"channel": channel, "recipient": recipient, "content": content},
        idempotency_key=idempotency_key
        or f"message_send:{channel}:{recipient}:{hash(json.dumps(content, sort_keys=True))}",
        tenant_id=tenant_id,
    )


async def publish_stock_changed(
    product_id: UUID,
    variant_id: UUID | None,
    delta: int,
    reason: str,
    tenant_id: UUID,
    session: AsyncSession | None = None,
) -> UUID:
    return await add_outbox_event(
        event_type="stock.changed",
        payload={
            "product_id": str(product_id),
            "variant_id": str(variant_id) if variant_id else None,
            "delta": delta,
            "reason": reason,
        },
        idempotency_key=f"stock_changed:{product_id}:{variant_id}:{reason}:{delta}",
        tenant_id=tenant_id,
    )


# =============================================================================
# RELAY WORKER (run as background task)
# =============================================================================


class OutboxRelay:
    """
    Relay worker that publishes outbox events to message brokers.

    Runs as a background task, polls for pending events, publishes them,
    and marks them as published. Handles retries with exponential backoff.
    """

    def __init__(
        self,
        batch_size: int = 100,
        poll_interval: float = 1.0,
        max_retries: int = 5,
    ):
        self.batch_size = batch_size
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._running = False

    def register_handler(self, event_type: str, handler: Callable[..., Any]) -> None:
        """Register a handler for an event type."""
        self._handlers[event_type] = handler

    async def start(self) -> None:
        """Start the relay worker."""
        self._running = True
        while self._running:
            await self._process_batch()
            import asyncio

            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Stop the relay worker."""
        self._running = False

    async def _process_batch(self, session: AsyncSession | None = None) -> None:
        from sqlalchemy import select

        if session is not None:
            # Use provided session (for tests)
            result = await session.execute(
                select(Outbox)
                .where(Outbox.status == OutboxEventStatus.PENDING.value)
                .order_by(Outbox.created_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            events = result.scalars().all()

            for event in events:
                await self._publish_event(session, event)
        else:
            # Create new session (for production use)
            from socialerp.database import get_session

            async with get_session() as session:
                result = await session.execute(
                    select(Outbox)
                    .where(Outbox.status == OutboxEventStatus.PENDING.value)
                    .order_by(Outbox.created_at)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
                events = result.scalars().all()

                for event in events:
                    await self._publish_event(session, event)

    async def _publish_event(self, session: AsyncSession, event: Outbox) -> None:
        handler = self._handlers.get(event.event_type)
        if not handler:
            event.status = OutboxEventStatus.FAILED.value
            event.last_error = f"No handler for event type: {event.event_type}"
            return

        try:
            await handler(event.payload, event.tenant_id)
            event.status = OutboxEventStatus.PUBLISHED.value
            event.published_at = datetime.utcnow()
        except Exception as e:
            event.retry_count += 1
            event.last_error = str(e)
            if event.retry_count >= self.max_retries:
                event.status = OutboxEventStatus.FAILED.value
            # Will be retried on next poll
