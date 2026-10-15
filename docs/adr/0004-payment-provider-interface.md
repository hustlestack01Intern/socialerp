# ADR 0004: Payment Provider Interface

**Status**: Accepted
**Date**: 2024-01-15
**Deciders**: Chief Engineer

---

## Context

SocialERP supports multiple M-Pesa payment paths per tenant:
1. **Licensed payment aggregator** (Pesapal, IntaSend) - fastest onboarding
2. **Tenant's own Daraja credentials** - full control, requires Safaricom approval
3. **Manual "pay to Till and confirm"** - fallback, human reconciliation

We need a clean abstraction that supports all three without leaking implementation details.

---

## Decision

**PaymentProvider interface** with concrete implementations. Tenant configures provider at onboarding.

---

## Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from uuid import UUID


@dataclass
class STKPushRequest:
    phone_number: str  # Normalized 2547XXXXXXXX
    amount: int  # KSH cents
    account_reference: str  # Order ID
    transaction_desc: str  # "Order #1234"
    callback_url: str  # Our webhook endpoint
    idempotency_key: str  # For deduplication


@dataclass
class STKPushResponse:
    checkout_request_id: str
    merchant_request_id: str
    response_code: str
    response_description: str


@dataclass
class PaymentStatus:
    status: str  # pending, completed, failed, cancelled
    mpesa_receipt_number: Optional[str]
    result_code: Optional[str]
    result_desc: Optional[str]
    amount: Optional[int]


class PaymentProvider(ABC):
    @abstractmethod
    async def initiate_stk_push(self, request: STKPushRequest) -> STKPushResponse:
        """Initiate STK Push. Returns immediately; callback comes later."""

    @abstractmethod
    async def query_status(self, checkout_request_id: str) -> PaymentStatus:
        """Query status (fallback for missing/late callbacks)."""

    @abstractmethod
    async def validate_callback(self, payload: dict, headers: dict) -> bool:
        """Verify callback authenticity (signature, source IP, etc.)."""

    @abstractmethod
    async def parse_callback(self, payload: dict) -> PaymentStatus:
        """Extract standardized status from callback."""

    @abstractmethod
    async def refund(self, mpesa_receipt_number: str, amount: int, reason: str) -> bool:
        """Initiate refund (human-approved only)."""
```

---

## Implementations

### 1. AggregatorProvider (Pesapal/IntaSend)
- Wraps aggregator API
- Handles their callback format
- Aggregator manages Daraja relationship

### 2. DirectDarajaProvider
- Uses tenant's own Daraja credentials
- Direct STK Push to Safaricom
- Handles Daraja callbacks natively

### 3. ManualFallbackProvider
- No API integration
- Generates "pay to Till X" instructions
- Marks paid when staff confirms (with audit log)

---

## Tenant Configuration

```python
class TenantPaymentConfig(BaseModel):
    provider: Literal["aggregator", "direct_daraja", "manual"]
    # Aggregator
    aggregator_name: Optional[Literal["pesapal", "intasend"]] = None
    aggregator_credentials: dict = {}
    # Direct Daraja
    daraja_consumer_key: Optional[str] = None
    daraja_consumer_secret: Optional[str] = None
    daraja_passkey: Optional[str] = None
    daraja_shortcode: Optional[str] = None
    # Manual
    till_number: Optional[str] = None
    confirmation_staff_ids: list[UUID] = []
```

---

## Callback Handling (Critical)

**Never trust callback amounts.** Always match against expected order.

```python
async def handle_callback(provider: PaymentProvider, payload: dict, headers: dict):
    # 1. Verify authenticity
    if not await provider.validate_callback(payload, headers):
        raise SecurityError("Invalid callback signature")

    # 2. Parse status
    status = await provider.parse_callback(payload)

    # 3. Find our order by checkout_request_id
    order = await get_order_by_checkout_id(status.checkout_request_id)

    # 4. MATCH AMOUNT - critical!
    if status.amount != order.total_cents:
        await log_security_event("payment.amount_mismatch", {...})
        raise PaymentError("Callback amount doesn't match order")

    # 5. Idempotency
    await complete_idempotency(key=status.checkout_request_id, scope="payment:callback", ...)

    # 6. Update order state
    await order.transition_to_paid()
```

---

## Failure Handling

| Failure Mode | Detection | Response |
|--------------|-----------|----------|
| User cancelled | `ResultCode=1032` "cancelled" | Mark order `cancelled`, release stock |
| Wrong PIN | `ResultCode=1032` "wrong PIN" | Retry STK Push (max 3), then cancel |
| Insufficient funds | `ResultCode=1` | Notify customer, allow retry |
| Timeout | No callback within 60s | Query status job, then cancel if failed |
| Duplicate callback | Idempotency key exists | Ignore, return 200 |
| Late callback after cancel | Order already cancelled | Log, reconcile, refund if paid |
| Partial payment | Amount < order total | Mark `partial`, notify for balance |

---

## Reconciliation

Daily job comparing:
- Our ledger: completed orders with `mpesa_receipt_number`
- Provider statement: API or CSV export
- Mismatches → alert + escalation

---

## Consequences

### Positive
- Clean separation: business logic doesn't know provider details
- Easy to add new aggregators
- Tenant can switch providers
- Testable with fake implementations

### Negative
- Interface must be stable (version if needed)
- Aggregator-specific features need abstraction or passthrough

---

## Related
- ADR 0001: Stack
- ADR 0003: Agent Autonomy (L2 uses PaymentProvider)
- ADR 0007: Idempotency (payment callbacks)