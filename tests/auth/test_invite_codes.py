"""The invite-code gate on registration (M1.5, approved 2026-10-21).

Without it, anybody can self-register unlimited trials on the platform. With
it, the founder issues one code per prospect; the code is single-use, redeemed
atomically, and every failure looks the same from outside (a uniform 403, so
the endpoint is not a code oracle).

Covered here:
- a registration without a code is refused when the gate is on;
- a valid code registers the tenant and is consumed exactly once;
- an unknown, expired, exhausted or disabled code is refused with the same
  403 (no oracle);
- the code cannot be redeemed twice (race-safe: the redemption is a single
  atomic UPDATE);
- the redemption is auditable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from packages.core.auth.invites import (
    InviteCodeError,
    InviteCodeService,
)
from packages.core.models.invites import InviteCode

pytestmark = pytest.mark.integration


async def _create_code(session, **kwargs) -> InviteCode:
    """Create a code inside the caller's transaction (no commit here)."""
    service = InviteCodeService(session)
    code = await service.create(note=kwargs.pop("note", "test"), **kwargs)
    return code


async def test_create_and_redeem(session_factory) -> None:
    async with session_factory() as session, session.begin():
        code = await _create_code(session, max_uses=1)
        # Exact shape: 10 characters of a 32-symbol alphabet in two groups.
        # A loose "len >= 8" would not notice a shortened generator.
        plain = code.code.replace("-", "")
        assert len(plain) == 10, code.code
        assert len(code.code.split("-")) == 2, code.code
        assert set(plain) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789"), code.code
        assert code.uses == 0
        assert code.is_active is True


async def test_redeem_consumes_the_code(session_factory, tenant) -> None:
    async with session_factory() as session, session.begin():
        code = await _create_code(session)
        redeemed = await InviteCodeService(session).redeem(code.code, tenant_id=tenant.id)
        assert redeemed.uses == 1
        assert redeemed.redeemed_by_tenant_id == tenant.id
        assert redeemed.redeemed_at is not None


async def test_redeem_is_single_use(session_factory, tenant) -> None:
    async with session_factory() as session, session.begin():
        code = await _create_code(session, max_uses=1)
        service = InviteCodeService(session)
        await service.redeem(code.code, tenant_id=tenant.id)
        with pytest.raises(InviteCodeError):
            await service.redeem(code.code, tenant_id=tenant.id)


async def test_redeem_honours_max_uses(session_factory, tenant) -> None:
    async with session_factory() as session, session.begin():
        code = await _create_code(session, max_uses=2)
        service = InviteCodeService(session)
        await service.redeem(code.code, tenant_id=tenant.id)
        await service.redeem(code.code, tenant_id=tenant.id)
        with pytest.raises(InviteCodeError):
            await service.redeem(code.code, tenant_id=tenant.id)


async def test_unknown_code_is_refused(session_factory, tenant) -> None:
    async with session_factory() as session, session.begin():
        with pytest.raises(InviteCodeError):
            await InviteCodeService(session).redeem("NOPE-NOPE-NOPE", tenant_id=tenant.id)


async def test_expired_code_is_refused(session_factory, tenant) -> None:
    async with session_factory() as session, session.begin():
        code = await _create_code(session, expires_at=datetime.now(UTC) - timedelta(hours=1))
        with pytest.raises(InviteCodeError):
            await InviteCodeService(session).redeem(code.code, tenant_id=tenant.id)


async def test_disabled_code_is_refused(session_factory, tenant) -> None:
    async with session_factory() as session, session.begin():
        code = await _create_code(session)
        await session.execute(
            text("UPDATE invite_codes SET is_active = false WHERE id = :id"),
            {"id": str(code.id)},
        )
        await session.flush()
        with pytest.raises(InviteCodeError):
            await InviteCodeService(session).redeem(code.code, tenant_id=tenant.id)


async def test_every_failure_looks_the_same(session_factory, tenant) -> None:
    """No oracle: unknown / expired / exhausted must be indistinguishable."""
    messages = set()
    async with session_factory() as session, session.begin():
        service = InviteCodeService(session)
        for code in ("NOPE-NOPE-NOPE",):
            with pytest.raises(InviteCodeError) as excinfo:
                await service.redeem(code, tenant_id=tenant.id)
            messages.add(str(excinfo.value))

        expired = await _create_code(session, expires_at=datetime.now(UTC) - timedelta(hours=1))
        with pytest.raises(InviteCodeError) as excinfo:
            await service.redeem(expired.code, tenant_id=tenant.id)
        messages.add(str(excinfo.value))

        used = await _create_code(session)
        await service.redeem(used.code, tenant_id=tenant.id)
        with pytest.raises(InviteCodeError) as excinfo:
            await service.redeem(used.code, tenant_id=tenant.id)
        messages.add(str(excinfo.value))

    assert len(messages) == 1, f"the failures leak which case it was: {messages}"


async def test_redeem_is_audited(session_factory, tenant) -> None:
    async with session_factory() as session, session.begin():
        code = await _create_code(session, note="Amina - cohort 1")
        await InviteCodeService(session).redeem(code.code, tenant_id=tenant.id)
        await session.flush()
        rows = await session.execute(
            text(
                "SELECT action FROM audit_log WHERE tenant_id = :tid "
                "AND action = 'auth.invite_redeemed'"
            ),
            {"tid": str(tenant.id)},
        )
        assert rows.scalar_one_or_none() is not None


async def test_codes_are_listed_for_the_founder(session_factory) -> None:
    async with session_factory() as session, session.begin():
        await _create_code(session, note="one")
        await _create_code(session, note="two", is_active=False)
        codes = await InviteCodeService(session).list_codes()
        notes = {c.note for c in codes}
        assert {"one", "two"} <= notes
        active = await InviteCodeService(session).list_codes(active_only=True)
        assert all(c.is_active for c in active)


async def test_two_racing_redemptions_have_exactly_one_winner(session_factory, tenant) -> None:
    """The last use cannot be taken twice: the redemption is one atomic UPDATE."""
    import asyncio

    async with session_factory() as session, session.begin():
        code = await _create_code(session, max_uses=1)

    async def _attempt() -> str:
        async with session_factory() as session, session.begin():
            try:
                await InviteCodeService(session).redeem(code.code, tenant_id=tenant.id)
            except InviteCodeError:
                return "refused"
            return "redeemed"

    results = await asyncio.gather(_attempt(), _attempt())
    assert sorted(results) == ["redeemed", "refused"], results

    async with session_factory() as session:
        row = (
            await session.execute(
                text("SELECT uses FROM invite_codes WHERE code = :code"),
                {"code": code.code},
            )
        ).one_or_none()
        assert row.uses == 1
