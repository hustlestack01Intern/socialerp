"""
WhatsApp SQLAlchemy Models

Database models for WhatsApp integration with RLS support.
"""

from __future__ import annotations

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

from packages.core.models.base import Base
from packages.core.models.core import User

# =============================================================================
# Enums
# =============================================================================


class WhatsAppMessageType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    DOCUMENT = "document"
    AUDIO = "audio"
    VIDEO = "video"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACTS = "contacts"
    INTERACTIVE = "interactive"
    BUTTON = "button"
    TEMPLATE = "template"
    REACTION = "reaction"
    ORDER = "order"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class WhatsAppMessageStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    DELETED = "deleted"


class WhatsAppConversationState(str, enum.Enum):
    OPEN = "open"
    AWAITING_RESPONSE = "awaiting_response"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDOFF_ACTIVE = "handoff_active"
    CLOSED = "closed"
    BLOCKED = "blocked"


class WhatsAppTemplateCategory(str, enum.Enum):
    MARKETING = "MARKETING"
    UTILITY = "UTILITY"
    AUTHENTICATION = "AUTHENTICATION"


class WhatsAppTemplateStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAUSED = "PAUSED"
    DELETED = "DELETED"


# =============================================================================
# WhatsApp Contact Model
# =============================================================================


class WhatsAppContact(Base):
    """WhatsApp contact (customer) within a tenant."""

    __tablename__ = "whatsapp_contacts"

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
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    wa_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 24h window tracking
    last_customer_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    window_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,  # For cleanup queries
    )

    # Consent & preferences
    opted_in_marketing: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    opted_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    blocked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    blocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
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
    messages: Mapped[list[WhatsAppMessage]] = relationship(
        "WhatsAppMessage",
        back_populates="contact",
        lazy="dynamic",
    )
    conversations: Mapped[list[WhatsAppConversation]] = relationship(
        "WhatsAppConversation",
        back_populates="contact",
        lazy="dynamic",
    )

    # Indexes & Constraints
    __table_args__ = (
        UniqueConstraint("tenant_id", "wa_id", name="uq_whatsapp_contacts_tenant_wa_id"),
        UniqueConstraint("tenant_id", "phone", name="uq_whatsapp_contacts_tenant_phone"),
        Index("ix_whatsapp_contacts_tenant_window", "tenant_id", "window_expires_at"),
        Index("ix_whatsapp_contacts_tenant_blocked", "tenant_id", "blocked"),
    )

    def update_window(self) -> None:
        """Update the 24-hour customer service window."""
        from datetime import datetime, timedelta

        now = datetime.now(UTC)
        self.last_customer_message_at = now
        self.window_expires_at = now + timedelta(hours=24)
        self.updated_at = now


# =============================================================================
# WhatsApp Message Model
# =============================================================================


class WhatsAppMessage(Base):
    """WhatsApp message (inbound or outbound)."""

    __tablename__ = "whatsapp_messages"

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
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whatsapp_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )  # "inbound" or "outbound"
    message_type: Mapped[str] = mapped_column(
        Enum(WhatsAppMessageType, native_enum=False),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(WhatsAppMessageStatus, native_enum=False),
        nullable=False,
        default=WhatsAppMessageStatus.SENT,
        server_default="sent",
    )

    # Content
    text_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    media_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Interactive/Template data
    interactive_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    template_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    template_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    template_components: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Context
    context_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whatsapp_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    referenced_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Meta fields
    meta_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    meta_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Processing
    is_echo: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
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
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    contact: Mapped[WhatsAppContact] = relationship(
        "WhatsAppContact",
        back_populates="messages",
        lazy="joined",
    )
    conversation: Mapped[WhatsAppConversation] = relationship(
        "WhatsAppConversation",
        back_populates="messages",
        lazy="joined",
    )

    # Indexes
    __table_args__ = (
        Index("ix_whatsapp_messages_tenant_created", "tenant_id", "created_at"),
        Index("ix_whatsapp_messages_tenant_conversation", "tenant_id", "conversation_id"),
        Index("ix_whatsapp_messages_tenant_meta_id", "tenant_id", "meta_message_id"),
        Index("ix_whatsapp_messages_tenant_status", "tenant_id", "status"),
    )


# =============================================================================
# WhatsApp Conversation Model
# =============================================================================


class WhatsAppConversation(Base):
    """WhatsApp conversation thread."""

    __tablename__ = "whatsapp_conversations"

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
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whatsapp_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        Enum(WhatsAppConversationState, native_enum=False),
        nullable=False,
        default=WhatsAppConversationState.OPEN,
        server_default="open",
    )

    # Handoff
    handoff_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    handoff_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    handoff_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # SLA
    first_response_due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_agent_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_customer_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Metadata
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
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
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    contact: Mapped[WhatsAppContact] = relationship(
        "WhatsAppContact",
        back_populates="conversations",
        lazy="joined",
    )
    messages: Mapped[list[WhatsAppMessage]] = relationship(
        "WhatsAppMessage",
        back_populates="conversation",
        lazy="dynamic",
    )
    assigned_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[handoff_assigned_to],
        lazy="joined",
    )

    # Indexes
    __table_args__ = (
        Index("ix_whatsapp_conversations_tenant_state", "tenant_id", "state"),
        Index("ix_whatsapp_conversations_tenant_contact", "tenant_id", "contact_id"),
        Index("ix_whatsapp_conversations_tenant_handoff", "tenant_id", "handoff_requested_at"),
        Index("ix_whatsapp_conversations_tenant_sla", "tenant_id", "first_response_due_at"),
    )


# =============================================================================
# WhatsApp Template Model
# =============================================================================


class WhatsAppTemplate(Base):
    """WhatsApp message template."""

    __tablename__ = "whatsapp_templates"

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
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(
        Enum(WhatsAppTemplateCategory, native_enum=False),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(10), nullable=False)

    # Content components
    header: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    footer: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    buttons: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )

    # Status
    status: Mapped[str] = mapped_column(
        Enum(WhatsAppTemplateStatus, native_enum=False),
        nullable=False,
        default=WhatsAppTemplateStatus.PENDING,
        server_default="pending",
    )
    meta_template_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
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
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Indexes
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "name", "language", name="uq_whatsapp_templates_tenant_name_lang",
        ),
        Index("ix_whatsapp_templates_tenant_status", "tenant_id", "status"),
    )


# =============================================================================
# WhatsApp Dedupe Model
# =============================================================================


class WhatsAppDedupe(Base):
    """Deduplication record for idempotent webhook processing."""

    __tablename__ = "whatsapp_dedupe"

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
    provider_message_id: Mapped[str] = mapped_column(String(100), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("whatsapp_messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Constraints
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider_message_id", name="uq_whatsapp_dedupe_tenant_provider_id",
        ),
        Index("ix_whatsapp_dedupe_tenant_processed", "tenant_id", "processed_at"),
    )


# =============================================================================
# Export all models
# =============================================================================

__all__ = [
    "WhatsAppContact",
    "WhatsAppConversation",
    "WhatsAppConversationState",
    "WhatsAppDedupe",
    "WhatsAppMessage",
    "WhatsAppMessageStatus",
    "WhatsAppMessageType",
    "WhatsAppTemplate",
    "WhatsAppTemplateCategory",
    "WhatsAppTemplateStatus",
]
