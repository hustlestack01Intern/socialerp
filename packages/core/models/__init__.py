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
from packages.core.models.billing import (
    BillingPaymentReference,
    PlanTier,
    Subscription,
    SubscriptionStatus,
    UsageCounter,
    UsageEvent,
)
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
from packages.core.models.instagram import (
    InstagramContact,
    InstagramConversation,
    InstagramConversationState,
    InstagramDedupe,
    InstagramDmEligibility,
    InstagramMessage,
    InstagramMessageStatus,
    InstagramMessageType,
)
from packages.core.models.invites import (
    InviteCode,
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
from packages.core.onboarding.model import (
    FallbackType,
    OnboardingState,
    OnboardingWizardProgress,
)

__all__ = [
    "AlertSeverity",
    "AlertStatus",
    "ApiKey",
    "ApiKeyScope",
    "AuditLog",
    "Base",
    "BillingPaymentReference",
    "Category",
    "ConsentRecord",
    "Customer",
    "CustomerEvent",
    "CustomerTrait",
    "Delivery",
    "DeliveryZone",
    "FallbackType",
    "InstagramContact",
    "InstagramConversation",
    "InstagramConversationState",
    "InstagramDedupe",
    "InstagramDmEligibility",
    "InstagramMessage",
    "InstagramMessageStatus",
    "InstagramMessageType",
    "InviteCode",
    "Location",
    "OnboardingState",
    "OnboardingWizardProgress",
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
    "PlanTier",
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
    "Subscription",
    "SubscriptionStatus",
    "Supplier",
    "Tenant",
    "UsageCounter",
    "UsageEvent",
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
