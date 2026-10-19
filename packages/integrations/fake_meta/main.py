"""
Fake Meta Server (WhatsApp/Instagram Webhook Simulator)

A FastAPI application that simulates the Meta Graph API webhook endpoints
for local development and CI testing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

# =============================================================================
# Data Models
# =============================================================================


class WebhookChallenge(BaseModel):
    hub_mode: str = Field(alias="hub.mode")
    hub_challenge: str = Field(alias="hub.challenge")
    hub_verify_token: str = Field(alias="hub.verify_token")


class WhatsAppMessage(BaseModel):
    object: str
    entry: list[dict[str, Any]]


class SendMessageRequest(BaseModel):
    to: str
    type: str
    text: dict[str, str] | None = None
    template: dict[str, Any] | None = None


class InstagramSendMessageRequest(BaseModel):
    recipient: dict[str, str]
    message: dict[str, Any]


class PrivateReplyRequest(BaseModel):
    message: dict[str, str]


class Recording(BaseModel):
    id: str
    name: str
    created_at: datetime
    webhooks: list[dict[str, Any]] = []


# =============================================================================
# In-Memory State
# =============================================================================


@dataclass
class FakeMetaState:
    verify_token: str = "test-verify-token"
    app_secret: str = "test-app-secret"
    rate_limit: int = 20  # requests per second
    window_24h_hours: int = 24

    # Rate limiting: {client_ip: [(timestamp, count)]}
    rate_limit_buckets: dict[str, list[tuple[float, int]]] = field(
        default_factory=lambda: defaultdict(list),
    )

    # 24h window tracking: {identity: last_customer_message_timestamp}
    # identity can be phone (WhatsApp) or thread_id (Instagram)
    customer_windows: dict[str, float] = field(default_factory=dict)

    # Instagram comment-to-DM eligibility tracking
    # {comment_id: {expires_at, attempts_remaining, consumed, thread_id, contact_id}}
    instagram_eligibility: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Recordings
    recordings: dict[str, Recording] = field(default_factory=dict)
    current_recording: Recording | None = None

    # Message log for inspection
    message_log: list[dict[str, Any]] = field(default_factory=list)


state = FakeMetaState()


# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="Fake Meta Server",
    description="WhatsApp/Instagram Webhook Simulator for SocialERP",
    version="0.1.0",
)


# =============================================================================
# Helpers
# =============================================================================


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify X-Hub-Signature-256."""
    if not signature.startswith("sha256="):
        return False
    expected = signature[7:]
    computed = hmac.new(state.app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, expected)


def check_rate_limit(client_ip: str) -> bool:
    """Check and update rate limit for client IP."""
    now = time.time()
    window_start = now - 1.0  # 1 second window

    # Clean old entries
    state.rate_limit_buckets[client_ip] = [
        (ts, cnt) for ts, cnt in state.rate_limit_buckets[client_ip] if ts > window_start
    ]

    # Count requests in window
    total = sum(cnt for _, cnt in state.rate_limit_buckets[client_ip])

    if total >= state.rate_limit:
        return False

    # Add current request
    state.rate_limit_buckets[client_ip].append((now, 1))
    return True


def is_within_24h_window(identity: str) -> bool:
    """Check if identity is within 24h customer service window."""
    last_msg = state.customer_windows.get(identity)
    if last_msg is None:
        return False
    return (time.time() - last_msg) < (state.window_24h_hours * 3600)


def update_customer_window(identity: str) -> None:
    """Update last customer message timestamp."""
    state.customer_windows[identity] = time.time()


