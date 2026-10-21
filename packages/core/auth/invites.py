"""Invite codes: issue, redeem, audit (M1.5).

The gate that keeps the first cohort *invited* instead of open to the
internet. The rules that matter:

* a code is consumed **atomically** — the redemption is a single conditional
  ``UPDATE ... RETURNING``, so two concurrent registrations cannot both use the
  last use of a code (no read-then-write race);
* every failure raises the same :class:`InviteCodeInvalid` with the same
  message, so the endpoint is not an oracle for "does this code exist";
* the redemption is audited against the tenant, so the founder can see which
  tenant used which code (``auth.invite_redeemed``).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.audit.logger import AuditLogger
from packages.core.models.invites import InviteCode

# Unambiguous alphabet (no 0/O, no 1/I): the founder reads these out loud.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 10
_CODE_GROUPS = 2


class InviteCodeError(ValueError):
    """The invite code cannot be used (unknown, expired, exhausted, disabled).

    One error, one message: the caller must not be able to tell which case it
    was (no enumeration oracle).
    """

    def __init__(self, reason: str = "This invite code is not valid.") -> None:
        super().__init__(reason)
        self.reason = reason


def _generate_code() -> str:
    """A human-readable, unambiguous code (e.g. ``K7M2Q9XR4B``)."""
    groups = [
        "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH // _CODE_GROUPS))
        for _ in range(_CODE_GROUPS)
    ]
    return "-".join(groups)


class InviteCodeService:
    """Issue and redeem invite codes. The caller owns the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        note: str | None = None,
        max_uses: int = 1,
        expires_at: datetime | None = None,
        created_by: str | None = None,
        is_active: bool = True,
    ) -> InviteCode:
        """Issue a new invite code."""
        if max_uses < 1:
            raise ValueError("max_uses must be at least 1")
        for _ in range(5):
            code = _generate_code()
            existing = await self.session.execute(
                select(InviteCode.id).where(InviteCode.code == code)
            )
            if existing.scalar_one_or_none() is None:
                break
        else:  # pragma: no cover - 32^10 codes
            raise RuntimeError("could not generate a unique invite code")

        invite = InviteCode(
            code=code,
            max_uses=max_uses,
            uses=0,
            is_active=is_active,
            expires_at=expires_at,
            note=note,
            created_by=created_by,
        )
        self.session.add(invite)
        await self.session.flush()
        return invite

    async def list_codes(self, *, active_only: bool = False) -> list[InviteCode]:
        """All codes, newest first (the founder's view)."""
        stmt = select(InviteCode).order_by(InviteCode.created_at.desc())
        if active_only:
            stmt = stmt.where(InviteCode.is_active.is_(True))
        rows = await self.session.execute(stmt)
        return list(rows.scalars().all())

    async def get_by_code(self, code: str) -> InviteCode | None:
        rows = await self.session.execute(
            select(InviteCode).where(InviteCode.code == code.strip().upper())
        )
        return rows.scalar_one_or_none()

    async def validate(self, code: str) -> InviteCode:
        """Check a code without consuming it (used to order the HTTP errors)."""
        invite = await self.get_by_code(code)
        if invite is None:
            raise InviteCodeError()
        now = datetime.now(UTC)
        if not invite.is_active:
            raise InviteCodeError()
        if invite.expires_at is not None and invite.expires_at <= now:
            raise InviteCodeError()
        if invite.uses >= invite.max_uses:
            raise InviteCodeError()
        return invite

    async def redeem(self, code: str, *, tenant_id: UUID) -> InviteCode:
        """Consume one use of the code, atomically.

        A single conditional ``UPDATE`` decides the winner, so two concurrent
        registrations cannot both take the last use. The redemption is audited
        against the tenant that used it.
        """
        now = datetime.now(UTC)
        stmt = (
            update(InviteCode)
            .where(
                InviteCode.code == code.strip().upper(),
                InviteCode.is_active.is_(True),
                InviteCode.uses < InviteCode.max_uses,
                # The expiry is part of the atomic decision, not a pre-check:
                # an expired code can never be redeemed, even in a race.
                or_(InviteCode.expires_at.is_(None), InviteCode.expires_at > now),
            )
            .values(
                uses=InviteCode.uses + 1,
                last_redeemed_at=now,
                # COALESCE semantics: keep the first redeemer, but always stamp
                # the latest redemption.
                redeemed_by_tenant_id=func.coalesce(InviteCode.redeemed_by_tenant_id, tenant_id),
                redeemed_at=func.coalesce(InviteCode.redeemed_at, now),
            )
            .returning(InviteCode)
        )
        result = await self.session.execute(stmt)
        invite = result.scalar_one_or_none()
        if invite is None:
            # Either unknown, disabled, expired or exhausted — one message.
            raise InviteCodeError()
        await AuditLogger(self.session).log_action(
            tenant_id=tenant_id,
            user_id=None,
            action="auth.invite_redeemed",
            resource="invite_code",
            resource_id=str(invite.id),
            after={
                "code_id": str(invite.id),
                "uses": invite.uses,
                "max_uses": invite.max_uses,
            },
        )
        return invite


__all__ = ["InviteCodeError", "InviteCodeService"]
