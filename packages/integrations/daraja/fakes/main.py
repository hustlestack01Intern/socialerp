"""
Fake Daraja Server - M-Pesa STK Push Simulation

Simulates Safaricom Daraja API for local development and CI testing.
Supports: STK Push, callbacks, query status, all failure modes,
reconciliation endpoint, fixture replay.
"""

import base64
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel
from pydantic_settings import BaseSettings

# =============================================================================
# SETTINGS
# =============================================================================


class FakeDarajaSettings(BaseSettings):
    PORT: int = 8081
    CONSUMER_KEY: str = "test_consumer_key"
    CONSUMER_SECRET: str = "test_consumer_secret"
    PASSKEY: str = "test_passkey"
    SHORTCODE: str = "174379"
    FIXTURES_DIR: str = "/fixtures"

    class Config:
        env_prefix = "FAKE_DARAJA_"


settings = FakeDarajaSettings()

# =============================================================================
# DATABASE (SQLite)
# =============================================================================

DB_PATH = Path("/tmp/fake_daraja.db")


def init_db():
    """Initialize SQLite database with schema."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stk_push_requests (
            id TEXT PRIMARY KEY,
            checkout_request_id TEXT UNIQUE NOT NULL,
            merchant_request_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            phone_number TEXT NOT NULL,
            amount INTEGER NOT NULL,
            account_reference TEXT NOT NULL,
            transaction_desc TEXT NOT NULL,
            callback_url TEXT NOT NULL,
            status TEXT DEFAULT 'pending',  -- pending, completed, failed, cancelled, timeout
            result_code TEXT,
            result_desc TEXT,
            mpesa_receipt_number TEXT,
            transaction_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS callbacks (
            id TEXT PRIMARY KEY,
            checkout_request_id TEXT NOT NULL,
            payload TEXT NOT NULL,  -- JSON
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_status_requests (
            id TEXT PRIMARY KEY,
            checkout_request_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            result TEXT NOT NULL,  -- JSON
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_records (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            date TEXT NOT NULL,  -- YYYY-MM-DD
            provider_statement TEXT NOT NULL,  -- JSON
            our_ledger TEXT NOT NULL,  -- JSON
            mismatches TEXT,  -- JSON
            status TEXT DEFAULT 'pending',  -- pending, resolved, escalated
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS failure_simulation (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            phone_number TEXT NOT NULL,
            failure_mode TEXT NOT NULL,
            active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


class STKPushRequest(BaseModel):
    BusinessShortCode: str
    Password: str
    Timestamp: str
    TransactionType: str = "CustomerPayBillOnline"
    Amount: int
    PartyA: str  # Phone number
    PartyB: str  # Shortcode
    PhoneNumber: str
    CallBackURL: str
    AccountReference: str
    TransactionDesc: str


class STKPushResponse(BaseModel):
    MerchantRequestID: str
    CheckoutRequestID: str
    ResponseCode: str = "0"
    ResponseDescription: str = "Success. Request accepted for processing"
    CustomerMessage: str = "Success. Request accepted for processing"


class CallbackPayload(BaseModel):
    Body: dict


class QueryRequest(BaseModel):
    BusinessShortCode: str
    Password: str
    Timestamp: str
    CheckoutRequestID: str


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def generate_password(shortcode: str, passkey: str, timestamp: str) -> str:
    """Generate Daraja password: base64(shortcode + passkey + timestamp)."""
    data = f"{shortcode}{passkey}{timestamp}"
    return base64.b64encode(data.encode()).decode()


def verify_password(shortcode: str, passkey: str, timestamp: str, password: str) -> bool:
    """Verify Daraja password."""
    expected = generate_password(shortcode, passkey, timestamp)
    return password == expected


def normalize_phone(phone: str) -> str:
    """Normalize phone to 2547XXXXXXXX format."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    elif phone.startswith("7") or phone.startswith("1"):
        phone = "254" + phone
    return phone


