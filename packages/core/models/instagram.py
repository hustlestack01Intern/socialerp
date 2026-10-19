"""
Instagram SQLAlchemy Models

Database models for Instagram Messaging API integration with RLS support.
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

# =============================================================================
# Enums
# =============================================================================


class InstagramMessageType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"
    STICKER = "sticker"
    LOCATION = "location"
    CONTACTS = "contacts"
    INTERACTIVE = "interactive"
    QUICK_REPLY = "quick_reply"
    STORY_REPLY = "story_reply"
    TEMPLATE = "template"
    UNKNOWN = "unknown"


class InstagramMessageStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    DELETED = "deleted"


class InstagramConversationState(str, enum.Enum):
    OPEN = "open"
    AWAITING_RESPONSE = "awaiting_response"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDOFF_ACTIVE = "handoff_active"
    CLOSED = "closed"
    BLOCKED = "blocked"


# =============================================================================
# Instagram Contact Model
# =============================================================================


class InstagramContact(Base):
    """Instagram contact (customer) within a tenant."""

    __tablename__ = "instagram_contacts"

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
    # Primary identifiers
    ig_scoped_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    ig_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Profile
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_picture_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Thread/conversation identity
    thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # 24h window tracking (same semantics as WhatsApp)
    last_customer_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    window_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
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
    messages: Mapped[list[InstagramMessage]] = relationship(
        "InstagramMessage",
        back_populates="contact",
        lazy="dynamic",
    )
    conversations: Mapped[list[InstagramConversation]] = relationship(
        "InstagramConversation",
        back_populates="contact",
        lazy="dynamic",
    )

    # Indexes & Constraints
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "ig_scoped_user_id",
            name="uq_instagram_contacts_tenant_ig_user_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "ig_handle",
            name="uq_instagram_contacts_tenant_ig_handle",
        ),
        Index("ix_instagram_contacts_tenant_window", "tenant_id", "window_expires_at"),
        Index("ix_instagram_contacts_tenant_thread", "tenant_id", "thread_id"),
    )

    def update_window(self) -> None:
        """Update the 24-hour customer service window."""
        from datetime import datetime, timedelta

        now = datetime.now(UTC)
        self.last_customer_message_at = now
        self.window_expires_at = now + timedelta(hours=24)
        self.updated_at = now


# =============================================================================
# Instagram Message Model
# =============================================================================


class InstagramMessage(Base):
    """Instagram message (inbound or outbound)."""

    __tablename__ = "instagram_messages"

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
        ForeignKey("instagram_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instagram_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )  # "inbound" or "outbound"
    message_type: Mapped[str] = mapped_column(
        Enum(InstagramMessageType, native_enum=False),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum(InstagramMessageStatus, native_enum=False),
        nullable=False,
        default=InstagramMessageStatus.SENT,
        server_default="sent",
    )

    # Content
    text_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    media_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Interactive/Quick reply data
    interactive_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    quick_reply_payload: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Referral (from ads/discover)
    referral_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Context
    context_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instagram_messages.id", ondelete="SET NULL"),
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
    contact: Mapped[InstagramContact] = relationship(
        "InstagramContact",
        back_populates="messages",
        lazy="joined",
    )
    conversation: Mapped[InstagramConversation] = relationship(
        "InstagramConversation",
        back_populates="messages",
        lazy="joined",
    )

    # Indexes
    __table_args__ = (
        Index("ix_instagram_messages_tenant_created", "tenant_id", "created_at"),
        Index("ix_instagram_messages_tenant_conversation", "tenant_id", "conversation_id"),
        Index("ix_instagram_messages_tenant_meta_id", "tenant_id", "meta_message_id"),
        Index("ix_instagram_messages_tenant_status", "tenant_id", "status"),
    )


# =============================================================================
# Instagram Conversation Model
# =============================================================================


class InstagramConversation(Base):
    """Instagram conversation thread."""

    __tablename__ = "instagram_conversations"

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
        ForeignKey("instagram_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        Enum(InstagramConversationState, native_enum=False),
        nullable=False,
        default=InstagramConversationState.OPEN,
        server_default="open",
    )

    # Thread identity
    thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

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
    contact: Mapped[InstagramContact] = relationship(
        "InstagramContact",
        back_populates="conversations",
        lazy="joined",
    )
    messages: Mapped[list[InstagramMessage]] = relationship(
        "InstagramMessage",
        back_populates="conversation",
        lazy="dynamic",
    )

    # Indexes
    __table_args__ = (
        Index("ix_instagram_conversations_tenant_state", "tenant_id", "state"),
        Index("ix_instagram_conversations_tenant_contact", "tenant_id", "contact_id"),
        Index("ix_instagram_conversations_tenant_thread", "tenant_id", "thread_id"),
        Index("ix_instagram_conversations_tenant_handoff", "tenant_id", "handoff_requested_at"),
        Index("ix_instagram_conversations_tenant_sla", "tenant_id", "first_response_due_at"),
    )


# =============================================================================
# Instagram Comment-to-DM Eligibility Model
# =============================================================================


class InstagramDmEligibility(Base):
    """Comment-to-DM private reply eligibility tracking."""

    __tablename__ = "instagram_dm_eligibility"

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
        ForeignKey("instagram_contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Source comment
    comment_id: Mapped[str] = mapped_column(String(100), nullable=False)
    media_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Eligibility state
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    attempts_remaining: Mapped[int] = mapped_column(
        default=1,
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    consumed_by_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instagram_messages.id", ondelete="SET NULL"),
        nullable=True,
    )

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

    # Indexes & Constraints
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "comment_id",
            name="uq_instagram_dm_eligibility_tenant_comment",
        ),
        Index("ix_instagram_dm_eligibility_tenant_expires", "tenant_id", "expires_at"),
    )


# =============================================================================
# Instagram Dedupe Model
# =============================================================================


class InstagramDedupe(Base):
    """Deduplication record for idempotent Instagram webhook processing."""

    __tablename__ = "instagram_dedupe"

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
        ForeignKey("instagram_messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Constraints
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider_message_id",
            name="uq_instagram_dedupe_tenant_provider_id",
        ),
        Index("ix_instagram_dedupe_tenant_processed", "tenant_id", "processed_at"),
    )


# =============================================================================
# Export all models
# =============================================================================

__all__ = [
    "InstagramContact",
    "InstagramConversation",
    "InstagramConversationState",
    "InstagramDedupe",
    "InstagramDmEligibility",
    "InstagramMessage",
    "InstagramMessageStatus",
    "InstagramMessageType",
]
