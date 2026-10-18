"""
Integration test configuration.

Sets up environment variables before test collection.
"""

import os

# Set test environment variables BEFORE any modules are imported
os.environ["DATABASE_URL"] = (
    "postgresql+asyncpg://socialerp:socialerp@localhost:5433/socialerp_test"
)
os.environ["META_APP_SECRET"] = "test_app_secret"
os.environ["META_VERIFY_TOKEN"] = "test_verify_token"
# Use a fixed UUID for consistent tenant identification
os.environ["META_WHATSAPP_PHONE_NUMBER_ID"] = "00000000-0000-0000-0000-000000000001"

# Clear any cached settings
import sys

if "packages.core.config" in sys.modules:
    from packages.core.config import get_settings

    get_settings.cache_clear()

if "packages.core.database" in sys.modules:
    import packages.core.database as db_module

    db_module._engine = None
    db_module._session_factory = None


def pytest_configure(config):
    """Configure pytest with test settings."""
    # Ensure environment variables are set
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+asyncpg://socialerp:socialerp@localhost:5433/socialerp_test",
    )
    os.environ.setdefault("META_APP_SECRET", "test_app_secret")
    os.environ.setdefault("META_VERIFY_TOKEN", "test_verify_token")
    os.environ.setdefault("META_WHATSAPP_PHONE_NUMBER_ID", "00000000-0000-0000-0000-000000000001")


def pytest_sessionstart(session):
    """Called after the Session object has been created and before test collection."""
    # Clear caches
    from packages.core.config import get_settings

    get_settings.cache_clear()

    import packages.core.database as db_module

    db_module._engine = None
    db_module._session_factory = None