def simulate_failure_mode(phone_number: str, tenant_id: str) -> dict | None:
    """Check if a failure mode is configured for this phone/tenant."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT failure_mode FROM failure_simulation
        WHERE tenant_id = ? AND phone_number = ? AND active = 1
        """,
        (tenant_id, phone_number),
    ).fetchone()
    conn.close()

    if not row:
        return None

    failure_mode = row["failure_mode"]

    failure_responses = {
        "insufficient_funds": {
            "ResultCode": "1",
            "ResultDesc": "Insufficient funds in M-Pesa account",
        },
        "wrong_pin": {
            "ResultCode": "1032",
            "ResultDesc": "Wrong PIN entered",
        },
        "cancelled": {
            "ResultCode": "1032",
            "ResultDesc": "Request cancelled by user",
        },
        "timeout": {
            "ResultCode": "1",
            "ResultDesc": "Request timeout",
        },
        "partial_payment": {
            "ResultCode": "0",
            "ResultDesc": "Partial payment received",
        },
        "wrong_amount": {
            "ResultCode": "0",
            "ResultDesc": "Payment received with different amount",
        },
    }

    return failure_responses.get(failure_mode)


# =============================================================================
# FIXTURE REPLAY
# =============================================================================

FIXTURES_DIR = Path(settings.FIXTURES_DIR)


def load_fixtures():
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
    title="Fake Daraja Server",
    version="1.0.0",
    lifespan=lifespan,
)


# =============================================================================
# HEALTH CHECK
# =============================================================================


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "fake-daraja"}


# =============================================================================
# OAUTH TOKEN ENDPOINT
# =============================================================================


@app.post("/oauth/v1/generate")
async def generate_token(
    request: Request,
    authorization: str = Header(...),
):
    """Generate OAuth access token (simulated)."""
    # In real Daraja, this uses Basic Auth with consumer_key:consumer_secret
    # For fake, we just return a token
    return {
        "access_token": f"fake_token_{uuid.uuid4().hex}",
        "expires_in": "3599",
    }


# =============================================================================
# STK PUSH ENDPOINT
# =============================================================================


