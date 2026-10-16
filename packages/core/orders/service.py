"""
Order service (Phase 6).

Responsibilities (all deterministic, none delegated to a model):

- ``create_order``: validate lines against the catalog, take the catalog price,
  compute ``subtotal``/``total`` in integer cents, reserve stock in the
  event-sourced ledger, create the ``Order`` + ``OrderItem`` rows, then move the
  order ``draft -> awaiting_payment`` so the Phase-4 STK flow can pay it.
- ``cancel_order``: release the order's reservations (a ``RELEASE`` movement per
  active reservation) and move a ``draft``/``awaiting_payment`` order to
  ``cancelled``. A paid order cannot be cancelled (refund is Phase 4, human-approved).
- ``record_sale_for_order``: called by the ``payment.completed`` outbox handler;
  writes one ``SELL`` movement per line (closing the reservation) and marks the
  reservations ``fulfilled``. Idempotent: a replayed event cannot double-count.
- ``mark_packed``: ``paid -> packed``.
- ``expire_stale_reservations``: release reservations past their TTL (worker job).

The event-sourced ledger is the source of truth for availability; the catalogue
``ProductVariant.quantity`` column is treated as a cached field and is not
decremented here (ADR 0013).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from socialerp.outbox import add_outbox_event
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.domain import CustomerPhone
from packages.core.domain.order import (
    IllegalTransitionError,
    OrderStatus,
    PaymentMethod,
    is_legal_transition,
)
from packages.core.domain.stock import (
    StockLedger,
    StockMovementReason,
    StockMovementRepository,
    StockMovementType,
)
from packages.core.domain.stock import StockMovement as StockMovementVO
from packages.core.models.catalog import ProductVariant
from packages.core.models.order import Order, OrderItem
from packages.core.models.reservation import Reservation
from packages.core.models.reservation import ReservationReason as ReservationReasonModel
from packages.core.models.reservation import ReservationStatus as ReservationStatusModel
from packages.core.models.stock import StockMovement as StockMovementModel
from packages.core.orders.transitions import apply_order_transition
from packages.core.repositories import CustomerRepository, OrderRepository

# =============================================================================
# Ports
# =============================================================================

# Does this order have a delivery in flight? Injected so the orders package does
# not import the delivery package at module scope (dependency direction
# delivery -> orders; ADR 0015). The default is a deferred import.
ActiveDeliveryLookup = Callable[[UUID, UUID], Awaitable[bool]]

# =============================================================================
# Shared predicates
# =============================================================================


def is_pod_in_transit(order: Order) -> bool:
    """True for a pay-on-delivery order that is already out_for_delivery.

    For POD the physical lifecycle precedes payment: the order leaves
    'packed' at rider pickup (packed -> out_for_delivery, a legal prepay edge
    too) and the STK Push fires at rider arrival. Payment completion must
    still turn reservations into sales even though the order status never
    passes through 'paid'. Used by the payment service (payable check,
    completion path) and by reservation expiry (never release stock that is
    moving toward a paying customer).
    """
    return (
        OrderStatus(order.status) is OrderStatus.OUT_FOR_DELIVERY
        and order.payment_method == PaymentMethod.MPESA_PAY_ON_DELIVERY.value
    )


# =============================================================================
# Errors
# =============================================================================


class OrderError(Exception):
    """Base error for the orders service."""


class OrderValidationError(OrderError):
    """The create/cancel request is malformed."""


class OrderNotFoundError(OrderError):
    """No order matches the given id for this tenant."""


class ItemNotFoundError(OrderError):
    """A requested variant/SKU does not exist in the tenant catalog."""


class ItemUnavailableError(OrderError):
    """A requested variant exists but is inactive."""


class InsufficientStockError(OrderError):
    """Not enough available stock to reserve the requested quantity."""


class PriceMismatchError(OrderError):
    """A caller-supplied expected price disagrees with the catalog price."""


class InvalidReservationError(OrderError):
    """A reservation could not be created/released consistently."""


class ReservationLostError(OrderError):
    """A paid order's reservation was released and cannot be re-established.

    Raised when payment arrives after the reservation TTL expired (or a cancel)
    and there is no longer enough available stock to re-reserve the line. Selling
    anyway would double-count the unit for whichever order reserved it next, so
    the sale is refused and surfaced for reconciliation (refund or manual
    re-order).
    """


# =============================================================================
# Request value objects
# =============================================================================


@dataclass(frozen=True)
class OrderLine:
    """One requested line: identify the variant by id or SKU, never by price."""

    quantity: int
    variant_id: UUID | None = None
    sku: str | None = None
    # Optional guard: if given, must equal the catalog price (never overrides it).
    expected_unit_price_cents: int | None = None


@dataclass(frozen=True)
class CreateOrderCommand:
    """Input for ``OrderService.create_order``."""

    customer_phone: str
    lines: tuple[OrderLine, ...]
    customer_name: str | None = None
    customer_email: str | None = None
    delivery_area: str | None = None
    delivery_address: str | None = None
    delivery_fee_cents: int = 0
    discount_cents: int = 0
    payment_method: PaymentMethod = PaymentMethod.MPESA_STK
    notes: str = ""
    idempotency_key: str | None = None


@dataclass
class _ResolvedLine:
    product_id: UUID
    variant_id: UUID | None
    sku: str
    name: str
    unit_price_cents: int
    quantity: int
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _SaleLine:
    product_id: UUID
    variant_id: UUID | None
    quantity: int


def _merge_resolved_lines(lines: list[_ResolvedLine]) -> list[_ResolvedLine]:
    """Fold duplicate variant lines into one (same order of first appearance)."""
    merged: dict[tuple[UUID, UUID | None], _ResolvedLine] = {}
    for line in lines:
        key = (line.product_id, line.variant_id)
        existing = merged.get(key)
        if existing is None:
            merged[key] = _ResolvedLine(
                product_id=line.product_id,
                variant_id=line.variant_id,
                sku=line.sku,
                name=line.name,
                unit_price_cents=line.unit_price_cents,
                quantity=line.quantity,
                attributes=line.attributes,
            )
        else:
            existing.quantity += line.quantity
    return list(merged.values())


def _merge_order_items(items: list[OrderItem]) -> list[_SaleLine]:
    """Fold persisted order lines by variant for a single, correct sale write."""
    merged: dict[tuple[UUID, UUID | None], _SaleLine] = {}
    for item in items:
        key = (item.product_id, item.variant_id)
        existing = merged.get(key)
        if existing is None:
            merged[key] = _SaleLine(
                product_id=item.product_id,
                variant_id=item.variant_id,
                quantity=item.quantity,
            )
        else:
            merged[key] = _SaleLine(
                product_id=existing.product_id,
                variant_id=existing.variant_id,
                quantity=existing.quantity + item.quantity,
            )
    return list(merged.values())


# =============================================================================
# Service
# =============================================================================


class OrderService:
    """Application service for orders (tenant-scoped)."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        reservation_ttl_minutes: int = 15,
        active_delivery_lookup: ActiveDeliveryLookup | None = None,
    ) -> None:
        self.session = session
        self.audit = AuditLogger(session)
        self.orders = OrderRepository(session)
        self.customers = CustomerRepository(session)
        self._movements = StockMovementRepository(session)
        self.reservation_ttl_minutes = reservation_ttl_minutes
        self._active_delivery_lookup = active_delivery_lookup

    # -- public read helpers ----------------------------------------------

    async def get_order(self, tenant_id: UUID, order_id: UUID) -> Order:
        order = await self.orders.get(tenant_id, order_id)
        if order is None:
            raise OrderNotFoundError(f"Order {order_id} not found")
        return order

    async def list_orders(
        self,
        tenant_id: UUID,
        *,
        status: str | None = None,
        customer_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Order], int]:
        return await self.orders.list(
            tenant_id,
            status=status,
            customer_id=customer_id,
            page=page,
            page_size=page_size,
        )

    async def list_items(self, tenant_id: UUID, order_id: UUID) -> list[OrderItem]:
        return await self.orders.items_for(tenant_id, order_id)

    # -- create -----------------------------------------------------------

    async def create_order(self, tenant_id: UUID, command: CreateOrderCommand) -> Order:
        """Create an order, reserve stock and move it to awaiting_payment."""
        if not command.lines:
            raise OrderValidationError("An order must have at least one line")
        if command.delivery_fee_cents < 0:
            raise OrderValidationError("Delivery fee cannot be negative")

        # Idempotency: a repeated key returns the order already created.
        if command.idempotency_key:
            existing = await self.orders.get_by_idempotency_key(tenant_id, command.idempotency_key)
            if existing is not None:
                return existing

        phone = CustomerPhone(command.customer_phone).value
        resolved: list[_ResolvedLine] = []
        for line in command.lines:
            if line.quantity <= 0:
                raise OrderValidationError("Line quantity must be positive")
            if (line.variant_id is None) == (line.sku is None):
                raise OrderValidationError("Each line needs exactly one of variant_id or sku")
            resolved.append(await self._resolve_line(tenant_id, line))
        # Collapse duplicate lines for the same variant so reservations, sales and
        # cart quantities agree (two lines of the same SKU are one line).
        resolved = _merge_resolved_lines(resolved)

        subtotal_cents = sum(r.unit_price_cents * r.quantity for r in resolved)
        if command.discount_cents < 0:
            raise OrderValidationError("Discount cannot be negative")
        if command.discount_cents > subtotal_cents:
            raise OrderValidationError("Discount cannot exceed the subtotal")
        total_cents = subtotal_cents + command.delivery_fee_cents - command.discount_cents
        if total_cents < 0:
            raise OrderValidationError("Order total cannot be negative")

        # Lock each variant and verify availability *before* writing any rows, so
        # an out-of-stock line leaves no partial order behind. The lock is held
        # for the rest of the transaction, making the later reserve atomic.
        ledgers: dict[tuple[UUID, UUID | None], StockLedger] = {}
        for r in resolved:
            key = (r.product_id, r.variant_id)
            if key in ledgers:
                continue
            await self._lock_variant(tenant_id, r.variant_id)
            ledger = await self._load_ledger(tenant_id, r.product_id, r.variant_id)
            if not ledger.can_reserve(r.quantity):
                raise InsufficientStockError(
                    f"Insufficient stock for SKU {r.sku}: "
                    f"available={ledger.available}, requested={r.quantity}"
                )
            ledgers[key] = ledger

        customer = await self.customers.get_or_create(
            tenant_id,
            phone,
            name=command.customer_name,
            email=command.customer_email,
        )

        order = Order(
            tenant_id=tenant_id,
            customer_id=customer.id,
            customer_phone=phone,
            customer_name=command.customer_name,
            customer_email=command.customer_email,
            subtotal_cents=subtotal_cents,
            delivery_fee_cents=command.delivery_fee_cents,
            discount_cents=command.discount_cents,
            total_cents=total_cents,
            status=OrderStatus.DRAFT.value,
            payment_method=command.payment_method.value,
            delivery_area=command.delivery_area,
            delivery_address=command.delivery_address,
            notes=command.notes,
            metadata_json=(
                {"idempotency_key": command.idempotency_key} if command.idempotency_key else {}
            ),
        )
        items = [
            OrderItem(
                tenant_id=tenant_id,
                product_id=r.product_id,
                variant_id=r.variant_id,
                sku=r.sku,
                name=r.name,
                attributes=r.attributes,
                unit_price_cents=r.unit_price_cents,
                quantity=r.quantity,
                total_price_cents=r.unit_price_cents * r.quantity,
            )
            for r in resolved
        ]
        await self.orders.add(order, items)

        # Reserve stock (ledger) and record the reservation rows.
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self.reservation_ttl_minutes)
        for r in resolved:
            ledger = ledgers[r.product_id, r.variant_id]
            movement = ledger.reserve(
                quantity=r.quantity,
                reference_id=order.id,
                reference_type="order",
            )
            await self._movements.add(movement)
            self.session.add(
                Reservation(
                    tenant_id=tenant_id,
                    product_id=r.product_id,
                    variant_id=r.variant_id,
                    quantity=r.quantity,
                    status=ReservationStatusModel.ACTIVE,
                    reason=ReservationReasonModel.ORDER_CREATED,
                    reference_id=order.id,
                    reference_type="order",
                    expires_at=expires_at,
                )
            )

        apply_order_transition(order, OrderStatus.AWAITING_PAYMENT)
        await self.session.flush()

        await add_outbox_event(
            event_type="order.created",
            payload={
                "order_id": str(order.id),
                "customer_phone": phone,
                "subtotal_cents": subtotal_cents,
                "total_cents": total_cents,
            },
            idempotency_key=f"order_created:{order.id}",
            tenant_id=tenant_id,
            session=self.session,
        )
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="order.created",
            resource="order",
            resource_id=order.id,
            after={
                "customer_phone": phone,
                "subtotal_cents": subtotal_cents,
                "delivery_fee_cents": command.delivery_fee_cents,
                "discount_cents": command.discount_cents,
                "total_cents": total_cents,
                "lines": [
                    {"sku": r.sku, "quantity": r.quantity, "unit_price_cents": r.unit_price_cents}
                    for r in resolved
                ],
            },
        )
        return order

    # -- cancel -----------------------------------------------------------

    async def cancel_order(self, tenant_id: UUID, order_id: UUID, reason: str) -> Order:
        """Cancel an unpaid order and release its reservations."""
        order = await self.get_order(tenant_id, order_id)
        current = OrderStatus(order.status)
        if not is_legal_transition(current, OrderStatus.CANCELLED):
            raise IllegalTransitionError(f"Illegal transition: {current.value} -> cancelled")

        released = await self._release_order_reservations(tenant_id, order_id)
        apply_order_transition(order, OrderStatus.CANCELLED)
        order.cancelled_reason = reason
        await self.session.flush()

        await add_outbox_event(
            event_type="order.cancelled",
            payload={"order_id": str(order_id), "reason": reason, "released": released},
            idempotency_key=f"order_cancelled:{order_id}",
            tenant_id=tenant_id,
            session=self.session,
        )
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="order.cancelled",
            resource="order",
            resource_id=order_id,
            metadata={"reason": reason, "released_quantity": released},
        )
        return order

    # -- sale side effect (payment.completed) -----------------------------

    async def record_sale_for_order(self, tenant_id: UUID, order_id: UUID) -> Order:
        """Turn a paid order's reservations into sales (idempotent).

        Called from the ``payment.completed`` outbox handler. A replayed event is
        a no-op: if a ``SELL`` movement already exists for the order, the line is
        skipped.
        """
        order = await self.get_order(tenant_id, order_id)
        if OrderStatus(order.status) is not OrderStatus.PAID and not is_pod_in_transit(order):
            # Nothing to sell yet (or a terminal state) -- not an error.
            # POD orders in transit are the exception: sale records at payment.
            return order

        items = await self.orders.items_for(tenant_id, order_id)
        for item in _merge_order_items(items):
            await self._lock_variant(tenant_id, item.variant_id)
            ledger = await self._load_ledger(tenant_id, item.product_id, item.variant_id)
            already_sold = any(
                m.reference_id == order_id
                for m in ledger.get_movements(movement_type=StockMovementType.SELL)
            )
            if already_sold:
                continue

            # A SELL closes the reservation for this order. If the reservation
            # TTL released it while payment was in flight, the unit is available
            # to other orders again: selling here without re-reserving would
            # double-count it for whichever order holds it next (oversell with
            # no negative-availability signal). Under the variant lock, either
            # re-establish the reservation (honouring the payment) or refuse.
            reserved = ledger.reserved_quantity_for(order_id)
            if reserved < item.quantity:
                deficit = item.quantity - reserved
                if ledger.available < deficit:
                    raise ReservationLostError(
                        f"Order {order_id} line variant={item.variant_id}: "
                        f"reservation was released while payment was in flight "
                        f"and only {ledger.available} unit(s) are available to "
                        f"re-reserve (need {deficit}); refusing to oversell"
                    )
                re_reserve = ledger.reserve(
                    quantity=deficit,
                    reference_id=order_id,
                    reference_type="order",
                    reason=StockMovementReason.ORDER_CREATED,
                )
                await self._movements.add(re_reserve)
                await self._reopen_released_reservations(
                    tenant_id, order_id, item.product_id, item.variant_id
                )

            movement = ledger.sell(
                quantity=item.quantity,
                reference_id=order_id,
                reference_type="order",
                reason=StockMovementReason.PAYMENT_CONFIRMED,
            )
            await self._movements.add(movement)
            await self._set_reservations_status(
                tenant_id,
                order_id,
                item.product_id,
                item.variant_id,
                ReservationStatusModel.FULFILLED,
            )

        await self.session.flush()
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="order.sale_recorded",
            resource="order",
            resource_id=order_id,
            metadata={"lines": len(items)},
        )
        return order

    # -- packed -----------------------------------------------------------

    async def mark_packed(self, tenant_id: UUID, order_id: UUID) -> Order:
        """Move an order to packed."""
        order = await self.get_order(tenant_id, order_id)
        # B3: guard in mark_packed (POD only; prepay still requires PAID).
        current = OrderStatus(order.status)
        is_pod = order.payment_method == PaymentMethod.MPESA_PAY_ON_DELIVERY.value
        if not is_pod and current is OrderStatus.AWAITING_PAYMENT:
            raise IllegalTransitionError(
                f"Illegal transition: {current.value} -> packed (prepay must be PAID first)"
            )
        apply_order_transition(order, OrderStatus.PACKED)
        await self.session.flush()
        await self.audit.log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="order.packed",
            resource="order",
            resource_id=order_id,
        )
        return order

    # -- reservation expiry ----------------------------------------------

    async def expire_stale_reservations(self, tenant_id: UUID | None = None) -> int:
        """Release active reservations past their TTL. Returns the count released.

        Grouped by (order, product, variant) so a single ``RELEASE`` movement is
        written per reservation reference.
        """
        now = datetime.now(UTC)
        query = select(Reservation).where(
            Reservation.status == ReservationStatusModel.ACTIVE.value,
            Reservation.expires_at <= now,
        )
        if tenant_id is not None:
            query = query.where(Reservation.tenant_id == tenant_id)
        rows = list((await self.session.execute(query)).scalars().all())

        released = 0
        for reservation in rows:
            # A pay-on-delivery order with an in-flight delivery keeps its
            # reservation: the goods are moving and payment completes at
            # delivery. Releasing here would sell the same unit twice.
            reference_id = reservation.reference_id
            if reference_id is not None and await self._has_active_delivery(
                reservation.tenant_id, reference_id
            ):
                continue
            await self._lock_variant(reservation.tenant_id, reservation.variant_id)
            ledger = await self._load_ledger(
                reservation.tenant_id, reservation.product_id, reservation.variant_id
            )
            if reference_id is not None:
                remaining = ledger.reserved_quantity_for(reference_id)
                if remaining >= reservation.quantity:
                    movement = ledger.release(
                        quantity=reservation.quantity,
                        reference_id=reference_id,
                        reference_type="order",
                        reason=StockMovementReason.RESERVATION_EXPIRED,
                    )
                    await self._movements.add(movement)
            reservation.status = ReservationStatusModel.EXPIRED
            reservation.released_at = now
            released += 1

        if released:
            await self.session.flush()
        return released

    # -- internals --------------------------------------------------------

    async def _has_active_delivery(self, tenant_id: UUID, order_id: UUID) -> bool:
        """Whether an order has a delivery in flight.

        Uses the injected lookup when provided (tests / composition root).
        Otherwise imports the delivery repository lazily: ``orders`` must not
        import ``delivery`` at module scope (the dependency direction is
        delivery -> orders; ADR 0015), and the deferred import breaks the cycle.
        """
        if self._active_delivery_lookup is not None:
            return await self._active_delivery_lookup(tenant_id, order_id)
        from packages.core.delivery.repository import DeliveryRepository

        delivery = await DeliveryRepository(self.session).active_delivery_for_order(
            tenant_id, order_id
        )
        return delivery is not None

    async def _lock_variant(self, tenant_id: UUID, variant_id: UUID | None) -> None:
        """Serialize concurrent order creation for the same variant.

        Availability is derived from the movement ledger, so we lock the variant
        row (not the ledger) to make the read-check-reserve sequence atomic
        across concurrent transactions. A ``raw`` statement avoids SQLAlchemy's
        joined eager-load (``FOR UPDATE`` cannot lock the nullable side of an
        outer join).
        """
        if variant_id is None:
            return
        await self.session.execute(
            text(
                "SELECT 1 FROM product_variants "
                "WHERE id = :id AND tenant_id = :tenant_id FOR UPDATE"
            ),
            {"id": str(variant_id), "tenant_id": str(tenant_id)},
        )

    async def _resolve_line(self, tenant_id: UUID, line: OrderLine) -> _ResolvedLine:
        if line.variant_id is not None:
            variant = await self._load_variant_by_id(tenant_id, line.variant_id)
        else:
            variant = await self._load_variant_by_sku(tenant_id, line.sku or "")
        if variant is None:
            ident = line.variant_id or line.sku
            raise ItemNotFoundError(f"Variant {ident!r} not found in catalog")
        if not variant.is_active:
            raise ItemUnavailableError(f"Variant {variant.sku!r} is not available")
        if (
            line.expected_unit_price_cents is not None
            and line.expected_unit_price_cents != variant.sell_price_cents
        ):
            raise PriceMismatchError(
                f"Expected {line.expected_unit_price_cents} for SKU {variant.sku!r} "
                f"but the catalog price is {variant.sell_price_cents}"
            )
        product = variant.product
        return _ResolvedLine(
            product_id=variant.product_id,
            variant_id=variant.id,
            sku=variant.sku,
            name=product.name if product is not None else variant.sku,
            unit_price_cents=variant.sell_price_cents,
            quantity=line.quantity,
            attributes={"variant_attributes": variant.attributes} if variant.attributes else {},
        )

    async def _load_variant_by_id(self, tenant_id: UUID, variant_id: UUID) -> ProductVariant | None:
        result = await self.session.execute(
            select(ProductVariant).where(
                ProductVariant.id == variant_id,
                ProductVariant.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def _load_variant_by_sku(self, tenant_id: UUID, sku: str) -> ProductVariant | None:
        result = await self.session.execute(
            select(ProductVariant).where(
                ProductVariant.sku == sku,
                ProductVariant.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def _load_ledger(
        self, tenant_id: UUID, product_id: UUID, variant_id: UUID | None
    ) -> StockLedger:
        rows = await self._movements.get_by_tenant_product(tenant_id, product_id, variant_id)
        movements = [_movement_model_to_domain(row) for row in rows]
        ledger = StockLedger(tenant_id=tenant_id, product_id=product_id, variant_id=variant_id)
        ledger.load(movements)
        return ledger

    async def _reopen_released_reservations(
        self,
        tenant_id: UUID,
        order_id: UUID,
        product_id: UUID,
        variant_id: UUID | None,
    ) -> None:
        """Re-activate reservations the expiry job released for a paid order.

        Called after re-reserving a line whose reservation TTL had expired, so
        that the subsequent ``_set_reservations_status(..., FULFILLED)`` sees an
        active row and the projection stays consistent with the ledger.
        """
        query = select(Reservation).where(
            Reservation.tenant_id == tenant_id,
            Reservation.reference_id == order_id,
            Reservation.product_id == product_id,
            Reservation.status == ReservationStatusModel.EXPIRED.value,
        )
        if variant_id is None:
            query = query.where(Reservation.variant_id.is_(None))
        else:
            query = query.where(Reservation.variant_id == variant_id)
        for reservation in (await self.session.execute(query)).scalars().all():
            reservation.status = ReservationStatusModel.ACTIVE.value
            reservation.released_at = None

    async def _set_reservations_status(
        self,
        tenant_id: UUID,
        order_id: UUID,
        product_id: UUID,
        variant_id: UUID | None,
        status: ReservationStatusModel,
    ) -> None:
        query = select(Reservation).where(
            Reservation.tenant_id == tenant_id,
            Reservation.reference_id == order_id,
            Reservation.product_id == product_id,
            Reservation.status == ReservationStatusModel.ACTIVE.value,
        )
        if variant_id is None:
            query = query.where(Reservation.variant_id.is_(None))
        else:
            query = query.where(Reservation.variant_id == variant_id)
        now = datetime.now(UTC)
        for reservation in (await self.session.execute(query)).scalars().all():
            reservation.status = status
            if status is ReservationStatusModel.FULFILLED:
                reservation.fulfilled_at = now
            elif status is ReservationStatusModel.RELEASED:
                reservation.released_at = now

    async def _release_order_reservations(self, tenant_id: UUID, order_id: UUID) -> int:
        """Release every active reservation for an order; returns total quantity."""
        query = select(Reservation).where(
            Reservation.tenant_id == tenant_id,
            Reservation.reference_id == order_id,
            Reservation.status == ReservationStatusModel.ACTIVE.value,
        )
        now = datetime.now(UTC)
        released_total = 0
        for reservation in (await self.session.execute(query)).scalars().all():
            await self._lock_variant(tenant_id, reservation.variant_id)
            ledger = await self._load_ledger(
                tenant_id, reservation.product_id, reservation.variant_id
            )
            remaining = ledger.reserved_quantity_for(order_id)
            if remaining >= reservation.quantity:
                movement = ledger.release(
                    quantity=reservation.quantity,
                    reference_id=order_id,
                    reference_type="order",
                    reason=StockMovementReason.ORDER_CANCELLED,
                )
                await self._movements.add(movement)
            reservation.status = ReservationStatusModel.RELEASED
            reservation.released_at = now
            released_total += reservation.quantity
        return released_total


def _movement_model_to_domain(row: StockMovementModel) -> StockMovementVO:
    """Convert a persisted movement row back to the ledger value object."""
    return StockMovementVO(
        id=row.id,
        tenant_id=row.tenant_id,
        product_id=row.product_id,
        variant_id=row.variant_id,
        movement_type=StockMovementType(row.movement_type),
        quantity_change=row.quantity_change,
        reason=StockMovementReason(row.reason),
        reference_id=row.reference_id,
        reference_type=row.reference_type,
        previous_available=row.previous_available,
        new_available=row.new_available,
        unit_cost_cents=row.unit_cost_cents,
        notes=row.notes or "",
        created_at=row.created_at,
        created_by=row.created_by,
    )
