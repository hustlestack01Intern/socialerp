"""Build a configured Aggregator provider from application settings."""

from __future__ import annotations

from packages.core.config import Settings, get_settings
from packages.core.domain.payment import PaymentProvider
from packages.integrations.aggregator.client import AggregatorPaymentProvider


def build_aggregator_provider(settings: Settings | None = None) -> PaymentProvider:
    """Construct the aggregator payment provider from global settings.

    These are platform-level credentials (e.g., our Pesapal/IntaSend account).
    Per-tenant aggregator selection is handled at the service layer.
    """
    s = settings or get_settings()
    allowed_ips = tuple(
        ip.strip() for ip in (s.aggregator_allowed_callback_ips or "").split(",") if ip.strip()
    )
    return AggregatorPaymentProvider(
        api_key=s.aggregator_api_key,
        api_secret=s.aggregator_api_secret,
        base_url=s.aggregator_base_url or "https://api.aggregator.example",
        callback_url=s.aggregator_callback_url or "",
        webhook_secret=s.aggregator_webhook_secret,
        aggregator_name=s.aggregator_name or "generic",
        allowed_callback_ips=allowed_ips,
    )


__all__ = ["build_aggregator_provider"]
