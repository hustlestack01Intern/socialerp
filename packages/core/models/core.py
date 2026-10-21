"""
Core SQLAlchemy Models

Core models for tenants, users, audit logs, and API keys.
"""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    UUID,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.core.models.base import Base, enum_values

# =============================================================================
# Enums
# =============================================================================


class UserRole(str, enum.Enum):
    """User roles within a tenant."""

    OWNER = "owner"
    STAFF = "staff"
    RIDER = "rider"


class ApiKeyScope(str, enum.Enum):
    """API key scopes."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


# =============================================================================
# Tenant Model
# =============================================================================


class Tenant(Base):
    """Tenant (business) - root of multi-tenant hierarchy."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    owner_email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optional public slug (onboarding/URLs). Nullable + unique; Postgres allows
    # multiple NULLs under a unique index. See ADR 0008.
    slug: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        unique=True,
        index=True,
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    # Relationships
    users: Mapped[list["User"]] = relationship(
        "User",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        "ApiKey",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    # Indexes
    __table_args__ = (
        Index("ix_tenants_owner_email", "owner_email"),
        Index("ix_tenants_is_active", "is_active"),
    )


# =============================================================================
# User Model
# =============================================================================


class User(Base):
    """User (owner or staff) belonging to a tenant."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Optional profile fields (product intent: owner/staff profiles). See ADR 0008.
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        # `values_callable=enum_values` persists the lowercase *value*
        # ("owner"), not the member name ("OWNER"): the migration chain
        # created `users.role` as a Postgres enum with lowercase labels and
        # a 'staff' server_default, so without this the ORM wrote a value the
        # database refused (found by the M1.5 sales-spine proof, 2026-10-21).
        Enum(UserRole, native_enum=False, values_callable=enum_values),
        nullable=False,
        default=UserRole.STAFF,
        server_default="staff",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="users",
        lazy="joined",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="user",
        lazy="dynamic",
    )

    # Constraints & Indexes
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index("ix_users_tenant_id_is_active", "tenant_id", "is_active"),
    )


# =============================================================================
# Audit Log Model
# =============================================================================


class AuditLog(Base):
    """Immutable audit log for all tenant actions."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # Agent-action audit fields (ADR 0008): the constitution requires every agent
    # action to record actor, decision, the rule that allowed it, and confidence.
    actor_type: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    # `resource` is nullable: agent-action entries use `payload`/`decision`
    # instead of a CRUD resource.
    resource: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    rule_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    after_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="audit_logs",
        lazy="joined",
    )
    user: Mapped["User | None"] = relationship(
        "User",
        back_populates="audit_logs",
        lazy="joined",
    )

    # Indexes for query patterns
    __table_args__ = (
        Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_log_tenant_action", "tenant_id", "action"),
        Index("ix_audit_log_tenant_resource", "tenant_id", "resource", "resource_id"),
        Index("ix_audit_log_user_created", "user_id", "created_at"),
    )


# =============================================================================
# API Key Model
# =============================================================================


class ApiKey(Base):
    """API key for programmatic access."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship(
        "Tenant",
        back_populates="api_keys",
        lazy="joined",
    )

    # Indexes
    __table_args__ = (
        Index("ix_api_keys_tenant_id_revoked", "tenant_id", "revoked_at"),
        Index("ix_api_keys_expires_at", "expires_at"),
    )


# =============================================================================
# Outbox Event Model
# =============================================================================


class OutboxEvent(Base):
    """Outbox event for reliable message delivery."""

    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    retry_count: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
        server_default="0",
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_outbox_events_tenant_created", "tenant_id", "created_at"),
        Index("ix_outbox_events_processed", "processed_at"),
    )


# =============================================================================
# Enums (re-export for convenience)
# =============================================================================


__all__ = [
    "ApiKey",
    "ApiKeyScope",
    "AuditLog",
    "OutboxEvent",
    "Tenant",
    "User",
    "UserRole",
]
