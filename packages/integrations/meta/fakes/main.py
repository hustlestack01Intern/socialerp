"""
Fake Meta Server - WhatsApp Cloud API + Instagram Messaging API

Simulates Meta's Graph API for local development and CI testing.
Supports: webhook verification, message sending, 24h window, rate limits,
signature verification, delivery/read receipts, fixture replay.
"""

import hashlib
import hmac
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings

# =============================================================================
# SETTINGS
# =============================================================================


class FakeMetaSettings(BaseSettings):
    PORT: int = 8080
    WEBHOOK_VERIFY_TOKEN: str = "test_verify_token"
    APP_SECRET: str = "test_app_secret"
    FIXTURES_DIR: str = "/fixtures"
    RATE_LIMIT_PER_SECOND: int = 20  # Coexistence mode limit

    class Config:
        env_prefix = "FAKE_META_"


settings = FakeMetaSettings()

# =============================================================================
# DATABASE (SQLite for simplicity)
# =============================================================================

DB_PATH = Path("/tmp/fake_meta.db")


def init_db():
    """Initialize SQLite database with schema."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            url TEXT NOT NULL,
            verify_token TEXT NOT NULL,
            fields TEXT NOT NULL,  -- JSON array of subscribed fields
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            channel TEXT NOT NULL,  -- 'whatsapp' or 'instagram'
            direction TEXT NOT NULL,  -- 'inbound' or 'outbound'
            from_number TEXT,
            to_number TEXT,
            content TEXT NOT NULL,  -- JSON
            status TEXT DEFAULT 'sent',  -- sent, delivered, read, failed
            meta_msg_id TEXT,  -- Meta's message ID
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            customer_id TEXT NOT NULL,  -- phone or IG user ID
            business_id TEXT NOT NULL,
            last_message_at TIMESTAMP,
            window_expires_at TIMESTAMP,  -- 24h customer service window
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            language TEXT NOT NULL,
            components TEXT NOT NULL,  -- JSON
            status TEXT DEFAULT 'APPROVED',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_limits (
            key TEXT PRIMARY KEY,
            count INTEGER DEFAULT 0,
            window_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# =============================================================================
# MODELS
# =============================================================================


class WhatsAppMessage(BaseModel):
    messaging_product: str = "whatsapp"
    to: str
    type: str = "text"
    text: dict | None = None
    template: dict | None = None
    context: dict | None = None


class InstagramMessage(BaseModel):
    recipient: dict  # {"id": "user_id"}
    message: dict  # {"text": "..."} or {"attachment": {...}}


class WebhookEntry(BaseModel):
    id: str
    changes: list[dict]


class WebhookPayload(BaseModel):
    object: str
    entry: list[WebhookEntry]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def verify_signature(payload: bytes, signature: str) -> bool:
    """Verify X-Hub-Signature-256."""
    expected = hmac.new(settings.APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


def check_rate_limit(key: str) -> bool:
    """Check and increment rate limit counter."""
    conn = get_db()
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=1)

    row = conn.execute(
        "SELECT count, window_start FROM rate_limits WHERE key = ?", (key,)
    ).fetchone()

    if row:
        count, ws = row
        ws_dt = datetime.fromisoformat(ws)
        if ws_dt < window_start:
            # New window
            conn.execute(
                "UPDATE rate_limits SET count = 1, window_start = ? WHERE key = ?",
                (now.isoformat(), key),
            )
        else:
            if count >= settings.RATE_LIMIT_PER_SECOND:
                conn.close()
                return False
            conn.execute("UPDATE rate_limits SET count = count + 1 WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO rate_limits (key, count, window_start) VALUES (?, 1, ?)",
            (key, now.isoformat()),
        )
    conn.commit()
    conn.close()
    return True


def get_or_create_conversation(
    tenant_id: str, channel: str, customer_id: str, business_id: str
) -> dict:
    """Get or create conversation, update 24h window."""
    conn = get_db()
    window_expires = datetime.utcnow() + timedelta(hours=24)

    row = conn.execute(
        """
        SELECT * FROM conversations
        WHERE tenant_id = ? AND channel = ? AND customer_id = ? AND business_id = ?
        """,
        (tenant_id, channel, customer_id, business_id),
    ).fetchone()

    if row:
        conn.execute(
            "UPDATE conversations SET last_message_at = ?, window_expires_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), window_expires.isoformat(), row["id"]),
        )
        conn.commit()
        conn.close()
        return dict(row)

    conv_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO conversations
           (id, tenant_id, channel, customer_id, business_id, last_message_at, window_expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            conv_id,
            tenant_id,
            channel,
            customer_id,
            business_id,
            datetime.utcnow().isoformat(),
            window_expires.isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return {
        "id": conv_id,
        "tenant_id": tenant_id,
        "channel": channel,
        "customer_id": customer_id,
        "business_id": business_id,
        "last_message_at": datetime.utcnow().isoformat(),
        "window_expires_at": window_expires.isoformat(),
    }


def is_window_open(tenant_id: str, channel: str, customer_id: str | None, business_id: str) -> bool:
    """Check if 24h customer service window is open."""
    conn = get_db()
    cust = customer_id or ""
    row = conn.execute(
        """
        SELECT window_expires_at FROM conversations
        WHERE tenant_id = ? AND channel = ? AND customer_id = ? AND business_id = ?
        """,
        (tenant_id, channel, cust, business_id),
    ).fetchone()
    conn.close()

    if not row:
        return False
    return datetime.fromisoformat(row["window_expires_at"]) > datetime.utcnow()


# =============================================================================
# FIXTURE REPLAY
# =============================================================================

FIXTURES_DIR = Path(settings.FIXTURES_DIR)


def load_fixtures():
    """Load webhook fixtures from JSON files."""
    fixtures = {}
    if FIXTURES_DIR.exists():
        for file in FIXTURES_DIR.glob("*.json"):
            with Path(file).open() as f:
                fixtures[file.stem] = json.load(f)
    return fixtures


FIXTURES = load_fixtures()


# =============================================================================
# LIFESPAN
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# =============================================================================
# FASTAPI APP
# =============================================================================

app = FastAPI(
    title="Fake Meta Server",
    version="1.0.0",
    lifespan=lifespan,
)


# =============================================================================
# HEALTH CHECK
# =============================================================================


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "fake-meta"}


# =============================================================================
# WEBHOOK VERIFICATION (GET)
# =============================================================================


@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
):
    """Meta webhook verification endpoint."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WEBHOOK_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