@app.post("/mpesa/stkpush/v1/processrequest")
async def stk_push(
    request: STKPushRequest,
    authorization: str = Header(...),
):
    """Process STK Push request."""
    # Verify password
    if not verify_password(
        request.BusinessShortCode, settings.PASSKEY, request.Timestamp, request.Password
    ):
        raise HTTPException(status_code=400, detail="Invalid password")

    # Normalize phone
    phone = normalize_phone(request.PhoneNumber)

    # Generate IDs
    checkout_request_id = (
        f"ws_CO_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
    )
    merchant_request_id = f"mreq_{uuid.uuid4().hex[:12]}"

    # Check for failure simulation
    failure = simulate_failure_mode(phone, request.BusinessShortCode)

    # Store request
    conn = get_db()
    conn.execute(
        """INSERT INTO stk_push_requests
           (id, checkout_request_id, merchant_request_id, tenant_id, phone_number, amount,
            account_reference, transaction_desc, callback_url, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()),
            checkout_request_id,
            merchant_request_id,
            request.BusinessShortCode,
            phone,
            request.Amount,
            request.AccountReference,
            request.TransactionDesc,
            request.CallBackURL,
            "pending" if not failure else "simulated_failure",
        ),
    )
    conn.commit()
    conn.close()

    # Return immediate response (Daraja returns immediately, callback comes later)
    return STKPushResponse(
        MerchantRequestID=merchant_request_id,
        CheckoutRequestID=checkout_request_id,
    ).model_dump()


# =============================================================================
# CALLBACK ENDPOINT (receives callbacks from our simulated processing)
# =============================================================================


@app.post("/callback/stk")
async def stk_callback(
    request: Request,
    tenant_id: str = Query(...),
):
    """Receive STK Push callback (simulated by our own worker)."""
    body = await request.json()

    # Store callback
    conn = get_db()
    checkout_request_id = body.get("Body", {}).get("stkCallback", {}).get("CheckoutRequestID")
    conn.execute(
        "INSERT INTO callbacks (id, checkout_request_id, payload) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), checkout_request_id, json.dumps(body)),
    )
    conn.commit()
    conn.close()

    return {"ResultCode": 0, "ResultDesc": "Callback received"}


# =============================================================================
# SIMULATE CALLBACK (for testing - triggers callback to tenant's URL)
# =============================================================================


@app.post("/simulate/callback/{checkout_request_id}")
async def simulate_callback(
    checkout_request_id: str,
    result_code: str = "0",
    result_desc: str = "Success",
    mpesa_receipt_number: str | None = None,
    transaction_date: str | None = None,
    amount: int | None = None,
):
    """Simulate a callback for a checkout request (for testing)."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM stk_push_requests WHERE checkout_request_id = ?", (checkout_request_id,)
    ).fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Checkout request not found")

    tenant_id = row["tenant_id"]
    phone_number = row["phone_number"]
    callback_url = row["callback_url"]
    original_amount = row["amount"]

    # Check for failure simulation
    failure = simulate_failure_mode(phone_number, tenant_id)

    if failure:
        result_code = failure["ResultCode"]
        result_desc = failure["ResultDesc"]

    # Override with provided values
    if mpesa_receipt_number is None and result_code == "0":
        mpesa_receipt_number = f"MPE{uuid.uuid4().hex[:10].upper()}"
    if transaction_date is None:
        transaction_date = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    if amount is None:
        amount = original_amount

    # Build callback payload (matching Daraja format)
    callback_payload = {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": row["merchant_request_id"],
                "CheckoutRequestID": checkout_request_id,
                "ResultCode": result_code,
                "ResultDesc": result_desc,
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": amount},
                        {"Name": "MpesaReceiptNumber", "Value": mpesa_receipt_number},
                        {"Name": "Balance", "Value": 0},
                        {"Name": "TransactionDate", "Value": int(transaction_date)},
                        {"Name": "PhoneNumber", "Value": int(phone_number)},
                    ]
                    if result_code == "0"
                    else []
                },
            }
        }
    }

    # Update request status
    status = "completed" if result_code == "0" else "failed"
    conn.execute(
        """UPDATE stk_push_requests
           SET status = ?, result_code = ?, result_desc = ?,
               mpesa_receipt_number = ?, transaction_date = ?, updated_at = ?
           WHERE checkout_request_id = ?""",
        (
            status,
            result_code,
            result_desc,
            mpesa_receipt_number,
            transaction_date,
            datetime.utcnow().isoformat(),
            checkout_request_id,
        ),
    )

    # Store callback
    conn.execute(
        "INSERT INTO callbacks (id, checkout_request_id, payload) VALUES (?, ?, ?)",
        (str(uuid.uuid4()), checkout_request_id, json.dumps(callback_payload)),
    )
    conn.commit()
    conn.close()

    # In real implementation, we'd POST to callback_url here
    # For testing, we just return the payload
    return {
        "status": "simulated",
        "callback_payload": callback_payload,
        "callback_url": callback_url,
    }


# =============================================================================
# QUERY STATUS ENDPOINT
# =============================================================================


