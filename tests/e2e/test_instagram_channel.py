"""
E2E Tests for Instagram Channel

Tests the full Instagram webhook -> pipeline -> sender flow against the
canonical fake Meta server (`packages.integrations.fake_meta`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.config import get_settings
from packages.core.domain import TenantId
from packages.core.models.instagram import (
    InstagramContact,
    InstagramConversation,
    InstagramDmEligibility,
    InstagramMessage,
    InstagramMessageType,
)


def _sign(body: bytes, secret: str) -> str:
    """HMAC-SHA256 signature header value for a raw webhook body."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture
async def ig_tenant(test_engine, monkeypatch):
    """A tenant with the Instagram channel enabled and launch approved.

    The webhook resolves the tenant from `META_INSTAGRAM_ACCOUNT_ID`, which we
    point at this tenant's id, and the Instagram sender posts to
    `META_GRAPH_URL`, which we point at the fake Meta server.
    """
    from socialerp.tenancy import Tenant

    async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session, session.begin():
        tenant = Tenant(
            id=uuid4(),
            name="IG Biz",
            slug=f"ig-biz-{uuid4().hex[:8]}",
            owner_phone="254700000000",
            owner_email="ig@example.com",
            is_active=True,
            settings={"instagram_enabled": True, "launch_approved": True},
        )
        session.add(tenant)
        await session.flush()

        # The plan decides which channels are processed (M1.6, U80): Instagram
        # is a Professional feature, so this tenant needs a Professional
        # subscription or the webhook is refused.
        from packages.core.models.billing import Subscription, SubscriptionStatus

        session.add(
            Subscription(
                tenant_id=tenant.id,
                plan="professional",
                status=SubscriptionStatus.ACTIVE.value,
                period_start=datetime.now(UTC),
                period_end=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.flush()

    monkeypatch.setenv("META_INSTAGRAM_ACCOUNT_ID", str(tenant.id))
    monkeypatch.setenv("META_GRAPH_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("META_GRAPH_API_VERSION", "")
    monkeypatch.setenv("META_APP_SECRET", "test_app_secret")
    get_settings.cache_clear()
    yield tenant
    get_settings.cache_clear()


@pytest.fixture
async def ig_client(ig_tenant, fake_meta_server) -> AsyncGenerator[AsyncClient]:
    """HTTP client for the real app (signature-authenticated webhooks).

    Depends on `fake_meta_server` so the outbound Graph API calls have a live
    fake to talk to.
    """
    from httpx import ASGITransport, AsyncClient

    from apps.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _post_signed(
    client: AsyncClient, payload: dict, secret: str = "test_app_secret",
) -> object:
    """POST a webhook to the real endpoint with a valid HMAC signature."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return await client.post(
        "/api/v1/webhooks/meta",
        content=body,
        headers={"X-Hub-Signature-256": _sign(body, secret), "Content-Type": "application/json"},
    )


class TestInstagramChannelE2E:
    """End-to-end tests for Instagram channel."""

    @pytest.mark.asyncio
    async def test_instagram_dm_happy_path(self, ig_client, ig_tenant, db_session):
        """Inbound DM -> webhook -> pipeline creates contact, message, conversation."""
        tenant_id = TenantId(ig_tenant.id)

        payload = {
            "object": "instagram",
            "entry": [{
                "id": str(ig_tenant.id),
                "time": 1700000000,
                "messaging": [{
                    "sender": {"id": "ig_user_123"},
                    "recipient": {"id": str(ig_tenant.id)},
                    "timestamp": 1700000000,
                    "message": {
                        "mid": "ig_msg_1",
                        "text": "Hello, I want to order a black hoodie",
                        "type": "text",
                    },
                }],
            }],
        }

        response = await _post_signed(ig_client, payload)
        assert response.status_code == 200
        assert response.json()["channel"] == "instagram"

        result = await db_session.execute(
            select(InstagramContact).where(
                InstagramContact.tenant_id == tenant_id,
                InstagramContact.ig_scoped_user_id == "ig_user_123",
            ),
        )
        contact = result.scalar_one_or_none()
        assert contact is not None

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

        result = await db_session.execute(
            select(InstagramConversation).where(
                InstagramConversation.tenant_id == tenant_id,
                InstagramConversation.contact_id == contact.id,
            ),
        )
        assert result.scalar_one_or_none() is not None

    @pytest.mark.asyncio
    async def test_instagram_comment_creates_dm_eligibility(self, ig_client, ig_tenant, db_session):
        """A comment opens a single-use, time-limited comment-to-DM eligibility."""
        tenant_id = TenantId(ig_tenant.id)

        payload = {
            "object": "instagram",
            "entry": [{
                "id": str(ig_tenant.id),
                "time": 1700000000,
                "changes": [{
                    "field": "comments",
                    "value": {
                        "id": "comment_123",
                        "text": "How much is the hoodie?",
                        "from": {"id": "ig_user_456", "username": "shopper_john"},
                        "media": {
                            "id": "media_789",
                            "permalink": "https://instagram.com/p/ABC123/",
                            "media_type": "IMAGE",
                        },
                    },
                }],
            }],
        }

        response = await _post_signed(ig_client, payload)
        assert response.status_code == 200

        result = await db_session.execute(
            select(InstagramDmEligibility).where(
                InstagramDmEligibility.tenant_id == tenant_id,
                InstagramDmEligibility.comment_id == "comment_123",
            ),
        )
        eligibility = result.scalar_one_or_none()
        assert eligibility is not None
        assert eligibility.attempts_remaining == 1
        assert eligibility.consumed_at is None

    @pytest.mark.asyncio
    async def test_private_reply_consumes_eligibility(self, ig_client, ig_tenant, db_session):
        """The sender posts the private reply to the fake Graph API and consumes eligibility."""
        from packages.integrations.meta.instagram_sender import InstagramSender

        tenant_id = TenantId(ig_tenant.id)

        # Open eligibility via the real webhook first.
        payload = {
            "object": "instagram",
            "entry": [{
                "id": str(ig_tenant.id),
                "time": 1700000000,
                "changes": [{
                    "field": "comments",
                    "value": {
                        "id": "comment_reply_1",
                        "text": "price?",
                        "from": {"id": "ig_user_789", "username": "buyer"},
                        "media": {
                            "id": "media_1",
                            "permalink": "https://x/",
                            "media_type": "IMAGE",
                        },
                    },
                }],
            }],
        }
        response = await _post_signed(ig_client, payload)
        assert response.status_code == 200

        # Meta independently knows about the comment, so the fake must too.
        import httpx

        async with httpx.AsyncClient(base_url="http://127.0.0.1:8081") as fake:
            seed = await fake.post(
                "/__fake/state/instagram/eligibility",
                params={
                    "comment_id": "comment_reply_1",
                    "thread_id": "ig:comment_reply_1",
                    "contact_id": "ig_user_789",
                },
            )
            assert seed.status_code == 200

        sender = InstagramSender(db_session)
        try:
            message = await sender.send_private_reply(
                tenant_id=tenant_id,
                comment_id="comment_reply_1",
                body="The hoodie is KES 2,500",
            )
        finally:
            await sender.close()

        assert message is not None

        result = await db_session.execute(
            select(InstagramDmEligibility).where(
                InstagramDmEligibility.tenant_id == tenant_id,
                InstagramDmEligibility.comment_id == "comment_reply_1",
            ),
        )
        eligibility = result.scalar_one_or_none()
        assert eligibility is not None
        assert eligibility.consumed_at is not None
        assert eligibility.attempts_remaining == 0
        assert eligibility.consumed_at is not None

    @pytest.mark.asyncio
    async def test_private_reply_without_eligibility_is_rejected(self, ig_tenant, db_session):
        """Sending a private reply for an unknown comment fails safely (no Graph call)."""
        from packages.integrations.meta.instagram_sender import InstagramSender

        sender = InstagramSender(db_session)
        try:
            with pytest.raises(ValueError):
                await sender.send_private_reply(
                    tenant_id=TenantId(ig_tenant.id),
                    comment_id="comment_unknown",
                    body="hi",
                )
        finally:
            await sender.close()


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
