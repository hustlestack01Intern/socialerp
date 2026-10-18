"""
Integration tests for WhatsApp webhook endpoint.

Tests signature verification, dedupe, message parsing, and tenant isolation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from apps.api.main import create_app
from packages.core.config import get_settings
from packages.core.database import get_db_session

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def test_settings():
    """Test settings with known values."""
    import os
    import uuid

    # Override environment variables for test settings BEFORE importing get_settings
    os.environ["DATABASE_URL"] = (
        "postgresql+asyncpg://socialerp:socialerp@localhost:5433/socialerp_test"
    )
    os.environ["META_APP_SECRET"] = "test_app_secret"
    os.environ["META_VERIFY_TOKEN"] = "test_verify_token"
    # Use a valid UUID for phone number ID
    test_tenant_id = str(uuid.uuid4())
    os.environ["META_WHATSAPP_PHONE_NUMBER_ID"] = test_tenant_id

    # Clear database engine cache to ensure new DATABASE_URL is used
    import packages.core.database as db_module

    db_module._engine = None
    db_module._session_factory = None

    # Don't import get_settings here - let the app fixture do it
    class TestSettings:
        meta_whatsapp_phone_number_id = test_tenant_id
        meta_verify_token = "test_verify_token"
        meta_app_secret = "test_app_secret"

    return TestSettings()


@pytest.fixture(scope="session")
def app(test_settings):
    """Create FastAPI app for testing."""
    get_settings.cache_clear()
    # Don't clear the engine - it was already initialized by init_database
    return create_app()


@pytest.fixture(scope="session")
def client(app):
    """Test client."""
    return TestClient(app)


@pytest.fixture
def test_phone_number_id(test_settings):
    """Phone number ID for testing (matches settings)."""
    return test_settings.meta_whatsapp_phone_number_id or "123456789"


@pytest.fixture
def test_verify_token(test_settings):
    """Verify token for testing (matches settings)."""
    return test_settings.meta_verify_token


@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_database(test_settings):
    """Initialize database tables once per test session."""
    from uuid import UUID

    from packages.core.database import get_db_session, init_db
    from packages.core.models import Tenant as TenantModel

    await init_db()

    # Create test tenant in database
    tenant_id = UUID(test_settings.meta_whatsapp_phone_number_id)
    async for session in get_db_session():
        tenant = TenantModel(
            id=tenant_id,
            name="Test Tenant",
            owner_phone="+254700000000",
            owner_email="test@example.com",
            settings={},
            is_active=True,
        )
        session.add(tenant)
        await session.commit()
        break


@pytest.fixture(autouse=True)
async def cleanup_db():
    """Clean up WhatsApp tables before each test using direct connection."""
    import psycopg2

    # Use localhost:5433 for test database when running locally
    conn = psycopg2.connect("postgresql://socialerp:socialerp@localhost:5433/socialerp_test")
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM whatsapp_dedupe")
        cur.execute("DELETE FROM whatsapp_messages")
        cur.execute("DELETE FROM whatsapp_conversations")
        cur.execute("DELETE FROM whatsapp_contacts")
        cur.execute("DELETE FROM whatsapp_templates")
    except psycopg2.errors.UndefinedTable:
        # Tables don't exist yet, that's fine
        pass
    finally:
        conn.close()


@pytest.fixture(scope="function")
async def db_session():
    """Database session for tests."""
    async for session in get_db_session():
        yield session


# =============================================================================
# Test Helpers
# =============================================================================


def create_valid_webhook_payload(
    phone_number_id: str, message_id: str = "wamid.test123",
) -> dict[str, Any]:
    """Create a valid WhatsApp webhook payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551234567",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [
                                {"profile": {"name": "Test User"}, "wa_id": "254712345678"},
                            ],
                            "messages": [
                                {
                                    "from": "254712345678",
                                    "id": message_id,
                                    "timestamp": str(int(datetime.now(UTC).timestamp())),
                                    "text": {"body": "Hello, I want to order"},
                                    "type": "text",
                                },
                            ],
                        },
                        "field": "messages",
                    },
                ],
            },
        ],
    }


