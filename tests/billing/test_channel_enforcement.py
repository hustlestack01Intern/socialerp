"""The channel cell is enforced (M1.6, U80).

Instagram is a Professional feature. Before M1.6 the matrix said so and the
code did not: ``approve_launch`` switched Instagram on for every tenant and
the webhook processed every Instagram message it received.

What must hold now:

* a trial or starter tenant is NOT switched on to Instagram, and the refusal
  is recorded;
* a Professional tenant is;
* a webhook that arrives for a channel the plan does not include is recorded
  but not processed - the customer is not answered on a channel the tenant
  did not buy, and nothing is silently dropped either (the raw event stays in
  the webhook log).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.core.config import get_settings
from packages.core.models.core import AuditLog, Tenant
from packages.core.models.instagram import InstagramMessage

pytestmark = pytest.mark.integration


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _set_plan(session_factory, tenant_id, plan: str) -> None:
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "UPDATE subscriptions SET plan = :plan, status = 'active', "
                "period_start = now(), period_end = now() + interval '30 days' "
                "WHERE tenant_id = :tid"
            ),
            {"plan": plan, "tid": str(tenant_id)},
        )


async def _audit_actions(session_factory, tenant_id) -> list[str]:
    async with session_factory() as session:
        rows = await session.execute(
            select(AuditLog.action).where(AuditLog.tenant_id == tenant_id)
        )
        return list(rows.scalars().all())


async def _tenant_settings(session_factory, tenant_id) -> dict:
    async with session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        return dict(tenant.settings or {})


@pytest.fixture
async def ig_tenant(test_engine, monkeypatch):
    """A tenant whose settings claim Instagram is on (the plan decides)."""
    async_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session, session.begin():
        tenant = Tenant(
            id=uuid4(),
            name="IG Biz",
            slug=f"ig-biz-{uuid4().hex[:8]}",
            owner_phone="254700000000",
            owner_email="ig@example.com",
            is_active=True,
            settings={"instagram_enabled": True, "launch_approved": True},
        )
        session.add(tenant)
        await session.flush()
        from packages.core.models.billing import Subscription, SubscriptionStatus

        session.add(
            Subscription(
                tenant_id=tenant.id,
                plan="trial",
                status=SubscriptionStatus.ACTIVE.value,
                period_start=datetime.now(UTC),
                period_end=datetime.now(UTC) + timedelta(days=14),
            )
        )
        await session.flush()

    monkeypatch.setenv("META_INSTAGRAM_ACCOUNT_ID", str(tenant.id))
    monkeypatch.setenv("META_GRAPH_URL", "http://127.0.0.1:8081")
    monkeypatch.setenv("META_GRAPH_API_VERSION", "")
    monkeypatch.setenv("META_APP_SECRET", "test_app_secret")
    get_settings.cache_clear()
    yield tenant
    get_settings.cache_clear()


@pytest.fixture
async def ig_client() -> AsyncGenerator[AsyncClient]:
    from apps.api.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _dm_payload(tenant_id, mid: str) -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": str(tenant_id),
                "time": 1700000000,
                "messaging": [
                    {
                        "sender": {"id": "ig_user_123"},
                        "recipient": {"id": str(tenant_id)},
                        "timestamp": 1700000000,
                        "message": {
                            "mid": mid,
                            "text": "Hello, I want to order a black hoodie",
                            "type": "text",
                        },
                    }
                ],
            }
        ],
    }


async def test_a_trial_tenant_is_not_switched_on_to_instagram(
    tenant, session_factory
) -> None:
    """approve_launch must not switch on a channel the plan does not include."""
    from packages.core.onboarding import OnboardingWizardService

    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant.id)},
        )
        service = OnboardingWizardService(session)
        for step in (1, 2, 3, 4, 5, 6):
            await service.start_step(tenant.id, step)
            await service.complete_step(tenant.id, step)
        await service.approve_launch(tenant.id)

    settings = await _tenant_settings(session_factory, tenant.id)
    assert settings.get("launch_approved") is True
    assert settings.get("instagram_enabled") is None
    assert "billing.channel_not_included" in await _audit_actions(
        session_factory, tenant.id
    )


async def test_a_professional_tenant_is_switched_on_to_instagram(
    tenant, session_factory
) -> None:
    from packages.core.onboarding import OnboardingWizardService

    await _set_plan(session_factory, tenant.id, "professional")
    async with session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": str(tenant.id)},
        )
        service = OnboardingWizardService(session)
        for step in (1, 2, 3, 4, 5, 6):
            await service.start_step(tenant.id, step)
            await service.complete_step(tenant.id, step)
        await service.approve_launch(tenant.id)

    settings = await _tenant_settings(session_factory, tenant.id)
    assert settings.get("instagram_enabled") is True
    assert "billing.channel_not_included" not in await _audit_actions(
        session_factory, tenant.id
    )


async def test_an_instagram_webhook_is_refused_for_a_trial(
    ig_client, ig_tenant, session_factory
) -> None:
    """The message is not turned into a conversation on a channel not bought."""
    body = json.dumps(_dm_payload(ig_tenant.id, "ig_refused_1"), separators=(",", ":")).encode()
    resp = await ig_client.post(
        "/api/v1/webhooks/meta",
        content=body,
        headers={
            "X-Hub-Signature-256": _sign(body, "test_app_secret"),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is False

    async with session_factory() as session:
        messages = (
            await session.execute(
                select(InstagramMessage).where(
                    InstagramMessage.tenant_id == ig_tenant.id
                )
            )
        ).scalars().all()
    assert messages == []
    assert "billing.channel_not_included" in await _audit_actions(
        session_factory, ig_tenant.id
    )