# =============================================================================
# WEBHOOK RECEIVER (POST)
# =============================================================================


@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_hub_signature_256: str = Header(alias="X-Hub-Signature-256"),
):
    """Receive webhook events from Meta (simulated)."""
    body = await request.body()

    # Verify signature (unless disabled for dev)
    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    payload = json.loads(body)

    # Store inbound messages and open the 24h customer service window.
    # WhatsApp: entry[].changes[].value.messages[]
    # Instagram: entry[].messaging[] with sender/recipient ids
    if payload.get("object") == "instagram":
        for entry in payload.get("entry", []):
            for messaging in entry.get("messaging", []):
                if not messaging.get("message"):
                    continue
                sender_id = (messaging.get("sender") or {}).get("id")
                recipient_id = (messaging.get("recipient") or {}).get("id")
                if not sender_id or not recipient_id:
                    continue
                get_or_create_conversation(
                    tenant_id=recipient_id,
                    channel="instagram",
                    customer_id=sender_id,
                    business_id=recipient_id,
                )
                conn = get_db()
                conn.execute(
                    """
                    INSERT INTO messages
                        (id, tenant_id, channel, direction, from_number,
                         to_number, content, meta_msg_id)
                    VALUES (?, ?, 'instagram', 'inbound', ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        recipient_id,
                        sender_id,
                        recipient_id,
                        json.dumps(messaging.get("message")),
                        (messaging.get("message") or {}).get("mid"),
                    ),
                )
                conn.commit()
                conn.close()
    else:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_number_id = value.get("metadata", {}).get("phone_number_id")
                for msg in value.get("messages", []):
                    # Inbound customer message opens the 24h window
                    if phone_number_id and msg.get("from"):
                        get_or_create_conversation(
                            tenant_id=phone_number_id,
                            channel="whatsapp",
                            customer_id=msg["from"],
                            business_id=phone_number_id,
                        )
                    conn = get_db()
                    conn.execute(
                        """
                        INSERT INTO messages
                            (id, tenant_id, channel, direction, from_number,
                             to_number, content, meta_msg_id)
                        VALUES (?, ?, 'whatsapp', 'inbound', ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            phone_number_id or "default",
                            msg.get("from"),
                            phone_number_id,
                            json.dumps(msg),
                            msg.get("id"),
                        ),
                    )
                    conn.commit()
                    conn.close()

    return {"status": "received"}