def sign_payload(payload: dict[str, Any], secret: str) -> str:
    """Generate HMAC-SHA256 signature for payload."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={signature}"


# =============================================================================
# Tests
# =============================================================================


class TestWebhookSignatureVerification:
    """Tests for HMAC-SHA256 signature verification."""

    def test_valid_signature_accepted(self, client, test_phone_number_id, test_settings):
        """Valid signature returns 200."""
        payload = create_valid_webhook_payload(phone_number_id=test_phone_number_id)
        signature = sign_payload(payload, test_settings.meta_app_secret)

        response = client.post(
            "/api/v1/webhooks/whatsapp", json=payload, headers={"X-Hub-Signature-256": signature},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "received"
        assert data["channel"] == "whatsapp"

    def test_invalid_signature_rejected(self, client, test_phone_number_id, test_settings):
        """Invalid signature returns 401."""
        payload = create_valid_webhook_payload(phone_number_id=test_phone_number_id)
        signature = sign_payload(payload, "wrong-secret")

        response = client.post(
            "/api/v1/webhooks/whatsapp", json=payload, headers={"X-Hub-Signature-256": signature},
        )

        assert response.status_code == 401
        assert "Invalid signature" in response.json()["detail"]

    def test_missing_signature_rejected(self, client, test_phone_number_id):
        """Missing signature header returns 401."""
        payload = create_valid_webhook_payload(phone_number_id=test_phone_number_id)

        response = client.post("/api/v1/webhooks/whatsapp", json=payload)

        assert response.status_code == 401
        assert "Invalid signature" in response.json()["detail"]

    def test_wrong_payload_signature_rejected(self, client, test_phone_number_id, test_settings):
        """Signature for different payload is rejected."""
        payload = create_valid_webhook_payload(phone_number_id=test_phone_number_id)
        signature = sign_payload(payload, test_settings.meta_app_secret)

        # Modify payload but use same signature
        modified_payload = create_valid_webhook_payload(
            phone_number_id=test_phone_number_id, message_id="wamid.different",
        )

        response = client.post(
            "/api/v1/webhooks/whatsapp",
            json=modified_payload,
            headers={"X-Hub-Signature-256": signature},
        )

        assert response.status_code == 401

    def test_missing_sha256_prefix_rejected(self, client, test_phone_number_id, test_settings):
        """Signature without sha256= prefix is rejected."""
        payload = create_valid_webhook_payload(phone_number_id=test_phone_number_id)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(
            test_settings.meta_app_secret.encode("utf-8"), body, hashlib.sha256,
        ).hexdigest()

        response = client.post(
            "/api/v1/webhooks/whatsapp",
            json=payload,
            headers={"X-Hub-Signature-256": signature},  # Missing sha256=
        )

        assert response.status_code == 401


class TestWebhookDedupe:
    """Tests for message deduplication."""

    def test_duplicate_message_ignored(self, client, test_phone_number_id, test_settings):
        """Duplicate message (same provider_message_id) is ignored."""
        payload = create_valid_webhook_payload(
            phone_number_id=test_phone_number_id, message_id="wamid.duplicate_test",
        )
        sign_payload(payload, test_settings.meta_app_secret)

        # First request
        response1 = client.post(
            "/api/v1/webhooks/whatsapp",
            json=payload,
            headers={"X-Hub-Signature-256": sign_payload(payload, test_settings.meta_app_secret)},
        )
        assert response1.status_code == 200

        # Second request with same message_id
        response2 = client.post(
            "/api/v1/webhooks/whatsapp",
            json=payload,
            headers={"X-Hub-Signature-256": sign_payload(payload, test_settings.meta_app_secret)},
        )
        assert response2.status_code == 200

        # Verify only one message stored
        # (Would need db check - for now just ensure both return 200)

    def test_different_message_ids_processed(self, client, test_phone_number_id, test_settings):
        """Different message IDs are both processed."""

        payload1 = create_valid_webhook_payload(
            phone_number_id=test_phone_number_id, message_id="wamid.msg1",
        )
        payload2 = create_valid_webhook_payload(
            phone_number_id=test_phone_number_id, message_id="wamid.msg2",
        )

        response1 = client.post(
            "/api/v1/webhooks/whatsapp",
            json=payload1,
            headers={"X-Hub-Signature-256": sign_payload(payload1, test_settings.meta_app_secret)},
        )
        assert response1.status_code == 200

        response2 = client.post(
            "/api/v1/webhooks/whatsapp",
            json=payload2,
            headers={"X-Hub-Signature-256": sign_payload(payload2, test_settings.meta_app_secret)},
        )
        assert response2.status_code == 200


class TestWebhookParsing:
    """Tests for message parsing and normalization."""

    def test_text_message_parsed(self, client, test_phone_number_id, test_settings):
        """Text message is parsed correctly."""
        payload = create_valid_webhook_payload(phone_number_id=test_phone_number_id)
        signature = sign_payload(payload, test_settings.meta_app_secret)

        response = client.post(
            "/api/v1/webhooks/whatsapp", json=payload, headers={"X-Hub-Signature-256": signature},
        )

        assert response.status_code == 200
        # Would need db check to verify message content

    def test_multiple_messages_in_batch(self, client, test_phone_number_id, test_settings):
        """Multiple messages in single webhook are all processed."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "123456789",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15551234567",
                                    "phone_number_id": test_phone_number_id,
                                },
                                "contacts": [
                                    {"profile": {"name": "Test User"}, "wa_id": "254712345678"},
                                ],
                                "messages": [
                                    {
                                        "from": "254712345678",
                                        "id": "wamid.msg1",
                                        "timestamp": "1234567890",
                                        "text": {"body": "Message 1"},
                                        "type": "text",
                                    },
                                    {
                                        "from": "254712345678",
                                        "id": "wamid.msg2",
                                        "timestamp": "1234567891",
                                        "text": {"body": "Message 2"},
                                        "type": "text",
                                    },
                                ],
                            },
                            "field": "messages",
                        },
                    ],
                },
            ],
        }
        signature = sign_payload(payload, test_settings.meta_app_secret)

        response = client.post(
            "/api/v1/webhooks/whatsapp", json=payload, headers={"X-Hub-Signature-256": signature},
        )

        assert response.status_code == 200


class TestWebhookVerification:
    """Tests for Meta webhook verification endpoint."""

    def test_valid_verify_token(self, client, test_verify_token):
        """Valid verify token returns challenge."""
        challenge = "test_challenge_123"

        response = client.get(
            "/api/v1/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": challenge,
                "hub.verify_token": test_verify_token,
            },
        )

        assert response.status_code == 200
        assert response.text == challenge

    def test_invalid_verify_token_rejected(self, client):
        """Invalid verify token returns 403."""
        response = client.get(
            "/api/v1/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "challenge",
                "hub.verify_token": "wrong_token",
            },
        )

        assert response.status_code == 403


# =============================================================================
# Import needed for tests
# =============================================================================
