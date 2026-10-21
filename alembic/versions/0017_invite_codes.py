"""Invite codes: the gate that keeps the first cohort invited (M1.5).

Adds ``invite_codes`` — a platform-scoped table (no ``tenant_id`` column, no
RLS, like the ``billing_payment_references`` ledger): the code is a platform
secret that the registration handler must be able to read for every tenant, and
it carries no customer PII. The redemption trail lives in the tenant-scoped
``audit_log`` and in ``redeemed_by_tenant_id`` on the row.

Grants: SELECT/INSERT/UPDATE. The app may issue codes and consume a use (the
redemption is a conditional UPDATE, so it must be able to write), but it may
not DELETE them (a code is never silently destroyed).

Revision ID: 0017_invite_codes
Revises: 0016_billing_payment_references
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0017_invite_codes"
down_revision = "0016_billing_payment_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invite_codes",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.Column("redeemed_by_tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_invite_codes_code"),
    )
    op.create_index("ix_invite_codes_code", "invite_codes", ["code"])
    # Platform-scoped on purpose (no RLS): the registration handler must be
    # able to read a code for any tenant, and the code holds no customer PII.
    op.execute("GRANT SELECT, INSERT, UPDATE ON invite_codes TO socialerp_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON invite_codes FROM socialerp_app")
    op.drop_index("ix_invite_codes_code", table_name="invite_codes")
    op.drop_table("invite_codes")
