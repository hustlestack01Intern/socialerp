"""
SaaS billing collection providers (plan M1.5, skeptic G4).

This is the seam for collecting *our* subscription fees from a tenant — separate
from ``packages.core.payments`` (the tenant's customer payments). It deliberately
does NOT import the payments package (enforced by
``tests/architecture/test_billing_dependencies.py``).

For the pilot there is no card rail and no Stripe webhook (deferred). The chosen
path is explicit and auditable: a **manual M-Pesa transfer** that a founder
confirms, which then applies an audited plan change. ``ManualBillingProvider``
models that; ``NoopBillingProvider`` is the default that records intent only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from packages.core.models.billing import PlanTier


@dataclass(frozen=True)
class CollectionInstruction:
    """What to tell the owner so they can pay us (never auto-charged)."""

    plan: PlanTier
    amount_kes: int
    paybill: str
    account_ref: str
    note: str


@dataclass(frozen=True)
class CollectionResult:
    """Outcome of a collection attempt / confirmation."""

    ok: bool
    confirmed_at: datetime | None = None
    reference: str | None = None
    error: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class BillingProvider(ABC):
    """Abstract SaaS-billing collection provider (ADR 0023)."""

    name: str

    @abstractmethod
    def instructions(
        self, tenant_id: str, plan: PlanTier, *, amount_kes: int
    ) -> CollectionInstruction:
        """Return the payment instructions the owner must act on manually."""

    @abstractmethod
    async def confirm(
        self,
        tenant_id: str,
        plan: PlanTier,
        *,
        reference: str,
        confirmed_by_user_id: str,
    ) -> CollectionResult:
        """Confirm a collection (human-initiated). Never does plan change itself.

        Applying the plan change is the caller's job (audited
        ``BillingService.apply_plan_change``); a provider must never mutate the
        subscription, so a forged/duplicate confirm cannot silently upgrade.
        """


class NoopBillingProvider(BillingProvider):
    """Records intent; confirms nothing. The safe default for trials."""

    name = "noop"

    def instructions(
        self, tenant_id: str, plan: PlanTier, *, amount_kes: int
    ) -> CollectionInstruction:
        return CollectionInstruction(
            plan=plan,
            amount_kes=amount_kes,
            paybill="",
            account_ref="",
            note="No collection rail configured; contact support to upgrade.",
        )

    async def confirm(
        self,
        tenant_id: str,
        plan: PlanTier,
        *,
        reference: str,
        confirmed_by_user_id: str,
    ) -> CollectionResult:
        return CollectionResult(ok=False, error="noop provider cannot confirm collections")


class ManualBillingProvider(BillingProvider):
    """Manual M-Pesa transfer the operator confirms from the bank/Till statement.

    ``paybill``/``account_ref_prefix`` come from settings. Confirmation is
    idempotent on ``(tenant, reference)`` so a double-click cannot double-apply.
    """

    name = "manual_mpesa"

    def __init__(
        self,
        *,
        paybill: str,
        account_ref_prefix: str = "ERP",
        now: object | None = None,
    ) -> None:
        self.paybill = paybill
        self.account_ref_prefix = account_ref_prefix
        self._now = now
        self._confirmed: set[tuple[str, str]] = set()

    def _timestamp(self) -> datetime:
        if callable(self._now):
            return self._now()
        return datetime.now(UTC)

    def instructions(
        self, tenant_id: str, plan: PlanTier, *, amount_kes: int
    ) -> CollectionInstruction:
        ref = f"{self.account_ref_prefix}-{tenant_id[:8].upper()}"
        return CollectionInstruction(
            plan=plan,
            amount_kes=amount_kes,
            paybill=self.paybill,
            account_ref=ref,
            note=(
                f"Pay KES {amount_kes} to Paybill {self.paybill} using account "
                f"{ref}, then reply with the M-Pesa confirmation code."
            ),
        )

    async def confirm(
        self,
        tenant_id: str,
        plan: PlanTier,
        *,
        reference: str,
        confirmed_by_user_id: str,
    ) -> CollectionResult:
        if not reference.strip():
            return CollectionResult(ok=False, error="missing M-Pesa reference")
        key = (tenant_id, reference.strip().upper())
        if key in self._confirmed:
            return CollectionResult(
                ok=True,
                confirmed_at=self._timestamp(),
                reference=reference,
                metadata={"idempotent_replay": True},
            )
        self._confirmed.add(key)
        return CollectionResult(
            ok=True,
            confirmed_at=self._timestamp(),
            reference=reference,
            metadata={"confirmed_by": confirmed_by_user_id},
        )


def build_default_billing_provider(*, paybill: str = "") -> BillingProvider:
    """Manual provider when a paybill is configured, else the noop default."""
    if paybill:
        return ManualBillingProvider(paybill=paybill)
    return NoopBillingProvider()


__all__ = [
    "BillingProvider",
    "CollectionInstruction",
    "CollectionResult",
    "ManualBillingProvider",
    "NoopBillingProvider",
    "build_default_billing_provider",
]
