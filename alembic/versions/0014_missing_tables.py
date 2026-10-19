"""M0.3 alignment: tables present in the ORM but absent from migrations.

The staging gate builds the test schema from ORM metadata, so tests passed
while a freshly migrated database was missing four tables the merged code
writes to:

- ``onboarding_wizard_progress`` (onboarding wizard state)
- ``customer_identities`` (CRM channel identity linking)
- ``outbox`` (``socialerp.outbox.add_outbox_event`` writes here)
- ``idempotency_keys`` (``socialerp.idempotency`` dedupe storage)

Every tenant-scoped table gets the standard isolation pattern
(policy + ``socialerp_app`` grants).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0014_missing_tables"
down_revision = "0013_instagram"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "onboarding_wizard_progress",
    "customer_identities",
    "outbox",
    "idempotency_keys",
)


def upgrade() -> None:
    # onboarding_wizard_progress (one row per tenant)
    op.create_table(
        "onboarding_wizard_progress",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "state",
            sa.String(length=30),
            server_default="NOT_STARTED",
            nullable=False,
        ),
        sa.Column("current_step", sa.Integer(), nullable=False),
        sa.Column(
            "step_timings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "fallback_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("launch_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.create_index(
        "ix_onboarding_wizard_progress_state",
        "onboarding_wizard_progress",
        ["state"],
    )

    # customer_identities
    op.create_table(
        "customer_identities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("channel_identity", sa.String(length=255), nullable=False),
        sa.Column("channel_identity_type", sa.String(length=20), nullable=False),
        sa.Column("consent_given_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_source", sa.String(length=50), nullable=True),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "customer_id",
            "channel",
            "channel_identity",
            name="uq_customer_identities_tenant_customer_channel",
        ),
    )
    op.create_index("ix_customer_identities_customer_id", "customer_identities", ["customer_id"])
    op.create_index(
        "ix_customer_identities_tenant_channel_identity",
        "customer_identities",
        ["tenant_id", "channel", "channel_identity"],
    )
    op.create_index(
        "ix_customer_identities_tenant_customer",
        "customer_identities",
        ["tenant_id", "customer_id"],
    )
    op.create_index("ix_customer_identities_tenant_id", "customer_identities", ["tenant_id"])

    # outbox (written by socialerp.outbox.add_outbox_event)
    op.create_table(
        "outbox",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_event_type", "outbox", ["event_type"])
    op.create_index("ix_outbox_status", "outbox", ["status"])
    op.create_index("ix_outbox_created_at", "outbox", ["created_at"])
    op.create_index("ix_outbox_idempotency", "outbox", ["idempotency_key"], unique=True)
    op.create_index(
        "ix_outbox_tenant_status_created",
        "outbox",
        ["tenant_id", "status", "created_at"],
    )

    # idempotency_keys
    op.create_table(
        "idempotency_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_idempotency_keys_key", "idempotency_keys", ["key"])
    op.create_index("ix_idempotency_keys_scope", "idempotency_keys", ["scope"])
    op.create_index("ix_idempotency_keys_request_hash", "idempotency_keys", ["request_hash"])
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])
    op.create_index(
        "ix_idempotency_tenant_scope_key",
        "idempotency_keys",
        ["tenant_id", "scope", "key"],
        unique=True,
    )
    op.create_index(
        "ix_idempotency_tenant_scope_hash",
        "idempotency_keys",
        ["tenant_id", "scope", "request_hash"],
        unique=True,
    )
    op.create_index("ix_idempotency_expires", "idempotency_keys", ["expires_at"])

    # Row-level security for every tenant-scoped table.
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_isolation ON {table}
            FOR ALL
            USING (tenant_id = app.current_tenant_id())
            WITH CHECK (tenant_id = app.current_tenant_id())
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO socialerp_app")


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("idempotency_keys")
    op.drop_table("outbox")
    op.drop_table("customer_identities")
    op.drop_table("onboarding_wizard_progress")
