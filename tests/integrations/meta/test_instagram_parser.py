"""
Tests for Instagram webhook parser and normalization.
"""

from __future__ import annotations

from packages.integrations.meta.instagram_parser import (
    detect_meta_channel,
    normalize_instagram_comment,
    normalize_instagram_dm,
    normalize_instagram_story_reply,
    parse_instagram_webhook,
)


class TestInstagramChannelDetection:
    def test_detect_whatsapp_channel(self):
        payload = {"object": "whatsapp_business_account", "entry": []}
        assert detect_meta_channel(payload) == "whatsapp"

    def test_detect_instagram_channel(self):
        payload = {"object": "instagram", "entry": []}
        assert detect_meta_channel(payload) == "instagram"

    def test_detect_unknown_channel_raises(self):
        payload = {"object": "unknown", "entry": []}
        try:
            detect_meta_channel(payload)
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "Unknown Meta object type" in str(e)


class TestParseInstagramWebhook:
    def test_parse_dm_only(self):
        payload = {
            "object": "instagram",
            "entry": [{
                "id": "123456789",
                "time": 1700000000,
                "messaging": [{
                    "sender": {"id": "user_123"},
                    "recipient": {"id": "123456789"},
                    "timestamp": 1700000000,
                    "message": {"mid": "msg_1", "text": "Hello"},
                }],
            }],
        }
        result = parse_instagram_webhook(payload)
        assert len(result) == 1
        assert result[0]["type"] == "dm"
        assert result[0]["message"]["message"]["text"] == "Hello"

    def test_parse_comment_only(self):
        payload = {
            "object": "instagram",
            "entry": [{
                "id": "123456789",
                "time": 1700000000,
                "comments": [
                    {
                        "id": "comment_1",
                        "text": "Great product!",
                        "from": {"id": "user_123", "username": "customer_handle"},
                        "media": {
                            "id": "media_1",
                            "permalink": "https://instagram.com/p/...",
                            "media_type": "IMAGE",
                        },
                        "timestamp": "2024-01-01T12:00:00+0000",
                    },
                ],
            }],
        }
        result = parse_instagram_webhook(payload)
        assert len(result) == 1
        assert result[0]["type"] == "comment"
        assert result[0]["comment"]["text"] == "Great product!"

    def test_parse_story_reply_only(self):
        payload = {
            "object": "instagram",
            "entry": [{
                "id": "123456789",
                "time": 1700000000,
                "story_replies": [{
                    "id": "story_reply_1",
                    "story": {"id": "story_1"},
                    "from": {"id": "user_123", "username": "customer_handle"},
                    "message": {"text": "Nice story!"},
                    "timestamp": 1700000000,
                }],
            }],
        }
        result = parse_instagram_webhook(payload)
        assert len(result) == 1
        assert result[0]["type"] == "story_reply"
        assert result[0]["story_reply"]["message"]["text"] == "Nice story!"

    def test_parse_mixed_entry(self):
        payload = {
            "object": "instagram",
            "entry": [{
                "id": "123456789",
                "time": 1700000000,
                "messaging": [
                    {"sender": {"id": "user_1"}, "message": {"mid": "m1", "text": "DM"}},
                ],
                "comments": [
                    {
                        "id": "c1",
                        "text": "Comment",
                        "from": {"id": "user_2", "username": "h2"},
                        "media": {"id": "m1"},
                        "timestamp": "2024-01-01T12:00:00+0000",
                    },
                ],
                "story_replies": [
                    {
                        "id": "sr1",
                        "story": {"id": "s1"},
                        "from": {"id": "user_3", "username": "h3"},
                        "message": {"text": "Reply"},
                        "timestamp": 1700000000,
                    },
                ],
            }],
        }
        result = parse_instagram_webhook(payload)
        assert len(result) == 3
        types = [r["type"] for r in result]
        assert "dm" in types
        assert "comment" in types
        assert "story_reply" in types


