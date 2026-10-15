"""
Core SQLAlchemy Models for SocialERP (compatibility import path)
"""

from packages.core.models.core import (
    ApiKey,
    ApiKeyScope,
    AuditLog,
    OutboxEvent,
    Tenant,
    User,
    UserRole,
)

__all__ = [
    "ApiKey",
    "ApiKeyScope",
    "AuditLog",
    "OutboxEvent",
    "Tenant",
    "User",
    "UserRole",
]
