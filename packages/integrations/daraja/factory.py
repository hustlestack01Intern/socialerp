"""Build a configured Daraja provider from application settings."""

from __future__ import annotations

from socialerp.settings import Settings, get_settings

from packages.core.domain.payment import PaymentProvider
from packages.integrations.daraja.client import DirectDarajaProvider


def build_direct_daraja_provider(settings: Settings | None = None) -> PaymentProvider:
    """Construct the direct (tenant-own-credentials) Daraja provider."""
    s = settings or get_settings()
    allowed_ips = tuple(
        ip.strip() for ip in (s.DARAJA_ALLOWED_CALLBACK_IPS or "").split(",") if ip.strip()
    )
    return DirectDarajaProvider(
        consumer_key=s.DARAJA_CONSUMER_KEY,
        consumer_secret=s.DARAJA_CONSUMER_SECRET,
        passkey=s.DARAJA_PASSKEY,
        shortcode=s.DARAJA_SHORTCODE,
        base_url=s.DARAJA_BASE_URL,
        callback_url=s.DARAJA_CALLBACK_URL,
        callback_token=s.DARAJA_CALLBACK_TOKEN,
        allowed_callback_ips=allowed_ips,
    )


__all__ = ["build_direct_daraja_provider"]
