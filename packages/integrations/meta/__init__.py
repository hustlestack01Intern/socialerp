"""
Meta (WhatsApp/Instagram) Integration Package

Exports all Meta integration components.
"""

from packages.integrations.meta.sender import TemplateManager, WhatsAppSender
from packages.integrations.meta.webhook import router as whatsapp_webhook_router
from packages.integrations.meta.window_tracker import (
    RateLimitBucket,
    RateLimiter,
    WhatsAppLimitsManager,
    WindowState,
    WindowTracker,
)

__all__ = [
    "RateLimitBucket",
    "RateLimiter",
    "TemplateManager",
    "WhatsAppLimitsManager",
    "WhatsAppSender",
    "WindowState",
    "WindowTracker",
    "whatsapp_webhook_router",
]
