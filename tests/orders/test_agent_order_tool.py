"""End-to-end L2: agent -> ServiceOrderCreator -> OrderService (real Postgres).

The full Phase-6 transact path for a WhatsApp order: the agent extracts intent,
resolves the item to a catalog SKU and calls the deterministic tool, which
creates a real order and reserves stock in the ledger. Tenant isolation is
proven through the agent path as well.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from socialerp.tenancy import Tenant
from sqlalchemy import select

from packages.agent.agent import Agent, BusinessFacts, CatalogItem, InMemoryCatalog
from packages.agent.intent import HeuristicIntentExtractor
from packages.agent.policy import AutonomyLevel, PolicyEngine
from packages.agent.service_tools import ServiceOrderCreator
from packages.core.domain.stock import StockLedger, StockMovementRepository
from packages.core.models.catalog import Product, ProductStatus, ProductVariant
from packages.core.models.order import Order
from packages.core.orders import OrderService
from packages.core.orders.service import _movement_model_to_domain

PHONE = "254712345678"
SKU = "HOOD-BLK-M"


async def _seed_variant(
    session,
    tenant_id: UUID,
    *,
    price_cents: int = 5000,
    qty: int = 10,
    sku: str = SKU,
) -> ProductVariant:
    product = Product(tenant_id=tenant_id, name="Hoodie", status=ProductStatus.ACTIVE.value)
    session.add(product)
    await session.flush()

    variant = ProductVariant(
        tenant_id=tenant_id,
        product_id=product.id,
        sku=sku,
        attributes=[],
        cost_price_cents=price_cents // 2,
        sell_price_cents=price_cents,
        quantity=qty,
        is_active=True,
    )
    session.add(variant)
    await session.flush()

    ledger = StockLedger(tenant_id=tenant_id, product_id=product.id, variant_id=variant.id)
    ledger.load([])
    movement = ledger.record_receive(
        quantity=qty,
        unit_cost_cents=price_cents // 2,
        reference_id=None,
        reference_type="purchase_order",
    )
    await StockMovementRepository(session).add(movement)
    return variant


def _agent(tenant_id: UUID, session, *, price_cents: int = 5000) -> Agent:
    catalog = InMemoryCatalog([
        CatalogItem(sku=SKU, name="Black Hoodie M", price_cents=price_cents, in_stock=True),
    ])
    return Agent(
        extractor=HeuristicIntentExtractor(),
        policy=PolicyEngine(),
        catalog=catalog,
        facts=BusinessFacts(),
        autonomy=AutonomyLevel.L2_TRANSACT,
        order_creator=ServiceOrderCreator(session, tenant_id),
    )


async def _available(session, tenant_id: UUID, product_id: UUID, variant_id: UUID) -> int:
    rows = await StockMovementRepository(session).get_by_tenant_product(
        tenant_id, product_id, variant_id,
    )
    ledger = StockLedger(tenant_id=tenant_id, product_id=product_id, variant_id=variant_id)
    ledger.load([_movement_model_to_domain(row) for row in rows])
    return ledger.available


def _orders_query(tenant_id: UUID):
    return select(Order).where(Order.tenant_id == tenant_id)


async def test_agent_l2_creates_a_real_reserved_order(tenant_session, tenant) -> None:
    variant = await _seed_variant(tenant_session, tenant.id, price_cents=5000, qty=10)

    outcome = await _agent(tenant.id, tenant_session).handle_message(
        "nataka 2 black hoodie deliver to Kilimani", customer_phone=PHONE,
    )

    assert outcome.sent is True
    assert outcome.grounded is True
    assert "KES 100" in outcome.reply_text  # 2 x KES 50 = 10,000 cents

    order = await OrderService(tenant_session).get_order(tenant.id, UUID(outcome.order_id))
    assert order.status == "awaiting_payment"
    assert order.total_cents == 10_000
    assert order.customer_phone == PHONE
    assert order.delivery_area == "Kilimani"

    assert await _available(tenant_session, tenant.id, variant.product_id, variant.id) == 8


async def test_agent_l2_out_of_stock_writes_no_order(tenant_session, tenant) -> None:
    await _seed_variant(tenant_session, tenant.id, price_cents=5000, qty=1)

    outcome = await _agent(tenant.id, tenant_session).handle_message(
        "nataka 2 black hoodie", customer_phone=PHONE,
    )

    assert outcome.sent is False
    assert outcome.order_id == ""
    assert "enough stock" in outcome.reply_text.lower()

    orders = (await tenant_session.execute(_orders_query(tenant.id))).scalars().all()
    assert orders == []


async def test_agent_l2_cannot_order_another_tenants_item(tenant_session, tenant) -> None:
    # The variant lives under a *different* tenant, so this tenant's catalog
    # lookup must fail closed even though the agent quotes the SKU.
    other_tenant_id = uuid4()
    tenant_session.add(
        Tenant(
            id=other_tenant_id,
            name="Other Business",
            slug=f"other-{other_tenant_id.hex[:8]}",
            owner_phone="254700000000",
            owner_email="other@example.com",
            is_active=True,
            settings={},
        ),
    )
    await tenant_session.flush()
    await _seed_variant(tenant_session, other_tenant_id, sku=SKU)

    agent = _agent(tenant.id, tenant_session)
    result = await agent.handle_message("nataka 1 black hoodie", customer_phone=PHONE)

    assert result.sent is False
    assert result.order_id == ""
    orders = (await tenant_session.execute(_orders_query(tenant.id))).scalars().all()
    assert orders == []
