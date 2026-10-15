"""
Orders API Routes

REST API endpoints for order management.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


# =============================================================================
# Pydantic Models
# =============================================================================


class OrderItemRequest(BaseModel):
    product_id: str
    variant_id: str | None = None
    quantity: int = Field(ge=1)


class CreateOrderRequest(BaseModel):
    customer_id: str
    customer_phone: str
    customer_name: str | None = None
    customer_email: str | None = None
    items: list[OrderItemRequest]
    delivery_info: dict[str, Any] | None = None
    payment_method: str = "mpesa_stk"
    notes: str = ""


class OrderResponse(BaseModel):
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
    order_type: str
    delivery_address: str | None
    delivery_area: str | None
    payment_method: str
    payment_status: str
    created_at: str
    updated_at: str
    confirmed_at: str | None
    paid_at: str | None
    packed_at: str | None
    shipped_at: str | None
    delivered_at: str | None
    cancelled_at: str | None
    cancelled_reason: str | None
    notes: str

    class Config:
        from_attributes = True


class OrderListResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    page_size: int


class UpdateOrderRequest(BaseModel):
    status: str | None = None
    delivery_info: dict[str, Any] | None = None
    payment_method: str | None = None
    notes: str | None = None


class CancelOrderRequest(BaseModel):
    reason: str


# =============================================================================
# Orders Endpoints (Placeholder - Full implementation in M3.5)
# =============================================================================

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


@router.post("", response_model=dict, status_code=201)
async def create_order(data: CreateOrderRequest) -> dict[str, str]:
    """Create a new order with stock reservation."""
    return {"order_id": "placeholder", "status": "draft"}


@router.get("", response_model=dict)
async def list_orders(
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    customer_id: str | None = None,
) -> dict[str, Any]:
    """List orders with pagination and filters."""
    return {"items": [], "total": 0, "page": 1, "page_size": 20}


@router.get("/{order_id}")
async def get_order(order_id: str) -> dict[str, Any]:
    """Get order by ID with items."""
    return {}


@router.patch("/{order_id}")
async def update_order(order_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Update order (status, delivery info, etc.)."""
    return {"status": "updated"}


@router.post("/{order_id}/cancel", status_code=200)
async def cancel_order(order_id: str, data: CancelOrderRequest) -> dict[str, str]:
    """Cancel an order."""
    return {"status": "cancelled"}


@router.post("/{order_id}/confirm", status_code=200)
async def confirm_order(order_id: str) -> dict[str, str]:
    """Confirm order (move from draft to awaiting_payment)."""
    return {"status": "awaiting_payment"}


@router.post("/{order_id}/pack", status_code=200)
async def pack_order(order_id: str) -> dict[str, str]:
    """Mark order as packed."""
    return {"status": "packed"}


@router.post("/{order_id}/ship", status_code=200)
async def ship_order(order_id: str) -> dict[str, str]:
    """Mark order as shipped/out for delivery."""
    return {"status": "out_for_delivery"}


@router.post("/{order_id}/deliver", status_code=200)
async def deliver_order(order_id: str) -> dict[str, str]:
    """Mark order as delivered."""
    return {"status": "delivered"}


@router.post("/{order_id}/refund", status_code=200)
async def refund_order(order_id: str, reason: str) -> dict[str, str]:
    """Refund an order."""
    return {"status": "refunded"}