# =============================================================================
# MESSAGING API - SEND MESSAGE (WhatsApp + Instagram share /{id}/messages)
# =============================================================================


@app.post("/v20.0/{business_id}/messages")
async def send_message(
    business_id: str,
    request: Request,
    authorization: str = Header(...),
):
    """Send a message via the WhatsApp Cloud API or Instagram Messaging API.

    Meta uses the same URL shape (`/{id}/messages`) for both; the channel is
    distinguished by the payload shape: Instagram payloads carry
    `recipient` + `message`, WhatsApp payloads carry `messaging_product`/`to`.
    """
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from e

    try:
        if "recipient" in payload and "message" in payload:
            return await _send_instagram_message(business_id, InstagramMessage(**payload))
        return await _send_whatsapp_message(business_id, WhatsAppMessage(**payload))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


async def _send_whatsapp_message(phone_number_id: str, message: WhatsAppMessage):
    """Send WhatsApp message (simulates Cloud API)."""
    # Rate limiting
    if not check_rate_limit(f"whatsapp:{phone_number_id}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Extract tenant from phone_number_id (simplified)
    tenant_id = phone_number_id

    # Check 24h window for free-form messages
    if message.type == "text" and message.text:
        customer_id = message.to
        if not is_window_open(tenant_id, "whatsapp", customer_id, phone_number_id):
            # Outside window - only templates allowed
            raise HTTPException(
                status_code=400, detail="Outside 24h customer service window. Use template message."
            )

    # Generate Meta message ID
    meta_msg_id = f"wamid.{uuid.uuid4().hex}"

    # Store outbound message
    conn = get_db()
    conn.execute(
        """
        INSERT INTO messages
            (id, tenant_id, channel, direction, from_number, to_number,
             content, meta_msg_id, status)
        VALUES (?, ?, 'whatsapp', 'outbound', ?, ?, ?, ?, 'sent')
        """,
        (
            str(uuid.uuid4()),
            tenant_id,
            phone_number_id,
            message.to,
            json.dumps(message.model_dump()),
            meta_msg_id,
        ),
    )
    conn.commit()
    conn.close()

    # Simulate async delivery receipt via webhook (in real implementation, this would be async)
    # For now, return success immediately
    return {
        "messaging_product": "whatsapp",
        "contacts": [{"input": message.to, "wa_id": message.to}],
        "messages": [{"id": meta_msg_id}],
    }


# =============================================================================
# INSTAGRAM MESSAGING API - SEND MESSAGE
# =============================================================================


async def _send_instagram_message(page_id: str, message: InstagramMessage):
    """Send Instagram message (simulates Messaging API)."""
    if not check_rate_limit(f"instagram:{page_id}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    tenant_id = page_id
    recipient_id = message.recipient.get("id")

    # Check 24h window
    if not is_window_open(tenant_id, "instagram", recipient_id, page_id):
        raise HTTPException(
            status_code=400, detail="Outside 24h reply window. User must initiate conversation."
        )

    meta_msg_id = f"igmid.{uuid.uuid4().hex}"

    conn = get_db()
    conn.execute(
        """
        INSERT INTO messages
            (id, tenant_id, channel, direction, from_number, to_number,
             content, meta_msg_id, status)
        VALUES (?, ?, 'instagram', 'outbound', ?, ?, ?, ?, 'sent')
        """,
        (
            str(uuid.uuid4()),
            tenant_id,
            page_id,
            recipient_id,
            json.dumps(message.model_dump()),
            meta_msg_id,
        ),
    )
    conn.commit()
    conn.close()

    return {"recipient_id": recipient_id, "message_id": meta_msg_id}


# =============================================================================
# TEMPLATE MANAGEMENT
# =============================================================================


@app.get("/v20.0/{business_account_id}/message_templates")
async def list_templates(business_account_id: str):
    """List message templates."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM templates WHERE tenant_id = ?", (business_account_id,)
    ).fetchall()
    conn.close()

    templates = []
    for row in rows:
        templates.append({
            "id": row["id"],
            "name": row["name"],
            "language": row["language"],
            "status": row["status"],
            "components": json.loads(row["components"]),
        })
    return {"data": templates}


@app.post("/v20.0/{business_account_id}/message_templates")
async def create_template(business_account_id: str, template: dict):
    """Create message template."""
    template_id = str(uuid.uuid4())
    conn = get_db()
    conn.execute(
        """INSERT INTO templates (id, tenant_id, name, language, components, status)
           VALUES (?, ?, ?, ?, ?, 'APPROVED')""",
        (
            template_id,
            business_account_id,
            template.get("name"),
            template.get("language", "en_US"),
            json.dumps(template.get("components", [])),
        ),
    )
    conn.commit()
    conn.close()
    return {"id": template_id, "status": "APPROVED"}


# =============================================================================
# FIXTURE REPLAY ENDPOINTS
# =============================================================================


@app.get("/fixtures")
async def list_fixtures():
    """List available fixtures."""
    return {"fixtures": list(FIXTURES.keys())}


@app.post("/fixtures/{fixture_name}/replay")
async def replay_fixture(fixture_name: str, tenant_id: str = "test"):
    """Replay a recorded webhook fixture."""
    if fixture_name not in FIXTURES:
        raise HTTPException(status_code=404, detail="Fixture not found")

    fixture = FIXTURES[fixture_name]

    # Simulate webhook delivery
    payload = json.dumps(fixture).encode()
    signature = hmac.new(settings.APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()

    # In a real implementation, this would POST to the tenant's webhook URL
    # For testing, we just return the fixture
    return {
        "fixture": fixture_name,
        "payload": fixture,
        "signature": f"sha256={signature}",
    }


# =============================================================================
# INSPECTION ENDPOINTS (for testing)
# =============================================================================


@app.get("/inspect/messages")
async def inspect_messages(tenant_id: str, channel: str | None = None, limit: int = 100):
    """Inspect stored messages (for test verification)."""
    conn = get_db()
    query = "SELECT * FROM messages WHERE tenant_id = ?"
    params = [tenant_id]
    if channel:
        query += " AND channel = ?"
        params.append(channel)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(str(limit))

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"messages": [dict(row) for row in rows]}


@app.get("/inspect/conversations")
async def inspect_conversations(tenant_id: str):
    """Inspect conversations and window status."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE tenant_id = ? ORDER BY last_message_at DESC",
        (tenant_id,),
    ).fetchall()
    conn.close()

    conversations = []
    for row in rows:
        r = dict(row)
        r["window_open"] = datetime.fromisoformat(r["window_expires_at"]) > datetime.utcnow()
        conversations.append(r)

    return {"conversations": conversations}


@app.delete("/inspect/reset")
async def reset_data(tenant_id: str | None = None):
    """Reset all data (for test isolation)."""
    conn = get_db()
    if tenant_id:
        conn.execute("DELETE FROM messages WHERE tenant_id = ?", (tenant_id,))
        conn.execute("DELETE FROM conversations WHERE tenant_id = ?", (tenant_id,))
        conn.execute("DELETE FROM templates WHERE tenant_id = ?", (tenant_id,))
    else:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM conversations")
        conn.execute("DELETE FROM templates")
        conn.execute("DELETE FROM rate_limits")
    conn.commit()
    conn.close()
    return {"status": "reset"}


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)
