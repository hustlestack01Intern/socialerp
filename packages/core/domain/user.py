"""
User Domain Models

Pydantic models for user domain (separate from SQLAlchemy models).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRole(str, Enum):
    """User roles within a tenant."""

    OWNER = "owner"
    STAFF = "staff"
    RIDER = "rider"


class UserBase(BaseModel):
    """Base user fields."""

    email: EmailStr
    role: UserRole = UserRole.STAFF
    is_active: bool = True


class UserCreate(UserBase):
    """Input for creating a user."""

    password: str = Field(min_length=12, max_length=128)
    tenant_id: UUID


class UserUpdate(BaseModel):
    """Input for updating a user."""

    email: EmailStr | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)


class User(UserBase):
    """User with database fields."""

    id: UUID
    tenant_id: UUID
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserList(BaseModel):
    """Paginated user list."""

    items: list[User]
    total: int
    page: int
    page_size: int


class UserWithTenant(User):
    """User with tenant info (for auth responses)."""

    tenant: Tenant  # Forward reference


# Import at bottom to avoid circular dependency
from packages.core.domain.tenant import Tenant  # ruff: ignore[module-import-not-at-top-of-file]

UserWithTenant.model_rebuild()
