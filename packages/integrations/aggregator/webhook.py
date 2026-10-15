"""
Aggregator Payment Webhook Receiver

FastAPI endpoint for receiving payment aggregator callbacks (Pesapal, IntaSend, etc.)
with signature verification, dedupe, and normalization.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from socialerp.settings import get_settings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.database import get_db_session
from packages.core.domain.payment import (
    CallbackVerificationError,
    PaymentProvider,
)
from packages.core.payments import (
    PaymentNotFoundError,
    PaymentService,
)
from packages.integrations.aggregator import build_aggregator_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["Aggregator Webhooks"])


def get_webhook_settings() -> Any:
    """Get current settings for webhook (avoids module-level caching issues)."""
    return get_settings()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    async for session in get_db_session():
        yield session


@lru_cache(maxsize=1)
def get_aggregator_provider() -> PaymentProvider:
    """Build the configured aggregator provider (cached per process)."""
    return build_aggregator_provider()


@router.post("/aggregator/{tenant_id}/{callback_token}")
async def aggregator_callback(
    tenant_id: UUID,
    callback_token: str,
    payload: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    provider: PaymentProvider = Depends(get_aggregator_provider),
    x_webhook_signature: str | None = Header(None, alias="x-webhook-signature"),
    x_signature: str | None = Header(None, alias="x-signature"),
) -> dict[str, object]:
    """Receive an aggregator payment callback (unauthenticated, token-guarded).

    Path: /api/v1/webhooks/aggregator/{tenant_id}/{callback_token}
    - tenant_id: The tenant this callback belongs to
    - callback_token: Shared secret to verify the callback source

    Headers:
    - x-webhook-signature or x-signature: HMAC signature for verification

    Body: Aggregator-normalized callback payload containing:
    - checkout_request_id: The aggregator's transaction reference
    - status: completed | failed | cancelled | pending
    - mpesa_receipt_number: M-Pesa receipt (if completed)
    - amount: Amount in KSH (or amount_cents)
    - phone_number: Customer phone number
    - transaction_date: Transaction timestamp
    - result_code: Aggregator result code
    - result_desc / message: Human-readable description
    """
    s = get_webhook_settings()
    expected_token = s.AGGREGATOR_WEBHOOK_SECRET
    if expected_token and callback_token != expected_token:
        logger.warning(f"Invalid aggregator callback token for tenant {tenant_id}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid callback token")

    # Set RLS tenant context
    await db.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )

    # Verify callback using provider
    headers = {}
    if x_webhook_signature:
        headers["x-webhook-signature"] = x_webhook_signature
    if x_signature:
        headers["x-signature"] = x_signature

    if not await provider.validate_callback(payload, headers):
        logger.warning(f"Aggregator callback validation failed for tenant {tenant_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid callback payload"
        )

    # Parse the callback into standardized result
    try:
        result = await provider.parse_callback(payload)
    except Exception as exc:
        logger.exception("Failed to parse aggregator callback")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed callback"
        ) from exc

    # Process through payment service
    service = PaymentService(db, provider)
    try:
        await service.handle_callback(tenant_id=tenant_id, payload=payload, headers=headers)
    except CallbackVerificationError as exc:
        logger.warning(f"Aggregator callback verification failed: {exc}")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except PaymentNotFoundError:
        # Unknown checkout id: acknowledge so the aggregator stops retrying
        logger.info(
            f"Aggregator callback for unknown checkout_request_id: {result.checkout_request_id}"
        )
        pass

    await db.commit()
    # Always ack verified callbacks; a non-2xx would make the aggregator retry
    return {"status": "accepted"}


__all__ = ["router"]
