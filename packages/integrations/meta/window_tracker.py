"""
24h Customer Service Window Tracker

Tracks the WhatsApp 24-hour customer service window per tenant and contact.
Under Coexistence mode, the limit is ~20 messages/second.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.config import get_settings
from packages.core.domain import CustomerPhone, TenantId
from packages.core.models import WhatsAppContact


@dataclass
class WindowState:
    """Current state of a 24h window."""

    is_open: bool
    expires_at: datetime | None
    last_customer_message_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_open": self.is_open,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "last_customer_message_at": self.last_customer_message_at.isoformat()
            if self.last_customer_message_at
            else None,
        }


class WindowTracker:
    """
    Tracks 24h customer service windows per (tenant_id, contact_phone).

    The window opens when a customer sends a message and stays open for 24 hours
    from the last customer message. Within the window, free-form messages are
    allowed. Outside the window, only template messages are permitted.

    Uses database for persistence (survives restarts) and in-memory cache for speed.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

        # In-memory cache: {(tenant_id, phone): (expires_at, last_message_at)}
        self._cache: dict[tuple[UUID, str], tuple[datetime, datetime | None]] = {}
        self._cache_ttl = timedelta(minutes=5)
        self._last_cache_cleanup = time.time()

    async def get_window_state(self, tenant_id: TenantId, phone: CustomerPhone) -> WindowState:
        """Get current window state for a contact."""
        # Check cache first
        cache_key = (tenant_id, str(phone))
        now = datetime.now(UTC)

        if cache_key in self._cache:
            expires_at, last_msg_at = self._cache[cache_key]
            if expires_at > now:
                return WindowState(
                    is_open=True,
                    expires_at=expires_at,
                    last_customer_message_at=last_msg_at,
                )
            else:
                # Expired - remove from cache
                del self._cache[cache_key]

        # Fallback to database
        result = await self.session.execute(
            select(WhatsAppContact).where(
                WhatsAppContact.tenant_id == tenant_id,
                WhatsAppContact.phone == str(phone),
            )
        )
        contact = result.scalar_one_or_none()

        if not contact:
            return WindowState(is_open=False, expires_at=None, last_customer_message_at=None)

        if contact.window_expires_at and contact.window_expires_at > now:
            # Update cache
            self._cache[cache_key] = (contact.window_expires_at, contact.last_customer_message_at)
            return WindowState(
                is_open=True,
                expires_at=contact.window_expires_at,
                last_customer_message_at=contact.last_customer_message_at,
            )

        return WindowState(
            is_open=False,
            expires_at=None,
            last_customer_message_at=contact.last_customer_message_at,
        )

    async def can_send_freeform(self, tenant_id: TenantId, phone: CustomerPhone) -> bool:
        """Check if free-form message can be sent to this contact."""
        state = await self.get_window_state(tenant_id, phone)
        return state.is_open

    async def update_on_customer_message(
        self, tenant_id: TenantId, phone: CustomerPhone
    ) -> WindowState:
        """Update window when customer sends a message."""
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=24)

        # Update database
        result = await self.session.execute(
            select(WhatsAppContact).where(
                WhatsAppContact.tenant_id == tenant_id,
                WhatsAppContact.phone == str(phone),
            )
        )
        contact = result.scalar_one_or_none()

        if contact:
            contact.last_customer_message_at = now
            contact.window_expires_at = expires_at
            contact.updated_at = now
        else:
            # Contact doesn't exist - this shouldn't happen in normal flow
            # but handle gracefully
            pass

        # Update cache
        cache_key = (tenant_id, str(phone))
        self._cache[cache_key] = (expires_at, now)

        return WindowState(
            is_open=True,
            expires_at=expires_at,
            last_customer_message_at=now,
        )

    async def close_window(self, tenant_id: TenantId, phone: CustomerPhone) -> None:
        """Manually close a window (e.g., when conversation is closed)."""
        cache_key = (tenant_id, str(phone))
        if cache_key in self._cache:
            del self._cache[cache_key]

        await self.session.execute(
            update(WhatsAppContact)
            .where(
                WhatsAppContact.tenant_id == tenant_id,
                WhatsAppContact.phone == str(phone),
            )
            .values(window_expires_at=None)
        )

    def _cleanup_cache(self) -> None:
        """Remove expired entries from cache."""
        now = time.time()
        if now - self._last_cache_cleanup < 300:  # Every 5 minutes
            return

        current_time = datetime.now(UTC)
        expired_keys = [
            key for key, (expires_at, _) in self._cache.items() if expires_at <= current_time
        ]
        for key in expired_keys:
            del self._cache[key]

        self._last_cache_cleanup = now


