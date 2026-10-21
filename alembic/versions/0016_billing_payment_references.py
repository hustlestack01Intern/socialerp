"""Billing: payment-reference ledger (money path F2, U68).

Adds ``billing_payment_references`` — the UNIQUE constraint that makes an
M-Pesa confirmation code pay for at most ONE plan change across ALL tenants.
Deliberately **without RLS**: cross-tenant uniqueness is the invariant, and
the row carries no customer PII (a confirmation code is a merchant-facing
transaction id). Grants only SELECT/INSERT (the app never updates/deletes
this ledger — append-only in practice).

Revision ID: 0016_billing_payment_references
Revises: 0015_billing
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0016_billing_payment_references"
down_revision = "0015_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_payment_references",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("reference", sa.String(length=30), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("plan", sa.String(length=20), nullable=False),
        sa.Column("amount_kes", sa.Integer(), nullable=False),
        sa.Column("recorded_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="uq_billing_payment_references_ref"),
    )
    op.create_index(
        "ix_billing_payment_references_tenant_id",
        "billing_payment_references",
        ["tenant_id"],
    )
    # Platform-scoped on purpose (no RLS): the app must detect a reference
    # already used by ANOTHER tenant. Append-only grants.
    op.execute("GRANT SELECT, INSERT ON billing_payment_references TO socialerp_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON billing_payment_references FROM socialerp_app")
    op.drop_index(
        "ix_billing_payment_references_tenant_id",
        table_name="billing_payment_references",
    )
    op.drop_table("billing_payment_references")
