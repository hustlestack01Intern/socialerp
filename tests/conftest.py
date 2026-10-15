"""
Pytest configuration and shared fixtures.
"""

import os
from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Set test environment before importing app modules
os.environ["ENVIRONMENT"] = "development"
os.environ["DEV_DISABLE_SIGNATURE_VERIFICATION"] = "true"
os.environ["DEV_AUTO_CREATE_TENANT"] = "true"
os.environ["POSTGRES_USER"] = "socialerp"
os.environ["POSTGRES_PASSWORD"] = "socialerp"
os.environ["POSTGRES_DB"] = "socialerp_test"
os.environ["POSTGRES_HOST"] = "localhost"
os.environ["POSTGRES_PORT"] = "5432"
os.environ["REDIS_URL"] = "redis://localhost:6379/1"
os.environ["SECRET_KEY"] = "test-secret-key-minimum-32-characters-long"
os.environ["ENCRYPTION_KEY"] = "test-encryption-key-32-bytes-long!!"


from socialerp.database import Base
from socialerp.tenancy import Tenant

# =============================================================================
# DATABASE FIXTURES
# =============================================================================
#
# NOTE: no custom `event_loop` fixture. pytest-asyncio's `asyncio_mode = auto`
# manages a function-scoped loop per async test, which matches the function
# scope of the engine/session fixtures. The previous session-scoped override
# caused "Future attached to a different loop" flakes when a function-scoped
# engine was reused across loops.


TEST_DB_URL_SYNC = "postgresql+psycopg2://socialerp:socialerp@localhost:5432/socialerp_test"
TEST_DB_URL_ASYNC = "postgresql+asyncpg://socialerp:socialerp@localhost:5432/socialerp_test"
RLS_DB_URL_ASYNC = "postgresql+asyncpg://socialerp_app:socialerp_app@localhost:5432/socialerp_test"

_RLS_FUNCTION_SQL = """
    CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid AS $$
        SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid;
    $$ LANGUAGE sql STABLE;
"""

# Tables that carry a tenant key and must enforce row-level security.
_RLS_TENANT_TABLES = (
    "tenants",
    "audit_log",
    "outbox",
    "idempotency_keys",
    "payments",
    "customers",
    "orders",
    "order_items",
    "reservations",
    "stock_movements",
    "products",
    "product_variants",
    "categories",
)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Build a fresh test schema once per session, as the DB superuser.

    DDL is transactional, and tenant isolation is only enforced for NON-owner
    roles, so we:
      1. reset the public schema and create every table from the canonical
         metadata (superuser connection, bypasses RLS for setup);
      2. install the RLS helper function and policies;
      3. (re)create the non-owner `socialerp_app` login role the isolation
         tests connect as.

    CREATE ROLE cannot run inside a transaction block, so it uses AUTOCOMMIT.
    """
    from sqlalchemy import create_engine
    from sqlalchemy import text as sa_text

    engine = create_engine(TEST_DB_URL_SYNC, future=True)

    with engine.begin() as conn:
        conn.execute(sa_text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(sa_text("CREATE SCHEMA public"))
        Base.metadata.create_all(conn)
        conn.execute(sa_text(_RLS_FUNCTION_SQL))
        for table in _RLS_TENANT_TABLES:
            conn.execute(sa_text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            if table == "tenants":
                conn.execute(
                    sa_text(
                        "CREATE POLICY tenant_isolation_policy ON tenants "
                        "USING (id = current_tenant_id()) "
                        "WITH CHECK (id = current_tenant_id())"
                    )
                )
            else:
                conn.execute(
                    sa_text(
                        f"CREATE POLICY tenant_isolation_policy ON {table} "
                        "USING (tenant_id = current_tenant_id()) "
                        "WITH CHECK (tenant_id = current_tenant_id())"
                    )
                )

    with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(
            sa_text(
                "DO $$ BEGIN "
                "CREATE ROLE socialerp_app WITH LOGIN PASSWORD 'socialerp_app'; "
                "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
            )
        )
        conn.execute(sa_text("ALTER ROLE socialerp_app WITH LOGIN PASSWORD 'socialerp_app'"))
        conn.execute(sa_text("GRANT USAGE ON SCHEMA public TO socialerp_app"))
        conn.execute(
            sa_text(
                "GRANT SELECT, INSERT, UPDATE, DELETE "
                "ON ALL TABLES IN SCHEMA public TO socialerp_app"
            )
        )

    engine.dispose()
    yield


@pytest.fixture(scope="function")
async def test_engine(_schema):
    """A function-scoped async engine bound to the current event loop.

    The schema is built once per session by `_schema`; this fixture never runs
    DDL, so it is cheap and safe across event loops.
    """
    from socialerp.database import reset_db

    reset_db()
    engine = create_async_engine(TEST_DB_URL_ASYNC, echo=False, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
async def rls_engine(_schema):
    """An engine connected as the non-owner `socialerp_app` role.

    Queries through this engine are subject to Row-Level Security, unlike the
    superuser connection used for test setup. Used to prove tenant isolation.
    """
    engine = create_async_engine(RLS_DB_URL_ASYNC, echo=False, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="function")
def rls_session_factory(rls_engine):
    """Session factory bound to the non-owner RLS role."""
    return async_sessionmaker(rls_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="function")
def session_factory(test_engine):
    """Create a session factory for each test."""
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(scope="function", autouse=True)
async def _reset_tables(test_engine):
    """Truncate all data tables before each test so runs are hermetic.

    The suite previously leaked rows between tests (results changed run to run).
    TRUNCATE ... CASCADE as the owner bypasses RLS and resets every table.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session, session.begin():
        result = await session.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        names = [row[0] for row in result]
        if names:
            quoted = ", ".join(f'"{n}"' for n in names)
            await session.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="function")
