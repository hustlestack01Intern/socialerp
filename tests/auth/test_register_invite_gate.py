"""The invite-code gate on ``POST /api/v1/auth/register`` (M1.5).

The gate is the difference between "the founder invites the first cohort" and
"anybody on the internet can start a trial". It is server-side: the code is
checked in the handler, not in the browser.

Covered here:
- with the gate on, a registration without a code is refused (403);
- with the gate on, a registration with a valid code succeeds and the code is
  consumed;
- with the gate on, an invalid code is refused with the same 403 as a missing
  one (no oracle);
- with the gate off (dev/test default), registration still works as before.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from packages.core.auth.invites import InviteCodeService
from packages.core.socialerp.settings import get_settings

pytestmark = pytest.mark.integration


def _payload(**overrides) -> dict[str, str]:
    email = f"invite-{uuid4().hex[:6]}@example.com"
    payload = {
        "business_name": "Invited Traders",
        "slug": f"invited-{uuid4().hex[:6]}",
        "email": email,
        "owner_email": email,
        "owner_password": "S3cret-pass!",
        "owner_name": "Amina",
        "phone": "254712000111",
    }
    payload.update(overrides)
    return payload


async def test_registration_without_a_code_is_refused(api_client, session_factory, monkeypatch):
    # The settings object is cached (lru_cache), so the flag is patched on the
    # instance the route reads - an env var would be too late.
    monkeypatch.setattr(get_settings(), "INVITE_CODES_REQUIRED", True)
    resp = await api_client.post("/api/v1/auth/register", json=_payload())
    assert resp.status_code == 403, resp.text
    # Nothing was created.
    async with session_factory() as session:
        count = await session.execute(
            text("SELECT count(*) FROM tenants WHERE owner_email = :email"),
            {"email": _payload()["owner_email"]},
        )
        assert count.scalar_one() == 0


async def test_registration_with_a_valid_code_succeeds(api_client, session_factory, monkeypatch):
    # The settings object is cached (lru_cache), so the flag is patched on the
    # instance the route reads - an env var would be too late.
    monkeypatch.setattr(get_settings(), "INVITE_CODES_REQUIRED", True)
    async with session_factory() as session, session.begin():
        code = await InviteCodeService(session).create(note="cohort 1")

    resp = await api_client.post("/api/v1/auth/register", json=_payload(invite_code=code.code))
    assert resp.status_code == 201, resp.text

    # The code was consumed exactly once.
    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT uses FROM invite_codes WHERE code = :code"),
                {"code": code.code},
            )
        ).one_or_none()
        assert row is not None
        assert row.uses == 1


async def test_an_invalid_code_is_refused_like_a_missing_one(
    api_client, session_factory, monkeypatch
):
    # The settings object is cached (lru_cache), so the flag is patched on the
    # instance the route reads - an env var would be too late.
    monkeypatch.setattr(get_settings(), "INVITE_CODES_REQUIRED", True)
    resp_missing = await api_client.post("/api/v1/auth/register", json=_payload())
    resp_invalid = await api_client.post(
        "/api/v1/auth/register", json=_payload(invite_code="NOPE-NOPE-NOPE")
    )
    assert resp_missing.status_code == 403
    assert resp_invalid.status_code == 403
    # Same shape of answer: an attacker learns nothing about which case it was.
    assert resp_missing.json()["detail"] == resp_invalid.json()["detail"]


async def test_a_used_code_cannot_be_reused(api_client, session_factory, monkeypatch):
    # The settings object is cached (lru_cache), so the flag is patched on the
    # instance the route reads - an env var would be too late.
    monkeypatch.setattr(get_settings(), "INVITE_CODES_REQUIRED", True)
    async with session_factory() as session, session.begin():
        code = await InviteCodeService(session).create(note="single use")

    first = await api_client.post("/api/v1/auth/register", json=_payload(invite_code=code.code))
    assert first.status_code == 201, first.text
    second = await api_client.post("/api/v1/auth/register", json=_payload(invite_code=code.code))
    assert second.status_code == 403, second.text


async def test_registration_without_the_gate_still_works(api_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "INVITE_CODES_REQUIRED", False)
    resp = await api_client.post("/api/v1/auth/register", json=_payload())
    assert resp.status_code == 201, resp.text
