"""
Instagram Webhook Parser & Normalizer

Parses Instagram Graph API webhook payloads into normalized message events
compatible with the shared pipeline (MessageReceived family).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def parse_instagram_webhook(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse Instagram webhook payload into normalized message events.

    Handles:
    - messaging: Direct messages (DMs)
    - comments: Post, reel, ad comments (eligible for private reply)
    - story_replies: Story replies (become DMs after business response)
    """
    messages = []

    for entry in payload.get("entry", []):
        # Process DMs
        for msg in entry.get("messaging", []):
            messages.append({
                "type": "dm",
                "message": msg,
                "ig_professional_account_id": entry.get("id"),
            })

        # Process comments (posts, reels, ads)
        for comment in entry.get("comments", []):
            messages.append({
                "type": "comment",
                "comment": comment,
                "ig_professional_account_id": entry.get("id"),
            })

        # Process story replies
        for story_reply in entry.get("story_replies", []):
            messages.append({
                "type": "story_reply",
                "story_reply": story_reply,
                "ig_professional_account_id": entry.get("id"),
            })

    return messages


def normalize_instagram_dm(msg: dict[str, Any], ig_professional_account_id: str) -> dict[str, Any]:
    """Normalize Instagram DM to internal format."""
    message = msg.get("message", {})
    message_type = _determine_message_type(message)

    normalized = {
        "provider_message_id": message.get("mid"),
        "meta_timestamp": datetime.fromtimestamp(message.get("timestamp", 0), tz=UTC)
        if message.get("timestamp")
        else None,
        "direction": "inbound",
        "message_type": message_type,
        "status": "sent",
        "text_body": None,
        "media_url": None,
        "media_mime_type": None,
        "media_sha256": None,
        "media_caption": None,
        "interactive_data": None,
        "quick_reply_payload": None,
        "referral_data": None,
        "context_message_id": None,
        "referenced_message_id": None,
        "ig_scoped_user_id": msg.get("sender", {}).get("id"),
        "ig_professional_account_id": ig_professional_account_id,
        "is_echo": message.get("is_echo", False),
    }

    # Extract content based on type
    if message_type == "text":
        normalized["text_body"] = message.get("text")
    elif message_type in ("image", "video", "audio", "file"):
        attachment = message.get("attachments", [{}])[0] if message.get("attachments") else {}
        normalized["media_url"] = attachment.get("payload", {}).get("url")
        normalized["media_mime_type"] = attachment.get("payload", {}).get("mime_type")
        normalized["media_caption"] = message.get("text")  # Instagram uses text field for caption
        normalized["text_body"] = message.get("text")  # Also set text_body for search/display
    elif message_type == "sticker":
        # Stickers are attachments with type=sticker
        attachment = message.get("attachments", [{}])[0] if message.get("attachments") else {}
        normalized["media_url"] = attachment.get("payload", {}).get("url")
        normalized["media_mime_type"] = "image/webp"
    elif message_type == "quick_reply":
        quick_reply = message.get("quick_reply", {})
        normalized["quick_reply_payload"] = quick_reply.get("payload")
        # Prefer message text, fall back to quick reply payload
        normalized["text_body"] = (
            message.get("text") or quick_reply.get("title") or quick_reply.get("payload")
        )
    elif message_type == "story_reply":
        # Story replies have same structure as DM but different webhook type
        normalized["text_body"] = message.get("text")
        if message.get("attachments"):
            attachment = message["attachments"][0]
            normalized["media_url"] = attachment.get("payload", {}).get("url")
            normalized["media_mime_type"] = attachment.get("payload", {}).get("mime_type")
    elif message_type == "location":
        normalized["interactive_data"] = {"location": message.get("location")}
    elif message_type == "contacts":
        normalized["interactive_data"] = {"contacts": message.get("contacts")}

    # Quick reply on non-quick_reply message types
    if message.get("quick_reply"):
        qr = message["quick_reply"]
        normalized["quick_reply_payload"] = qr.get("payload")
        # Only set text_body from quick_reply if not already set from message text
        if not normalized["text_body"]:
            normalized["text_body"] = qr.get("title") or qr.get("payload")

    # Referral (from ads/discover/profile)
    if message.get("referral"):
        ref = message["referral"]
        normalized["referral_data"] = {
            "source": ref.get("source"),  # "ads", "discover", "profile"
            "type": ref.get("type"),      # "story", "feed", "reels"
            "ref": ref.get("ref"),
            "ad_id": ref.get("ad_id"),
            "campaign_id": ref.get("campaign_id"),
        }

    # Context (reply/forward)
    if "context" in message:
        normalized["context_message_id"] = message["context"].get("id")
        normalized["referenced_message_id"] = message["context"].get("id")

    return normalized