async def db_session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Create a new database session for each test."""
    async with session_factory() as session, session.begin():
        yield session
        # Rollback after each test
        await session.rollback()


@pytest.fixture(scope="function")
async def tenant(test_engine) -> Tenant:
    """Create a test tenant and persist it."""
    from socialerp.tenancy import Tenant
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session, session.begin():
        tenant = Tenant(
            id=uuid4(),
            name="Test Business",
            slug=f"test-biz-{uuid4().hex[:8]}",
            owner_phone="254712345678",
            owner_email="test@example.com",
            is_active=True,
            settings={},
        )
        session.add(tenant)
        await session.flush()
        return tenant


@pytest.fixture(scope="function")
async def tenant_session(db_session, tenant):
    """Database session with tenant context set."""
    from socialerp.tenancy import reset_current_tenant_id, set_current_tenant_id

    token = set_current_tenant_id(tenant.id)
    await db_session.execute(
        text("SELECT set_config('app.current_tenant_id', :tid, true)"), {"tid": str(tenant.id)}
    )

    yield db_session

    reset_current_tenant_id(token)


# =============================================================================
# REDIS FIXTURE
# =============================================================================


@pytest.fixture(scope="session")
async def redis_client():
    """Redis client for tests."""
    import redis.asyncio as redis

    client = redis.from_url("redis://localhost:6379/1", decode_responses=True)
    yield client
    await client.flushdb()
    await client.close()


# =============================================================================
# HTTP CLIENT FIXTURES
# =============================================================================


@pytest.fixture(scope="function")
async def api_client(tenant):
    """Async HTTP client for API tests."""
    from httpx import ASGITransport, AsyncClient

    from apps.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Set tenant header for multi-tenancy
        client.headers["X-Tenant-ID"] = str(tenant.id)
        yield client


# =============================================================================
# MOCK/FAKE SERVICE FIXTURES
# =============================================================================


@pytest.fixture
def fake_meta_base_url():
    """Base URL for fake Meta server."""
    return os.getenv("DEV_FAKE_META_URL", "http://localhost:8080")


@pytest.fixture
def fake_daraja_base_url():
    """Base URL for fake Daraja server."""
    return os.getenv("DEV_FAKE_DARAJA_URL", "http://localhost:8081")


@pytest.fixture
def mock_meta_webhook_secret():
    """Mock Meta webhook secret for testing."""
    return "test_webhook_verify_token"


@pytest.fixture
def mock_daraja_credentials():
    """Mock Daraja credentials for testing."""
    return {
        "consumer_key": "test_consumer_key",
        "consumer_secret": "test_consumer_secret",
        "passkey": "test_passkey",
        "shortcode": "174379",
    }


# =============================================================================
# UTILITY FIXTURES
# =============================================================================


@pytest.fixture
def sample_whatsapp_message():
    """Sample WhatsApp inbound message payload."""
    return {
        "messaging_product": "whatsapp",
        "contacts": [{"profile": {"name": "John Doe"}, "wa_id": "254712345678"}],
        "messages": [
            {
                "from": "254712345678",
                "id": "wamid.test123",
                "timestamp": "1700000000",
                "text": {"body": "Hello, I want to order 2 black hoodies size M"},
                "type": "text",
            }
        ],
    }


@pytest.fixture
def sample_instagram_message():
    """Sample Instagram inbound message payload."""
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "123456789",
                "time": 1700000000,
                "messaging": [
                    {
                        "sender": {"id": "ig_user_123"},
                        "recipient": {"id": "ig_page_456"},
                        "timestamp": 1700000000,
                        "message": {
                            "mid": "ig_msg_123",
                            "text": "Hi, how much is the red dress?",
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def sample_stk_push_callback_success():
    """Sample successful STK Push callback."""
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "mreq_test123",
                "CheckoutRequestID": "ws_CO_test123",
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": 5000},
                        {"Name": "MpesaReceiptNumber", "Value": "MPE123456789"},
                        {"Name": "Balance", "Value": 0},
                        {"Name": "TransactionDate", "Value": 20240115103000},
                        {"Name": "PhoneNumber", "Value": 254712345678},
                    ]
                },
            }
        }
    }


@pytest.fixture
def sample_stk_push_callback_failed():
    """Sample failed STK Push callback."""
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "mreq_test123",
                "CheckoutRequestID": "ws_CO_test123",
                "ResultCode": 1032,
                "ResultDesc": "Request cancelled by user",
                "CallbackMetadata": {"Item": []},
            }
        }
    }


# =============================================================================
# PYTEST CONFIGURATION
# =============================================================================


def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "contract: Contract tests")
    config.addinivalue_line("markers", "property: Property-based tests")
    config.addinivalue_line("markers", "slow: Slow tests")
    config.addinivalue_line("markers", "eval: Evaluation tests")


# =============================================================================
# ASYNC HELPERS
# =============================================================================


async def async_iter(iterable):
    """Helper to iterate async generator in tests."""
    result = []
    async for item in iterable:
        result.append(item)
    return result
