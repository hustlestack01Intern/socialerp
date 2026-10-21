"""The registration path is one transaction, and it is audited (M1.5).

Two properties the route must hold, both found by the skeptic after the
first version of the invite gate:

1. **Atomicity.** The handler must not commit mid-request. If anything after
   the writes fails (here: the token issuance), the whole registration rolls
   back: no tenant, no owner, no subscription, and the invite code is *not*
   consumed. A prospect who got a 500 must be able to retry with the same
   code.
2. **Audit.** A stranger creating a tenant on the platform is exactly what an
   audit log is for. The registration writes one row in the same
   transaction, so a rolled-back registration leaves no row either.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from packages.core.auth.invites import InviteCodeService
from packages.core.socialerp.settings import get_settings

pytestmark = pytest.mark.integration


def _payload(**overrides) -> dict[str, str]:
    email = f"atomic-{uuid4().hex[:6]}@example.com"
    payload = {
        "business_name": "Atomic Traders",
        "slug": f"atomic-{uuid4().hex[:6]}",
        "email": email,
        "owner_email": email,
        "owner_password": "S3cret-pass!",
        "owner_name": "Amina",
        "phone": "254712000111",
    }
    payload.update(overrides)
    return payload


async def _issue_code(session_factory, note: str) -> str:
    async with session_factory() as session, session.begin():
        code = await InviteCodeService(session).create(note=note)
    return code.code


async def test_registration_is_audited(api_client, session_factory, monkeypatch):
    """The registration writes an audit row, in the same transaction."""
    monkeypatch.setattr(get_settings(), "INVITE_CODES_REQUIRED", True)
    code = await _issue_code(session_factory, "audited")
    payload = _payload(invite_code=code)

    resp = await api_client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 201, resp.text

    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT action, resource, tenant_id FROM audit_log "
                    "WHERE action = 'auth.tenant_registered' AND tenant_id = :tid"
                ),
                {"tid": resp.json()["tenant_id"] if "tenant_id" in resp.json() else None},
            )
        ).all()
    # The response may not carry the tenant id; find the row by action and
    # by the email that was registered.
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT a.action, a.resource, a.tenant_id::text AS tenant_id, t.owner_email "
                    "FROM audit_log a JOIN tenants t ON t.id = a.tenant_id "
                    "WHERE a.action = 'auth.tenant_registered' AND t.owner_email = :email"
                ),
                {"email": payload["owner_email"]},
            )
        ).all()
    assert len(rows) == 1, rows
    assert rows[0].resource == "tenant"
    assert rows[0].tenant_id


async def test_a_failed_registration_leaves_nothing_behind(
    api_client, session_factory, monkeypatch
):
    """If the handler fails after the writes, everything rolls back.

    In particular the invite code must NOT be consumed: the prospect can
    retry with the same code.
    """
    monkeypatch.setattr(get_settings(), "INVITE_CODES_REQUIRED", True)
    code = await _issue_code(session_factory, "atomic")
    payload = _payload(invite_code=code)

    # Make the last step of the handler fail (token issuance).
    import apps.api.routes.auth as auth_module

    def _boom(*args, **kwargs):
        raise RuntimeError("token issuance failed")

    monkeypatch.setattr(auth_module, "_issue_tokens", _boom)

    with pytest.raises(RuntimeError):
        await api_client.post("/api/v1/auth/register", json=payload)

    async with session_factory() as session:
        tenants = (
            await session.execute(
                text("SELECT count(*) FROM tenants WHERE owner_email = :email"),
                {"email": payload["owner_email"]},
            )
        ).scalar_one()
        users = (
            await session.execute(
                text("SELECT count(*) FROM users WHERE email = :email"),
                {"email": payload["owner_email"]},
            )
        ).scalar_one()
        subs = (
            await session.execute(
                text(
                    "SELECT count(*) FROM subscriptions s JOIN tenants t ON t.id = s.tenant_id "
                    "WHERE t.owner_email = :email"
                ),
                {"email": payload["owner_email"]},
            )
        ).scalar_one()
        uses = (
            await session.execute(
                text("SELECT uses FROM invite_codes WHERE code = :code"),
                {"code": code},
            )
        ).scalar_one()
        audits = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_log a JOIN tenants t ON t.id = a.tenant_id "
                    "WHERE t.owner_email = :email"
                ),
                {"email": payload["owner_email"]},
            )
        ).scalar_one()

    assert tenants == 0, "the tenant must not survive a failed registration"
    assert users == 0, "the owner must not survive a failed registration"
    assert subs == 0, "the subscription must not survive a failed registration"
    assert uses == 0, "the invite code must not be consumed by a failed registration"
    assert audits == 0, "a rolled-back registration must leave no audit row"
