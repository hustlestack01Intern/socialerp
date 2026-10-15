"""
WhatsApp Domain Models

Domain models for WhatsApp Business API integration.
All models include tenant_id for RLS isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from packages.core.domain import CustomerPhone, TenantId

# =============================================================================
# Enums
# =============================================================================


class WhatsAppMessageType(str, Enum):
    """WhatsApp message types."""

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


class WhatsAppMessageStatus(str, Enum):
    """WhatsApp message delivery status."""

    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    DELETED = "deleted"


class WhatsAppConversationState(str, Enum):
    """Conversation state machine."""

    OPEN = "open"
    AWAITING_RESPONSE = "awaiting_response"
    HANDOFF_REQUESTED = "handoff_requested"
    HANDOFF_ACTIVE = "handoff_active"
    CLOSED = "closed"
    BLOCKED = "blocked"


class WhatsAppTemplateCategory(str, Enum):
    """WhatsApp template categories."""

    MARKETING = "MARKETING"
    UTILITY = "UTILITY"
    AUTHENTICATION = "AUTHENTICATION"


class WhatsAppTemplateStatus(str, Enum):
    """WhatsApp template approval status."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAUSED = "PAUSED"
    DELETED = "DELETED"


# =============================================================================
# Contact Model
# =============================================================================


class WhatsAppContact(BaseModel):
    """WhatsApp contact (customer) within a tenant."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: TenantId
    phone: CustomerPhone
    wa_id: str  # WhatsApp's internal ID for this contact
    name: str | None = None
    profile_picture_url: str | None = None

    # 24h window tracking
    last_customer_message_at: datetime | None = None
    window_expires_at: datetime | None = None

    # Consent & preferences
    opted_in_marketing: bool = False
    opted_in_at: datetime | None = None
    blocked: bool = False
    blocked_at: datetime | None = None
    blocked_reason: str | None = None

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_window_open(self) -> bool:
        """Check if 24h customer service window is open."""
        if self.window_expires_at is None:
            return False
        return datetime.now(UTC) < self.window_expires_at

    def update_window(self) -> None:
        """Update 24h window on customer message."""
        now = datetime.now(UTC)
        self.last_customer_message_at = now
        self.window_expires_at = now.replace(hour=now.hour + 24)
        self.updated_at = now


# =============================================================================
# Message Models
# =============================================================================


class WhatsAppMessageBase(BaseModel):
    """Base fields for WhatsApp message."""

    tenant_id: TenantId
    contact_id: UUID
    conversation_id: UUID
    direction: str  # "inbound" or "outbound"
    message_type: WhatsAppMessageType
    status: WhatsAppMessageStatus = WhatsAppMessageStatus.SENT

    # Content (varies by type)
    text_body: str | None = None
    media_url: str | None = None
    media_mime_type: str | None = None
    media_sha256: str | None = None
    media_caption: str | None = None

    # Interactive/Button/Template data
    interactive_data: dict[str, Any] | None = None
    template_name: str | None = None
    template_language: str | None = None
    template_components: list[dict[str, Any]] | None = None

    # Context
    context_message_id: UUID | None = None  # For replies
    referenced_message_id: UUID | None = None  # For forwarded/quoted

    # Meta fields
    meta_message_id: str | None = None  # WhatsApp's message ID (e.g., "wamid.xxx")
    meta_timestamp: datetime | None = None  # WhatsApp's timestamp

    # Processing
    is_echo: bool = False  # True if this is an echo of our own outbound
    processing_error: str | None = None

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)


class WhatsAppMessage(WhatsAppMessageBase):
    """WhatsApp message with database fields."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    failed_at: datetime | None = None


class WhatsAppMessageCreate(WhatsAppMessageBase):
    """Input for creating a WhatsApp message."""


class WhatsAppMessageUpdate(BaseModel):
    """Input for updating a WhatsApp message."""

    status: WhatsAppMessageStatus | None = None
    delivered_at: datetime | None = None
    read_at: datetime | None = None
    failed_at: datetime | None = None
    processing_error: str | None = None


# =============================================================================
# Conversation Models
# =============================================================================


