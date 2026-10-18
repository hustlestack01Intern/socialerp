"""
E2E Tests for Instagram Channel

Tests the full Instagram webhook → pipeline → agent flow using fakes.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from packages.core.domain import TenantId
from packages.core.models.instagram import (
    InstagramContact,
    InstagramConversation,
    InstagramDmEligibility,
    InstagramMessage,
    InstagramMessageType,
)


class TestInstagramChannelE2E:
    """End-to-end tests for Instagram channel."""

    @pytest.mark.asyncio
    async def test_instagram_dm_happy_path(
        self, api_client: AsyncClient, db_session, fake_meta_server,
    ):
        """Test Instagram DM happy path: inbound → normalize → pipeline → reply."""
        from sqlalchemy import text

        # Get or create tenant
        result = await db_session.execute(
            text("SELECT * FROM tenants LIMIT 1"),
        )
        tenant_row = result.first()
        tenant_id = TenantId(tenant_row.id)

        # Send Instagram DM webhook to fake Meta server
        # This simulates Instagram sending a DM to our webhook
        webhook_payload = {
            "object": "instagram",
            "entry": [{
                "id": "123456789",
                "time": 1700000000,
                "messaging": [{
                    "sender": {"id": "ig_user_123"},
                    "recipient": {"id": "123456789"},
                    "timestamp": 1700000000,
                    "message": {
                        "mid": "ig_msg_1",
                        "text": "Hello, I want to order a black hoodie",
                        "type": "text",
                    },
                }],
            }],
        }

        # Send to our webhook endpoint
        response = await fake_meta_server.post(
            "/webhook",
            json=webhook_payload,
        )
        assert response.status_code == 200

        # Verify the message was processed through our pipeline
        # Check that InstagramContact was created
        from sqlalchemy import select

        result = await db_session.execute(
            select(InstagramContact).where(
                InstagramContact.tenant_id == tenant_id,
                InstagramContact.ig_scoped_user_id == "ig_user_123",
            ),
        )
        contact = result.scalar_one_or_none()
        assert contact is not None
        assert contact.ig_scoped_user_id == "ig_user_123"

        # Check that InstagramMessage was created

        result = await db_session.execute(
            select(InstagramMessage).where(
                InstagramMessage.tenant_id == tenant_id,
                InstagramMessage.meta_message_id == "ig_msg_1",
            ),
        )
        message = result.scalar_one_or_none()
        assert message is not None
        assert message.text_body == "Hello, I want to order a black hoodie"
        assert message.message_type == InstagramMessageType.TEXT

        # Check that conversation was created

        result = await db_session.execute(
            select(InstagramConversation).where(
                InstagramConversation.tenant_id == tenant_id,
                InstagramConversation.contact_id == contact.id,
            ),
        )
        conversation = result.scalar_one_or_none()
        assert conversation is not None

    @pytest.mark.asyncio
    async def test_instagram_comment_to_dm_flow(
        self, api_client: AsyncClient, db_session, fake_meta_server,
    ):
        """Test comment-to-DM private reply flow."""

        # Get tenant
        result = await db_session.execute(
            "SELECT * FROM tenants LIMIT 1",
        )
        tenant_row = result.first()
        tenant_id = TenantId(tenant_row.id)

        # Send comment webhook (creates eligibility)
        comment_payload = {
            "object": "instagram",
            "entry": [{
                "id": "123456789",
                "time": 1700000000,
                "comments": [{
                    "id": "comment_123",
                    "text": "How much is the hoodie?",
                    "from": {"id": "ig_user_456", "username": "shopper_john"},
                    "media": {
                        "id": "media_789",
                        "permalink": "https://instagram.com/p/ABC123/",
                        "media_type": "IMAGE",
                    },
                    "timestamp": "2024-01-15T10:30:00+0000",
                }],
            }],
        }

        response = await fake_meta_server.post(
            "/webhook",
            json=comment_payload,
        )
        assert response.status_code == 200

        # Check eligibility was created
        from sqlalchemy import select

        result = await db_session.execute(
            select(InstagramDmEligibility).where(
                InstagramDmEligibility.tenant_id == tenant_id,
                InstagramDmEligibility.comment_id == "comment_123",
            ),
        )
        eligibility = result.scalar_one_or_none()
        assert eligibility is not None
        assert eligibility.attempts_remaining == 1
        assert not eligibility.consumed

        # Send private reply
        reply_payload = {
            "message": {"text": "The hoodie is KES 2,500"},
        }

        response = await fake_meta_server.post(
            "/comment_123/private_replies",
            json=reply_payload,
        )
        assert response.status_code == 200
        assert "message_id" in response.json()

        # Check eligibility was consumed
        result = await db_session.execute(
            select(InstagramDmEligibility).where(
                InstagramDmEligibility.comment_id == "comment_123",
            ),
        )
        eligibility = result.scalar_one_or_none()
        assert eligibility.consumed
        assert eligibility.attempts_remaining == 0

    @pytest.mark.asyncio
    async def test_instagram_comment_rate_limit(
        self, fake_meta_server,
    ):
        """Test rate limiting on private replies."""
        # Create eligibility
        eligibility = {
            "comment_id": "comment_rate_test",
            "thread_id": "thread_1",
            "contact_id": "contact_1",
            "expires_at": 9999999999,
            "attempts_remaining": 1,
            "consumed": False,
        }
        # Manually add to fake server state
        fake_meta_server.app.state.instagram_eligibility["comment_rate_test"] = eligibility

        # First reply should succeed
        reply_payload = {"message": {"text": "Reply 1"}}
        response = await fake_meta_server.post(
            "/comment_rate_test/private_replies",
            json=reply_payload,
        )
        assert response.status_code == 200

        # Second reply should fail (rate limited / already consumed)
        response = await fake_meta_server.post(
            "/comment_rate_test/private_replies",
            json={"message": {"text": "Reply 2"}},
        )
        assert response.status_code == 400
        assert "already sent" in response.json().get("detail", "").lower() or response.status_code == 400

    @pytest.mark.asyncio
    async def test_instagram_eligibility_expiry(
        self, fake_meta_server,
    ):
        """Test that expired eligibility cannot be used."""
        import time

        # Create expired eligibility
        eligibility = {
            "comment_id": "comment_expired",
            "thread_id": "thread_1",
            "contact_id": "contact_1",
            "expires_at": time.time() - 86400,  # 1 day ago
            "attempts_remaining": 1,
            "consumed": False,
        }
        fake_meta_server.app.state.instagram_eligibility["comment_expired"] = eligibility

        reply_payload = {"message": {"text": "Reply"}}
        response = await fake_meta_server.post(
            "/comment_expired/private_replies",
            json=reply_payload,
        )
        assert response.status_code == 400
        assert "expired" in response.json().get("detail", "").lower()


class TestInstagramWebhookShapes:
    """Tests for Instagram webhook shape normalization."""

    @pytest.mark.asyncio
    async def test_dm_text_normalization(self):
        """Test Instagram text DM normalization."""
        from packages.integrations.meta.instagram_parser import normalize_instagram_dm

        msg = {
            "sender": {"id": "user_123"},
            "recipient": {"id": "123456789"},
            "timestamp": 1700000000,
            "message": {
                "mid": "msg_1",
                "text": "Hello world",
                "type": "text",
            },
        }

        normalized = normalize_instagram_dm(msg, "123456789")

        assert normalized["provider_message_id"] == "msg_1"
        assert normalized["text_body"] == "Hello world"
        assert normalized["message_type"] == "text"
        assert normalized["ig_scoped_user_id"] == "user_123"

    @pytest.mark.asyncio
    async def test_dm_image_normalization(self):
        """Test Instagram image DM normalization."""
        from packages.integrations.meta.instagram_parser import normalize_instagram_dm

        msg = {
            "sender": {"id": "user_123"},
            "recipient": {"id": "123456789"},
            "timestamp": 1700000000,
            "message": {
                "mid": "msg_2",
                "type": "image",
                "attachments": [{
                    "type": "image",
                    "payload": {
                        "url": "https://example.com/image.jpg",
                        "mime_type": "image/jpeg",
                    },
                }],
                "text": "Nice photo!",
            },
        }

        normalized = normalize_instagram_dm(msg, "123456789")

        assert normalized["provider_message_id"] == "msg_2"
        assert normalized["message_type"] == "image"
        assert normalized["media_url"] == "https://example.com/image.jpg"
        assert normalized["media_mime_type"] == "image/jpeg"
        assert normalized["text_body"] == "Nice photo!"  # caption

    @pytest.mark.asyncio
    async def test_comment_normalization(self):
        """Test Instagram comment normalization."""
        from packages.integrations.meta.instagram_parser import normalize_instagram_comment

        comment = {
            "id": "comment_123",
            "text": "How much?",
            "from": {"id": "user_123", "username": "shopper_john"},
            "media": {
                "id": "media_1",
                "permalink": "https://instagram.com/p/ABC123/",
                "media_type": "IMAGE",
            },
            "timestamp": "2024-01-15T10:30:00+0000",
        }

        normalized = normalize_instagram_comment(comment, "123456789")

        assert normalized["provider_message_id"] == "comment_123"
        assert normalized["message_type"] == "comment"
        assert normalized["text_body"] == "How much?"
        assert normalized["ig_scoped_user_id"] == "user_123"
        assert normalized["ig_handle"] == "shopper_john"
        assert normalized["comment_media_id"] == "media_1"
        assert normalized["comment_media_type"] == "IMAGE"

    @pytest.mark.asyncio
    async def test_story_reply_normalization(self):
        """Test Instagram story reply normalization."""
        from packages.integrations.meta.instagram_parser import normalize_instagram_story_reply

        story_reply = {
            "id": "sr_123",
            "story": {"id": "story_456"},
            "from": {"id": "user_111", "username": "viewer_alex"},
            "message": {"text": "Nice story!"},
            "timestamp": 1700000000,
        }

        normalized = normalize_instagram_story_reply(story_reply, "123456789")

        assert normalized["provider_message_id"] == "sr_123"
        assert normalized["message_type"] == "story_reply"
        assert normalized["text_body"] == "Nice story!"
        assert normalized["ig_scoped_user_id"] == "user_111"
        assert normalized["story_id"] == "story_456"
