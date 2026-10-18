"""
Signed, expiring capability tokens for the public tracking endpoints (U29).

The public tracking routes (``GET /tracking/{order_id}`` and
``GET /api/v1/tracking/{order_id}``) carry no tenant context, so a bare
order-UUID lookup would serve ANY order whose unguessable UUID is known —
including another tenant's order. To fail closed, every tracking URL must
carry a token minted here:

    /tracking/{order_id}?t=<token>

Token format (URL-safe, three ``.``-separated parts)::

    v1.<expires_unix>.<urlsafe_b64 hmac-sha256 digest, no padding>

The signature is HMAC-SHA256 over the canonical message::

    v1:{tenant_id.hex}:{order_id.hex}:{expires_unix}

so the token is bound to BOTH the tenant and the order: a token minted for
tenant A's order cannot validate against tenant B's order (or any other
order), and tampering with any component invalidates the signature. The
signing key is ``TRACKING_TOKEN_SECRET`` (falling back to ``SECRET_KEY``).

Verification is constant-time (``hmac.compare_digest``) and fail-closed:
callers must treat ``False`` exactly like "order not found" — never leak
whether the order exists.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

from socialerp.settings import get_settings

_TOKEN_VERSION = "v1"


def _signing_key() -> bytes:
    settings = get_settings()
    secret = settings.TRACKING_TOKEN_SECRET or settings.SECRET_KEY
    return secret.encode("utf-8")


def _canonical_message(tenant_id: UUID, order_id: UUID, expires_unix: int) -> bytes:
    return f"{_TOKEN_VERSION}:{tenant_id.hex}:{order_id.hex}:{expires_unix}".encode()


def sign_tracking_token(
    tenant_id: UUID,
    order_id: UUID,
    *,
    ttl: timedelta | None = None,
    now: datetime | None = None,
) -> str:
    """Mint a signed, expiring tracking token bound to (tenant, order).

    ``ttl`` defaults to ``TRACKING_TOKEN_TTL_DAYS`` (30 days); ``now`` and
    ``ttl`` are overridable for tests (e.g. mint an already-expired token).
    """
    settings = get_settings()
    if ttl is None:
        ttl = timedelta(days=settings.TRACKING_TOKEN_TTL_DAYS)
    if now is None:
        now = datetime.now(UTC)
    expires_unix = int((now + ttl).timestamp())
    digest = hmac.new(
        _signing_key(),
        _canonical_message(tenant_id, order_id, expires_unix),
        hashlib.sha256,
    ).digest()
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"{_TOKEN_VERSION}.{expires_unix}.{signature}"


def verify_tracking_token(
    token: str | None,
    tenant_id: UUID,
    order_id: UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """True only for a well-formed, unexpired token bound to (tenant, order).

    Fail-closed: ``None``, empty, malformed, tampered, wrong-version,
    wrong-tenant, wrong-order and expired tokens all return ``False``.
    """
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    version, expires_raw, signature = parts
    if version != _TOKEN_VERSION:
        return False
    try:
        expires_unix = int(expires_raw)
    except ValueError:
        return False
    if now is None:
        now = datetime.now(UTC)
    if expires_unix <= int(now.timestamp()):
        return False

    expected = hmac.new(
        _signing_key(),
        _canonical_message(tenant_id, order_id, expires_unix),
        hashlib.sha256,
    ).digest()
    try:
        # Re-pad: the token strips ``=`` padding for URL-safety.
        # ``binascii.Error`` (invalid base64) is a ValueError subclass.
        candidate = base64.b64decode(
            signature + "=" * (-len(signature) % 4), altchars=b"-_", validate=True
        )
    except ValueError:
        return False
    if len(candidate) != len(expected):
        return False
    return hmac.compare_digest(candidate, expected)


def build_tracking_url(base_url: str, tenant_id: UUID, order_id: UUID) -> str:
    """Canonical public tracking URL for an order, token embedded."""
    token = sign_tracking_token(tenant_id, order_id)
    return f"{base_url.rstrip('/')}/tracking/{order_id}?t={token}"


__all__ = ["build_tracking_url", "sign_tracking_token", "verify_tracking_token"]
