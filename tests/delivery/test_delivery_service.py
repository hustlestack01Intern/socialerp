"""
Phase 7 delivery service tests (service level, real Postgres).

Deterministic fee arithmetic, dispatch validation, the delivery FSM and its
coupling to the order FSM, plus the pay-on-delivery invariants: a POD order in
transit is payable, payment completion records the sale without an illegal
order transition, and reservation expiry never releases stock that is moving
towards a paying customer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select

from packages.core.delivery import (
    DeliveryService,
    OrderNotDispatchableError,
    PaymentNotCollectedError,
    ProofOfDeliveryRequiredError,
    RiderNotFoundError,
    ZoneNotFoundError,
)
from packages.core.domain.delivery import (
    DeliveryFailureReason,
    DeliveryStatus,
    IllegalDeliveryTransitionError,
)
from packages.core.domain.order import OrderStatus
from packages.core.domain.payment import (
    ProviderPaymentResult,
    ProviderPaymentStatus,
    STKPushResponse,
)
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.delivery import Delivery, DeliveryZone, Rider
from packages.core.models.order import Customer, Order, PaymentMethod
from packages.core.models.payment import Payment, PaymentRecordStatus
from packages.core.models.reservation import Reservation
from packages.core.models.reservation import ReservationStatus as ReservationStatusModel
from packages.core.orders import OrderService
from packages.core.payments import PaymentService
from packages.core.repositories import OrderRepository

TENANT_PHONE = "254712345678"


# =============================================================================
# Seed helpers
# =============================================================================


async def _seed_product(session_factory: Any, tenant_id: UUID) -> tuple[UUID, UUID]:
    async with session_factory() as session, session.begin():
        product = Product(tenant_id=tenant_id, name="Hoodie", status=ProductStatus.ACTIVE.value)
        session.add(product)
        await session.flush()
        variant = ProductVariant(
            tenant_id=tenant_id,
            product_id=product.id,
            sku="HOODIE-M",
            attributes=[],
            cost_price_cents=250_000,
            sell_price_cents=500_000,
            quantity=0,
            is_active=True,
        )
        session.add(variant)
        await session.flush()
        return product.id, variant.id


async def _seed_reservation(
    session_factory: Any,
    tenant_id: UUID,
    *,
    order_id: UUID,
    product_id: UUID,
    variant_id: UUID,
    quantity: int = 1,
) -> UUID:
    async with session_factory() as session, session.begin():
        reservation = Reservation(
            tenant_id=tenant_id,
            product_id=product_id,
            variant_id=variant_id,
            quantity=quantity,
            status=ReservationStatusModel.ACTIVE.value,
            reference_id=order_id,
            reference_type="order",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),  # already stale
        )
        session.add(reservation)
        await session.flush()
        return reservation.id


async def _seed_packed_order(
    session_factory: Any,
    tenant_id: UUID,
    *,
    status: OrderStatus = OrderStatus.PACKED,
    payment_method: PaymentMethod = PaymentMethod.MPESA_STK,
    total_cents: int = 500_000,
) -> UUID:
    async with session_factory() as session, session.begin():
        customer = Customer(tenant_id=tenant_id, phone=TENANT_PHONE, name="Buyer")
        session.add(customer)
        await session.flush()
        order = Order(
            tenant_id=tenant_id,
            customer_id=customer.id,
            customer_phone=TENANT_PHONE,
            total_cents=total_cents,
            status=status.value,
            payment_method=payment_method.value,
        )
        session.add(order)
        await session.flush()
        return order.id


async def _seed_zone(
    session_factory: Any, tenant_id: UUID, *, name: str = "Kilimani", fee_cents: int = 2000
) -> UUID:
    async with session_factory() as session, session.begin():
        zone = DeliveryZone(tenant_id=tenant_id, name=name, fee_cents=fee_cents, active=True)
        session.add(zone)
        await session.flush()
        return zone.id


async def _seed_rider(
    session_factory: Any, tenant_id: UUID, *, phone: str = TENANT_PHONE, name: str = "Rider One"
) -> UUID:
    async with session_factory() as session, session.begin():
        rider = Rider(tenant_id=tenant_id, phone=phone, name=name, active=True)
        session.add(rider)
        await session.flush()
        return rider.id


# =============================================================================
# Fee arithmetic (pure, deterministic)
# =============================================================================


class TestDeliveryFeeArithmetic:
    def test_flat_zone_fee(self) -> None:
        fee = DeliveryService.compute_delivery_fee_cents(
            zone_fee_cents=2000, order_total_cents=500_000, free_delivery_threshold_cents=0
        )
        assert fee == 2000

    def test_free_above_threshold(self) -> None:
        fee = DeliveryService.compute_delivery_fee_cents(
            zone_fee_cents=2000,
            order_total_cents=1_000_000,
            free_delivery_threshold_cents=1_000_000,
        )
        assert fee == 0

    def test_threshold_zero_disables_waiver(self) -> None:
        fee = DeliveryService.compute_delivery_fee_cents(
            zone_fee_cents=1500, order_total_cents=10_000_000, free_delivery_threshold_cents=0
        )
        assert fee == 1500

    def test_fee_never_negative(self) -> None:
        fee = DeliveryService.compute_delivery_fee_cents(
            zone_fee_cents=-500, order_total_cents=1000, free_delivery_threshold_cents=0
        )
        assert fee == 0


# =============================================================================
# Dispatch + FSM
# =============================================================================


class TestDispatch:
    async def test_dispatch_creates_assigned_delivery_with_fee(
        self, tenant, session_factory
    ) -> None:
        order_id = await _seed_packed_order(session_factory, tenant.id)
        async with session_factory() as session:
            service = DeliveryService(session)
            zone = await service.create_zone(tenant.id, name="Kilimani", fee_cents=2000)
            rider = await service.create_rider(tenant.id, phone=TENANT_PHONE, name="Rider One")
            delivery = await service.dispatch(
                tenant.id, order_id=order_id, rider_id=rider.id, zone_id=zone.id
            )
            assert delivery.status == DeliveryStatus.ASSIGNED.value
            assert delivery.fee_cents == 2000
            # Order stays packed until the rider picks up.
            assert (await session.get(Order, order_id)).status == OrderStatus.PACKED.value

    async def test_dispatch_is_idempotent_for_active_delivery(
        self, tenant, session_factory
    ) -> None:
        order_id = await _seed_packed_order(session_factory, tenant.id)
        async with session_factory() as session:
            service = DeliveryService(session)
            zone = await service.create_zone(tenant.id, name="Kilimani", fee_cents=2000)
            rider = await service.create_rider(tenant.id, phone=TENANT_PHONE, name="Rider One")
            first = await service.dispatch(
                tenant.id, order_id=order_id, rider_id=rider.id, zone_id=zone.id
            )
            second = await service.dispatch(
                tenant.id, order_id=order_id, rider_id=rider.id, zone_id=zone.id
            )
            assert first.id == second.id

    async def test_dispatch_rejects_unpacked_order(self, tenant, session_factory) -> None:
        order_id = await _seed_packed_order(
            session_factory, tenant.id, status=OrderStatus.AWAITING_PAYMENT
        )
        async with session_factory() as session:
            service = DeliveryService(session)
            zone = await service.create_zone(tenant.id, name="Kilimani", fee_cents=2000)
            rider = await service.create_rider(tenant.id, phone=TENANT_PHONE, name="Rider One")
            with pytest.raises(OrderNotDispatchableError):
                await service.dispatch(
                    tenant.id, order_id=order_id, rider_id=rider.id, zone_id=zone.id
                )

    async def test_dispatch_rejects_inactive_rider(self, tenant, session_factory) -> None:
        order_id = await _seed_packed_order(session_factory, tenant.id)
        async with session_factory() as session:
            service = DeliveryService(session)
            zone = await service.create_zone(tenant.id, name="Kilimani", fee_cents=2000)
            rider = await service.create_rider(tenant.id, phone=TENANT_PHONE, name="Rider One")
            await service.deactivate_rider(tenant.id, rider.id)
            with pytest.raises(RiderNotFoundError):
                await service.dispatch(
                    tenant.id, order_id=order_id, rider_id=rider.id, zone_id=zone.id
                )

    async def test_dispatch_rejects_unknown_zone(self, tenant, session_factory) -> None:
        order_id = await _seed_packed_order(session_factory, tenant.id)
        async with session_factory() as session:
            service = DeliveryService(session)
            rider = await service.create_rider(tenant.id, phone=TENANT_PHONE, name="Rider One")
            with pytest.raises(ZoneNotFoundError):
                await service.dispatch(
                    tenant.id, order_id=order_id, rider_id=rider.id, zone_id=UUID(int=0)
                )


class TestDeliveryFSM:
    async def _dispatched(self, session_factory, tenant) -> tuple[UUID, UUID]:
        order_id = await _seed_packed_order(session_factory, tenant.id)
        async with session_factory() as session, session.begin():
            service = DeliveryService(session)
            zone = await service.create_zone(tenant.id, name="Kilimani", fee_cents=2000)
            rider = await service.create_rider(tenant.id, phone=TENANT_PHONE, name="Rider One")
            delivery = await service.dispatch(
                tenant.id, order_id=order_id, rider_id=rider.id, zone_id=zone.id
            )
        return delivery.id, order_id

    async def test_happy_path_pickup_arrival_delivery(self, tenant, session_factory) -> None:
        delivery_id, order_id = await self._dispatched(session_factory, tenant)
        async with session_factory() as session:
            svc = DeliveryService(session)
            await svc.mark_picked_up(tenant.id, delivery_id)
            assert (
                await session.get(Delivery, delivery_id)
            ).status == DeliveryStatus.PICKED_UP.value
            assert (await session.get(Order, order_id)).status == OrderStatus.OUT_FOR_DELIVERY.value

            await svc.mark_arrived(tenant.id, delivery_id)
            assert (await session.get(Delivery, delivery_id)).status == DeliveryStatus.ARRIVED.value

            await svc.mark_delivered(
                tenant.id, delivery_id, proof_photo="https://cdn.example.test/pod.jpg"
            )
            delivery = await session.get(Delivery, delivery_id)
            assert delivery.status == DeliveryStatus.DELIVERED.value
            assert delivery.proof_photo == "https://cdn.example.test/pod.jpg"
            assert (await session.get(Order, order_id)).status == OrderStatus.DELIVERED.value

    async def test_failed_delivery_then_retry(self, tenant, session_factory) -> None:
        delivery_id, order_id = await self._dispatched(session_factory, tenant)
        async with session_factory() as session:
            svc = DeliveryService(session)
            await svc.mark_picked_up(tenant.id, delivery_id)
            await svc.mark_arrived(tenant.id, delivery_id)
            await svc.mark_failed(
                tenant.id,
                delivery_id,
                reason=DeliveryFailureReason.WRONG_ADDRESS,
                notes="Gate code wrong",
            )
            delivery = await session.get(Delivery, delivery_id)
            assert delivery.status == DeliveryStatus.FAILED.value
            assert delivery.failure_reason == DeliveryFailureReason.WRONG_ADDRESS.value
            assert (await session.get(Order, order_id)).status == OrderStatus.FAILED_DELIVERY.value

            await svc.retry_after_failure(tenant.id, delivery_id)
            assert (
                await session.get(Delivery, delivery_id)
            ).status == DeliveryStatus.PICKED_UP.value
            assert (await session.get(Order, order_id)).status == OrderStatus.OUT_FOR_DELIVERY.value

    async def test_illegal_delivery_transition_raises(self, tenant, session_factory) -> None:
        delivery_id, _ = await self._dispatched(session_factory, tenant)
        async with session_factory() as session:
            svc = DeliveryService(session)
            # assigned -> delivered is illegal; the rider must progress in order.
            with pytest.raises(IllegalDeliveryTransitionError):
                await svc.mark_delivered(tenant.id, delivery_id)

    async def test_terminal_delivery_cannot_transition(self, tenant, session_factory) -> None:
        delivery_id, _ = await self._dispatched(session_factory, tenant)
        async with session_factory() as session:
            svc = DeliveryService(session)
            await svc.mark_picked_up(tenant.id, delivery_id)
            await svc.mark_arrived(tenant.id, delivery_id)
            await svc.mark_delivered(tenant.id, delivery_id)
            with pytest.raises(IllegalDeliveryTransitionError):
                await svc.retry_after_failure(tenant.id, delivery_id)


# =============================================================================
# Pay-on-delivery invariants (payment service interplay)
# =============================================================================


class _PodFakeProvider:
    """Minimal provider double for the POD callback path (no network)."""

    async def initiate_stk_push(self, request):  # type: ignore[no-untyped-def]
        return STKPushResponse(
            checkout_request_id="ws_CO_pod",
            merchant_request_id="mreq_pod",
            response_code="0",
            response_description="Accepted",
            customer_message="Enter PIN",
        )

    async def query_status(self, checkout_request_id):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def validate_callback(self, payload, headers):  # type: ignore[no-untyped-def]
        return True

    async def parse_callback(self, payload):  # type: ignore[no-untyped-def]
        return ProviderPaymentResult(
            status=ProviderPaymentStatus.COMPLETED,
            checkout_request_id="ws_CO_pod",
            mpesa_receipt_number="MPEPOD1",
            result_code=0,
            result_desc="OK",
            amount_cents=500_000,
        )

    async def refund(self, *args):  # type: ignore[no-untyped-def]
        return False


class TestPayOnDelivery:
    async def _arrived_pod(self, session_factory, tenant) -> tuple[UUID, UUID]:
        order_id = await _seed_packed_order(
            session_factory,
            tenant.id,
            status=OrderStatus.PACKED,
            payment_method=PaymentMethod.MPESA_PAY_ON_DELIVERY,
        )
        async with session_factory() as session, session.begin():
            service = DeliveryService(session)
            zone = await service.create_zone(tenant.id, name="Kilimani", fee_cents=2000)
            rider = await service.create_rider(tenant.id, phone=TENANT_PHONE, name="Rider One")
            delivery = await service.dispatch(
                tenant.id, order_id=order_id, rider_id=rider.id, zone_id=zone.id
            )
            await service.mark_picked_up(tenant.id, delivery.id)
            await service.mark_arrived(tenant.id, delivery.id)
        return delivery.id, order_id

    async def _mark_order_paid(self, session_factory, order_id: UUID) -> None:
        async with session_factory() as session, session.begin():
            order = await session.get(Order, order_id)
            assert order is not None
            order.payment_status = "completed"

    async def test_pod_delivered_before_payment_raises(self, tenant, session_factory) -> None:
        delivery_id, order_id = await self._arrived_pod(session_factory, tenant)
        async with session_factory() as session:
            service = DeliveryService(session)
            with pytest.raises(PaymentNotCollectedError):
                await service.mark_delivered(
                    tenant.id, delivery_id, proof_photo="https://cdn.example.test/pod.jpg"
                )
            assert (await session.get(Delivery, delivery_id)).status == DeliveryStatus.ARRIVED.value
            assert (await session.get(Order, order_id)).status == OrderStatus.OUT_FOR_DELIVERY.value

    async def test_pod_delivered_for_refunded_order_raises(self, tenant, session_factory) -> None:
        """A refunded POD order keeps paid_at but is not 'currently collected'."""
        delivery_id, order_id = await self._arrived_pod(session_factory, tenant)
        async with session_factory() as session, session.begin():
            order = await session.get(Order, order_id)
            assert order is not None
            order.payment_status = "refunded"
            order.paid_at = datetime.now(UTC)

        async with session_factory() as session:
            service = DeliveryService(session)
            with pytest.raises(PaymentNotCollectedError):
                await service.mark_delivered(
                    tenant.id, delivery_id, proof_photo="https://cdn.example.test/pod.jpg"
                )

    async def test_pod_delivered_without_proof_raises(self, tenant, session_factory) -> None:
        delivery_id, order_id = await self._arrived_pod(session_factory, tenant)
        await self._mark_order_paid(session_factory, order_id)

        async with session_factory() as session:
            service = DeliveryService(session)
            with pytest.raises(ProofOfDeliveryRequiredError):
                await service.mark_delivered(tenant.id, delivery_id)
            assert (await session.get(Delivery, delivery_id)).status == DeliveryStatus.ARRIVED.value

    async def test_pod_delivered_after_payment_with_proof_succeeds(
        self, tenant, session_factory
    ) -> None:
        delivery_id, order_id = await self._arrived_pod(session_factory, tenant)
        await self._mark_order_paid(session_factory, order_id)
        async with session_factory() as session:
            service = DeliveryService(session)
            await service.mark_delivered(
                tenant.id, delivery_id, proof_photo="https://cdn.example.test/pod.jpg"
            )
            assert (
                await session.get(Delivery, delivery_id)
            ).status == DeliveryStatus.DELIVERED.value
            assert (await session.get(Order, order_id)).status == OrderStatus.DELIVERED.value

    async def test_pod_order_in_transit_is_payable_and_completion_skips_paid_status(
        self, tenant, session_factory
    ) -> None:
        order_id = await _seed_packed_order(
            session_factory,
            tenant.id,
            status=OrderStatus.OUT_FOR_DELIVERY,
            payment_method=PaymentMethod.MPESA_PAY_ON_DELIVERY,
        )
        async with session_factory() as session:
            service = PaymentService(session, _PodFakeProvider())  # type: ignore[arg-type]
            payment = await service.initiate_stk_payment(
                tenant_id=tenant.id,
                order_id=order_id,
                phone_number=TENANT_PHONE,
                callback_url="https://app.example.test/cb",
                idempotency_key="delivery:pod-test:arrived",
            )
            assert payment.status == "initiated"

            # Complete it via the callback path (amount must match exactly).
            await service.handle_callback(
                tenant_id=tenant.id,
                payload={
                    "Body": {"stkCallback": {"CheckoutRequestID": "ws_CO_pod", "ResultCode": 0}}
                },
                headers={},
            )
            await session.flush()

            order = await OrderRepository(session).get(tenant.id, order_id)
            assert order is not None
            # The order stays out_for_delivery (never 'paid'); the payment completed.
            assert order.status == OrderStatus.OUT_FOR_DELIVERY.value
            assert order.payment_status == "completed"
            assert order.payment_reference == "MPEPOD1"

            # The payment row is completed; the sale is recorded via payment.completed.
            payment_row = (
                await session.execute(select(Payment).where(Payment.order_id == order_id))
            ).scalar_one()
            assert payment_row.status == PaymentRecordStatus.COMPLETED.value

    async def test_reservation_expiry_skips_orders_with_active_delivery(
        self, tenant, session_factory
    ) -> None:
        product_id, variant_id = await _seed_product(session_factory, tenant.id)
        order_id = await _seed_packed_order(
            session_factory,
            tenant.id,
            status=OrderStatus.PACKED,
            payment_method=PaymentMethod.MPESA_PAY_ON_DELIVERY,
        )
        zone_id = await _seed_zone(session_factory, tenant.id)

        async with session_factory() as session:
            service = DeliveryService(session)
            rider = await service.create_rider(tenant.id, phone=TENANT_PHONE, name="Rider One")
            delivery = await service.dispatch(
                tenant.id, order_id=order_id, rider_id=rider.id, zone_id=zone_id
            )
            await service.mark_picked_up(tenant.id, delivery.id)

            reservation_id = await _seed_reservation(
                session_factory,
                tenant.id,
                order_id=order_id,
                product_id=product_id,
                variant_id=variant_id,
            )

            # An expired reservation on an order with an in-flight delivery is
            # NOT released — the goods are moving towards a paying customer.
            released = await OrderService(session).expire_stale_reservations(tenant.id)
            assert released == 0
            async with session_factory() as session:
                reservation = await session.get(Reservation, reservation_id)
                assert reservation is not None
                assert reservation.status == ReservationStatusModel.ACTIVE.value

    async def test_reservation_expiry_releases_orders_without_delivery(
        self, tenant, session_factory
    ) -> None:
        product_id, variant_id = await _seed_product(session_factory, tenant.id)
        # Control: same stale reservation, but no delivery in flight.
        order_id = await _seed_packed_order(session_factory, tenant.id)
        reservation_id = await _seed_reservation(
            session_factory,
            tenant.id,
            order_id=order_id,
            product_id=product_id,
            variant_id=variant_id,
        )
        async with session_factory() as session:
            released = await OrderService(session).expire_stale_reservations(tenant.id)
            assert released == 1
            reservation = await session.get(Reservation, reservation_id)
            assert reservation is not None
            assert reservation.status == ReservationStatusModel.EXPIRED.value