def normalize_phone(phone: str) -> str:
    """Normalize phone to 254XXXXXXXXX format."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("254") and len(digits) == 12:
        return digits
    elif digits.startswith("0") and len(digits) == 10:
        return "254" + digits[1:]
    elif (digits.startswith("7") and len(digits) == 9) or (
        digits.startswith("1") and len(digits) == 9
    ):
        return "254" + digits
    return digits


# =============================================================================
# Instagram Eligibility Helpers
# =============================================================================


def create_instagram_eligibility(
    comment_id: str,
    thread_id: str,
    contact_id: str,
    expires_in_days: int = 7,
) -> dict[str, Any]:
    """Create a new eligibility record for comment-to-DM."""
    now = time.time()
    expires_at = now + (expires_in_days * 86400)
    return {
        "comment_id": comment_id,
        "thread_id": thread_id,
        "contact_id": contact_id,
        "expires_at": expires_at,
        "attempts_remaining": 1,
        "consumed": False,
        "consumed_at": None,
        "created_at": now,
    }


def get_instagram_eligibility(comment_id: str) -> dict[str, Any] | None:
    """Get eligibility record for a comment."""
    return state.instagram_eligibility.get(comment_id)


def consume_instagram_eligibility(comment_id: str) -> bool:
    """Mark eligibility as consumed. Returns True if successful."""
    eligibility = state.instagram_eligibility.get(comment_id)
    if not eligibility:
        return False
    if eligibility["consumed"]:
        return False
    if time.time() > eligibility["expires_at"]:
        return False
    if eligibility["attempts_remaining"] <= 0:
        return False

    eligibility["attempts_remaining"] -= 1
    if eligibility["attempts_remaining"] <= 0:
        eligibility["consumed"] = True
        eligibility["consumed_at"] = time.time()
    return True


# =============================================================================
# Endpoints
# =============================================================================


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "fake-meta"}


@app.get("/webhook")
async def webhook_verify(
    hub_mode: str = Query(alias="hub.mode"),
    hub_challenge: str = Query(alias="hub.challenge"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
):
    """Meta webhook verification endpoint (WhatsApp + Instagram)."""
    if hub_mode == "subscribe" and hub_verify_token == state.verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Invalid verify token")


@app.post("/webhook")
async def webhook_receive(
    request: Request,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    """Receive WhatsApp/Instagram webhook."""
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Signature verification
    body = await request.body()
    if not verify_signature(body, x_hub_signature_256 or ""):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse and normalize
    try:
        payload = await request.json()
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="Invalid JSON") from e

    # Track for recording
    webhook_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "headers": dict(request.headers),
        "body": payload,
        "client_ip": client_ip,
    }

    if state.current_recording:
        state.current_recording.webhooks.append(webhook_entry)

    state.message_log.append(webhook_entry)

    # Handle WhatsApp webhooks
    if payload.get("object") == "whatsapp_business_account":
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if "messages" in value:
                    for msg in value["messages"]:
                        from_phone = msg.get("from")
                        if from_phone:
                            update_customer_window(normalize_phone(from_phone))

    # Handle Instagram webhooks
    elif payload.get("object") == "instagram":
        for entry in payload.get("entry", []):
            # Process DMs
            for msg in entry.get("messaging", []):
                sender_id = msg.get("sender", {}).get("id")
                if sender_id:
                    update_customer_window(f"ig:{sender_id}")

            # Process comments (create eligibility for private reply)
            for comment in entry.get("comments", []):
                comment_id = comment.get("id")
                if comment_id:
                    # Create eligibility (7-day window, 1 attempt)
                    from_id = comment.get("from", {}).get("id")
                    thread_id = f"ig:{comment_id}"  # Simplified thread mapping
                    eligibility = create_instagram_eligibility(
                        comment_id=comment_id,
                        thread_id=thread_id,
                        contact_id=from_id or "unknown",
                    )
                    state.instagram_eligibility[comment_id] = eligibility

            # Process story replies (same as DMs for window tracking)
            for story_reply in entry.get("story_replies", []):
                from_id = story_reply.get("from", {}).get("id")
                if from_id:
                    update_customer_window(f"ig:{from_id}")

    return {"status": "received"}


# =============================================================================
# WhatsApp Send Endpoint
# =============================================================================

@app.post("/messages")
async def send_message(
    request: Request,
    message: SendMessageRequest,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    """Send WhatsApp message (simulates Meta Send API)."""
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Check 24h window for free-form messages
    to_phone = normalize_phone(message.to)

    if message.type == "text" and message.text:
        # Free-form message - check window
        if not is_within_24h_window(to_phone):
            raise HTTPException(
                status_code=400,
                detail="Outside 24h customer service window. Use template message.",
            )
    elif message.type == "template":
        # Template messages allowed outside window
        pass

    # Log outgoing message
    outbound_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "direction": "outbound",
        "to": to_phone,
        "type": message.type,
        "content": message.text or message.template,
    }

    if state.current_recording:
        state.current_recording.webhooks.append(outbound_entry)

    state.message_log.append(outbound_entry)

    return {
        "messaging_product": "whatsapp",
        "contacts": [{"input": message.to, "wa_id": to_phone}],
        "messages": [{"id": f"wamid.{uuid4().hex[:20]}"}],
    }


# =============================================================================
# Instagram Send Endpoint
# =============================================================================

@app.post("/instagram/messages")
async def send_instagram_message(
    request: Request,
    message: InstagramSendMessageRequest,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    """Send Instagram DM (simulates Meta Send API)."""
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    recipient = message.recipient
    thread_key = recipient.get("thread_key")
    message_data = message.message

    # Check 24h window for free-form messages
    if (
        thread_key
        and message_data.get("text")
        and not message_data.get("template")
        and not is_within_24h_window(f"ig:{thread_key}")
    ):
        raise HTTPException(
            status_code=400,
            detail="Outside 24h customer service window. Use template message.",
        )

    # Log outgoing message
    outbound_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "direction": "outbound",
        "platform": "instagram",
        "thread_key": thread_key,
        "content": message_data,
    }

    if state.current_recording:
        state.current_recording.webhooks.append(outbound_entry)

    state.message_log.append(outbound_entry)

    return {
        "messaging_product": "instagram",
        "message_id": f"igmid.{uuid4().hex[:20]}",
    }


# =============================================================================
# Comment-to-DM Private Reply Endpoint
# =============================================================================

@app.post("/{comment_id}/private_replies")
async def send_private_reply(
    request: Request,
    comment_id: str,
    reply: PrivateReplyRequest,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    """Send private reply to a comment (comment-to-DM)."""
    # Rate limiting (separate limit for private replies: 750/hr)
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Check eligibility
    eligibility = get_instagram_eligibility(comment_id)
    if not eligibility:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 200, "message": "Private reply not allowed"}},
        )

    # Check if consumed
    if eligibility["consumed"]:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 200, "message": "Private reply already sent"}},
        )

    # Check expiry
    if time.time() > eligibility["expires_at"]:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 200, "message": "Private reply window expired"}},
        )

    # Check attempts
    if eligibility["attempts_remaining"] <= 0:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 200, "message": "No attempts remaining"}},
        )

    # Consume eligibility
    success = consume_instagram_eligibility(comment_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 200, "message": "Failed to consume eligibility"}},
        )

    # Log outgoing private reply
    outbound_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "direction": "outbound",
        "platform": "instagram",
        "type": "private_reply",
        "comment_id": comment_id,
        "content": reply.message,
    }

    if state.current_recording:
        state.current_recording.webhooks.append(outbound_entry)

    state.message_log.append(outbound_entry)

    return {"message_id": f"igmid.{uuid4().hex[:20]}"}


# =============================================================================
# Instagram OAuth Mock Endpoints
# =============================================================================

@app.get("/instagram/oauth/authorize")
async def instagram_oauth_authorize(
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query(...),
    response_type: str = Query(...),
    state_param: str = Query(None, alias="state"),
):
    """Mock Instagram OAuth authorization endpoint."""
    # In real implementation, this would show consent screen
    # For testing, immediately redirect with code
    mock_code = f"mock_code_{uuid4().hex[:16]}"
    redirect = f"{redirect_uri}?code={mock_code}"
    if state_param:
        redirect += f"&state={state_param}"
    return Response(content="", status_code=302, headers={"Location": redirect})


@app.post("/instagram/oauth/token")
async def instagram_oauth_token(
    client_id: str = Query(...),
    client_secret: str = Query(...),
    grant_type: str = Query(...),
    code: str = Query(...),
    redirect_uri: str = Query(...),
):
    """Mock Instagram OAuth token endpoint."""
    if grant_type != "authorization_code":
        raise HTTPException(status_code=400, detail="Invalid grant_type")

    # Return mock tokens
    return {
        "access_token": f"mock_access_token_{uuid4().hex[:32]}",
        "token_type": "bearer",
        "expires_in": 3600,
    }


@app.get("/instagram/accounts")
async def instagram_get_accounts(
    access_token: str = Query(...),
):
    """Mock Instagram accounts endpoint (returns connected IG Professional accounts)."""
    return {
        "data": [
            {
                "id": "123456789",
                "username": "test_business",
                "name": "Test Business",
                "profile_picture_url": "https://example.com/profile.jpg",
            },
        ],
    }


@app.get("/instagram/ig-users")
async def instagram_get_ig_users(
    access_token: str = Query(...),
):
    """Mock Instagram users endpoint (returns connected IG Professional
    accounts with permissions)."""
    return {
        "data": [
            {
                "id": "123456789",
                "username": "test_business",
                "name": "Test Business",
                "tasks": ["MODERATE"],
            },
        ],
    }


# =============================================================================
# Admin Endpoints (unchanged)
# =============================================================================

@app.post("/admin/record")
async def start_recording(name: str = "recording"):
    """Start recording webhook sequence."""
    recording = Recording(
        id=str(uuid4())[:8],
        name=name,
        created_at=datetime.now(UTC),
    )
    state.current_recording = recording
    state.recordings[recording.id] = recording
    return {"recording_id": recording.id, "name": recording.name}


@app.get("/admin/recordings")
async def list_recordings():
    """List all recordings."""
    return [
        {
            "id": r.id,
            "name": r.name,
            "created_at": r.created_at.isoformat(),
            "count": len(r.webhooks),
        }
        for r in state.recordings.values()
    ]


@app.get("/admin/replay/{recording_id}")
async def replay_recording(recording_id: str):
    """Replay a recorded webhook sequence."""
    recording = state.recordings.get(recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    return {
        "recording_id": recording.id,
        "name": recording.name,
        "created_at": recording.created_at.isoformat(),
        "webhooks": recording.webhooks,
    }


@app.delete("/admin/recordings/{recording_id}")
async def delete_recording(recording_id: str):
    """Delete a recording."""
    if recording_id in state.recordings:
        del state.recordings[recording_id]
    if state.current_recording and state.current_recording.id == recording_id:
        state.current_recording = None
    return {"status": "deleted"}


@app.get("/admin/messages")
async def get_message_log(limit: int = 100):
    """Get recent message log."""
    return state.message_log[-limit:]


@app.delete("/admin/messages")
async def clear_message_log():
    """Clear message log."""
    state.message_log.clear()
    return {"status": "cleared"}


@app.post("/admin/reset")
async def reset_state():
    """Reset all state (for testing)."""
    global state
    state = FakeMetaState()
    return {"status": "reset"}


# =============================================================================
# Admin State Inspection
# =============================================================================

@app.get("/__fake/state/instagram/eligibility")
async def get_eligibility_state():
    """Get current Instagram eligibility state (for tests)."""
    return {
        comment_id: {
            "thread_id": e["thread_id"],
            "contact_id": e["contact_id"],
            "expires_at": e["expires_at"],
            "attempts_remaining": e["attempts_remaining"],
            "consumed": e["consumed"],
            "consumed_at": e["consumed_at"],
        }
        for comment_id, e in state.instagram_eligibility.items()
    }


@app.post("/__fake/state/instagram/eligibility/{comment_id}/consume")
async def consume_eligibility_manual(comment_id: str):
    """Manually consume eligibility (for tests)."""
    success = consume_instagram_eligibility(comment_id)
    return {"consumed": success}


@app.post("/__fake/state/instagram/rate-limit/reset")
async def reset_instagram_rate_limit():
    """Reset rate limit buckets (for tests)."""
    state.rate_limit_buckets.clear()
    return {"status": "reset"}


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
