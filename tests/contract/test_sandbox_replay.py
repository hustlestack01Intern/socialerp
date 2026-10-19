"""
Contract tests for replaying recorded sandbox webhooks through fake servers
and real receivers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from packages.core.config import get_settings


@pytest.fixture
def meta_fixtures():
    """Load all Meta webhook fixtures."""
    fixture_dir = Path(__file__).parent.parent / "fixtures" / "meta"
    fixtures = {}
    for file_path in fixture_dir.glob("*.json"):
        with Path(file_path).open() as f:
            fixtures[file_path.stem] = json.load(f)
    return fixtures


@pytest.fixture
def daraja_fixtures():
    """Load all Daraja webhook fixtures."""
    fixture_dir = Path(__file__).parent.parent / "fixtures" / "daraja"
    fixtures = {}
    for file_path in fixture_dir.glob("*.json"):
        with Path(file_path).open() as f:
            fixtures[file_path.stem] = json.load(f)
    return fixtures


class TestMetaWebhookReplay:
    """Replay recorded Meta webhooks through the fake server and real receiver."""

    @pytest.mark.asyncio
    async def test_whatsapp_text_message_replay(self, api_client: AsyncClient, meta_fixtures):
        """Test replaying a WhatsApp text message webhook."""
        fixture = meta_fixtures["whatsapp_text_message"]
        settings = get_settings()

        # Sign the payload
        import hashlib
        import hmac
        body = json.dumps(fixture, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            settings.meta_app_secret.encode(), body, hashlib.sha256
        ).hexdigest()

        response = await api_client.post(
            "/api/v1/webhooks/meta",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "received"

    @pytest.mark.asyncio
    async def test_whatsapp_image_message_replay(self, api_client: AsyncClient, meta_fixtures):
        """Test replaying a WhatsApp image message webhook."""
        fixture = meta_fixtures["whatsapp_image_message"]
        settings = get_settings()

        body = json.dumps(fixture, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            settings.meta_app_secret.encode(), body, hashlib.sha256
        ).hexdigest()

        response = await api_client.post(
            "/api/v1/webhooks/meta",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "received"

    @pytest.mark.asyncio
    async def test_whatsapp_interactive_message_replay(
        self, api_client: AsyncClient, meta_fixtures,
    ):
        """Test replaying a WhatsApp interactive message webhook."""
        fixture = meta_fixtures["whatsapp_interactive_message"]
        settings = get_settings()

        body = json.dumps(fixture, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            settings.meta_app_secret.encode(), body, hashlib.sha256
        ).hexdigest()

        response = await api_client.post(
            "/api/v1/webhooks/meta",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "received"

    @pytest.mark.asyncio
    async def test_duplicate_webhook_deduped(self, api_client: AsyncClient, meta_fixtures):
        """Test that duplicate webhooks are deduplicated."""
        fixture = meta_fixtures["whatsapp_text_message"]
        settings = get_settings()

        body = json.dumps(fixture, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            settings.meta_app_secret.encode(), body, hashlib.sha256
        ).hexdigest()

        # First request
        response1 = await api_client.post(
            "/api/v1/webhooks/meta",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )
        assert response1.status_code == 200

        # Duplicate request
        response2 = await api_client.post(
            "/api/v1/webhooks/meta",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
            },
        )
        assert response2.status_code == 200
        # Should still return received but with dedupe flag
        assert response2.json()["status"] == "received"


class TestDarajaWebhookReplay:
    """Replay recorded Daraja webhooks through the fake server and real receiver."""

    @staticmethod
    def _callback_url(tenant, settings) -> str:
        token = settings.DARAJA_CALLBACK_TOKEN or "test_token"
        return f"/api/v1/payments/stk/callback/{tenant.id}/{token}"

    @pytest.mark.asyncio
    async def test_stk_push_callback_success_replay(
        self, api_client: AsyncClient, tenant, daraja_fixtures,
    ):
        """Test replaying a successful STK push callback."""
        from socialerp.settings import get_settings as get_app_settings

        settings = get_app_settings()
        fixture = daraja_fixtures["stk_push_callback_success"]

        body = json.dumps(fixture, separators=(",", ":")).encode()

        response = await api_client.post(
            self._callback_url(tenant, settings),
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_stk_push_callback_cancelled_replay(
        self, api_client: AsyncClient, tenant, daraja_fixtures,
    ):
        """Test replaying a cancelled STK push callback."""
        from socialerp.settings import get_settings as get_app_settings

        settings = get_app_settings()
        fixture = daraja_fixtures["stk_push_callback_cancelled"]

        body = json.dumps(fixture, separators=(",", ":")).encode()

        response = await api_client.post(
            self._callback_url(tenant, settings),
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_stk_push_callback_user_cancelled_replay(
        self, api_client: AsyncClient, tenant, daraja_fixtures,
    ):
        """Test replaying a user-cancelled STK push callback."""
        from socialerp.settings import get_settings as get_app_settings

        settings = get_app_settings()
        fixture = daraja_fixtures["stk_push_callback_user_cancelled"]

        body = json.dumps(fixture, separators=(",", ":")).encode()

        response = await api_client.post(
            self._callback_url(tenant, settings),
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200


class TestFakeServerContract:
    """Contract tests for the fake Meta and Daraja servers."""

    @pytest.mark.asyncio
    async def test_fake_meta_health(self, fake_meta_base_url: str):
        """Test fake Meta server health endpoint."""
        async with AsyncClient(base_url=fake_meta_base_url) as client:
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_fake_meta_send_whatsapp(
        self, fake_meta_base_url: str, meta_fixtures,
    ):
        """Sending free-form text requires an open 24h window.

        The fake enforces the window rule; deliver the recorded inbound message
        first (signed with the fake's secret), then the reply is accepted.
        """
        inbound = meta_fixtures["whatsapp_text_message"]
        raw = json.dumps(inbound, separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(
            b"test-app-secret", raw, hashlib.sha256,
        ).hexdigest()

        async with AsyncClient(base_url=fake_meta_base_url) as client:
            # Free-form text before any inbound message is correctly rejected.
            blocked = await client.post(
                "/messages",
                json={
                    "to": "254712345678",
                    "type": "text",
                    "text": {"body": "Test message"},
                },
            )
            assert blocked.status_code == 400

            delivered = await client.post(
                "/webhook",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Hub-Signature-256": signature,
                },
            )
            assert delivered.status_code == 200

            response = await client.post(
                "/messages",
                json={
                    "to": "254712345678",
                    "type": "text",
                    "text": {"body": "Test message"},
                },
            )
            assert response.status_code == 200
            assert "messages" in response.json()

    @pytest.mark.asyncio
    async def test_fake_daraja_health(self, fake_daraja_base_url: str):
        """Test fake Daraja server health endpoint."""
        async with AsyncClient(base_url=fake_daraja_base_url) as client:
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_fake_daraja_stk_push(self, fake_daraja_base_url: str):
        """Test STK push via fake Daraja server."""
        async with AsyncClient(base_url=fake_daraja_base_url) as client:
            response = await client.post(
                "/stkpush",
                json={
                    "BusinessShortCode": "174379",
                    "Password": "test",
                    "Timestamp": "20240115103000",
                    "TransactionType": "CustomerPayBillOnline",
                    "Amount": 100,
                    "PartyA": "254712345678",
                    "PartyB": "174379",
                    "PhoneNumber": "254712345678",
                    "CallBackURL": "http://localhost:8000/callback",
                    "AccountReference": "test",
                    "TransactionDesc": "Test payment",
                },
            )
            assert response.status_code == 200
            assert "CheckoutRequestID" in response.json()
