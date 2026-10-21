"""Least privilege for the invite-code table (M1.5, skeptic NEW-3).

``0017_invite_codes`` granted the application role a table-wide ``UPDATE``
on ``invite_codes``. The service only ever runs one conditional statement -
``UPDATE invite_codes SET uses = uses + 1 ... WHERE code = :code AND ...`` -
so the grant was broader than the code needs: any future path could reset
``uses`` to 0 or clear ``expires_at`` and re-mint redemptions from a single
code.

This migration narrows the grant to the four columns the redemption
actually writes, and revokes the table-wide ``UPDATE``. ``SELECT`` and
``INSERT`` stay: the handler reads a code to validate it and inserts one row
when a code is issued.

The downgrade restores the previous, broader grant.
"""

from __future__ import annotations

from alembic import op

revision = "0019_invite_code_grants"
down_revision = "0018_missing_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE UPDATE ON invite_codes FROM socialerp_app")
    op.execute(
        "GRANT UPDATE (uses, last_redeemed_at, redeemed_by_tenant_id, redeemed_at) "
        "ON invite_codes TO socialerp_app"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE UPDATE (uses, last_redeemed_at, redeemed_by_tenant_id, redeemed_at) "
        "ON invite_codes FROM socialerp_app"
    )
    op.execute("GRANT UPDATE ON invite_codes TO socialerp_app")
