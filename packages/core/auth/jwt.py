"""
JWT Token Utilities

JWT encoding/decoding for access and refresh tokens.
Uses RS256 for asymmetric signing (production) or HS256 for development.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from jose import jwt
from pydantic import BaseModel

from packages.core.domain.auth import TokenPayload


class JWTSettings(BaseModel):
    """JWT configuration settings."""

    # Algorithm
    algorithm: str = "HS256"  # Use RS256 in production with proper keys

    # Secret key (HS256) - must be changed in production!
    secret_key: str = os.environ.get("APP_SECRET_KEY", "dev-secret-change-in-production")

    # Public/Private keys for RS256 (production)
    private_key: str | None = None
    public_key: str | None = None

    # Token expiry
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # Issuer / Audience
    issuer: str = "socialerp"
    audience: str = "socialerp-api"

    # Key ID for key rotation
    key_id: str = "v1"


# Global settings instance
_settings = JWTSettings()


def get_jwt_settings() -> JWTSettings:
    """Get JWT settings (can be overridden in tests)."""
    return _settings


def configure_jwt(settings: JWTSettings) -> None:
    """Configure JWT settings (for testing or production setup)."""
    global _settings
    _settings = settings


def create_access_token(
    user_id: str,
    tenant_id: str,
    roles: list[str],
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token."""
    settings = get_jwt_settings()

    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)

    now = datetime.now(UTC)
    jti = str(uuid4())

    payload = TokenPayload(
        sub=user_id,
        tenant_id=tenant_id,
        roles=roles,
        type="access",
        exp=int(expire.timestamp()),
        iat=int(now.timestamp()),
        jti=jti,
    )

    # Prepare claims
    claims = payload.model_dump()
    claims["iss"] = settings.issuer
    claims["aud"] = settings.audience

    headers = {"kid": settings.key_id} if settings.algorithm == "RS256" else None

    if settings.algorithm == "RS256" and settings.private_key:
        token = jwt.encode(claims, settings.private_key, algorithm="RS256", headers=headers)
    else:
        token = jwt.encode(claims, settings.secret_key, algorithm="HS256", headers=headers)

    return str(token)


def create_refresh_token(
    user_id: str,
    tenant_id: str,
    roles: list[str],
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT refresh token."""
    settings = get_jwt_settings()

    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)

    now = datetime.now(UTC)
    jti = str(uuid4())

    payload = TokenPayload(
        sub=user_id,
        tenant_id=tenant_id,
        roles=roles,
        type="refresh",
        exp=int(expire.timestamp()),
        iat=int(now.timestamp()),
        jti=jti,
    )

    claims = payload.model_dump()
    claims["iss"] = settings.issuer
    claims["aud"] = settings.audience

    headers = {"kid": settings.key_id} if settings.algorithm == "RS256" else None

    if settings.algorithm == "RS256" and settings.private_key:
        token = jwt.encode(claims, settings.private_key, algorithm="RS256", headers=headers)
    else:
        token = jwt.encode(claims, settings.secret_key, algorithm="HS256", headers=headers)

    return str(token)


def create_token_pair(
    user_id: str,
    tenant_id: str,
    roles: list[str],
) -> tuple[str, str]:
    """Create both access and refresh tokens."""
    access_token = create_access_token(user_id, tenant_id, roles)
    refresh_token = create_refresh_token(user_id, tenant_id, roles)
    return access_token, refresh_token


def decode_token(token: str, token_type: str = "access") -> TokenPayload:
    """Decode and validate a JWT token."""
    settings = get_jwt_settings()

    try:
        if settings.algorithm == "RS256" and settings.public_key:
            payload = jwt.decode(
                token,
                settings.public_key,
                algorithms=[settings.algorithm],
                audience=settings.audience,
                issuer=settings.issuer,
            )
        else:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.algorithm],
                audience=settings.audience,
                issuer=settings.issuer,
            )
    except jwt.ExpiredSignatureError as e:
        raise ValueError("Token has expired") from e
    except jwt.JWTError as e:
        raise ValueError(f"Invalid token: {e}") from e

    # Verify token type
    if payload.get("type") != token_type:
        raise ValueError(f"Expected {token_type} token, got {payload.get('type')}")

    # Validate required fields
    required_fields = ["sub", "tenant_id", "roles", "type", "exp", "iat", "jti"]
    for field in required_fields:
        if field not in payload:
            raise ValueError(f"Missing required field: {field}")

    return TokenPayload(**payload)


def decode_access_token(token: str) -> TokenPayload:
    """Decode and validate an access token."""
    return decode_token(token, "access")


def decode_refresh_token(token: str) -> TokenPayload:
    """Decode and validate a refresh token."""
    return decode_token(token, "refresh")


def get_token_claims(token: str) -> dict[str, Any]:
    """Get raw token claims without validation (for debugging)."""
    get_jwt_settings()
    result = jwt.get_unverified_claims(token)
    return cast(dict[str, Any], result)


def generate_token_id() -> str:
    """Generate a unique token ID (jti)."""
    return str(uuid4())


def generate_secure_secret(length: int = 64) -> str:
    """Generate a secure random secret for JWT signing."""
    return secrets.token_urlsafe(length)
