"""
SQLAlchemy Models Package

Re-export all models for convenience.
"""

from packages.core.models.alert import (
    AlertSeverity,
    AlertStatus,
    OwnerAlert,
)
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
from packages.core.models.crm import (
    ConsentRecord,
    CustomerEvent,
    CustomerTrait,
)
from packages.core.models.delivery import (
    Delivery,
    DeliveryZone,
    Rider,
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
from packages.core.models.payment import (
    Payment,
    PaymentMethodType,
    PaymentRecordStatus,
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
    "AlertSeverity",
    "AlertStatus",
    "ApiKey",
    "ApiKeyScope",
    "AuditLog",
    "Base",
    "Category",
    "ConsentRecord",
    "Customer",
    "CustomerEvent",
    "CustomerTrait",
    "Delivery",
    "DeliveryZone",
    "Location",
    "Order",
    "OrderItem",
    "OrderStatus",
    "OrderType",
    "OutboxEvent",
    "OwnerAlert",
    "Payment",
    "PaymentMethod",
    "PaymentMethodType",
    "PaymentRecordStatus",
    "PaymentStatus",
    "Product",
    "ProductStatus",
    "ProductVariant",
    "Reservation",
    "ReservationReason",
    "ReservationStatus",
    "Rider",
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