def normalize_instagram_comment(
    comment: dict[str, Any], ig_professional_account_id: str,
) -> dict[str, Any]:
    """Normalize Instagram comment (eligible for private reply)."""
    return {
        "provider_message_id": comment.get("id"),
        "meta_timestamp": _parse_iso_timestamp(comment.get("timestamp")),
        "direction": "inbound",
        "message_type": "comment",
        "status": "sent",
        "text_body": comment.get("text"),
        "media_url": None,
        "media_mime_type": None,
        "media_sha256": None,
        "media_caption": None,
        "interactive_data": None,
        "quick_reply_payload": None,
        "referral_data": None,
        "context_message_id": None,
        "referenced_message_id": None,
        "ig_scoped_user_id": comment.get("from", {}).get("id"),
        "ig_handle": comment.get("from", {}).get("username"),
        "ig_professional_account_id": ig_professional_account_id,
        # Comment-specific fields
        "comment_media_id": comment.get("media", {}).get("id"),
        "comment_media_type": comment.get("media", {}).get("media_type"),
        "comment_media_permalink": comment.get("media", {}).get("permalink"),
        "comment_parent_id": comment.get("parent_id"),
        "is_hidden": comment.get("is_hidden", False),
        "like_count": comment.get("like_count", 0),
    }


def normalize_instagram_story_reply(
    story_reply: dict[str, Any], ig_professional_account_id: str,
) -> dict[str, Any]:
    """Normalize Instagram story reply."""
    message = story_reply.get("message", {})

    return {
        "provider_message_id": story_reply.get("id"),
        "meta_timestamp": datetime.fromtimestamp(story_reply.get("timestamp", 0), tz=UTC)
        if story_reply.get("timestamp")
        else None,
        "direction": "inbound",
        "message_type": "story_reply",
        "status": "sent",
        "text_body": message.get("text"),
        "media_sha256": None,
        "media_caption": None,
        "interactive_data": None,
        "quick_reply_payload": None,
        "referral_data": None,
        "context_message_id": None,
        "referenced_message_id": None,
        "ig_scoped_user_id": story_reply.get("from", {}).get("id"),
        "ig_handle": story_reply.get("from", {}).get("username"),
        "ig_professional_account_id": ig_professional_account_id,
        "story_id": story_reply.get("story", {}).get("id"),
        # Media attachments in story reply
        "media_url": (
            message.get("attachments", [{}])[0].get("payload", {}).get("url")
            if message.get("attachments")
            else None
        ),
        "media_mime_type": (
            message.get("attachments", [{}])[0].get("payload", {}).get("mime_type")
            if message.get("attachments")
            else None
        ),
    }


def _determine_message_type(message: dict[str, Any]) -> str:
    """Determine message type from Instagram message object."""
    # Check for quick reply first (can be on any message type)
    if message.get("quick_reply"):
        return "quick_reply"

    # Standard type field
    msg_type = message.get("type", "unknown")

    # Map Instagram types to our normalized types
    type_map = {
        "text": "text",
        "image": "image",
        "video": "video",
        "audio": "audio",
        "file": "file",
        "sticker": "sticker",
        "location": "location",
        "contacts": "contacts",
    }

    return type_map.get(msg_type, "unknown")


def _parse_iso_timestamp(timestamp_str: str | None) -> datetime | None:
    """Parse ISO 8601 timestamp to datetime."""
    if not timestamp_str:
        return None
    try:
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except Exception:
        logger.warning(f"Failed to parse timestamp: {timestamp_str}")
        return None


# =============================================================================
# Channel Detection
# =============================================================================


def detect_meta_channel(payload: dict[str, Any]) -> str:
    """Detect whether webhook payload is WhatsApp or Instagram."""
    obj = payload.get("object")
    if obj == "whatsapp_business_account":
        return "whatsapp"
    if obj == "instagram":
        return "instagram"
    raise ValueError(f"Unknown Meta object type: {obj}")


# =============================================================================
# Normalized Event Type (shared with WhatsApp)
# =============================================================================


def to_normalized_message_received(
    normalized: dict[str, Any],
    contact_id: str,
    conversation_id: str,
    message_id: str,
) -> dict[str, Any]:
    """Convert normalized Instagram message to MessageReceived event format (dict)."""
    return {
        "tenant_id": normalized.get("tenant_id"),
        "contact_id": contact_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "provider_message_id": normalized.get("provider_message_id"),
        "direction": normalized.get("direction", "inbound"),
        "message_type": normalized.get("message_type", "unknown"),
        "text_body": normalized.get("text_body"),
        "media_url": normalized.get("media_url"),
        "meta_timestamp": normalized.get("meta_timestamp"),
        # Instagram-specific metadata
        "metadata": {
            "channel": "instagram",
            "ig_scoped_user_id": normalized.get("ig_scoped_user_id"),
            "ig_handle": normalized.get("ig_handle"),
            "ig_professional_account_id": normalized.get("ig_professional_account_id"),
            "quick_reply_payload": normalized.get("quick_reply_payload"),
            "referral_data": normalized.get("referral_data"),
            "is_echo": normalized.get("is_echo", False),
        },
    }
