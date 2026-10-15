"""
Orders API Routes (Phase 6)

Thin, tenant-scoped HTTP layer over :class:`packages.core.orders.OrderService`:

- ``POST   /api/v1/orders``                (authenticated): create an order
  (catalog-validated, stock reserved, moved to ``awaiting_payment``).
- ``GET    /api/v1/orders``                (authenticated): list orders.
- ``GET    /api/v1/orders/{order_id}``     (authenticated): order with items.
- ``POST   /api/v1/orders/{order_id}/cancel`` (authenticated): cancel + release.
- ``POST   /api/v1/orders/{order_id}/pack``   (authenticated): ``paid -> packed``.

Prices are never accepted from the client; the service takes them from the
catalog. Initiate payment with ``POST /api/v1/payments/orders/{id}/stk``.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.auth.dependencies import get_current_tenant_id, get_db
from packages.core.domain.order import IllegalTransitionError, PaymentMethod
from packages.core.models.order import Order, OrderItem
from packages.core.orders import (
    CreateOrderCommand,
    InsufficientStockError,
    ItemNotFoundError,
    ItemUnavailableError,
    OrderLine,
    OrderNotFoundError,
    OrderService,
    OrderValidationError,
    PriceMismatchError,
)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


# =============================================================================
# Schemas
# =============================================================================


class OrderLineRequest(BaseModel):
    quantity: int = Field(ge=1)
    variant_id: UUID | None = None
    sku: str | None = None
    expected_unit_price_cents: int | None = Field(default=None, ge=0)


class CreateOrderBody(BaseModel):
    customer_phone: str = Field(min_length=7)
    items: list[OrderLineRequest] = Field(min_length=1)
    customer_name: str | None = None
    customer_email: str | None = None
    delivery_area: str | None = None
    delivery_address: str | None = None
    delivery_fee_cents: int = Field(default=0, ge=0)
    discount_cents: int = Field(default=0, ge=0)
    payment_method: str = "mpesa_stk"
    notes: str = ""


class CancelOrderBody(BaseModel):
    reason: str = Field(min_length=1)


class OrderItemView(BaseModel):
    id: str
    product_id: str
    variant_id: str | None
    sku: str
    name: str
    unit_price_cents: int
    quantity: int
    total_price_cents: int


class OrderView(BaseModel):
    id: str
    tenant_id: str
    customer_id: str
    customer_phone: str
    customer_name: str | None
    customer_email: str | None
    subtotal_cents: int
    delivery_fee_cents: int
    discount_cents: int
    total_cents: int
    status: str
    payment_method: str
    payment_status: str
    delivery_area: str | None
    delivery_address: str | None
    notes: str
    created_at: str
    confirmed_at: str | None
    paid_at: str | None
    packed_at: str | None
    cancelled_at: str | None
    cancelled_reason: str | None
    items: list[OrderItemView] = Field(default_factory=list)


class OrderListResponse(BaseModel):
    items: list[OrderView]
    total: int
    page: int
    page_size: int


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _item_view(item: OrderItem) -> OrderItemView:
    return OrderItemView(
        id=str(item.id),
        product_id=str(item.product_id),
        variant_id=str(item.variant_id) if item.variant_id else None,
        sku=item.sku,
        name=item.name,
        unit_price_cents=item.unit_price_cents,
        quantity=item.quantity,
        total_price_cents=item.total_price_cents,
    )


def _view(order: Order, items: list[OrderItem] | None = None) -> OrderView:
    return OrderView(
        id=str(order.id),
        tenant_id=str(order.tenant_id),
        customer_id=str(order.customer_id),
        customer_phone=order.customer_phone,
        customer_name=order.customer_name,
        customer_email=order.customer_email,
        subtotal_cents=order.subtotal_cents,
        delivery_fee_cents=order.delivery_fee_cents,
        discount_cents=order.discount_cents,
        total_cents=order.total_cents,
        status=str(getattr(order.status, "value", order.status)),
        payment_method=str(getattr(order.payment_method, "value", order.payment_method)),
        payment_status=str(getattr(order.payment_status, "value", order.payment_status)),
        delivery_area=order.delivery_area,
        delivery_address=order.delivery_address,
        notes=order.notes or "",
        created_at=_iso(order.created_at) or "",
        confirmed_at=_iso(order.confirmed_at),
        paid_at=_iso(order.paid_at),
        packed_at=_iso(order.packed_at),
        cancelled_at=_iso(order.cancelled_at),
        cancelled_reason=order.cancelled_reason,
        items=[_item_view(i) for i in (items or [])],
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.post("", response_model=OrderView, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: CreateOrderBody,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OrderView:
    """Create an order, reserve stock and move it to awaiting_payment."""
    try:
        payment_method = PaymentMethod(body.payment_method)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unknown payment_method") from exc

    command = CreateOrderCommand(
        customer_phone=body.customer_phone,
        lines=tuple(
            OrderLine(
                quantity=item.quantity,
                variant_id=item.variant_id,
                sku=item.sku,
                expected_unit_price_cents=item.expected_unit_price_cents,
            )
            for item in body.items
        ),
        customer_name=body.customer_name,
        customer_email=body.customer_email,
        delivery_area=body.delivery_area,
        delivery_address=body.delivery_address,
        delivery_fee_cents=body.delivery_fee_cents,
        discount_cents=body.discount_cents,
        payment_method=payment_method,
        notes=body.notes,
        idempotency_key=idempotency_key,
    )

    service = OrderService(db)
    try:
        order = await service.create_order(tenant_id, command)
    except (OrderValidationError, ItemNotFoundError, ItemUnavailableError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except PriceMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InsufficientStockError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    items = await service.list_items(tenant_id, order.id)
    return _view(order, items)


@router.get("", response_model=OrderListResponse)
async def list_orders(
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    customer_id: UUID | None = None,
) -> OrderListResponse:
    """List orders with pagination and filters."""
    service = OrderService(db)
    orders, total = await service.list_orders(
        tenant_id,
        status=status_filter,
        customer_id=customer_id,
        page=page,
        page_size=page_size,
    )
    views: list[OrderView] = []
    for order in orders:
        items = await service.list_items(tenant_id, order.id)
        views.append(_view(order, items))
    return OrderListResponse(items=views, total=total, page=page, page_size=page_size)


@router.get("/{order_id}", response_model=OrderView)
async def get_order(
    order_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrderView:
    """Get an order with its line items."""
    service = OrderService(db)
    try:
        order = await service.get_order(tenant_id, order_id)
    except OrderNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    items = await service.list_items(tenant_id, order_id)
    return _view(order, items)


@router.post("/{order_id}/cancel", response_model=OrderView)
async def cancel_order(
    order_id: UUID,
    body: CancelOrderBody,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrderView:
    """Cancel an unpaid order and release its stock reservations."""
    service = OrderService(db)
    try:
        order = await service.cancel_order(tenant_id, order_id, body.reason)
    except OrderNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IllegalTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    items = await service.list_items(tenant_id, order_id)
    return _view(order, items)


@router.post("/{order_id}/pack", response_model=OrderView)
async def pack_order(
    order_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_current_tenant_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OrderView:
    """Mark a paid order as packed."""
    service = OrderService(db)
    try:
        order = await service.mark_packed(tenant_id, order_id)
    except OrderNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IllegalTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    items = await service.list_items(tenant_id, order_id)
    return _view(order, items)
