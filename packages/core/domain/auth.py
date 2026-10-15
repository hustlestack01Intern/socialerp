"""
Auth Domain Models

Pydantic models for authentication domain.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TokenPair(BaseModel):
    """Access and refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class TokenPayload(BaseModel):
    """JWT token payload."""

    sub: str  # user_id
    tenant_id: str
    roles: list[str]
    type: str  # "access" or "refresh"
    exp: int
    iat: int
    jti: str


class LoginRequest(BaseModel):
    """Login request."""

    email: str
    password: str


class RefreshRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str


class ApiKeyBase(BaseModel):
    """Base API key fields."""

    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=list)


class ApiKeyCreate(ApiKeyBase):
    """Input for creating an API key."""

    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ApiKeyResponse(ApiKeyBase):
    """API key response (includes the plain key only on creation)."""

    id: UUID
    tenant_id: UUID
    key: str | None = None  # Only present on creation
    key_prefix: str
    expires_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ApiKeyList(BaseModel):
    """Paginated API key list."""

    items: list[ApiKeyResponse]
    total: int
    page: int
    page_size: int


class TokenBlacklistEntry(BaseModel):
    """Blacklisted token entry."""

    jti: str
    token_type: str  # "access" or "refresh"
    expires_at: datetime
    reason: str = "logout"
