"""
Fake Daraja Server (M-Pesa STK Push Simulator)

A FastAPI application that simulates the Safaricom Daraja API endpoints
for local development and CI testing.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

# =============================================================================
# Data Models
# =============================================================================


class STKPushRequest(BaseModel):
    BusinessShortCode: str
    Password: str
    Timestamp: str
    TransactionType: str
    Amount: int
    PartyA: str
    PartyB: str
    PhoneNumber: str
    CallBackURL: str
    AccountReference: str
    TransactionDesc: str


class STKPushResponse(BaseModel):
    MerchantRequestID: str
    CheckoutRequestID: str
    ResponseCode: str
    ResponseDescription: str
    CustomerMessage: str


class CallbackRequest(BaseModel):
    Body: dict[str, Any]


class QueryRequest(BaseModel):
    BusinessShortCode: str
    Password: str
    Timestamp: str
    CheckoutRequestID: str


class ScenarioConfig(BaseModel):
    # success, timeout, insufficient_funds, cancelled, duplicate, late_callback, wrong_amount
    scenario: str
    delay_seconds: int = 0
    custom_amount: int | None = None


class Recording(BaseModel):
    id: str
    name: str
    created_at: datetime
    events: list[dict[str, Any]] = []


# =============================================================================
# In-Memory State
# =============================================================================


@dataclass
class STKRequestRecord:
    checkout_request_id: str
    merchant_request_id: str
    request: STKPushRequest
    scenario: str
    status: str  # pending, completed, failed, cancelled
    callback_sent: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    callback_data: dict[str, Any] | None = None


@dataclass
class FakeDarajaState:
    consumer_key: str = "test-consumer-key"
    consumer_secret: str = "test-consumer-secret"
    passkey: str = "test-passkey"
    shortcode: str = "174379"

    # STK Push records
    stk_records: dict[str, STKRequestRecord] = field(default_factory=dict)

    # Current scenario
    current_scenario: str = "success"
    current_scenario_delay: int = 0
    custom_amount: int | None = None

    # Callback URL for async callbacks
    callback_url: str = "http://host.docker.internal:8000/api/v1/payments/callback"

    # Recordings
    recordings: dict[str, Recording] = field(default_factory=dict)
    current_recording: Recording | None = None

    # Event log
    event_log: list[dict[str, Any]] = field(default_factory=list)


state = FakeDarajaState()


# =============================================================================
# Scenario Definitions
# =============================================================================

SCENARIOS = {
    "success": {
        "result_code": "0",
        "result_desc": "The service request is processed successfully.",
        "callback_metadata": {
            "Item": [
                {"Name": "Amount", "Value": None},  # Filled from request
                {"Name": "MpesaReceiptNumber", "Value": None},  # Generated
                {"Name": "Balance", "Value": ""},
                {"Name": "TransactionDate", "Value": None},  # Generated
                {"Name": "PhoneNumber", "Value": None},  # From request
            ],
        },
    },
    "insufficient_funds": {
        "result_code": "1",
        "result_desc": "Insufficient funds in the account.",
        "callback_metadata": {"Item": []},
    },
    "cancelled": {
        "result_code": "1032",
        "result_desc": "Request cancelled by user",
        "callback_metadata": {"Item": []},
    },
    "wrong_pin": {
        "result_code": "1037",
        "result_desc": "Wrong PIN entered",
        "callback_metadata": {"Item": []},
    },
    "timeout": {
        "result_code": "1001",
        "result_desc": "Request timed out",
        "callback_metadata": {"Item": []},
    },
}


# =============================================================================
# Helpers
# =============================================================================


def generate_checkout_request_id() -> str:
    return f"ws_CO_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(100000, 999999)}"


def generate_merchant_request_id() -> str:
    return f"mreq_{uuid4().hex[:24]}"


def generate_mpesa_receipt() -> str:
    return f"NLJ{uuid4().hex[:7].upper()}"


def generate_transaction_date() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def build_callback(record: STKRequestRecord, scenario: str) -> dict[str, Any]:
    """Build callback payload for a completed STK request."""
    scenario_config = SCENARIOS.get(scenario, SCENARIOS["success"])
    metadata: dict[str, Any] = {}
    raw_metadata = scenario_config["callback_metadata"]
    from typing import cast

    raw_items = cast(dict[str, Any], raw_metadata).get("Item", [])
    # mypy: treat as list of dicts
    items: list[dict[str, Any]] = []
    for i in raw_items:
        items.append(dict(i))
    metadata["Item"] = items

    # Fill in dynamic values
    for item in metadata["Item"]:
        if item["Name"] == "Amount":
            item["Value"] = record.request.Amount
        elif item["Name"] == "MpesaReceiptNumber":
            item["Value"] = generate_mpesa_receipt()
        elif item["Name"] == "TransactionDate":
            item["Value"] = generate_transaction_date()
        elif item["Name"] == "PhoneNumber":
            # record.request.PhoneNumber is a string in the model; cast for safety
            from typing import cast

            item["Value"] = int(record.request.PhoneNumber)

    # Handle wrong_amount scenario
    if scenario == "wrong_amount" and state.current_recording:
        # Use custom amount if set
        custom_amount = getattr(state, "custom_amount", None)
        if custom_amount:
            for item in metadata["Item"]:
                if item["Name"] == "Amount":
                    item["Value"] = custom_amount

    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": record.merchant_request_id,
                "CheckoutRequestID": record.checkout_request_id,
                "ResultCode": int(str(scenario_config["result_code"])),
                "ResultDesc": str(scenario_config["result_desc"]),
                "CallbackMetadata": metadata,
            },
        },
    }


async def send_callback(record: STKRequestRecord, scenario: str):
    """Send async callback to the configured URL."""
    await asyncio.sleep(state.current_scenario_delay)

    callback_payload = build_callback(record, scenario)

    # Log the callback
    state.event_log.append({
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "callback_sent",
        "checkout_request_id": record.checkout_request_id,
        "scenario": scenario,
        "payload": callback_payload,
    })

    if state.current_recording:
        state.current_recording.events.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "type": "callback",
            "checkout_request_id": record.checkout_request_id,
            "payload": callback_payload,
        })

    record.callback_sent = True
    record.callback_data = callback_payload

    # In a real implementation, we'd HTTP POST to callback_url
    # For the fake, we just log it and the test can poll /query


# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="Fake Daraja Server",
    description="M-Pesa STK Push Simulator for SocialERP",
    version="0.1.0",
)

# Hold strong references to background callback tasks so they are not GC'd.
_background_tasks: set[asyncio.Task] = set()


# Add scenario delay as state attribute
state.current_scenario_delay = 0


# =============================================================================
# Endpoints
# =============================================================================


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "fake-daraja"}


@app.post("/stkpush", response_model=STKPushResponse)
async def stk_push(request: STKPushRequest):
    """Initiate STK Push (Lipa Na M-Pesa Online)."""
    # Validate request
    if request.BusinessShortCode != state.shortcode:
        raise HTTPException(status_code=400, detail="Invalid BusinessShortCode")

    if request.Amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    # Generate IDs
    checkout_request_id = generate_checkout_request_id()
    merchant_request_id = generate_merchant_request_id()

    # Determine scenario
    scenario = state.current_scenario

    # Create record
    record = STKRequestRecord(
        checkout_request_id=checkout_request_id,
        merchant_request_id=merchant_request_id,
        request=request,
        scenario=scenario,
        status="pending",
    )
    state.stk_records[checkout_request_id] = record

    # Log event
    state.event_log.append({
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "stk_push",
        "checkout_request_id": checkout_request_id,
        "amount": request.Amount,
        "phone": request.PhoneNumber,
        "scenario": scenario,
    })

    if state.current_recording:
        state.current_recording.events.append({
            "timestamp": datetime.now(UTC).isoformat(),
            "type": "stk_push",
            "checkout_request_id": checkout_request_id,
            "request": request.model_dump(),
            "scenario": scenario,
        })

    # Schedule async callback for non-timeout scenarios
    if scenario != "timeout":
        task = asyncio.create_task(send_callback(record, scenario))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return STKPushResponse(
        MerchantRequestID=merchant_request_id,
        CheckoutRequestID=checkout_request_id,
        ResponseCode="0",
        ResponseDescription="Success. Request accepted for processing",
        CustomerMessage="Success. Request accepted for processing",
    )


@app.post("/callback")
async def receive_callback(request: Request):
    """Receive callback (for testing callback handling)."""
    body = await request.json()

    state.event_log.append({
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "callback_received",
        "payload": body,
    })

    return {"ResultCode": 0, "ResultDesc": "Callback received successfully"}


@app.post("/query")
async def query_status(request: QueryRequest):
    """Query STK Push transaction status."""
    record = state.stk_records.get(request.CheckoutRequestID)

    if not record:
        raise HTTPException(status_code=404, detail="CheckoutRequestID not found")

    # Build response based on current status
    if record.status == "completed" and record.callback_data:
        callback = record.callback_data
        result_code = callback["Body"]["stkCallback"]["ResultCode"]
        result_desc = callback["Body"]["stkCallback"]["ResultDesc"]
    elif record.status == "pending":
        result_code = 1001  # Still pending
        result_desc = "Transaction is being processed"
    else:
        result_code = 1
        result_desc = "Failed"

    state.event_log.append({
        "timestamp": datetime.now(UTC).isoformat(),
        "type": "query_status",
        "checkout_request_id": request.CheckoutRequestID,
        "result_code": result_code,
    })

    return {
        "ResponseCode": "0",
        "ResponseDescription": "Success",
        "MerchantRequestID": record.merchant_request_id,
        "CheckoutRequestID": record.checkout_request_id,
        "ResultCode": str(result_code),
        "ResultDesc": result_desc,
    }


@app.post("/admin/scenario")
async def set_scenario(config: ScenarioConfig):
    """Set the current scenario for STK Push simulations."""
    if config.scenario not in SCENARIOS and config.scenario not in [
        "duplicate",
        "late_callback",
        "wrong_amount",
    ]:
        raise HTTPException(status_code=400, detail=f"Unknown scenario: {config.scenario}")

    state.current_scenario = config.scenario
    state.current_scenario_delay = config.delay_seconds

    if config.custom_amount is not None:
        state.custom_amount = config.custom_amount

    return {
        "scenario": state.current_scenario,
        "delay_seconds": state.current_scenario_delay,
        "custom_amount": getattr(state, "custom_amount", None),
    }


@app.get("/admin/scenario")
async def get_scenario():
    """Get current scenario."""
    return {
        "scenario": state.current_scenario,
        "delay_seconds": state.current_scenario_delay,
        "custom_amount": getattr(state, "custom_amount", None),
    }


@app.post("/admin/record")
async def start_recording(name: str = "recording"):
    """Start recording event sequence."""
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
        {"id": r.id, "name": r.name, "created_at": r.created_at.isoformat(), "count": len(r.events)}
        for r in state.recordings.values()
    ]


@app.get("/admin/replay/{recording_id}")
async def replay_recording(recording_id: str):
    """Replay a recorded event sequence."""
    recording = state.recordings.get(recording_id)
    if not recording:
        raise HTTPException(status_code=404, detail="Recording not found")

    return {
        "recording_id": recording.id,
        "name": recording.name,
        "created_at": recording.created_at.isoformat(),
        "events": recording.events,
    }


@app.delete("/admin/recordings/{recording_id}")
async def delete_recording(recording_id: str):
    """Delete a recording."""
    if recording_id in state.recordings:
        del state.recordings[recording_id]
    if state.current_recording and state.current_recording.id == recording_id:
        state.current_recording = None
    return {"status": "deleted"}


@app.get("/admin/events")
async def get_event_log(limit: int = 100):
    """Get recent event log."""
    return state.event_log[-limit:]


@app.delete("/admin/events")
async def clear_event_log():
    """Clear event log."""
    state.event_log.clear()
    return {"status": "cleared"}


@app.get("/admin/stk-records")
async def get_stk_records():
    """Get all STK Push records."""
    return {
        k: {
            "checkout_request_id": v.checkout_request_id,
            "merchant_request_id": v.merchant_request_id,
            "amount": v.request.Amount,
            "phone": v.request.PhoneNumber,
            "scenario": v.scenario,
            "status": v.status,
            "callback_sent": v.callback_sent,
            "created_at": v.created_at.isoformat(),
        }
        for k, v in state.stk_records.items()
    }


@app.post("/admin/reset")
async def reset_state():
    """Reset all state (for testing)."""
    global state
    state = FakeDarajaState()
    return {"status": "reset"}


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8082)