# =============================================================================
# Rate Limiter
# =============================================================================


@dataclass
class RateLimitBucket:
    """Token bucket for rate limiting."""

    tokens: float
    last_refill: float
    capacity: int
    refill_rate: float  # tokens per second

    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if successful."""
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    def _refill(self) -> None:
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now


class RateLimiter:
    """
    Token bucket rate limiter for outbound WhatsApp messages.

    Under Coexistence mode: ~20 messages/second per tenant.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

        # In-memory buckets: {tenant_id: RateLimitBucket}
        self._buckets: dict[UUID, RateLimitBucket] = {}

        # Coexistence limit: 20 msg/s
        self.default_capacity = 20
        self.default_refill_rate = 20.0  # tokens per second

    def _get_bucket(self, tenant_id: TenantId) -> RateLimitBucket:
        """Get or create rate limit bucket for tenant."""
        if tenant_id not in self._buckets:
            self._buckets[tenant_id] = RateLimitBucket(
                tokens=self.default_capacity,
                last_refill=time.time(),
                capacity=self.default_capacity,
                refill_rate=self.default_refill_rate,
            )
        return self._buckets[tenant_id]

    async def check_rate_limit(
        self, tenant_id: TenantId, tokens: int = 1
    ) -> tuple[bool, dict[str, Any]]:
        """
        Check if request is within rate limit.

        Returns:
            (allowed, metadata) where metadata includes retry_after if limited
        """
        bucket = self._get_bucket(tenant_id)
        allowed = bucket.consume(tokens)

        if not allowed:
            # Calculate retry-after
            tokens_needed = tokens - bucket.tokens
            retry_after = tokens_needed / bucket.refill_rate
            return False, {
                "allowed": False,
                "retry_after": retry_after,
                "limit": bucket.capacity,
                "remaining": int(bucket.tokens),
            }

        return True, {
            "allowed": True,
            "limit": bucket.capacity,
            "remaining": int(bucket.tokens),
        }

    async def get_rate_limit_status(self, tenant_id: TenantId) -> dict[str, Any]:
        """Get current rate limit status for a tenant."""
        bucket = self._get_bucket(tenant_id)
        bucket._refill()
        return {
            "limit": bucket.capacity,
            "remaining": int(bucket.tokens),
            "reset_after": (bucket.capacity - bucket.tokens) / bucket.refill_rate,
        }

    async def reset_tenant(self, tenant_id: TenantId) -> None:
        """Reset rate limit for a tenant (e.g., after plan upgrade)."""
        if tenant_id in self._buckets:
            del self._buckets[tenant_id]


# =============================================================================
# Combined Manager
# =============================================================================


class WhatsAppLimitsManager:
    """Combined window tracker and rate limiter."""

    def __init__(self, session: AsyncSession):
        self.window_tracker = WindowTracker(session)
        self.rate_limiter = RateLimiter(session)

    async def can_send_message(
        self,
        tenant_id: TenantId,
        phone: CustomerPhone,
        is_template: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Check if a message can be sent.

        Args:
            tenant_id: Tenant ID
            phone: Recipient phone number
            is_template: Whether this is a template message (bypasses 24h window)

        Returns:
            (allowed, metadata)
        """
        # Check rate limit first
        rate_allowed, rate_meta = await self.rate_limiter.check_rate_limit(tenant_id)
        if not rate_allowed:
            return False, {"error": "rate_limited", **rate_meta}

        # Check 24h window for free-form messages
        if not is_template:
            window_allowed = await self.window_tracker.can_send_freeform(tenant_id, phone)
            if not window_allowed:
                return False, {
                    "error": "window_closed",
                    "message": "Outside 24h customer service window. Use template message.",
                }

        return True, {"rate_limit": rate_meta}

    async def send_message(
        self,
        tenant_id: TenantId,
        phone: CustomerPhone,
        is_template: bool = False,
    ) -> tuple[bool, dict[str, Any]]:
        """
        Record a message send attempt (updates limits).

        Should be called after successful send to Meta API.
        """
        # Consume rate limit token
        await self.rate_limiter.check_rate_limit(tenant_id, tokens=1)

        # Update window if not template
        if not is_template:
            await self.window_tracker.update_on_customer_message(tenant_id, phone)

        return True, {}