@app.post("/mpesa/stkpushquery/v1/query")
async def query_status(
    request: QueryRequest,
    authorization: str = Header(...),
):
    """Query STK Push status."""
    # Verify password
    if not verify_password(
        request.BusinessShortCode, settings.PASSKEY, request.Timestamp, request.Password
    ):
        raise HTTPException(status_code=400, detail="Invalid password")

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM stk_push_requests WHERE checkout_request_id = ?",
        (request.CheckoutRequestID,),
    ).fetchone()

    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Checkout request not found")

    # Build response matching Daraja format
    result = {
        "ResponseCode": "0",
        "ResponseDescription": "Success",
        "MerchantRequestID": row["merchant_request_id"],
        "CheckoutRequestID": row["checkout_request_id"],
        "ResultCode": row["result_code"] or "0",
        "ResultDesc": row["result_desc"] or "Success",
    }

    if row["mpesa_receipt_number"]:
        result["MpesaReceiptNumber"] = row["mpesa_receipt_number"]
    if row["transaction_date"]:
        result["TransactionDate"] = row["transaction_date"]
    if row["phone_number"]:
        result["PhoneNumber"] = row["phone_number"]

    # Store query
    conn.execute(
        """
        INSERT INTO query_status_requests (id, checkout_request_id, tenant_id, result)
        VALUES (?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), request.CheckoutRequestID, row["tenant_id"], json.dumps(result)),
    )
    conn.commit()
    conn.close()

    return result


# =============================================================================
# RECONCILIATION ENDPOINT
# =============================================================================


@app.post("/reconciliation/daily")
async def daily_reconciliation(
    tenant_id: str,
    date: str = Query(description="Date in YYYY-MM-DD format"),
    provider_statement: dict | None = None,
):
    """Run daily reconciliation between our ledger and provider statement."""
    # In real implementation, this would fetch from provider
    # For fake, we use provided statement or generate mock

    if provider_statement is None:
        provider_statement = {}
    conn = get_db()

    # Get our ledger for the date
    our_rows = conn.execute(
        """SELECT * FROM stk_push_requests
           WHERE tenant_id = ? AND date(created_at) = ? AND status IN ('completed', 'failed')""",
        (tenant_id, date),
    ).fetchall()

    our_ledger = [dict(r) for r in our_rows]

    # Compare (simplified)
    mismatches = []

    # Check for missing in provider
    provider_ids = {
        item.get("CheckoutRequestID") for item in provider_statement.get("transactions", [])
    }
    our_ids = {r["checkout_request_id"] for r in our_rows}

    for missing in our_ids - provider_ids:
        mismatches.append({
            "type": "missing_in_provider",
            "checkout_request_id": missing,
        })

    for extra in provider_ids - our_ids:
        mismatches.append({
            "type": "extra_in_provider",
            "checkout_request_id": extra,
        })

    # Store reconciliation record
    record_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO reconciliation_records
           (id, tenant_id, date, provider_statement, our_ledger, mismatches, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            record_id,
            tenant_id,
            date,
            json.dumps(provider_statement),
            json.dumps(our_ledger),
            json.dumps(mismatches),
            "resolved" if not mismatches else "escalated",
        ),
    )
    conn.commit()
    conn.close()

    return {
        "reconciliation_id": record_id,
        "date": date,
        "our_transactions": len(our_ledger),
        "provider_transactions": len(provider_statement.get("transactions", [])),
        "mismatches": len(mismatches),
        "mismatch_details": mismatches,
    }


# =============================================================================
# FAILURE SIMULATION CONFIGURATION
# =============================================================================


class FailureSimulationConfig(BaseModel):
    tenant_id: str
    phone_number: str
    failure_mode: str


@app.post("/simulate/failure")
async def configure_failure(config: FailureSimulationConfig):
    """Configure a failure mode for a specific phone/tenant."""
    tenant_id = config.tenant_id
    failure_mode = config.failure_mode
    valid_modes = [
        "insufficient_funds",
        "wrong_pin",
        "cancelled",
        "timeout",
        "duplicate_callback",
        "late_callback",
        "partial_payment",
        "wrong_amount",
        "none",  # Clear failure
    ]

    if failure_mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"Invalid failure mode. Valid: {valid_modes}")

    phone = normalize_phone(config.phone_number)

    conn = get_db()
    if failure_mode == "none":
        conn.execute(
            "UPDATE failure_simulation SET active = 0 WHERE tenant_id = ? AND phone_number = ?",
            (tenant_id, phone),
        )
    else:
        conn.execute(
            """
            INSERT OR REPLACE INTO failure_simulation
                (id, tenant_id, phone_number, failure_mode, active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (str(uuid.uuid4()), tenant_id, phone, failure_mode),
        )
    conn.commit()
    conn.close()

    return {
        "status": "configured",
        "tenant_id": tenant_id,
        "phone_number": phone,
        "failure_mode": failure_mode,
    }


@app.get("/simulate/failure")
async def list_failures(tenant_id: str | None = None):
    """List active failure simulations."""
    conn = get_db()
    if tenant_id:
        rows = conn.execute(
            "SELECT * FROM failure_simulation WHERE tenant_id = ? AND active = 1", (tenant_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM failure_simulation WHERE active = 1").fetchall()
    conn.close()
    return {"failures": [dict(row) for row in rows]}


# =============================================================================
# FIXTURE REPLAY
# =============================================================================


@app.get("/fixtures")
async def list_fixtures():
    return {"fixtures": list(FIXTURES.keys())}


@app.post("/fixtures/{fixture_name}/replay")
async def replay_fixture(fixture_name: str):
    if fixture_name not in FIXTURES:
        raise HTTPException(status_code=404, detail="Fixture not found")
    return {"fixture": fixture_name, "payload": FIXTURES[fixture_name]}


# =============================================================================
# INSPECTION ENDPOINTS
# =============================================================================


@app.get("/inspect/stk_requests")
async def inspect_stk_requests(tenant_id: str, limit: int = 100):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM stk_push_requests WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
        (tenant_id, limit),
    ).fetchall()
    conn.close()
    return {"requests": [dict(row) for row in rows]}


@app.get("/inspect/callbacks")
async def inspect_callbacks(tenant_id: str | None = None, checkout_request_id: str | None = None):
    conn = get_db()
    if checkout_request_id:
        rows = conn.execute(
            "SELECT * FROM callbacks WHERE checkout_request_id = ? ORDER BY received_at DESC",
            (checkout_request_id,),
        ).fetchall()
    elif tenant_id:
        rows = conn.execute(
            """SELECT c.* FROM callbacks c
               JOIN stk_push_requests s ON s.checkout_request_id = c.checkout_request_id
               WHERE s.tenant_id = ? ORDER BY c.received_at DESC LIMIT 100""",
            (tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM callbacks ORDER BY received_at DESC LIMIT 100"
        ).fetchall()
    conn.close()
    return {"callbacks": [dict(row) for row in rows]}


@app.get("/inspect/reconciliation")
async def inspect_reconciliation(tenant_id: str, limit: int = 50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM reconciliation_records WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
        (tenant_id, limit),
    ).fetchall()
    conn.close()
    return {"reconciliations": [dict(row) for row in rows]}


@app.delete("/inspect/reset")
async def reset_data(tenant_id: str | None = None):
    conn = get_db()
    if tenant_id:
        conn.execute("DELETE FROM stk_push_requests WHERE tenant_id = ?", (tenant_id,))
        conn.execute(
            """
            DELETE FROM callbacks WHERE checkout_request_id IN
                (SELECT checkout_request_id FROM stk_push_requests WHERE tenant_id = ?)
            """,
            (tenant_id,),
        )
        conn.execute("DELETE FROM query_status_requests WHERE tenant_id = ?", (tenant_id,))
        conn.execute("DELETE FROM reconciliation_records WHERE tenant_id = ?", (tenant_id,))
        conn.execute("DELETE FROM failure_simulation WHERE tenant_id = ?", (tenant_id,))
    else:
        conn.execute("DELETE FROM stk_push_requests")
        conn.execute("DELETE FROM callbacks")
        conn.execute("DELETE FROM query_status_requests")
        conn.execute("DELETE FROM reconciliation_records")
        conn.execute("DELETE FROM failure_simulation")
    conn.commit()
    conn.close()
    return {"status": "reset"}


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.PORT)
