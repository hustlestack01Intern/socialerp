"""
Public order tracking projection (no PII).

Builds a minimal, customer-safe view of an order's progress for the public
tracking page. The lookup is keyed by the order's UUID and gated by a signed,
expiring capability token (see ``packages.core.delivery.tracking_token``,
UNCERTAINTY U29), and the projection deliberately EXCLUDES everything
sensitive:

- no customer name / phone / email / delivery address,
- no rider identity or phone / location,
- no internal tenant id or audit fields.

It DOES include the order status, the delivery status, the delivery zone name
(the customer's own area) and a timestamped timeline — all of which the customer
already knows from their own order.

Fail-closed contract (U29): ``build_tracking`` returns ``None`` — which both
public routes turn into a bare 404 — when the order does not exist OR when the
capability token is missing, malformed, tampered, expired, or bound to a
different tenant/order. "Invalid token" and "unknown order" are deliberately
indistinguishable to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.delivery.tracking_token import verify_tracking_token
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


async def build_tracking(
    session: AsyncSession, order_id: UUID, *, token: str | None
) -> TrackingView | None:
    """Return the tracking view, or ``None`` when the order is unknown or the
    capability token is missing/invalid (fail closed — U29).

    The token is verified against the order's OWN tenant id, so a token minted
    for one tenant's order never unlocks another tenant's order.
    """
    order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
    if order is None:
        return None
    if not verify_tracking_token(token, order.tenant_id, order_id):
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
