"""
Event System and Outbox Pattern

Base classes for domain events and the outbox pattern for reliable
side-effect processing with at-least-once delivery guarantees.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import uuid4

from packages.core.domain import TenantId

# =============================================================================
# Event Base Classes
# =============================================================================


class DomainEvent(ABC):
    """
    Base class for all domain events.

    Events are immutable, serializable, and carry all information
    needed for downstream processing without requiring additional queries.
    """

    __slots__ = ("event_id", "event_type", "occurred_at", "tenant_id")

    def __init__(
        self,
        tenant_id: TenantId,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        self.event_id = event_id or str(uuid4())
        self.tenant_id = tenant_id
        self.occurred_at = occurred_at or datetime.now(UTC)
        self.event_type = self.__class__.__name__

    def to_json(self) -> str:
        """Serialize event to JSON for outbox storage."""
        data = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "tenant_id": str(self.tenant_id),
            "occurred_at": self.occurred_at.isoformat(),
            "payload": self._payload(),
        }
        return json.dumps(data, separators=(",", ":"))

    @abstractmethod
    def _payload(self) -> dict[str, Any]:
        """Return event-specific payload as dict."""

    @classmethod
    def from_json(cls, json_str: str) -> DomainEvent:
        """Deserialize event from JSON (requires registry)."""
        json.loads(json_str)
        # In production, use a registry mapping event_type to class
        raise NotImplementedError("Use EventRegistry for deserialization")

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, name):
            raise AttributeError(f"'{self.__class__.__name__}' is immutable")
        super().__setattr__(name, value)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(event_id={self.event_id}, tenant_id={self.tenant_id})"


# =============================================================================
# Specific Domain Events (examples for M0)
# =============================================================================


class TenantCreated(DomainEvent):
    """Published when a new tenant is onboarded."""

    __slots__ = ("owner_email", "owner_phone", "tenant_name")

    def __init__(
        self,
        tenant_id: TenantId,
        tenant_name: str,
        owner_phone: str,
        owner_email: str,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.tenant_name = tenant_name
        self.owner_phone = owner_phone
        self.owner_email = owner_email

    def _payload(self) -> dict[str, Any]:
        return {
            "tenant_name": self.tenant_name,
            "owner_phone": self.owner_phone,
            "owner_email": self.owner_email,
        }


class ProductCreated(DomainEvent):
    """Published when a product is added to catalog."""

    __slots__ = ("cost_price_cents", "name", "product_id", "sell_price_cents", "sku")

    def __init__(
        self,
        tenant_id: TenantId,
        product_id: str,
        sku: str,
        name: str,
        cost_price_cents: int,
        sell_price_cents: int,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.product_id = product_id
        self.sku = sku
        self.name = name
        self.cost_price_cents = cost_price_cents
        self.sell_price_cents = sell_price_cents

    def _payload(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "sku": self.sku,
            "name": self.name,
            "cost_price_cents": self.cost_price_cents,
            "sell_price_cents": self.sell_price_cents,
        }


class StockMovementRecorded(DomainEvent):
    """Published when a stock movement is recorded (immutable ledger entry)."""

    __slots__ = (
        "movement_type",
        "new_available_quantity",
        "product_id",
        "quantity_change",
        "reason",
        "reference_id",
        "variant_id",
    )

    def __init__(
        self,
        tenant_id: TenantId,
        product_id: str,
        variant_id: str | None,
        movement_type: str,
        quantity_change: int,
        new_available_quantity: int,
        reference_id: str | None,
        reason: str,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.product_id = product_id
        self.variant_id = variant_id
        self.movement_type = movement_type
        self.quantity_change = quantity_change
        self.new_available_quantity = new_available_quantity
        self.reference_id = reference_id
        self.reason = reason

    def _payload(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "variant_id": self.variant_id,
            "movement_type": self.movement_type,
            "quantity_change": self.quantity_change,
            "new_available_quantity": self.new_available_quantity,
            "reference_id": self.reference_id,
            "reason": self.reason,
        }


class OrderCreated(DomainEvent):
    """Published when an order is created (draft state)."""

    __slots__ = ("customer_phone", "items", "order_id", "total_cents")

    def __init__(
        self,
        tenant_id: TenantId,
        order_id: str,
        customer_phone: str,
        items: list[dict[str, Any]],
        total_cents: int,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.order_id = order_id
        self.customer_phone = customer_phone
        self.items = items
        self.total_cents = total_cents

    def _payload(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "customer_phone": self.customer_phone,
            "items": self.items,
            "total_cents": self.total_cents,
        }


class OrderStatusChanged(DomainEvent):
    """Published when order transitions between states."""

    __slots__ = ("new_status", "old_status", "order_id", "reason")

    def __init__(
        self,
        tenant_id: TenantId,
        order_id: str,
        old_status: str,
        new_status: str,
        reason: str | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.order_id = order_id
        self.old_status = old_status
        self.new_status = new_status
        self.reason = reason

    def _payload(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "reason": self.reason,
        }


class PaymentInitiated(DomainEvent):
    """Published when STK Push is sent to customer."""

    __slots__ = (
        "amount_cents",
        "checkout_request_id",
        "order_id",
        "payment_id",
        "phone",
        "provider",
    )

    def __init__(
        self,
        tenant_id: TenantId,
        payment_id: str,
        order_id: str,
        checkout_request_id: str,
        phone: str,
        amount_cents: int,
        provider: str,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.payment_id = payment_id
        self.order_id = order_id
        self.checkout_request_id = checkout_request_id
        self.phone = phone
        self.amount_cents = amount_cents
        self.provider = provider

    def _payload(self) -> dict[str, Any]:
        return {
            "payment_id": self.payment_id,
            "order_id": self.order_id,
            "checkout_request_id": self.checkout_request_id,
            "phone": self.phone,
            "amount_cents": self.amount_cents,
            "provider": self.provider,
        }


class PaymentCompleted(DomainEvent):
    """Published when payment callback confirms success."""

    __slots__ = (
        "amount_cents",
        "checkout_request_id",
        "mpesa_receipt_number",
        "order_id",
        "payment_id",
    )

    def __init__(
        self,
        tenant_id: TenantId,
        payment_id: str,
        order_id: str,
        checkout_request_id: str,
        mpesa_receipt_number: str,
        amount_cents: int,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.payment_id = payment_id
        self.order_id = order_id
        self.checkout_request_id = checkout_request_id
        self.mpesa_receipt_number = mpesa_receipt_number
        self.amount_cents = amount_cents

    def _payload(self) -> dict[str, Any]:
        return {
            "payment_id": self.payment_id,
            "order_id": self.order_id,
            "checkout_request_id": self.checkout_request_id,
            "mpesa_receipt_number": self.mpesa_receipt_number,
            "amount_cents": self.amount_cents,
        }


class PaymentFailed(DomainEvent):
    """Published when payment fails or times out."""

    __slots__ = ("checkout_request_id", "order_id", "payment_id", "reason", "result_code")

    def __init__(
        self,
        tenant_id: TenantId,
        payment_id: str,
        order_id: str,
        checkout_request_id: str,
        reason: str,
        result_code: str | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.payment_id = payment_id
        self.order_id = order_id
        self.checkout_request_id = checkout_request_id
        self.reason = reason
        self.result_code = result_code

    def _payload(self) -> dict[str, Any]:
        return {
            "payment_id": self.payment_id,
            "order_id": self.order_id,
            "checkout_request_id": self.checkout_request_id,
            "reason": self.reason,
            "result_code": self.result_code,
        }


class ReservationCreated(DomainEvent):
    """Published when stock is reserved for an order."""

    __slots__ = ("expires_at", "order_id", "product_id", "quantity", "reservation_id", "variant_id")

    def __init__(
        self,
        tenant_id: TenantId,
        reservation_id: str,
        order_id: str,
        product_id: str,
        variant_id: str | None,
        quantity: int,
        expires_at: datetime,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.reservation_id = reservation_id
        self.order_id = order_id
        self.product_id = product_id
        self.variant_id = variant_id
        self.quantity = quantity
        self.expires_at = expires_at

    def _payload(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "order_id": self.order_id,
            "product_id": self.product_id,
            "variant_id": self.variant_id,
            "quantity": self.quantity,
            "expires_at": self.expires_at.isoformat(),
        }


class ReservationReleased(DomainEvent):
    """Published when reservation expires or is cancelled."""

    __slots__ = ("reason", "reservation_id")

    def __init__(
        self,
        tenant_id: TenantId,
        reservation_id: str,
        reason: str,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.reservation_id = reservation_id
        self.reason = reason

    def _payload(self) -> dict[str, Any]:
        return {
            "reservation_id": self.reservation_id,
            "reason": self.reason,
        }


class MessageReceived(DomainEvent):
    """Published when inbound message is processed."""

    __slots__ = (
        "channel",
        "content",
        "conversation_id",
        "customer_phone",
        "message_id",
        "message_type",
    )

    def __init__(
        self,
        tenant_id: TenantId,
        conversation_id: str,
        message_id: str,
        channel: str,
        customer_phone: str,
        content: str,
        message_type: str,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.channel = channel
        self.customer_phone = customer_phone
        self.content = content
        self.message_type = message_type

    def _payload(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "channel": self.channel,
            "customer_phone": self.customer_phone,
            "content": self.content,
            "message_type": self.message_type,
        }


class AgentActionLogged(DomainEvent):
    """Published for every agent action (audit trail)."""

    __slots__ = (
        "action_type",
        "confidence",
        "conversation_id",
        "input_summary",
        "output_summary",
        "policy_decision",
        "rule_that_allowed",
        "tool_calls",
    )

    def __init__(
        self,
        tenant_id: TenantId,
        conversation_id: str,
        action_type: str,
        input_summary: str,
        tool_calls: list[dict[str, Any]],
        output_summary: str,
        policy_decision: str,
        rule_that_allowed: str,
        confidence: float,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> None:
        super().__init__(tenant_id, event_id, occurred_at)
        self.conversation_id = conversation_id
        self.action_type = action_type
        self.input_summary = input_summary
        self.tool_calls = tool_calls
        self.output_summary = output_summary
        self.policy_decision = policy_decision
        self.rule_that_allowed = rule_that_allowed
        self.confidence = confidence

    def _payload(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "action_type": self.action_type,
            "input_summary": self.input_summary,
            "tool_calls": self.tool_calls,
            "output_summary": self.output_summary,
            "policy_decision": self.policy_decision,
            "rule_that_allowed": self.rule_that_allowed,
            "confidence": self.confidence,
        }


# =============================================================================
# Outbox Pattern
# =============================================================================


class OutboxEvent:
    """
    Wrapper for domain events stored in the outbox table.

    The outbox table is written in the same transaction as the domain change,
    ensuring atomicity. A separate processor reads from the outbox and
    publishes to message brokers / webhooks / handlers.
    """

    __slots__ = (
        "created_at",
        "event_type",
        "id",
        "last_error",
        "payload",
        "processed_at",
        "retry_count",
        "tenant_id",
    )

    def __init__(
        self,
        tenant_id: TenantId,
        event_type: str,
        payload: str,
        id: str | None = None,
        created_at: datetime | None = None,
        processed_at: datetime | None = None,
        retry_count: int = 0,
        last_error: str | None = None,
    ) -> None:
        self.id = id or str(uuid4())
        self.tenant_id = tenant_id
        self.event_type = event_type
        self.payload = payload
        self.created_at = created_at or datetime.now(UTC)
        self.processed_at = processed_at
        self.retry_count = retry_count
        self.last_error = last_error

    @classmethod
    def from_domain_event(cls, event: DomainEvent) -> OutboxEvent:
        return cls(
            tenant_id=event.tenant_id,
            event_type=event.event_type,
            payload=event.to_json(),
        )

    def mark_processed(self) -> OutboxEvent:
        return OutboxEvent(
            id=self.id,
            tenant_id=self.tenant_id,
            event_type=self.event_type,
            payload=self.payload,
            created_at=self.created_at,
            processed_at=datetime.now(UTC),
            retry_count=self.retry_count,
            last_error=self.last_error,
        )

    def mark_failed(self, error: str) -> OutboxEvent:
        return OutboxEvent(
            id=self.id,
            tenant_id=self.tenant_id,
            event_type=self.event_type,
            payload=self.payload,
            created_at=self.created_at,
            processed_at=self.processed_at,
            retry_count=self.retry_count + 1,
            last_error=error,
        )


# =============================================================================
# Event Registry (for deserialization)
# =============================================================================


class EventRegistry:
    """
    Registry mapping event type names to event classes.
    Used for deserializing events from the outbox.
    """

    _registry: ClassVar[dict[str, type[DomainEvent]]] = {}

    @classmethod
    def register(cls, event_class: type[DomainEvent]) -> type[DomainEvent]:
        cls._registry[event_class.__name__] = event_class
        return event_class

    @classmethod
    def get(cls, event_type: str) -> type[DomainEvent] | None:
        return cls._registry.get(event_type)

    @classmethod
    def deserialize(cls, json_str: str) -> DomainEvent:
        data = json.loads(json_str)
        event_type = data.get("event_type")
        event_class = cls.get(event_type)
        if event_class is None:
            raise ValueError(f"Unknown event type: {event_type}")
        raise NotImplementedError("Use pydantic model_validate for full deserialization")


# Auto-register all events defined in this module
for _name, _obj in list(globals().items()):
    if isinstance(_obj, type) and issubclass(_obj, DomainEvent) and _obj is not DomainEvent:
        EventRegistry.register(_obj)
