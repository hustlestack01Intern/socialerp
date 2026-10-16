"""
Delivery Service

Deterministic application service for the delivery lifecycle. Fee arithmetic
lives ONLY here (and in ``compute_delivery_fee_cents``) — the LLM/agent never
computes or quotes a delivery fee. Every transition is audited; the order FSM
(money/stock authority, ADR 0006) and the delivery FSM (physical lifecycle) are
enforced together so the two never diverge.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.delivery.repository import DeliveryRepository
from packages.core.domain.delivery import (
    DeliveryFailureReason,
    DeliveryStatus,
    IllegalDeliveryTransitionError,
    is_legal_delivery_transition,
)
from packages.core.domain.order import OrderStatus, PaymentMethod, PaymentStatus
from packages.core.models.delivery import Delivery, DeliveryZone, Rider
from packages.core.models.order import Order
from packages.core.orders.service import OrderNotFoundError
from packages.core.orders.transitions import apply_order_transition
from packages.core.repositories import OrderRepository

# =============================================================================
# Errors
# =============================================================================


class DeliveryError(Exception):
    """Base error for the delivery service."""


class OrderNotDispatchableError(DeliveryError):
    """The order is not in a state that allows dispatch."""


class RiderNotFoundError(DeliveryError):
    """No active rider matches for this tenant."""


class ZoneNotFoundError(DeliveryError):
    """No active zone matches for this tenant."""


class ActiveDeliveryExistsError(DeliveryError):
    """The order already has an in-flight delivery (idempotent dispatch guard)"""


class PaymentNotCollectedError(DeliveryError):
    """A pay-on-delivery order was marked delivered before payment was collected."""


class ProofOfDeliveryRequiredError(DeliveryError):
    """A pay-on-delivery delivery was marked delivered without a proof photo."""


# =============================================================================
# Service
# =============================================================================


class DeliveryService:
    """Tenant-scoped delivery lifecycle transitions."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit = AuditLogger(session)
        self.deliveries = DeliveryRepository(session)
        self.orders = OrderRepository(session)

    # -- fee arithmetic (deterministic; the LLM never quotes fees) ----------

    @staticmethod
    def compute_delivery_fee_cents(
        *,
        zone_fee_cents: int,
        order_total_cents: int,
        free_delivery_threshold_cents: int,
    ) -> int:
        """Flat zone fee, waived at or above the tenant's free-delivery threshold.

        A threshold of 0 disables the waiver. The fee can never be negative
        and never depends on anything but these three inputs.
        """
        if free_delivery_threshold_cents > 0 and order_total_cents >= free_delivery_threshold_cents:
            return 0
        return max(zone_fee_cents, 0)

    # -- zones ------------------------------------------------------------

    async def create_zone(self, tenant_id: UUID, *, name: str, fee_cents: int) -> DeliveryZone:
        zone = DeliveryZone(tenant_id=tenant_id, name=name, fee_cents=fee_cents, active=True)
        await self.deliveries.add_zone(zone)
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.zone_created",
            resource="delivery_zone",
            resource_id=zone.id,
            after={"name": name, "fee_cents": fee_cents},
        )
        return zone

    async def list_zones(self, tenant_id: UUID, *, active_only: bool = False) -> list[DeliveryZone]:
        return await self.deliveries.list_zones(tenant_id, active_only=active_only)

    async def update_zone(
        self,
        tenant_id: UUID,
        zone_id: UUID,
        *,
        name: str | None = None,
        fee_cents: int | None = None,
        active: bool | None = None,
    ) -> DeliveryZone:
        zone = await self.deliveries.get_zone(tenant_id, zone_id)
        if zone is None:
            raise ZoneNotFoundError(f"Delivery zone {zone_id} not found")
        await self.deliveries.update_zone(zone, name=name, fee_cents=fee_cents, active=active)
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.zone_updated",
            resource="delivery_zone",
            resource_id=zone.id,
            before={"name": zone.name, "fee_cents": zone.fee_cents, "active": zone.active},
            after={"name": name, "fee_cents": fee_cents, "active": active},
        )
        return zone

    # -- riders -------------------------------------------------------------

    async def create_rider(self, tenant_id: UUID, *, phone: str, name: str) -> Rider:
        rider = Rider(tenant_id=tenant_id, phone=phone, name=name, active=True)
        await self.deliveries.add_rider(rider)
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.rider_created",
            resource="rider",
            resource_id=rider.id,
            after={"phone": phone, "name": name},
        )
        return rider

    async def list_riders(self, tenant_id: UUID, *, active_only: bool = False) -> list[Rider]:
        return await self.deliveries.list_riders(tenant_id, active_only=active_only)

    async def deactivate_rider(self, tenant_id: UUID, rider_id: UUID) -> Rider:
        rider = await self.deliveries.get_rider(tenant_id, rider_id)
        if rider is None:
            raise RiderNotFoundError(f"Rider {rider_id} not found")
        await self.deliveries.deactivate_rider(rider)
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.rider_deactivated",
            resource="rider",
            resource_id=rider.id,
        )
        return rider

    # -- dispatch ------------------------------------------------------------

    async def dispatch(
        self,
        tenant_id: UUID,
        *,
        order_id: UUID,
        rider_id: UUID,
        zone_id: UUID,
        free_delivery_threshold_cents: int = 0,
    ) -> Delivery:
        """Assign a packed order to a rider for a zone. Creates the delivery
        row (status ``assigned``); the order stays ``packed`` until the rider
        picks up. Idempotent: an existing in-flight delivery is returned as-is.
        """
        existing = await self.deliveries.active_delivery_for_order(tenant_id, order_id)
        if existing is not None:
            return existing

        order = await self._get_order(tenant_id, order_id)
        if order.status != OrderStatus.PACKED.value:
            raise OrderNotDispatchableError(
                f"Order {order_id} is {order.status!r}, expected 'packed'"
            )

        rider = await self.deliveries.get_rider(tenant_id, rider_id)
        if rider is None or not rider.active:
            raise RiderNotFoundError(f"Active rider {rider_id} not found")

        zone = await self.deliveries.get_zone(tenant_id, zone_id)
        if zone is None or not zone.active:
            raise ZoneNotFoundError(f"Active zone {zone_id} not found")

        fee_cents = self.compute_delivery_fee_cents(
            zone_fee_cents=zone.fee_cents,
            order_total_cents=order.total_cents,
            free_delivery_threshold_cents=free_delivery_threshold_cents,
        )

        delivery = Delivery(
            tenant_id=tenant_id,
            order_id=order_id,
            rider_id=rider.id,
            zone_id=zone.id,
            fee_cents=fee_cents,
            status=DeliveryStatus.ASSIGNED.value,
        )
        await self.deliveries.add_delivery(delivery)
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.dispatched",
            resource="delivery",
            resource_id=delivery.id,
            after={
                "order_id": str(order_id),
                "rider_id": str(rider.id),
                "zone_id": str(zone.id),
                "fee_cents": fee_cents,
            },
        )
        return delivery

    # -- rider quick-reply transitions ---------------------------------------

    async def mark_picked_up(self, tenant_id: UUID, delivery_id: UUID) -> Delivery:
        delivery = await self._get_delivery(tenant_id, delivery_id)
        self._transition_delivery(delivery, DeliveryStatus.PICKED_UP)
        delivery.picked_up_at = datetime.now(UTC)

        order = await self._get_order(tenant_id, delivery.order_id)
        apply_order_transition(order, OrderStatus.OUT_FOR_DELIVERY)

        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.picked_up",
            resource="delivery",
            resource_id=delivery.id,
            after={"order_id": str(delivery.order_id)},
        )
        return delivery

    async def mark_arrived(self, tenant_id: UUID, delivery_id: UUID) -> Delivery:
        delivery = await self._get_delivery(tenant_id, delivery_id)
        self._transition_delivery(delivery, DeliveryStatus.ARRIVED)
        delivery.arrived_at = datetime.now(UTC)
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.arrived",
            resource="delivery",
            resource_id=delivery.id,
            after={"order_id": str(delivery.order_id)},
        )
        return delivery

    async def mark_delivered(
        self,
        tenant_id: UUID,
        delivery_id: UUID,
        *,
        proof_photo: str | None = None,
        notes: str | None = None,
    ) -> Delivery:
        delivery = await self._get_delivery(tenant_id, delivery_id)
        order = await self._get_order(tenant_id, delivery.order_id)

        # B4: a pay-on-delivery order is delivered only after payment is
        # collected and evidenced. Marking it delivered unpaid would close the
        # sale without money and prevent the sale side effect from running.
        # Prepay orders are already paid before dispatch, so they are unaffected.
        if order.payment_method == PaymentMethod.MPESA_PAY_ON_DELIVERY.value:
            # "Currently collected" -- not merely "ever paid": a refunded order
            # keeps paid_at, so is_order_paid() would wrongly let it be delivered.
            collected = getattr(order.payment_status, "value", order.payment_status)
            if collected != PaymentStatus.COMPLETED.value:
                raise PaymentNotCollectedError(
                    f"Order {order.id} is pay-on-delivery and not yet collected "
                    f"(payment_status={order.payment_status!r})"
                )
            if proof_photo is None:
                raise ProofOfDeliveryRequiredError(
                    f"Order {order.id} is pay-on-delivery; a proof photo is required"
                )

        self._transition_delivery(delivery, DeliveryStatus.DELIVERED)
        delivery.delivered_at = datetime.now(UTC)
        if proof_photo is not None:
            delivery.proof_photo = proof_photo
        if notes is not None:
            delivery.notes = notes

        apply_order_transition(order, OrderStatus.DELIVERED)

        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.delivered",
            resource="delivery",
            resource_id=delivery.id,
            after={"order_id": str(delivery.order_id), "proof_photo": proof_photo},
        )
        return delivery

    async def mark_failed(
        self,
        tenant_id: UUID,
        delivery_id: UUID,
        *,
        reason: DeliveryFailureReason | str,
        notes: str | None = None,
    ) -> Delivery:
        delivery = await self._get_delivery(tenant_id, delivery_id)
        self._transition_delivery(delivery, DeliveryStatus.FAILED)
        reason_value = reason.value if isinstance(reason, DeliveryFailureReason) else reason
        delivery.failure_reason = reason_value
        if notes is not None:
            delivery.notes = notes

        order = await self._get_order(tenant_id, delivery.order_id)
        apply_order_transition(order, OrderStatus.FAILED_DELIVERY)

        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.failed",
            resource="delivery",
            resource_id=delivery.id,
            after={"order_id": str(delivery.order_id), "reason": reason_value},
        )
        return delivery

    async def retry_after_failure(self, tenant_id: UUID, delivery_id: UUID) -> Delivery:
        """Put a failed delivery back on the road with the same rider. The
        order returns to out_for_delivery (failed_delivery -> out_for_delivery
        is a legal order-FSM edge)."""
        delivery = await self._get_delivery(tenant_id, delivery_id)
        self._transition_delivery(delivery, DeliveryStatus.PICKED_UP)
        delivery.picked_up_at = datetime.now(UTC)

        order = await self._get_order(tenant_id, delivery.order_id)
        apply_order_transition(order, OrderStatus.OUT_FOR_DELIVERY)

        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="delivery.retried",
            resource="delivery",
            resource_id=delivery.id,
            after={"order_id": str(delivery.order_id)},
        )
        return delivery

    # -- reads ---------------------------------------------------------------

    async def get_delivery(self, tenant_id: UUID, delivery_id: UUID) -> Delivery | None:
        return await self.deliveries.get_delivery(tenant_id, delivery_id)

    async def list_deliveries(
        self, tenant_id: UUID, *, status: str | None = None
    ) -> list[Delivery]:
        return await self.deliveries.list_deliveries(tenant_id, status=status)

    async def is_pay_on_delivery(self, tenant_id: UUID, order_id: UUID) -> bool:
        """True when the order pays at delivery (mpesa_pod)."""
        order = await self.orders.get(tenant_id, order_id)
        return (
            order is not None and order.payment_method == PaymentMethod.MPESA_PAY_ON_DELIVERY.value
        )

    # -- internals -----------------------------------------------------------

    async def _get_order(self, tenant_id: UUID, order_id: UUID) -> Order:
        order = await self.orders.get(tenant_id, order_id)
        if order is None:
            raise OrderNotFoundError(f"Order {order_id} not found")
        return order

    async def _get_delivery(self, tenant_id: UUID, delivery_id: UUID) -> Delivery:
        delivery = await self.deliveries.get_delivery(tenant_id, delivery_id)
        if delivery is None:
            raise DeliveryError(f"Delivery {delivery_id} not found")
        return delivery

    @staticmethod
    def _transition_delivery(delivery: Delivery, new_status: DeliveryStatus) -> None:
        current = DeliveryStatus(delivery.status)
        if not is_legal_delivery_transition(current, new_status):
            raise IllegalDeliveryTransitionError(
                f"Illegal transition: {current.value} -> {new_status.value}"
            )
        delivery.status = new_status.value
