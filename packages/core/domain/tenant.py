"""
Tenant Domain Models

Pydantic models for tenant domain (separate from SQLAlchemy models).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TenantSettings(BaseModel):
    """Tenant configuration settings."""

    model_config = ConfigDict(extra="forbid")

    # Business settings
    business_name: str = ""
    business_description: str = ""
    currency: str = "KES"
    timezone: str = "Africa/Nairobi"

    # Delivery settings
    delivery_areas: list[str] = Field(default_factory=list)
    delivery_hours_start: str = "08:00"
    delivery_hours_end: str = "20:00"
    default_delivery_fee_cents: int = 0
    free_delivery_threshold_cents: int = 0

    # Payment settings
    payment_providers: list[str] = Field(default_factory=list)  # daraja, aggregator, manual
    mpesa_till_number: str = ""
    mpesa_consumer_key: str = ""
    mpesa_consumer_secret: str = ""
    mpesa_passkey: str = ""
    mpesa_callback_url: str = ""

    # Agent settings
    autonomy_level: str = "L0"  # L0, L1, L2, L3
    agent_tone: str = "friendly"  # friendly, professional, casual
    auto_reply_enabled: bool = True
    human_handoff_threshold: float = 0.7

    # Inventory settings
    low_stock_threshold: int = 5
    hide_out_of_stock: bool = True
    max_discount_percent: int = 10

    # Feature flags
    feature_whatsapp: bool = True
    feature_instagram: bool = False
    feature_tiktok: bool = False
    feature_pay_on_delivery: bool = True
    feature_owner_console: bool = True


class TenantBase(BaseModel):
    """Base tenant fields."""

    name: str = Field(min_length=1, max_length=200)
    owner_phone: str = Field(pattern=r"^254[17]\d{8}$")
    owner_email: EmailStr
    settings: TenantSettings = Field(default_factory=TenantSettings)


class TenantCreate(TenantBase):
    """Input for creating a tenant."""


class TenantUpdate(BaseModel):
    """Input for updating a tenant."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    owner_phone: str | None = Field(default=None, pattern=r"^254[17]\d{8}$")
    owner_email: EmailStr | None = None
    settings: TenantSettings | None = None
    is_active: bool | None = None


class Tenant(TenantBase):
    """Tenant with database fields."""

    id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TenantList(BaseModel):
    """Paginated tenant list."""

    items: list[Tenant]
    total: int
    page: int
    page_size: int
