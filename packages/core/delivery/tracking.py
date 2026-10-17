"""
Public order tracking projection (no PII).

Builds a minimal, customer-safe view of an order's progress for the public
tracking page. The lookup is keyed by the order's UUID (unguessable), and the
projection deliberately EXCLUDES everything sensitive:

- no customer name / phone / email / delivery address,
- no rider identity or phone / location,
- no internal tenant id or audit fields.

It DOES include the order status, the delivery status, the delivery zone name
(the customer's own area) and a timestamped timeline — all of which the customer
already knows from their own order.

Production note (UNCERTAINTY U29): the public lookup has no tenant context, so
under strict RLS it relies on a privileged read. Today the API path does not
enable RLS (TenantContextMiddleware is not wired — see STATE.md); when Phase 10
wires tenancy, this endpoint must move to a SECURITY DEFINER lookup function or
receive a signed tracking token. Tracked honestly, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.models.delivery import Delivery, DeliveryZone
from packages.core.models.order import Order


@dataclass(frozen=True)
class TrackingStep:
    """One timeline entry: a label and when it happened."""

    label: str
    at: datetime


@dataclass(frozen=True)
class TrackingView:
    """Customer-safe tracking projection."""

    order_ref: str
    order_status: str
    delivery_status: str | None
    zone_name: str | None
    payment_method: str
    steps: tuple[TrackingStep, ...]


def _short_ref(order_id: UUID) -> str:
    return str(order_id)[:8].upper()


async def build_tracking(session: AsyncSession, order_id: UUID) -> TrackingView | None:
    """Return the tracking view for an order, or ``None`` when it does not exist."""
    order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if order is None:
        return None

    delivery = (
        (
            await session.execute(
                select(Delivery)
                .where(Delivery.order_id == order_id)
                .order_by(Delivery.created_at.desc(), Delivery.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )

    zone_name: str | None = None
    if delivery is not None:
        zone = (
            await session.execute(select(DeliveryZone).where(DeliveryZone.id == delivery.zone_id))
        ).scalar_one_or_none()
        zone_name = zone.name if zone is not None else None

    steps: list[TrackingStep] = [TrackingStep("Order placed", order.created_at)]
    if order.paid_at is not None:
        steps.append(TrackingStep("Payment received", order.paid_at))
    if order.packed_at is not None:
        steps.append(TrackingStep("Packed", order.packed_at))
    if delivery is not None and delivery.picked_up_at is not None:
        steps.append(TrackingStep("Out for delivery", delivery.picked_up_at))
    if delivery is not None and delivery.arrived_at is not None:
        steps.append(TrackingStep("Arrived", delivery.arrived_at))
    if order.delivered_at is not None:
        steps.append(TrackingStep("Delivered", order.delivered_at))

    return TrackingView(
        order_ref=_short_ref(order.id),
        order_status=str(getattr(order.status, "value", order.status)),
        delivery_status=str(delivery.status) if delivery is not None else None,
        zone_name=zone_name,
        payment_method=str(getattr(order.payment_method, "value", order.payment_method)),
        steps=tuple(steps),
    )


__all__ = ["TrackingStep", "TrackingView", "build_tracking"]
