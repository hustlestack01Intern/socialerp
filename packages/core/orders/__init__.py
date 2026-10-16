"""
Orders service (Phase 6).

Deterministic order creation, stock reservation, cancellation and the sale
side effect. The LLM never participates: prices come from the catalog, totals
are integer-cents arithmetic here, and availability comes from the event-sourced
stock ledger.

See ``docs/adr/0013-order-creation-reservation-and-sale.md``.
"""

from packages.core.orders.service import (
    CreateOrderCommand,
    InsufficientStockError,
    InvalidReservationError,
    ItemNotFoundError,
    ItemUnavailableError,
    OrderError,
    OrderLine,
    OrderNotFoundError,
    OrderService,
    OrderValidationError,
    PriceMismatchError,
    ReservationLostError,
)

__all__ = [
    "CreateOrderCommand",
    "InsufficientStockError",
    "InvalidReservationError",
    "ItemNotFoundError",
    "ItemUnavailableError",
    "OrderError",
    "OrderLine",
    "OrderNotFoundError",
    "OrderService",
    "OrderValidationError",
    "PriceMismatchError",
    "ReservationLostError",
]
