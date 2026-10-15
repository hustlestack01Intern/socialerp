"""
L2 transaction tools (Phase 6).

The LLM extracts structured intent; deterministic code validates against the
catalog, computes totals and reserves stock (AGENTS.md §5). The agent talks to
the transaction layer through these types only — the tool owns all money and
stock arithmetic and returns the authoritative result for the reply.

Two implementations:
- :class:`FakeOrderCreator` — deterministic, in-memory; agent tests and evals.
- ``packages.agent.service_tools.ServiceOrderCreator`` — backed by the real
  ``OrderService`` (imported lazily; not pulled into the offline eval path).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from packages.agent.errors import AgentError

# -- stable error codes a tool may report (safe to branch on in the agent) ---
UNKNOWN_ITEM = "unknown_item"
ITEM_UNAVAILABLE = "item_unavailable"
INSUFFICIENT_STOCK = "insufficient_stock"
PRICE_CHANGED = "price_changed"
VALIDATION = "validation"
INTERNAL = "internal"

CUSTOMER_MESSAGES = {
    UNKNOWN_ITEM: "I couldn't find that item in our catalogue.",
    ITEM_UNAVAILABLE: "That item is currently unavailable.",
    INSUFFICIENT_STOCK: "Sorry, we don't have enough stock for that order.",
    PRICE_CHANGED: "The price of that item just changed, so I need a moment to confirm.",
    VALIDATION: "I need a valid phone number to place that order.",
    INTERNAL: "I couldn't complete that order — a team member will help shortly.",
}


class OrderToolError(AgentError):
    """The tool itself failed unexpectedly (not a business rejection)."""


@dataclass(frozen=True)
class OrderCreateLine:
    """One line the agent resolved from the catalog (price is a guard, never a price)."""

    sku: str
    quantity: int
    expected_unit_price_cents: int | None = None


@dataclass(frozen=True)
class OrderCreateRequest:
    """Agent -> tool: what the customer asked for, already catalog-resolved."""

    customer_phone: str
    lines: tuple[OrderCreateLine, ...]
    delivery_area: str | None = None
    # The conversation wiring passes the provider message id here so a retried
    # webhook cannot create a second order (the tool dedupes on it).
    idempotency_key: str | None = None


@dataclass(frozen=True)
class OrderCreateResult:
    """Tool -> agent: the authoritative outcome (money decided by the tool)."""

    ok: bool
    order_id: str = ""
    total_cents: int = 0
    status: str = ""
    error_code: str = ""
    message: str = ""  # customer-safe when ok=False

    @property
    def customer_message(self) -> str:
        return self.message or CUSTOMER_MESSAGES.get(self.error_code, INTERNAL)


class OrderCreator(Protocol):
    """The transactional tool the agent may call at L2 (never below)."""

    async def create_order(self, request: OrderCreateRequest) -> OrderCreateResult: ...


class FakeOrderCreator:
    """Deterministic in-memory ``OrderCreator`` for agent tests and evals.

    Mirrors the real semantics without a database: catalog prices only,
    stock decremented on reserve, idempotent on key. Totals are computed here,
    never by the model or the agent.
    """

    def __init__(
        self,
        *,
        prices: dict[str, int],
        stock: dict[str, int] | None = None,
    ) -> None:
        self.prices = dict(prices)
        self.stock = dict(stock) if stock is not None else dict.fromkeys(prices, 10)
        self.orders: dict[str, OrderCreateResult] = {}
        self.requests: list[OrderCreateRequest] = []
        self.created: list[OrderCreateResult] = []

    async def create_order(self, request: OrderCreateRequest) -> OrderCreateResult:
        self.requests.append(request)
        if request.idempotency_key:
            existing = self.orders.get(request.idempotency_key)
            if existing is not None:
                return existing

        if not request.customer_phone or len(request.customer_phone) < 9:
            return _fail(VALIDATION)
        if not request.lines:
            return _fail(VALIDATION)

        for line in request.lines:
            if line.sku not in self.prices:
                return _fail(UNKNOWN_ITEM)
            if self.stock.get(line.sku, 0) < line.quantity:
                return _fail(INSUFFICIENT_STOCK)
            if (
                line.expected_unit_price_cents is not None
                and line.expected_unit_price_cents != self.prices[line.sku]
            ):
                return _fail(PRICE_CHANGED)

        for line in request.lines:
            self.stock[line.sku] = self.stock.get(line.sku, 0) - line.quantity

        total = sum(self.prices[line.sku] * line.quantity for line in request.lines)
        result = OrderCreateResult(
            ok=True,
            order_id=f"ord-{uuid4().hex[:12]}",
            total_cents=total,
            status="awaiting_payment",
        )
        if request.idempotency_key:
            self.orders[request.idempotency_key] = result
        self.created.append(result)
        return result


def _fail(code: str) -> OrderCreateResult:
    return OrderCreateResult(ok=False, error_code=code, message=CUSTOMER_MESSAGES[code])


__all__ = [
    "CUSTOMER_MESSAGES",
    "INSUFFICIENT_STOCK",
    "INTERNAL",
    "ITEM_UNAVAILABLE",
    "PRICE_CHANGED",
    "UNKNOWN_ITEM",
    "VALIDATION",
    "FakeOrderCreator",
    "OrderCreateLine",
    "OrderCreateRequest",
    "OrderCreateResult",
    "OrderCreator",
    "OrderToolError",
]
