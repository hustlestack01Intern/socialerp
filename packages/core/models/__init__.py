"""
SQLAlchemy Models Package

Re-export all models for convenience.
"""

from packages.core.models.base import Base
from packages.core.models.catalog import (
    Category,
    Location,
    Product,
    ProductStatus,
    ProductVariant,
    Supplier,
    VariantAttributeType,
)
from packages.core.models.core import (
    ApiKey,
    ApiKeyScope,
    AuditLog,
    OutboxEvent,
    Tenant,
    User,
    UserRole,
)
from packages.core.models.order import (
    Customer,
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    PaymentMethod,
    PaymentStatus,
)
from packages.core.models.reservation import (
    Reservation,
    ReservationReason,
    ReservationStatus,
)
from packages.core.models.stock import (
    StockMovement,
    StockMovementReason,
    StockMovementType,
)
from packages.core.models.whatsapp import (
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppConversationState,
    WhatsAppDedupe,
    WhatsAppMessage,
    WhatsAppMessageStatus,
    WhatsAppMessageType,
    WhatsAppTemplate,
    WhatsAppTemplateCategory,
    WhatsAppTemplateStatus,
)

__all__ = [
    "ApiKey",
    "ApiKeyScope",
    "AuditLog",
    "Base",
    "Category",
    "Customer",
    "Location",
    "Order",
    "OrderItem",
    "OrderStatus",
    "OrderType",
    "OutboxEvent",
    "PaymentMethod",
    "PaymentStatus",
    "Product",
    "ProductStatus",
    "ProductVariant",
    "Reservation",
    "ReservationReason",
    "ReservationStatus",
    "StockMovement",
    "StockMovementReason",
    "StockMovementType",
    "Supplier",
    "Tenant",
    "User",
    "UserRole",
    "VariantAttributeType",
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
