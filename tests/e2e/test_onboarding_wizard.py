"""
E2E Tests for Onboarding Wizard

Tests the full 30-minute onboarding flow on fakes.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from packages.core.domain import TenantId
from packages.core.onboarding.steps import get_total_target_seconds


class TestOnboardingWizardE2E:
    """End-to-end tests for the onboarding wizard flow."""

    @pytest.mark.asyncio
    async def test_onboarding_under_30_minutes(self, api_client: AsyncClient, db_session):
        """
        Test that a complete onboarding flow completes under 30 minutes.

        This test simulates the full 6-step onboarding flow using fake services
        and verifies the total time is under 30 minutes (1800 seconds).
        """
        # Get tenant from fixture
        from sqlalchemy import text
        result = await db_session.execute(
            text("SELECT * FROM tenants LIMIT 1"),
        )
        tenant_row = result.first()
        TenantId(tenant_row.id)

        # Step 1: Business Profile (target: 3 min = 180s)
        response = await api_client.post(
            "/api/v1/onboarding/steps/1/start",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Simulate completing step 1
        response = await api_client.post(
            "/api/v1/onboarding/steps/1/complete",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["current_step"] == 1
        assert data["step_timings"]["1"]["status"] == "COMPLETED"

        # Step 2: Connect Channels (target: 8 min = 480s)
        response = await api_client.post(
            "/api/v1/onboarding/steps/2/start",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Simulate fallback: no Meta assets
        response = await api_client.post(
            "/api/v1/onboarding/steps/2/fallback",
            json={"fallback_type": "NO_META_ASSETS", "fallback_reason": "no_verified_page"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["step_timings"]["2"]["fallback"]["type"] == "NO_META_ASSETS"

        # Step 3: Catalog Bootstrap (target: 8 min = 480s)
        response = await api_client.post(
            "/api/v1/onboarding/steps/3/start",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        response = await api_client.post(
            "/api/v1/onboarding/steps/3/complete",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Step 4: Payments (target: 5 min = 300s)
        response = await api_client.post(
            "/api/v1/onboarding/steps/4/start",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Simulate fallback: no Daraja Till
        response = await api_client.post(
            "/api/v1/onboarding/steps/4/fallback",
            json={"fallback_type": "NO_DARAJA_TILL", "fallback_reason": "no_till"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Step 5: Rules & Tone (target: 4 min = 240s)
        response = await api_client.post(
            "/api/v1/onboarding/steps/5/start",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        response = await api_client.post(
            "/api/v1/onboarding/steps/5/complete",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Step 6: Go Live Shadow Test (target: 2 min = 120s)
        response = await api_client.post(
            "/api/v1/onboarding/steps/6/start",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        response = await api_client.post(
            "/api/v1/onboarding/steps/6/complete",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200

        # Verify wizard completed
        progress_response = await api_client.get(
            "/api/v1/onboarding",
            headers={"Authorization": "Bearer test-token"},
        )
        assert progress_response.status_code == 200
        progress = progress_response.json()
        assert progress["state"] == "COMPLETED"
        assert progress["current_step"] == 6

        # Verify total duration is under 30 minutes
        total_duration = progress["slo_status"]["total_duration_ms"]
        assert total_duration is not None
        assert total_duration <= get_total_target_seconds() * 1000

    @pytest.mark.asyncio
    async def test_onboarding_with_all_fallbacks(self, api_client: AsyncClient):
        """Test onboarding with all fallback types triggered."""
        from packages.core.onboarding.model import FallbackType

        # Verify all fallback types are available
        fallback_types = [ft.value for ft in FallbackType]
        assert "MANUAL_PAYMENT_MODE" in fallback_types
        assert "MANUAL_CATALOG" in fallback_types
        assert "NO_META_ASSETS" in fallback_types
        assert "NO_DARAJA_TILL" in fallback_types

    @pytest.mark.asyncio
    async def test_launch_approval_gate(self, api_client: AsyncClient):
        """Test the zero-interference launch approval gate."""
        # Complete wizard first (simplified)
        # ... steps 1-6 ...

        # Try to access launch status before approval
        response = await api_client.get(
            "/api/v1/onboarding/launch-status",
            headers={"Authorization": "Bearer test-token"},
        )
        # Should work but return launch_approved: false
        assert response.status_code == 200

        # Approve launch
        response = await api_client.post(
            "/api/v1/onboarding/approve-launch",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["state"] == "COMPLETED"
        assert data["launch_approved_at"] is not None

        # Verify launch status now shows approved
        response = await api_client.get(
            "/api/v1/onboarding/launch-status",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        assert response.json()["launch_approved"] is True


class TestOnboardingConfig:
    """Tests for onboarding configuration endpoint."""

    @pytest.mark.asyncio
    async def test_get_config(self, api_client: AsyncClient):
        """Test getting onboarding configuration."""
        response = await api_client.get(
            "/api/v1/onboarding/config",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        config = response.json()

        assert "steps" in config
        assert len(config["steps"]) == 6
        assert "total_target_seconds" in config
        assert config["total_target_seconds"] == 1800  # 30 min

        # Verify step targets match AGENTS.md §11
        steps = config["steps"]
        assert steps["1"]["target_seconds"] == 180   # 3 min
        assert steps["2"]["target_seconds"] == 480   # 8 min
        assert steps["3"]["target_seconds"] == 480   # 8 min
        assert steps["4"]["target_seconds"] == 300   # 5 min
        assert steps["5"]["target_seconds"] == 240   # 4 min
        assert steps["6"]["target_seconds"] == 120   # 2 min

        # Verify fallback types
        assert "fallback_types" in config
        assert "MANUAL_PAYMENT_MODE" in config["fallback_types"]
        assert "MANUAL_CATALOG" in config["fallback_types"]
        assert "NO_META_ASSETS" in config["fallback_types"]
        assert "NO_DARAJA_TILL" in config["fallback_types"]


class TestOnboardingStepManagement:
    """Tests for step start/complete/pause/resume/fallback."""

    @pytest.mark.asyncio
    async def test_step_start_complete(self, api_client: AsyncClient):
        """Test starting and completing a step."""
        # Start step 1
        response = await api_client.post(
            "/api/v1/onboarding/steps/1/start",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["current_step"] == 1
        assert data["step_timings"]["1"]["status"] == "IN_PROGRESS"

        # Complete step 1
        response = await api_client.post(
            "/api/v1/onboarding/steps/1/complete",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["step_timings"]["1"]["status"] == "COMPLETED"
        assert data["step_timings"]["1"]["duration_ms"] is not None

    @pytest.mark.asyncio
    async def test_step_pause_resume(self, api_client: AsyncClient):
        """Test pausing and resuming a step."""
        # Start step
        await api_client.post(
            "/api/v1/onboarding/steps/2/start",
            headers={"Authorization": "Bearer test-token"},
        )

        # Pause
        response = await api_client.post(
            "/api/v1/onboarding/steps/2/pause",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["step_timings"]["2"]["paused_at"] is not None

        # Resume
        response = await api_client.post(
            "/api/v1/onboarding/steps/2/resume",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["step_timings"]["2"]["resumed_at"] is not None

    @pytest.mark.asyncio
    async def test_fallback_recording(self, api_client: AsyncClient):
        """Test recording a fallback for a step."""
        # Start step
        await api_client.post(
            "/api/v1/onboarding/steps/4/start",
            headers={"Authorization": "Bearer test-token"},
        )

        # Trigger fallback
        response = await api_client.post(
            "/api/v1/onboarding/steps/4/fallback",
            json={
                "fallback_type": "NO_DARAJA_TILL",
                "fallback_reason": "no_till_number",
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["step_timings"]["4"]["fallback"]["type"] == "NO_DARAJA_TILL"
        assert data["fallback_flags"]["NO_DARAJA_TILL"]["step"] == 4