class TestNormalizeInstagramDM:
    def test_normalize_text_dm(self):
        msg = {
            "sender": {"id": "user_123"},
            "recipient": {"id": "123456789"},
            "timestamp": 1700000000,
            "message": {
                "mid": "msg_1",
                "text": "Hello, I want to order",
                "type": "text",
            },
        }
        normalized = normalize_instagram_dm(msg, "123456789")

        assert normalized["provider_message_id"] == "msg_1"
        assert normalized["direction"] == "inbound"
        assert normalized["message_type"] == "text"
        assert normalized["text_body"] == "Hello, I want to order"
        assert normalized["ig_scoped_user_id"] == "user_123"
        assert normalized["ig_professional_account_id"] == "123456789"
        assert normalized["is_echo"] is False

    def test_normalize_image_dm(self):
        msg = {
            "sender": {"id": "user_123"},
            "recipient": {"id": "123456789"},
            "timestamp": 1700000000,
            "message": {
                "mid": "msg_2",
                "type": "image",
                "attachments": [
                    {
                        "type": "image",
                        "payload": {
                            "url": "https://example.com/image.jpg",
                            "mime_type": "image/jpeg",
                        },
                    },
                ],
                "text": "Nice shirt!",
            },
        }
        normalized = normalize_instagram_dm(msg, "123456789")

        assert normalized["message_type"] == "image"
        assert normalized["media_url"] == "https://example.com/image.jpg"
        assert normalized["media_mime_type"] == "image/jpeg"
        assert normalized["text_body"] == "Nice shirt!"  # caption

    def test_normalize_quick_reply(self):
        msg = {
            "sender": {"id": "user_123"},
            "recipient": {"id": "123456789"},
            "timestamp": 1700000000,
            "message": {
                "mid": "msg_3",
                "type": "text",
                "text": "Yes, please",
                "quick_reply": {"payload": "ORDER_CONFIRMED"},
            },
        }
        normalized = normalize_instagram_dm(msg, "123456789")

        assert normalized["message_type"] == "quick_reply"
        assert normalized["quick_reply_payload"] == "ORDER_CONFIRMED"
        assert normalized["text_body"] == "Yes, please"

    def test_normalize_referral(self):
        msg = {
            "sender": {"id": "user_123"},
            "recipient": {"id": "123456789"},
            "timestamp": 1700000000,
            "message": {
                "mid": "msg_4",
                "text": "Hi from ad",
                "referral": {
                    "source": "ads",
                    "type": "feed",
                    "ref": "campaign_123",
                    "ad_id": "ad_456",
                    "campaign_id": "camp_789",
                },
            },
        }
        normalized = normalize_instagram_dm(msg, "123456789")

        assert normalized["referral_data"] is not None
        assert normalized["referral_data"]["source"] == "ads"
        assert normalized["referral_data"]["ad_id"] == "ad_456"

    def test_normalize_context(self):
        msg = {
            "sender": {"id": "user_123"},
            "recipient": {"id": "123456789"},
            "timestamp": 1700000000,
            "message": {
                "mid": "msg_5",
                "text": "Replying to your question",
                "context": {"id": "original_msg_123"},
            },
        }
        normalized = normalize_instagram_dm(msg, "123456789")

        assert normalized["context_message_id"] == "original_msg_123"
        assert normalized["referenced_message_id"] == "original_msg_123"

    def test_normalize_echo(self):
        msg = {
            "sender": {"id": "123456789"},
            "recipient": {"id": "user_123"},
            "timestamp": 1700000000,
            "message": {
                "mid": "msg_6",
                "text": "Business reply",
                "is_echo": True,
            },
        }
        normalized = normalize_instagram_dm(msg, "123456789")

        assert normalized["is_echo"] is True
        assert normalized["ig_scoped_user_id"] == "123456789"  # sender is the business


