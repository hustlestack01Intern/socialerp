"""
Per-tenant payment-provider resolution (shared).

Both the API (``apps/api/routes/payments.py``) and the worker (rider WhatsApp
flow) must resolve the *same* provider for a tenant, so the selection logic
lives here instead of inside the HTTP layer.

Selection comes from the tenant's ``payment_providers`` setting, in order:
``daraja`` (tenant's own Daraja credentials via the platform config),
``aggregator`` (Pesapal/IntaSend-style), ``manual`` (pay-to-Till). Unknown
entries are skipped; an empty selection falls back to direct Daraja, the
historical default. Falls back to direct Daraja if the tenant row is missing
(defense in depth — auth normally guarantees it exists).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.domain.payment import PaymentProvider
from packages.core.models.core import Tenant
from packages.integrations.aggregator import build_aggregator_provider
from packages.integrations.daraja import build_direct_daraja_provider
from packages.integrations.manual import ManualPaymentProvider

# Providers own HTTP clients, so they are built once per tenant (and per
# provider selection) and reused across requests/jobs.
_provider_cache: dict[tuple[UUID, tuple[str, ...]], PaymentProvider] = {}


def build_provider(providers: tuple[str, ...], tenant: Tenant | None) -> PaymentProvider:
    """Instantiate the first enabled provider for a tenant."""
    for name in providers:
        if name == "daraja":
            return build_direct_daraja_provider()
        if name == "aggregator":
            return build_aggregator_provider()
        if name == "manual":
            from socialerp.settings import get_settings

            settings = get_settings()
            till = (
                tenant.settings.get("mpesa_till_number", "") if tenant and tenant.settings else ""
            )
            return ManualPaymentProvider(
                callback_token=settings.MANUAL_PAYMENT_TOKEN,
                till=till,
            )
    return build_direct_daraja_provider()


async def resolve_tenant_payment_provider(db: AsyncSession, tenant_id: UUID) -> PaymentProvider:
    """Resolve (and cache) the tenant's first enabled payment provider."""
    tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    providers: tuple[str, ...] = ()
    if tenant is not None:
        raw = tenant.settings.get("payment_providers", []) if tenant.settings else []
        providers = tuple(p for p in raw if isinstance(p, str))

    key = (tenant_id, providers)
    cached = _provider_cache.get(key)
    if cached is not None:
        return cached

    provider = build_provider(providers, tenant)
    _provider_cache[key] = provider
    return provider


def clear_provider_cache() -> None:
    """Drop the cache (tests and, later, a settings-change hook)."""
    _provider_cache.clear()


__all__ = [
    "build_provider",
    "clear_provider_cache",
    "resolve_tenant_payment_provider",
]
