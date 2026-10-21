"""Invite codes (M1.5).

A platform-scoped table: the founder issues a code, a prospect uses it once to
register, and the code is consumed. It exists so the first cohort is *invited*
rather than *open to the internet* (the trial-stacking defence, U68).

Deliberately **without** a ``tenant_id`` column and therefore without RLS: the
code is a platform secret, it must be readable by the registration handler for
every tenant, and it holds no customer PII. The redemption trail lives in the
tenant-scoped ``audit_log`` (``auth.invite_redeemed``) and in
``redeemed_by_tenant_id`` on the row itself. Same reasoning as the
platform-scoped ``billing_payment_references`` ledger.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.core.models.base import Base


class InviteCode(Base):
    """One invite code, redeemable ``max_uses`` times."""

    __tablename__ = "invite_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # The secret itself. Upper-case, no ambiguous characters.
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    # Optional expiry (the founder can bound a code to a campaign).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free-text context: who the code was issued to ("Amina - cohort 1").
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Who issued it (the founder's name/email, not a user id: the issuer is not
    # necessarily a tenant user).
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The tenant that redeemed it (single-use codes: set once).
    redeemed_by_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(),
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InviteCode {self.code} uses={self.uses}/{self.max_uses}>"

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "max_uses": self.max_uses,
            "uses": self.uses,
            "is_active": self.is_active,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "note": self.note,
            "created_by": self.created_by,
            "redeemed_by_tenant_id": str(self.redeemed_by_tenant_id)
            if self.redeemed_by_tenant_id
            else None,
            "redeemed_at": self.redeemed_at.isoformat() if self.redeemed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
