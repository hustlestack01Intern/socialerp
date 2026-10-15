"""
Domain Package for SocialERP

Re-exports from core domain package and its submodules.
"""

from socialerp.domain.auth import (
    ApiKeyBase,
    ApiKeyCreate,
    ApiKeyList,
    ApiKeyResponse,
    LoginRequest,
    RefreshRequest,
    TokenBlacklistEntry,
    TokenPair,
    TokenPayload,
)
from socialerp.domain.catalog import (
    Category,
    Product,
    ProductVariant,
)
from socialerp.domain.order import (
    CancelOrderRequest,
    CreateOrderRequest,
    Customer,
    DeliveryInfo,
    Order,
    OrderItem,
    OrderStatus,
    OrderType,
    PaymentInfo,
    PaymentMethod,
    PaymentStatus,
    UpdateOrderRequest,
)
from socialerp.domain.reservation import (
    Reservation,
    ReservationManager,
    ReservationReason,
    ReservationStatus,
)
from socialerp.domain.stock import (
    StockLedger,
    StockMovement,
    StockMovementReason,
    StockMovementType,
)
from socialerp.domain.tenant import (
    Tenant,
    TenantCreate,
    TenantList,
    TenantSettings,
    TenantUpdate,
)
from socialerp.domain.user import (
    User,
    UserCreate,
    UserList,
    UserRole,
    UserUpdate,
)
from socialerp.domain.whatsapp import (
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

__all__ = [  # ruff: ignore[unsorted-dunder-all]  (grouped by submodule for readability)
    # auth
    "TokenPair",
    "TokenPayload",
    "LoginRequest",
    "RefreshRequest",
    "ApiKeyBase",
    "ApiKeyCreate",
    "ApiKeyResponse",
    "ApiKeyList",
    "TokenBlacklistEntry",
    # catalog
    "Category",
    "Product",
    "ProductVariant",
    # order
    "Order",
    "OrderItem",
    "OrderStatus",
    "OrderType",
    "PaymentMethod",
    "PaymentStatus",
    "Customer",
    "DeliveryInfo",
    "PaymentInfo",
    "CreateOrderRequest",
    "UpdateOrderRequest",
    "CancelOrderRequest",
    # reservation
    "Reservation",
    "ReservationStatus",
    "ReservationReason",
    "ReservationManager",
    # stock
    "StockLedger",
    "StockMovement",
    "StockMovementReason",
    "StockMovementType",
    # tenant
    "Tenant",
    "TenantSettings",
    "TenantCreate",
    "TenantUpdate",
    "TenantList",
    # user
    "User",
    "UserRole",
    "UserCreate",
    "UserUpdate",
    "UserList",
    # whatsapp
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
