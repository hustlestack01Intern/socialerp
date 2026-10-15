"""
WhatsApp Domain Models for SocialERP

Re-exports from core domain whatsapp package.
"""

from packages.core.domain.whatsapp import (
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppConversationBase,
    WhatsAppConversationCreate,
    WhatsAppConversationState,
    WhatsAppConversationUpdate,
    WhatsAppDedupe,
    WhatsAppMessage,
    WhatsAppMessageBase,
    WhatsAppMessageCreate,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
    WhatsAppMessageUpdate,
    WhatsAppTemplate,
    WhatsAppTemplateBase,
    WhatsAppTemplateCategory,
    WhatsAppTemplateComponent,
    WhatsAppTemplateCreate,
    WhatsAppTemplateStatus,
    WhatsAppTemplateUpdate,
)

__all__ = [
    "WhatsAppContact",
    "WhatsAppConversation",
    "WhatsAppConversationBase",
    "WhatsAppConversationCreate",
    "WhatsAppConversationState",
    "WhatsAppConversationUpdate",
    "WhatsAppDedupe",
    "WhatsAppMessage",
    "WhatsAppMessageBase",
    "WhatsAppMessageCreate",
    "WhatsAppMessageStatus",
    "WhatsAppMessageType",
    "WhatsAppMessageUpdate",
    "WhatsAppTemplate",
    "WhatsAppTemplateBase",
    "WhatsAppTemplateCategory",
    "WhatsAppTemplateComponent",
    "WhatsAppTemplateCreate",
    "WhatsAppTemplateStatus",
    "WhatsAppTemplateUpdate",
]