class WhatsAppConversationBase(BaseModel):
    """Base fields for WhatsApp conversation."""

    tenant_id: TenantId
    contact_id: UUID
    state: WhatsAppConversationState = WhatsAppConversationState.OPEN

    # Handoff
    handoff_requested_at: datetime | None = None
    handoff_reason: str | None = None
    handoff_assigned_to: UUID | None = None  # User ID of owner/staff
    handoff_accepted_at: datetime | None = None

    # SLA
    first_response_due_at: datetime | None = None
    last_agent_message_at: datetime | None = None
    last_customer_message_at: datetime | None = None

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class WhatsAppConversation(WhatsAppConversationBase):
    """WhatsApp conversation with database fields."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None


class WhatsAppConversationCreate(WhatsAppConversationBase):
    """Input for creating a WhatsApp conversation."""


class WhatsAppConversationUpdate(BaseModel):
    """Input for updating a WhatsApp conversation."""

    state: WhatsAppConversationState | None = None
    handoff_requested_at: datetime | None = None
    handoff_reason: str | None = None
    handoff_assigned_to: UUID | None = None
    handoff_accepted_at: datetime | None = None
    first_response_due_at: datetime | None = None
    tags: list[str] | None = None


# =============================================================================
# Template Models
# =============================================================================


class WhatsAppTemplateComponent(BaseModel):
    """WhatsApp template component (header, body, footer, buttons)."""

    type: str  # "HEADER", "BODY", "FOOTER", "BUTTONS"
    format: str | None = None  # "TEXT", "IMAGE", "VIDEO", "DOCUMENT", "LOCATION"
    text: str | None = None
    example: dict[str, Any] | None = None
    buttons: list[dict[str, Any]] | None = None


class WhatsAppTemplateBase(BaseModel):
    """Base fields for WhatsApp template."""

    tenant_id: TenantId
    name: str = Field(min_length=1, max_length=512)
    category: WhatsAppTemplateCategory
    language: str = Field(default="en", pattern=r"^[a-z]{2}(_[A-Z]{2})?$")

    # Content
    header: WhatsAppTemplateComponent | None = None
    body: WhatsAppTemplateComponent
    footer: WhatsAppTemplateComponent | None = None
    buttons: list[WhatsAppTemplateComponent] = Field(default_factory=list)

    # Status
    status: WhatsAppTemplateStatus = WhatsAppTemplateStatus.PENDING
    meta_template_id: str | None = None
    rejection_reason: str | None = None

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)


class WhatsAppTemplate(WhatsAppTemplateBase):
    """WhatsApp template with database fields."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_at: datetime | None = None
    rejected_at: datetime | None = None


class WhatsAppTemplateCreate(WhatsAppTemplateBase):
    """Input for creating a WhatsApp template."""


class WhatsAppTemplateUpdate(BaseModel):
    """Input for updating a WhatsApp template."""

    category: WhatsAppTemplateCategory | None = None
    header: WhatsAppTemplateComponent | None = None
    body: WhatsAppTemplateComponent | None = None
    footer: WhatsAppTemplateComponent | None = None
    buttons: list[WhatsAppTemplateComponent] | None = None
    status: WhatsAppTemplateStatus | None = None


# =============================================================================
# Dedupe Model
# =============================================================================


class WhatsAppDedupe(BaseModel):
    """Deduplication record for idempotent webhook processing."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: TenantId
    provider_message_id: str  # WhatsApp's message ID (wamid.xxx)
    processed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message_id: UUID | None = None  # Link to created message

    @property
    def composite_key(self) -> str:
        """Unique key for dedupe lookup."""
        return f"{self.tenant_id}:{self.provider_message_id}"


# =============================================================================
# Webhook Payload Models (for parsing inbound)
# =============================================================================


class WhatsAppWebhookContact(BaseModel):
    """Contact in webhook payload."""

    profile: dict[str, Any] | None = None
    wa_id: str


class WhatsAppWebhookMessage(BaseModel):
    """Message in webhook payload."""

    from_: str = Field(alias="from")
    id: str
    timestamp: str
    type: str
    text: dict[str, str] | None = None
    image: dict[str, Any] | None = None
    document: dict[str, Any] | None = None
    audio: dict[str, Any] | None = None
    video: dict[str, Any] | None = None
    sticker: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    contacts: list[dict[str, Any]] | None = None
    interactive: dict[str, Any] | None = None
    button: dict[str, Any] | None = None
    reaction: dict[str, Any] | None = None
    order: dict[str, Any] | None = None
    system: dict[str, Any] | None = None
    context: dict[str, Any] | None = None


class WhatsAppWebhookChange(BaseModel):
    """Change in webhook entry."""

    value: dict[str, Any]
    field: str


class WhatsAppWebhookEntry(BaseModel):
    """Entry in webhook payload."""

    id: str
    changes: list[WhatsAppWebhookChange]


class WhatsAppWebhookPayload(BaseModel):
    """Full WhatsApp webhook payload."""

    object: str
    entry: list[WhatsAppWebhookEntry]


# =============================================================================
# Outbound Request Models
# =============================================================================


class WhatsAppSendTextRequest(BaseModel):
    """Request to send text message."""

    to: CustomerPhone
    body: str
    preview_url: bool = False


class WhatsAppSendTemplateRequest(BaseModel):
    """Request to send template message."""

    to: CustomerPhone
    template_name: str
    language_code: str = "en"
    components: list[dict[str, Any]] = Field(default_factory=list)


class WhatsAppSendMediaRequest(BaseModel):
    """Request to send media message."""

    to: CustomerPhone
    media_type: str  # "image", "document", "audio", "video", "sticker"
    media_url: str
    caption: str | None = None
    filename: str | None = None


class WhatsAppSendInteractiveRequest(BaseModel):
    """Request to send interactive message (buttons, list)."""

    to: CustomerPhone
    type: str  # "button", "list", "product", "product_list"
    header: dict[str, Any] | None = None
    body: dict[str, Any]
    footer: dict[str, Any] | None = None
    action: dict[str, Any]


class WhatsAppSendResponse(BaseModel):
    """Response from WhatsApp send API."""

    messaging_product: str
    contacts: list[dict[str, str]]
    messages: list[dict[str, str]]
