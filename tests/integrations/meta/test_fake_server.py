"""
Integration tests for Fake Meta server.
"""

import hashlib
import hmac
import json

import httpx
import pytest


class TestFakeMetaServer:
    """Tests for Fake Meta server."""

    @pytest.fixture
    def base_url(self):
        return "http://localhost:8080"

    @pytest.fixture
    def app_secret(self):
        return "test_app_secret"

    @pytest.fixture
    def verify_token(self):
        return "test_verify_token"

    @pytest.fixture
    def tenant_phone_id(self):
        return "123456789"

    def sign_payload(self, payload: dict, secret: str) -> str:
        """Generate X-Hub-Signature-256."""
        body = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={signature}"

    async def open_whatsapp_window(
        self, client, base_url: str, phone_number_id: str, app_secret: str, customer: str,
    ) -> None:
        """Send an inbound WhatsApp message so the 24h window opens."""
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
                                    "phone_number_id": phone_number_id,
                                },
                                "contacts": [{"profile": {"name": "Customer"}, "wa_id": customer}],
                                "messages": [
                                    {
                                        "from": customer,
                                        "id": "wamid.inbound.open",
                                        "timestamp": "1700000000",
                                        "text": {"body": "Hi"},
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
        response = await client.post(
            f"{base_url}/webhook",
            json=payload,
            headers={"X-Hub-Signature-256": self.sign_payload(payload, app_secret)},
        )
        assert response.status_code == 200, response.text

    async def open_instagram_window(
        self, client, base_url: str, page_id: str, app_secret: str, customer: str,
    ) -> None:
        """Send an inbound Instagram message so the 24h reply window opens."""
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": page_id,
                    "time": 1700000000,
                    "messaging": [
                        {
                            "sender": {"id": customer},
                            "recipient": {"id": page_id},
                            "timestamp": 1700000000,
                            "message": {"mid": "ig_msg_inbound", "text": "Hi"},
                        },
                    ],
                },
            ],
        }
        response = await client.post(
            f"{base_url}/webhook",
            json=payload,
            headers={"X-Hub-Signature-256": self.sign_payload(payload, app_secret)},
        )
        assert response.status_code == 200, response.text

    @pytest.mark.asyncio
    async def test_health_check(self, base_url):
        """Health endpoint returns 200."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/health")
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_webhook_verification_success(self, base_url, verify_token):
        """Webhook verification returns challenge."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/webhook",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": verify_token,
                    "hub.challenge": "test_challenge_123",
                },
            )
            assert response.status_code == 200
            assert response.text == "test_challenge_123"

    @pytest.mark.asyncio
    async def test_webhook_verification_failure(self, base_url):
        """Webhook verification fails with wrong token."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/webhook",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong_token",
                    "hub.challenge": "test_challenge_123",
                },
            )
            assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_receive_whatsapp_webhook(self, base_url, app_secret, tenant_phone_id):
        """Receive and store WhatsApp webhook."""
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
                                    "display_phone_number": "254712345678",
                                    "phone_number_id": tenant_phone_id,
                                },
                                "messages": [
                                    {
                                        "from": "254798765432",
                                        "id": "wamid.test123",
                                        "timestamp": "1700000000",
                                        "text": {"body": "Hello"},
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

        signature = self.sign_payload(payload, app_secret)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base_url}/webhook", json=payload, headers={"X-Hub-Signature-256": signature},
            )
            assert response.status_code == 200
            assert response.json()["status"] == "received"

    @pytest.mark.asyncio
    async def test_receive_webhook_invalid_signature(self, base_url):
        """Reject webhook with invalid signature."""
        payload = {"object": "test", "entry": []}
        signature = "sha256=invalid"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{base_url}/webhook", json=payload, headers={"X-Hub-Signature-256": signature},
            )
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_send_whatsapp_message(self, base_url, tenant_phone_id, app_secret):
        """Send WhatsApp message via Cloud API (after customer opens 24h window)."""
        message = {
            "messaging_product": "whatsapp",
            "to": "254798765432",
            "type": "text",
            "text": {"body": "Hello from business!"},
        }

        async with httpx.AsyncClient() as client:
            # Customer messages first, opening the 24h service window
            await self.open_whatsapp_window(
                client, base_url, tenant_phone_id, app_secret, "254798765432",
            )

            response = await client.post(
                f"{base_url}/v20.0/{tenant_phone_id}/messages",
                json=message,
                headers={"Authorization": f"Bearer {app_secret}"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "messages" in data
            assert len(data["messages"]) == 1
            assert "id" in data["messages"][0]

    @pytest.mark.asyncio
    async def test_rate_limiting(self, base_url, tenant_phone_id, app_secret):
        """Rate limiting enforced at ~20 msg/sec."""
        message = {
            "messaging_product": "whatsapp",
            "to": "254798765432",
            "type": "text",
            "text": {"body": "Test"},
        }

        async with httpx.AsyncClient() as client:
            # Open the window so 400s don't mask rate limiting
            await self.open_whatsapp_window(
                client, base_url, tenant_phone_id, app_secret, "254798765432",
            )

            # Ensure a fresh 1-second rate-limit window
            import asyncio

            await asyncio.sleep(1.05)

            # Send 25 messages quickly
            responses = []
            for _ in range(25):
                response = await client.post(
                    f"{base_url}/v20.0/{tenant_phone_id}/messages",
                    json=message,
                    headers={"Authorization": f"Bearer {app_secret}"},
                )
                responses.append(response.status_code)

            # First 20 should succeed, rest should be 429
            assert responses[:20] == [200] * 20
            assert all(r == 429 for r in responses[20:])

    @pytest.mark.asyncio
    async def test_inspect_messages(self, base_url, tenant_phone_id):
        """Inspect stored messages."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/inspect/messages", params={"tenant_id": tenant_phone_id},
            )
            assert response.status_code == 200
            data = response.json()
            assert "messages" in data

    @pytest.mark.asyncio
    async def test_inspect_conversations(self, base_url, tenant_phone_id):
        """Inspect conversations and window status."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{base_url}/inspect/conversations", params={"tenant_id": tenant_phone_id},
            )
            assert response.status_code == 200
            data = response.json()
            assert "conversations" in data
            for conv in data["conversations"]:
                assert "window_open" in conv

    @pytest.mark.asyncio
    async def test_reset_data(self, base_url, tenant_phone_id):
        """Reset test data."""
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{base_url}/inspect/reset", params={"tenant_id": tenant_phone_id},
            )
            assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_instagram_send_message(self, base_url, app_secret):
        """Send Instagram message (after the user opens the 24h window)."""
        message = {"recipient": {"id": "ig_user_123"}, "message": {"text": "Hello from business!"}}

        async with httpx.AsyncClient() as client:
            # User messages first, opening the 24h reply window
            await self.open_instagram_window(
                client, base_url, "ig_page_456", app_secret, "ig_user_123",
            )

            response = await client.post(
                f"{base_url}/v20.0/ig_page_456/messages",
                json=message,
                headers={"Authorization": f"Bearer {app_secret}"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "message_id" in data


class TestFakeMetaFixtures:
    """Tests for fixture replay functionality."""

    @pytest.fixture
    def base_url(self):
        return "http://localhost:8080"

    @pytest.mark.asyncio
    async def test_list_fixtures(self, base_url):
        """List available fixtures."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{base_url}/fixtures")
            assert response.status_code == 200
            assert "fixtures" in response.json()
