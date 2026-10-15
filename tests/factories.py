"""
Test factories using FactoryBoy for generating test data.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import factory
from factory import Faker, SubFactory
from factory.alchemy import SQLAlchemyModelFactory
from socialerp.audit import AuditLog
from socialerp.idempotency import IdempotencyRecord
from socialerp.outbox import Outbox
from socialerp.tenancy import Tenant


class TenantFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Tenant
        sqlalchemy_session_persistence = "commit"

    id = factory.LazyAttribute(lambda _: uuid4())
    name = Faker("company")
    slug = factory.LazyAttribute(lambda o: o.name.lower().replace(" ", "-")[:50])
    owner_phone = factory.Sequence(lambda n: f"2547{n:08d}")
    owner_email = Faker("email")
    is_active = True
    settings = factory.LazyFunction(dict)
    created_at = factory.LazyAttribute(lambda _: datetime.now(UTC))
    updated_at = factory.LazyAttribute(lambda _: datetime.now(UTC))


class AuditLogFactory(SQLAlchemyModelFactory):
    class Meta:
        model = AuditLog
        sqlalchemy_session_persistence = "commit"

    id = factory.LazyAttribute(lambda _: uuid4())
    tenant = SubFactory(TenantFactory)
    actor_type = factory.Iterator(["user", "agent", "system", "webhook"])
    actor_id = factory.LazyAttribute(lambda _: str(uuid4())[:20])
    action = factory.Iterator([
        "order.created",
        "order.status_changed",
        "payment.callback",
        "agent.reply",
        "stock.movement",
        "user.login",
    ])
    payload = factory.LazyAttribute(
        lambda _: {
            "test": "data",
            "value": Faker("random_int", min=1, max=1000).evaluate(None, None, {}),
        }
    )
    decision = factory.Iterator(["allowed", "denied", "handed_off", "error"])
    rule_ref = factory.LazyAttribute(lambda _: f"policy:{Faker('word').evaluate(None, None, {})}")
    confidence = factory.LazyAttribute(
        lambda _: round(Faker("pyfloat", min_value=0.5, max_value=1.0).evaluate(None, None, {}), 2)
    )
    correlation_id = factory.LazyAttribute(lambda _: str(uuid4()))
    created_at = factory.LazyAttribute(lambda _: datetime.utcnow())


class OutboxFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Outbox
        sqlalchemy_session_persistence = "commit"

    id = factory.LazyAttribute(lambda _: uuid4())
    tenant = SubFactory(TenantFactory)
    event_type = factory.Iterator([
        "order.created",
        "payment.confirmed",
        "message.send",
        "stock.changed",
    ])
    payload = factory.LazyAttribute(lambda _: {"order_id": str(uuid4()), "amount": 5000})
    idempotency_key = factory.LazyAttribute(lambda _: f"idemp_{uuid4().hex[:32]}")
    status = factory.Iterator(["pending", "published", "failed"])
    retry_count = 0
    last_error = None
    created_at = factory.LazyAttribute(lambda _: datetime.utcnow())
    published_at = factory.Maybe(
        factory.SelfAttribute("status"),
        yes_declaration=factory.LazyAttribute(lambda _: datetime.utcnow()),
        no_declaration=None,
    )


class IdempotencyRecordFactory(SQLAlchemyModelFactory):
    class Meta:
        model = IdempotencyRecord
        sqlalchemy_session_persistence = "commit"

    id = factory.LazyAttribute(lambda _: uuid4())
    tenant = SubFactory(TenantFactory)
    key = factory.LazyAttribute(lambda _: f"key_{uuid4().hex[:20]}")
    scope = factory.Iterator(["webhook:meta", "payment:stk_push", "api:order_create"])
    request_hash = factory.LazyAttribute(lambda _: uuid4().hex[:64])
    response = factory.LazyAttribute(lambda _: {"status": "success", "id": str(uuid4())})
    status = factory.Iterator(["pending", "completed", "failed"])
    expires_at = factory.LazyAttribute(lambda _: datetime.utcnow() + timedelta(hours=24))
    created_at = factory.LazyAttribute(lambda _: datetime.utcnow())
    completed_at = factory.Maybe(
        factory.SelfAttribute("status"),
        yes_declaration=factory.LazyAttribute(lambda _: datetime.utcnow()),
        no_declaration=None,
    )


# =============================================================================
# DOMAIN FACTORIES (for business logic tests)
# =============================================================================


class ProductFactory:
    """Factory for product domain objects (not DB models)."""

    @staticmethod
    def build(**overrides):
        from uuid import uuid4

        defaults = {
            "id": uuid4(),
            "tenant_id": uuid4(),
            "name": "Test Product",
            "description": "A test product",
            "sku": f"SKU-{uuid4().hex[:8].upper()}",
            "base_price_cents": 1000,  # 10.00 KSH
            "cost_price_cents": 600,
            "track_stock": True,
            "reorder_level": 10,
            "is_active": True,
            "created_at": datetime.utcnow(),
        }
        defaults.update(overrides)
        return type("Product", (), defaults)()

    @staticmethod
    def batch(count: int, **overrides):
        return [ProductFactory.build(**overrides) for _ in range(count)]


class VariantFactory:
    @staticmethod
    def build(product_id=None, **overrides):
        from uuid import uuid4

        defaults = {
            "id": uuid4(),
            "product_id": product_id or uuid4(),
            "name": "Default",
            "sku_suffix": "DEF",
            "price_adjustment_cents": 0,
            "stock_quantity": 100,
            "attributes": {"color": "black", "size": "M"},
            "is_active": True,
        }
        defaults.update(overrides)
        return type("Variant", (), defaults)()


class OrderFactory:
    @staticmethod
    def build(tenant_id=None, customer_id=None, **overrides):
        from uuid import uuid4

        from socialerp.orders import OrderStatus  # Will be created later

        defaults = {
            "id": uuid4(),
            "tenant_id": tenant_id or uuid4(),
            "customer_id": customer_id or uuid4(),
            "status": OrderStatus.DRAFT,
            "items": [],
            "subtotal_cents": 0,
            "tax_cents": 0,
            "delivery_fee_cents": 0,
            "discount_cents": 0,
            "total_cents": 0,
            "payment_method": "stk_push",
            "delivery_address": "Kilimani, Nairobi",
            "notes": "",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        defaults.update(overrides)
        return type("Order", (), defaults)()


class CustomerFactory:
    @staticmethod
    def build(tenant_id=None, **overrides):
        from uuid import uuid4

        defaults = {
            "id": uuid4(),
            "tenant_id": tenant_id or uuid4(),
            "phone": f"2547{Faker('random_number', digits=8).evaluate(None, None, {})}",
            "name": Faker("name").evaluate(None, None, {}),
            "email": Faker("email").evaluate(None, None, {}),
            "channel": "whatsapp",
            "channel_id": None,
            "tags": [],
            "consent_marketing": False,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        defaults.update(overrides)
        return type("Customer", (), defaults)()


# =============================================================================
# FIXTURE HELPERS
# =============================================================================


def create_test_tenant(session, **kwargs) -> Tenant:
    """Create and persist a test tenant."""
    tenant = TenantFactory.build(**kwargs)
    session.add(tenant)
    session.flush()
    return tenant


async def create_test_tenant_async(session, **kwargs) -> Tenant:
    """Async version."""
    tenant = TenantFactory.build(**kwargs)
    session.add(tenant)
    await session.flush()
    return tenant
