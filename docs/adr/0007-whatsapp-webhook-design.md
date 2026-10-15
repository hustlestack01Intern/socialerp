# ADR 0007: WhatsApp Webhook Signature Verification Design

## Status
Accepted

## Context
WhatsApp Cloud API sends webhooks to our endpoint for inbound messages, status updates, and other events. Meta requires HMAC-SHA256 signature verification using the app secret to prevent forged webhooks.

## Decision
Implement signature verification at the webhook entry point:
1. Read raw request body before JSON parsing
2. Verify `X-Hub-Signature-256` header using HMAC-SHA256 with app secret
3. Use constant-time comparison (`hmac.compare_digest`) to prevent timing attacks
4. Return 401 immediately for invalid signatures
5. Log invalid attempts for security monitoring

## Implementation
```python
def verify_hmac_sha256(payload: bytes, signature: str, secret: str) -> bool:
    if not signature.startswith("sha256="):
        return False
    expected = signature[7:]
    computed = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, expected)
```

## Security Considerations
- Never log the raw payload or signature
- Reject requests without signature header
- Return 401 for all signature failures (don't distinguish between missing/invalid)
- Log security events for audit trail

## Testing
- Valid signature with correct secret → 200 OK
- Invalid secret → 401
- Missing header → 401
- Modified payload → 401
- Missing `sha256=` prefix → 401

## References
- Meta Webhook Security Documentation
- SPEC §3 Integration Truth Table (WhatsApp)
- SPEC §12 Security, Privacy, Compliance