class TestNormalizeInstagramComment:
    def test_normalize_post_comment(self):
        comment = {
            "id": "comment_123",
            "text": "How much is this?",
            "from": {"id": "user_456", "username": "shopper_john"},
            "media": {
                "id": "media_789",
                "permalink": "https://instagram.com/p/ABC123/",
                "media_type": "IMAGE",
                "owner": {"id": "123456789"},
            },
            "timestamp": "2024-01-15T10:30:00+0000",
            "like_count": 5,
            "parent_id": None,
            "is_hidden": False,
        }
        normalized = normalize_instagram_comment(comment, "123456789")

        assert normalized["provider_message_id"] == "comment_123"
        assert normalized["message_type"] == "comment"
        assert normalized["text_body"] == "How much is this?"
        assert normalized["ig_scoped_user_id"] == "user_456"
        assert normalized["ig_handle"] == "shopper_john"
        assert normalized["comment_media_id"] == "media_789"
        assert normalized["comment_media_type"] == "IMAGE"
        assert normalized["comment_media_permalink"] == "https://instagram.com/p/ABC123/"

    def test_normalize_reel_comment(self):
        comment = {
            "id": "comment_reel_1",
            "text": "Love this!",
            "from": {"id": "user_789", "username": "fan_jane"},
            "media": {
                "id": "reel_123",
                "permalink": "https://instagram.com/reel/XYZ/",
                "media_type": "REELS",
                "owner": {"id": "123456789"},
            },
            "timestamp": "2024-01-16T14:00:00+0000",
            "like_count": 100,
            "parent_id": None,
            "is_hidden": False,
        }
        normalized = normalize_instagram_comment(comment, "123456789")

        assert normalized["comment_media_type"] == "REELS"

    def test_normalize_ad_comment(self):
        comment = {
            "id": "comment_ad_1",
            "text": "Interested",
            "from": {"id": "user_999", "username": "buyer_bob"},
            "media": {
                "id": "ad_creative_123",
                "permalink": "https://instagram.com/p/AD123/",
                "media_type": "IMAGE",
                "owner": {"id": "123456789"},
            },
            "timestamp": "2024-01-17T09:00:00+0000",
            "like_count": 2,
            "parent_id": None,
            "is_hidden": False,
        }
        normalized = normalize_instagram_comment(comment, "123456789")

        assert normalized["comment_media_id"] == "ad_creative_123"


class TestNormalizeInstagramStoryReply:
    def test_normalize_story_reply_text(self):
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
        assert normalized["ig_handle"] == "viewer_alex"
        assert normalized["story_id"] == "story_456"

    def test_normalize_story_reply_with_media(self):
        story_reply = {
            "id": "sr_456",
            "story": {"id": "story_789"},
            "from": {"id": "user_222", "username": "viewer_sam"},
            "message": {
                "text": "Cool!",
                "attachments": [
                    {
                        "type": "image",
                        "payload": {
                            "url": "https://example.com/story_reply.jpg",
                            "mime_type": "image/jpeg",
                        },
                    },
                ],
            },
            "timestamp": 1700000001,
        }
        normalized = normalize_instagram_story_reply(story_reply, "123456789")

        assert normalized["media_url"] == "https://example.com/story_reply.jpg"
        assert normalized["media_mime_type"] == "image/jpeg"


class TestDetermineMessageType:
    def test_quick_reply_priority(self):
        from packages.integrations.meta.instagram_parser import _determine_message_type
        # Quick reply should take priority over text type
        msg = {"type": "text", "quick_reply": {"payload": "TEST"}}
        assert _determine_message_type(msg) == "quick_reply"

    def test_standard_types(self):
        from packages.integrations.meta.instagram_parser import _determine_message_type
        assert _determine_message_type({"type": "text"}) == "text"
        assert _determine_message_type({"type": "image"}) == "image"
        assert _determine_message_type({"type": "video"}) == "video"
        assert _determine_message_type({"type": "audio"}) == "audio"
        assert _determine_message_type({"type": "file"}) == "file"
        assert _determine_message_type({"type": "sticker"}) == "sticker"
        assert _determine_message_type({"type": "location"}) == "location"
        assert _determine_message_type({"type": "contacts"}) == "contacts"
        assert _determine_message_type({"type": "unknown"}) == "unknown"
