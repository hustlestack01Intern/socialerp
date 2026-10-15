"""
Payments API Routes (Phase 4)

Thin HTTP layer over :class:`packages.core.payments.PaymentService`:

- ``POST /api/v1/payments/orders/{order_id}/stk`` (authenticated): send an
  M-Pesa STK push for a payable order.
- ``GET  /api/v1/payments/orders/{order_id}`` (authenticated): payment history
  for an order.
- ``POST /api/v1/payments/stk/callback/{tenant_id}/{callback_token}``
  (unauthenticated): Safaricom Daraja result callback.

Tenant resolution for the callback: the path carries the tenant id plus a
shared secret, so the handler can set the RLS tenant context before looking the
payment up. This keeps every tenant table under RLS with no un-scoped lookup
table (ADR 0010).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from socialerp.settings import get_settings
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.dependencies import get_current_tenant_id, get_db
from packages.core.domain.payment import (
    CallbackVerificationError,
    PaymentProvider,
    PaymentProviderError,
)
from packages.core.models.payment import Payment
from packages.core.payments import (
    OrderNotFoundError,
    OrderNotPayableError,
    PaymentNotFoundError,
    PaymentService,
)
from packages.integrations.daraja import build_direct_daraja_provider

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


# =============================================================================
# Provider dependency
# =============================================================================


@lru_cache(maxsize=1)
def get_payment_provider() -> PaymentProvider:
    """Build the configured M-Pesa provider (cached per process)."""
    return build_direct_daraja_provider()


# =============================================================================
# Schemas
# =============================================================================


class STKPushBody(BaseModel):
    """Body for initiating an STK push."""

    phone_number: str = Field(..., pattern=r"^254[0-9]{9}$")


class STKPushResult(BaseModel):
    payment_id: str
    order_id: str
    checkout_request_id: str | None
    status: str
    amount_cents: int


class PaymentView(BaseModel):
    id: str
    order_id: str
    phone_number: str
    amount_cents: int
    status: str
    checkout_request_id: str | None
    mpesa_receipt_number: str | None
    result_code: int | None
    failure_reason: str | None


def _status_value(value: object) -> str:
    """Render an enum-or-string status as its plain value."""
    return str(getattr(value, "value", value))


def _view(payment: Payment) -> PaymentView:
    return PaymentView(
        id=str(payment.id),
        order_id=str(payment.order_id),
        phone_number=payment.phone_number,
        amount_cents=payment.amount_cents,
        status=_status_value(payment.status),
        checkout_request_id=payment.checkout_request_id,
        mpesa_receipt_number=payment.mpesa_receipt_number,
        result_code=payment.result_code,
        failure_reason=payment.failure_reason,
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/orders/{order_id}/stk", response_model=STKPushResult, status_code=202)
async def initiate_stk(
    order_id: UUID,
    body: STKPushBody,
    request: Request,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[PaymentProvider, Depends(get_payment_provider)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> STKPushResult:
    """Send an STK push for a payable order."""
    s = get_settings()
    callback_url = s.DARAJA_CALLBACK_URL or (
        f"{str(request.base_url).rstrip('/')}"
        f"/api/v1/payments/stk/callback/{tenant_id}/{s.DARAJA_CALLBACK_TOKEN}"
    )
    service = PaymentService(db, provider)
    try:
        payment = await service.initiate_stk_payment(
            tenant_id=tenant_id,
            order_id=order_id,
            phone_number=body.phone_number,
            callback_url=callback_url,
            idempotency_key=idempotency_key or f"stk:{order_id}:{body.phone_number}",
        )
    except OrderNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OrderNotPayableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PaymentProviderError as exc:
        # Persist the failed attempt before surfacing the error.
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await db.commit()
    return STKPushResult(
        payment_id=str(payment.id),
        order_id=str(payment.order_id),
        checkout_request_id=payment.checkout_request_id,
        status=_status_value(payment.status),
        amount_cents=payment.amount_cents,
    )


@router.get("/orders/{order_id}", response_model=list[PaymentView])
async def list_order_payments(
    order_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[PaymentView]:
    """List payment attempts for an order, newest first."""
    stmt = (
        select(Payment)
        .where(Payment.tenant_id == tenant_id, Payment.order_id == order_id)
        .order_by(Payment.created_at.desc())
    )
    payments = (await db.execute(stmt)).scalars().all()
    return [_view(p) for p in payments]


@router.post("/stk/callback/{tenant_id}/{callback_token}")
async def stk_callback(
    tenant_id: UUID,
    callback_token: str,
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[PaymentProvider, Depends(get_payment_provider)],
    x_callback_token: Annotated[str | None, Header(alias="x-callback-token")] = None,
) -> dict[str, object]:
    """Receive a Daraja STK result callback (unauthenticated, token-guarded)."""
    s = get_settings()
    expected = s.DARAJA_CALLBACK_TOKEN
    if expected and callback_token != expected:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid callback token")

    # Attribute the callback to a tenant, then enforce RLS for the lookup.
    await db.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )

    service = PaymentService(db, provider)
    headers = {"x-callback-token": x_callback_token} if x_callback_token else {}
    try:
        await service.handle_callback(tenant_id=tenant_id, payload=payload, headers=headers)
    except CallbackVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except PaymentNotFoundError:
        # Unknown checkout id: acknowledge so the provider stops retrying.
        pass
    await db.commit()
    # Always ack verified callbacks; a non-2xx would make Daraja retry.
    return {"ResultCode": 0, "ResultDesc": "Accepted"}
