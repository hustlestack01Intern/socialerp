"""
Fake Meta Server (WhatsApp/Instagram Webhook Simulator)

A FastAPI application that simulates the Meta Graph API webhook endpoints
for local development and CI testing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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
        default_factory=lambda: defaultdict(list)
    )

    # 24h window tracking: {phone_number: last_customer_message_timestamp}
    customer_windows: dict[str, float] = field(default_factory=dict)

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


def is_within_24h_window(phone: str) -> bool:
    """Check if phone number is within 24h customer service window."""
    last_msg = state.customer_windows.get(phone)
    if last_msg is None:
        return False
    return (time.time() - last_msg) < (state.window_24h_hours * 3600)


def update_customer_window(phone: str) -> None:
    """Update last customer message timestamp."""
    state.customer_windows[phone] = time.time()


def normalize_phone(phone: str) -> str:
    """Normalize phone to 254XXXXXXXXX format."""
    import re

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
    """Meta webhook verification endpoint."""
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

    # Update 24h window for customer messages
    if payload.get("object") == "whatsapp_business_account":
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if "messages" in value:
                    for msg in value["messages"]:
                        from_phone = msg.get("from")
                        if from_phone:
                            update_customer_window(normalize_phone(from_phone))

    return {"status": "received"}


@app.post("/messages")
async def send_message(
    request: Request,
    message: SendMessageRequest,
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
):
    """Send message (simulates Meta Send API)."""
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
                status_code=400, detail="Outside 24h customer service window. Use template message."
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
# Main
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8081)
