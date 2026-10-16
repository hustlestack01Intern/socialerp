"""
Delivery API Routes (Phase 7b)

Thin, tenant-scoped HTTP layer over :class:`packages.core.delivery.DeliveryService`:

- ``POST   /api/v1/delivery/zones``                 (authenticated): create a zone.
- ``GET    /api/v1/delivery/zones``                 (authenticated): list zones.
- ``PATCH  /api/v1/delivery/zones/{zone_id}``       (authenticated): update a zone.
- ``POST   /api/v1/delivery/riders``                (authenticated): create a rider.
- ``GET    /api/v1/delivery/riders``                (authenticated): list riders.
- ``POST   /api/v1/delivery/riders/{rider_id}/deactivate`` (authenticated).
- ``POST   /api/v1/delivery/orders/{order_id}/dispatch``   (authenticated): assign.
- ``GET    /api/v1/delivery/deliveries``            (authenticated): list.
- ``GET    /api/v1/delivery/deliveries/{delivery_id}`` (authenticated): get.
- ``POST   /api/v1/delivery/deliveries/{delivery_id}/picked-up`` (authenticated).
- ``POST   /api/v1/delivery/deliveries/{delivery_id}/arrived`` (authenticated):
  for a pay-on-delivery order this fires the STK Push (goods already moving).
- ``POST   /api/v1/delivery/deliveries/{delivery_id}/delivered`` (authenticated).
- ``POST   /api/v1/delivery/deliveries/{delivery_id}/failed``    (authenticated).
- ``POST   /api/v1/delivery/deliveries/{delivery_id}/retry``     (authenticated).

Fee arithmetic lives only in ``DeliveryService``. The POD trigger reuses the
existing STK payload construction (see ``routes/payments.py``) so both entry
points normalise the callback URL the same way.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from socialerp.settings import get_settings
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.routes.payments import get_tenant_payment_provider
from packages.core.auth.dependencies import get_current_tenant_id, get_db
from packages.core.delivery import (
    DeliveryError,
    DeliveryService,
    OrderNotDispatchableError,
    PaymentNotCollectedError,
    ProofOfDeliveryRequiredError,
    RiderNotFoundError,
    ZoneNotFoundError,
)
from packages.core.domain.delivery import IllegalDeliveryTransitionError
from packages.core.domain.order import IllegalTransitionError, PaymentMethod
from packages.core.domain.payment import PaymentProviderError
from packages.core.orders import OrderNotFoundError
from packages.core.orders.service import is_order_paid
from packages.core.payments import OrderNotPayableError, PaymentService
from packages.core.repositories import OrderRepository

router = APIRouter(prefix="/api/v1/delivery", tags=["delivery"])


# =============================================================================
# Schemas
# =============================================================================


class CreateZoneBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    fee_cents: int = Field(ge=0)


class UpdateZoneBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    fee_cents: int | None = Field(default=None, ge=0)
    active: bool | None = None


class CreateRiderBody(BaseModel):
    phone: str = Field(min_length=7, max_length=20)
    name: str = Field(min_length=1, max_length=100)


class DispatchBody(BaseModel):
    rider_id: UUID
    zone_id: UUID
    free_delivery_threshold_cents: int = Field(default=0, ge=0)


class DeliveredBody(BaseModel):
    proof_photo: str | None = None
    notes: str | None = None


class FailedBody(BaseModel):
    reason: str = Field(min_length=1, max_length=50)
    notes: str | None = None


class ZoneView(BaseModel):
    id: str
    name: str
    fee_cents: int
    active: bool


class RiderView(BaseModel):
    id: str
    phone: str
    name: str
    active: bool


class DeliveryView(BaseModel):
    id: str
    order_id: str
    rider_id: str
    zone_id: str
    fee_cents: int
    status: str
    failure_reason: str | None
    proof_photo: str | None
    notes: str | None
    picked_up_at: str | None
    arrived_at: str | None
    delivered_at: str | None


class STKTriggerView(BaseModel):
    payment_id: str
    order_id: str
    checkout_request_id: str | None
    status: str
    amount_cents: int


class ArrivedResult(BaseModel):
    delivery: DeliveryView
    stk: STKTriggerView | None = None
    stk_error: str | None = None


class DeliveryListResponse(BaseModel):
    items: list[DeliveryView]
    total: int


# =============================================================================
# Serialization
# =============================================================================


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _zone(zone: Any) -> ZoneView:
    return ZoneView(id=str(zone.id), name=zone.name, fee_cents=zone.fee_cents, active=zone.active)


def _rider(rider: Any) -> RiderView:
    return RiderView(id=str(rider.id), phone=rider.phone, name=rider.name, active=rider.active)


def _delivery(delivery: Any) -> DeliveryView:
    return DeliveryView(
        id=str(delivery.id),
        order_id=str(delivery.order_id),
        rider_id=str(delivery.rider_id),
        zone_id=str(delivery.zone_id),
        fee_cents=delivery.fee_cents,
        status=delivery.status,
        failure_reason=delivery.failure_reason,
        proof_photo=delivery.proof_photo,
        notes=delivery.notes,
        picked_up_at=_iso(delivery.picked_up_at),
        arrived_at=_iso(delivery.arrived_at),
        delivered_at=_iso(delivery.delivered_at),
    )


def _status_value(value: Any) -> str:
    return str(getattr(value, "value", value))


# =============================================================================
# Zones
# =============================================================================


@router.post("/zones", response_model=ZoneView, status_code=201)
async def create_zone(
    body: CreateZoneBody,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ZoneView:
    service = DeliveryService(db)
    zone = await service.create_zone(tenant_id, name=body.name, fee_cents=body.fee_cents)
    await db.commit()
    return _zone(zone)


@router.get("/zones", response_model=list[ZoneView])
async def list_zones(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    active_only: bool = False,
) -> list[ZoneView]:
    service = DeliveryService(db)
    return [_zone(z) for z in await service.list_zones(tenant_id, active_only=active_only)]


@router.patch("/zones/{zone_id}", response_model=ZoneView)
async def update_zone(
    zone_id: UUID,
    body: UpdateZoneBody,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ZoneView:
    service = DeliveryService(db)
    try:
        zone = await service.update_zone(
            tenant_id, zone_id, name=body.name, fee_cents=body.fee_cents, active=body.active
        )
    except ZoneNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return _zone(zone)


# =============================================================================
# Riders
# =============================================================================


@router.post("/riders", response_model=RiderView, status_code=201)
async def create_rider(
    body: CreateRiderBody,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RiderView:
    service = DeliveryService(db)
    rider = await service.create_rider(tenant_id, phone=body.phone, name=body.name)
    await db.commit()
    return _rider(rider)


@router.get("/riders", response_model=list[RiderView])
async def list_riders(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    active_only: bool = False,
) -> list[RiderView]:
    service = DeliveryService(db)
    return [_rider(r) for r in await service.list_riders(tenant_id, active_only=active_only)]


@router.post("/riders/{rider_id}/deactivate", response_model=RiderView)
async def deactivate_rider(
    rider_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RiderView:
    service = DeliveryService(db)
    try:
        rider = await service.deactivate_rider(tenant_id, rider_id)
    except RiderNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return _rider(rider)


# =============================================================================
# Deliveries
# =============================================================================


@router.post("/orders/{order_id}/dispatch", response_model=DeliveryView, status_code=201)
async def dispatch(
    order_id: UUID,
    body: DispatchBody,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeliveryView:
    service = DeliveryService(db)
    try:
        delivery = await service.dispatch(
            tenant_id,
            order_id=order_id,
            rider_id=body.rider_id,
            zone_id=body.zone_id,
            free_delivery_threshold_cents=body.free_delivery_threshold_cents,
        )
    except OrderNotDispatchableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (RiderNotFoundError, ZoneNotFoundError, OrderNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return _delivery(delivery)


@router.get("/deliveries", response_model=DeliveryListResponse)
async def list_deliveries(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: str | None = Query(None, alias="status"),
) -> DeliveryListResponse:
    service = DeliveryService(db)
    deliveries = await service.list_deliveries(tenant_id, status=status_filter)
    return DeliveryListResponse(items=[_delivery(d) for d in deliveries], total=len(deliveries))


@router.get("/deliveries/{delivery_id}", response_model=DeliveryView)
async def get_delivery(
    delivery_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeliveryView:
    service = DeliveryService(db)
    delivery = await service.get_delivery(tenant_id, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="Delivery not found")
    return _delivery(delivery)


@router.post("/deliveries/{delivery_id}/picked-up", response_model=DeliveryView)
async def mark_picked_up(
    delivery_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeliveryView:
    service = DeliveryService(db)
    try:
        delivery = await service.mark_picked_up(tenant_id, delivery_id)
    except (IllegalDeliveryTransitionError, IllegalTransitionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeliveryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return _delivery(delivery)


@router.post("/deliveries/{delivery_id}/arrived", response_model=ArrivedResult)
async def mark_arrived(
    delivery_id: UUID,
    request: Request,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ArrivedResult:
    """Mark arrival; for a POD order, request payment (STK Push) on the spot.

    The payment provider is resolved lazily, only when an STK actually needs to
    fire: a prepay arrival must not depend on the tenant's provider being
    configured. Any trigger failure is reported in ``stk_error`` and the arrival
    is still committed.
    """
    service = DeliveryService(db)
    try:
        delivery = await service.mark_arrived(tenant_id, delivery_id)
    except (IllegalDeliveryTransitionError, IllegalTransitionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeliveryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    order = await OrderRepository(db).get(tenant_id, delivery.order_id)
    stk: STKTriggerView | None = None
    stk_error: str | None = None
    if (
        order is not None
        and order.payment_method == PaymentMethod.MPESA_PAY_ON_DELIVERY.value
        and not is_order_paid(order)
    ):
        settings = get_settings()
        callback_url = settings.DARAJA_CALLBACK_URL or (
            f"{str(request.base_url).rstrip('/')}"
            f"/api/v1/payments/stk/callback/{tenant_id}/{settings.DARAJA_CALLBACK_TOKEN}"
        )
        try:
            provider = await get_tenant_payment_provider(tenant_id, db)
            payment = await PaymentService(db, provider).initiate_stk_payment(
                tenant_id=tenant_id,
                order_id=delivery.order_id,
                phone_number=order.customer_phone,
                callback_url=callback_url,
                idempotency_key=f"delivery:{delivery_id}:arrived",
            )
        except ValueError as exc:
            # No usable provider configured for this tenant; do not 500 the
            # arrival. The owner must finish payment setup.
            stk_error = f"payment provider unavailable: {exc}"
        except PaymentProviderError as exc:
            stk_error = str(exc)
        except OrderNotPayableError as exc:
            # Raced: the order was paid (or left a payable state) between the
            # check and the STK call. Nothing left to charge.
            stk_error = str(exc)
        else:
            stk = STKTriggerView(
                payment_id=str(payment.id),
                order_id=str(payment.order_id),
                checkout_request_id=payment.checkout_request_id,
                status=_status_value(payment.status),
                amount_cents=payment.amount_cents,
            )

    await db.commit()
    return ArrivedResult(delivery=_delivery(delivery), stk=stk, stk_error=stk_error)


@router.post("/deliveries/{delivery_id}/delivered", response_model=DeliveryView)
async def mark_delivered(
    delivery_id: UUID,
    body: DeliveredBody,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeliveryView:
    service = DeliveryService(db)
    try:
        delivery = await service.mark_delivered(
            tenant_id, delivery_id, proof_photo=body.proof_photo, notes=body.notes
        )
    except (PaymentNotCollectedError, ProofOfDeliveryRequiredError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (IllegalDeliveryTransitionError, IllegalTransitionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeliveryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return _delivery(delivery)


@router.post("/deliveries/{delivery_id}/failed", response_model=DeliveryView)
async def mark_failed(
    delivery_id: UUID,
    body: FailedBody,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeliveryView:
    service = DeliveryService(db)
    try:
        delivery = await service.mark_failed(
            tenant_id, delivery_id, reason=body.reason, notes=body.notes
        )
    except (IllegalDeliveryTransitionError, IllegalTransitionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeliveryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return _delivery(delivery)


@router.post("/deliveries/{delivery_id}/retry", response_model=DeliveryView)
async def retry_after_failure(
    delivery_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DeliveryView:
    service = DeliveryService(db)
    try:
        delivery = await service.retry_after_failure(tenant_id, delivery_id)
    except (IllegalDeliveryTransitionError, IllegalTransitionError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DeliveryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return _delivery(delivery)


__all__ = ["router"]
