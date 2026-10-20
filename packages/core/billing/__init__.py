"""
Billing package (trial spine, plan M1).

SaaS subscription + usage metering. Deliberately independent of
``packages.core.payments`` (tenant customer payments): see the dependency test
``tests/architecture/test_billing_dependencies.py``.
"""

from packages.core.billing.entitlements import (
    AgentLevel,
    ApiAccess,
    Entitlements,
    all_plans,
    entitlements_for,
)
from packages.core.billing.service import (
    METRIC_LLM_CALLS,
    METRIC_LLM_SPEND_USD_MICROS,
    METRIC_MESSAGES_INBOUND,
    BillingService,
    EntitlementDecision,
    utc_day_key,
    utc_month_key,
)
from packages.core.models.billing import PlanTier, SubscriptionStatus

__all__ = [
    "METRIC_LLM_CALLS",
    "METRIC_LLM_SPEND_USD_MICROS",
    "METRIC_MESSAGES_INBOUND",
    "AgentLevel",
    "ApiAccess",
    "BillingService",
    "EntitlementDecision",
    "Entitlements",
    "PlanTier",
    "SubscriptionStatus",
    "all_plans",
    "entitlements_for",
    "utc_day_key",
    "utc_month_key",
]
