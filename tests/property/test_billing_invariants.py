"""
Property tests for the plan x feature entitlement matrix and period keys
(plan M1.4, skeptic G2; invariants INV6-INV9, INV14).

Pure functions only — no DB. The DB-backed conservation properties live in
``tests/billing/test_billing_service.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from hypothesis import given
from hypothesis import strategies as st

from packages.core.billing import entitlements_for, utc_day_key, utc_month_key
from packages.core.models.billing import PlanTier

ALL_PLANS = list(PlanTier)
FEATURES = ["channels", "agent_level", "api_access", "insights", "seats"]


class TestMatrixTotality:
    """INV6: every plan x feature cell is defined (no default-by-omission)."""

    @given(st.sampled_from(ALL_PLANS))
    def test_every_plan_has_every_feature(self, plan: PlanTier) -> None:
        ents = entitlements_for(plan)
        for feature in FEATURES:
            assert hasattr(ents, feature), f"{plan} missing {feature}"

    def test_unknown_plan_fails_closed_to_starter(self) -> None:
        from packages.core.billing.entitlements import entitlements_for as ef

        ents = ef("not-a-real-plan")
        assert ents.plan is PlanTier.STARTER


class TestTierMonotonicity:
    """INV7: starter ⪯ professional ⪯ enterprise componentwise."""

    def test_agent_level_chain(self) -> None:
        assert (
            entitlements_for(PlanTier.STARTER).agent_level
            <= entitlements_for(PlanTier.PROFESSIONAL).agent_level
            <= entitlements_for(PlanTier.ENTERPRISE).agent_level
        )

    def test_api_chain(self) -> None:
        assert (
            entitlements_for(PlanTier.STARTER).api_access
            <= entitlements_for(PlanTier.PROFESSIONAL).api_access
            <= entitlements_for(PlanTier.ENTERPRISE).api_access
        )

    def test_channels_chain(self) -> None:
        starter = entitlements_for(PlanTier.STARTER).channels
        pro = entitlements_for(PlanTier.PROFESSIONAL).channels
        ent = entitlements_for(PlanTier.ENTERPRISE).channels
        assert starter <= pro <= ent

    def test_seats_chain(self) -> None:
        starter = entitlements_for(PlanTier.STARTER).seats
        pro = entitlements_for(PlanTier.PROFESSIONAL).seats
        ent = entitlements_for(PlanTier.ENTERPRISE).seats
        assert starter is not None and pro is not None
        assert starter <= pro
        assert ent is None  # unlimited


class TestTrialIsProfessionalWhatsAppOnly:
    """INV8: trial = professional-tier agent, WhatsApp only, ⊆ professional."""

    def test_trial_agent_is_l2(self) -> None:
        from packages.core.billing.entitlements import AgentLevel

        assert entitlements_for(PlanTier.TRIAL).agent_level == AgentLevel.L2

    def test_trial_channels_is_whatsapp_only(self) -> None:
        assert entitlements_for(PlanTier.TRIAL).channels == frozenset({"whatsapp"})

    def test_trial_subset_of_professional(self) -> None:
        trial = entitlements_for(PlanTier.TRIAL)
        pro = entitlements_for(PlanTier.PROFESSIONAL)
        assert trial.channels <= pro.channels
        assert trial.agent_level <= pro.agent_level
        assert trial.api_access <= pro.api_access


class TestPeriodKeys:
    """INV14: period key is a pure function of the (UTC) event time."""

    @given(st.datetimes(timezones=st.just(UTC)))
    def test_day_key_is_deterministic(self, moment: datetime) -> None:
        assert utc_day_key(moment) == utc_day_key(moment)

    def test_day_key_is_utc_not_local(self) -> None:
        # 2026-03-01T00:30 in Nairobi (UTC+3) is 2026-02-28T21:30 UTC.
        nairobi = timezone(timedelta(hours=3))
        moment = datetime(2026, 3, 1, 0, 30, tzinfo=nairobi)
        assert utc_day_key(moment) == "2026-02-28"

    def test_naive_is_treated_as_utc(self) -> None:
        naive = datetime(2026, 3, 1, 0, 30)
        assert utc_day_key(naive) == "2026-03-01"

    def test_month_key(self) -> None:
        assert utc_month_key(datetime(2026, 3, 31, 23, 0, tzinfo=UTC)) == "2026-03"

    @given(
        st.datetimes(
            min_value=datetime(2020, 1, 1, tzinfo=UTC),
            max_value=datetime(2030, 1, 1, tzinfo=UTC),
            timezones=st.just(UTC),
        )
    )
    def test_day_key_format(self, moment: datetime) -> None:
        key = utc_day_key(moment)
        # YYYY-MM-DD
        assert len(key) == 10 and key[4] == "-" and key[7] == "-"
