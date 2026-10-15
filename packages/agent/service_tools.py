"""
DB-backed L2 tools wired to the core services (Phase 6).

Dependency direction: the agent depends on the domain services (agent -> core).
This module is imported explicitly by the conversation wiring and by integration
tests; it is deliberately **not** exported from ``packages.agent.__init__`` so a
bare ``import packages.agent`` (offline evals) does not pull in SQLAlchemy.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from packages.agent.tools import (
    CUSTOMER_MESSAGES,
    INSUFFICIENT_STOCK,
    INTERNAL,
    ITEM_UNAVAILABLE,
    PRICE_CHANGED,
    UNKNOWN_ITEM,
    OrderCreateRequest,
    OrderCreateResult,
)
from packages.core.orders import (
    CreateOrderCommand,
    InsufficientStockError,
    ItemNotFoundError,
    ItemUnavailableError,
    OrderLine,
    OrderService,
    PriceMismatchError,
)


class ServiceOrderCreator:
    """Agent ``OrderCreator`` backed by the real, deterministic ``OrderService``.

    The agent supplies catalog-resolved lines (SKU + quantity + a price guard);
    ``OrderService`` re-validates prices against the catalog, computes totals and
    reserves stock in the event-sourced ledger. Errors are mapped to stable codes
    with a customer-safe message that never leaks internals.
    """

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def create_order(self, request: OrderCreateRequest) -> OrderCreateResult:
        command = CreateOrderCommand(
            customer_phone=request.customer_phone,
            lines=tuple(
                OrderLine(
                    quantity=line.quantity,
                    sku=line.sku,
                    expected_unit_price_cents=line.expected_unit_price_cents,
                )
                for line in request.lines
            ),
            delivery_area=request.delivery_area,
            idempotency_key=request.idempotency_key,
        )
        try:
            order = await OrderService(self.session).create_order(self.tenant_id, command)
        except ItemNotFoundError:
            return _reject(UNKNOWN_ITEM)
        except ItemUnavailableError:
            return _reject(ITEM_UNAVAILABLE)
        except InsufficientStockError:
            return _reject(INSUFFICIENT_STOCK)
        except PriceMismatchError:
            return _reject(PRICE_CHANGED)
        except Exception:
            return _reject(INTERNAL)
        return OrderCreateResult(
            ok=True,
            order_id=str(order.id),
            total_cents=order.total_cents,
            status=order.status,
        )


def _reject(code: str) -> OrderCreateResult:
    return OrderCreateResult(ok=False, error_code=code, message=CUSTOMER_MESSAGES[code])


__all__ = ["ServiceOrderCreator"]
