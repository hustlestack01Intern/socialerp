"""Phase 9: Instagram tables with RLS.

Instagram contacts, conversations, messages, comment-to-DM eligibility,
and deduplication. Every table carries tenant_id and follows the standard
isolation pattern (policy + socialerp_app grants).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0013_instagram"
down_revision = "0012_crm"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "instagram_contacts",
    "instagram_conversations",
    "instagram_messages",
    "instagram_dm_eligibility",
    "instagram_dedupe",
)


def upgrade() -> None:
    # instagram_contacts
    op.create_table(
        "instagram_contacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ig_scoped_user_id", sa.String(100), nullable=False),
        sa.Column("ig_handle", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("profile_picture_url", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.String(100), nullable=True),
        # 24h window tracking
        sa.Column(
            "last_customer_message_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "window_expires_at", sa.DateTime(timezone=True), nullable=True, index=True
        ),
        # Consent & preferences
        sa.Column(
            "opted_in_marketing",
            sa.Boolean(),
            nullable=False,
            default=False,
            server_default="false",
        ),
        sa.Column("opted_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "blocked", sa.Boolean(), nullable=False, default=False, server_default="false"
        ),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        # Metadata
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            default=dict,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_instagram_contacts_tenant_ig_user_id",
        "instagram_contacts",
        ["tenant_id", "ig_scoped_user_id"],
        unique=True,
    )
    op.create_index(
        "ix_instagram_contacts_tenant_ig_handle",
        "instagram_contacts",
        ["tenant_id", "ig_handle"],
        unique=True,
    )
    op.create_index(
        "ix_instagram_contacts_tenant_window",
        "instagram_contacts",
        ["tenant_id", "window_expires_at"],
    )
    op.create_index(
        "ix_instagram_contacts_tenant_thread",
        "instagram_contacts",
        ["tenant_id", "thread_id"],
    )

    # instagram_conversations
    op.create_table(
        "instagram_conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instagram_contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.String(20),
            nullable=False,
            default="open",
            server_default="open",
        ),
        sa.Column("thread_id", sa.String(100), nullable=True),
        # Handoff
        sa.Column("handoff_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handoff_reason", sa.Text(), nullable=True),
        sa.Column(
            "handoff_assigned_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("handoff_accepted_at", sa.DateTime(timezone=True), nullable=True),
        # SLA
        sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_agent_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_customer_message_at", sa.DateTime(timezone=True), nullable=True),
        # Metadata
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            default=dict,
            server_default="{}",
        ),
        sa.Column(
            "tags", postgresql.JSONB(), nullable=False, default=list, server_default="[]"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_instagram_conversations_tenant_state",
        "instagram_conversations",
        ["tenant_id", "state"],
    )
    op.create_index(
        "ix_instagram_conversations_tenant_contact",
        "instagram_conversations",
        ["tenant_id", "contact_id"],
    )
    op.create_index(
        "ix_instagram_conversations_tenant_thread",
        "instagram_conversations",
        ["tenant_id", "thread_id"],
    )
    op.create_index(
        "ix_instagram_conversations_tenant_handoff",
        "instagram_conversations",
        ["tenant_id", "handoff_requested_at"],
    )
    op.create_index(
        "ix_instagram_conversations_tenant_sla",
        "instagram_conversations",
        ["tenant_id", "first_response_due_at"],
    )

    # instagram_messages
    op.create_table(
        "instagram_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instagram_contacts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instagram_conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("message_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, default="sent", server_default="sent"),
        # Content
        sa.Column("text_body", sa.Text(), nullable=True),
        sa.Column("media_url", sa.Text(), nullable=True),
        sa.Column("media_mime_type", sa.String(100), nullable=True),
        sa.Column("media_sha256", sa.String(64), nullable=True),
        sa.Column("media_caption", sa.Text(), nullable=True),
        # Interactive/Quick reply data
        sa.Column("interactive_data", postgresql.JSONB(), nullable=True),
        sa.Column("quick_reply_payload", sa.String(512), nullable=True),
        # Referral
        sa.Column("referral_data", postgresql.JSONB(), nullable=True),
        # Context
        sa.Column(
            "context_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instagram_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("referenced_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        # Meta fields
        sa.Column("meta_message_id", sa.String(100), nullable=True, index=True),
        sa.Column("meta_timestamp", sa.DateTime(timezone=True), nullable=True),
        # Processing
        sa.Column(
            "is_echo",
            sa.Boolean(),
            nullable=False,
            default=False,
            server_default="false",
        ),
        sa.Column("processing_error", sa.Text(), nullable=True),
        # Metadata
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            default=dict,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_instagram_messages_tenant_created",
        "instagram_messages",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_instagram_messages_tenant_conversation",
        "instagram_messages",
        ["tenant_id", "conversation_id"],
    )
    op.create_index(
        "ix_instagram_messages_tenant_meta_id",
        "instagram_messages",
        ["tenant_id", "meta_message_id"],
    )
    op.create_index(
        "ix_instagram_messages_tenant_status",
        "instagram_messages",
        ["tenant_id", "status"],
    )

    # instagram_dm_eligibility
    op.create_table(
        "instagram_dm_eligibility",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instagram_contacts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("comment_id", sa.String(100), nullable=False),
        sa.Column("media_id", sa.String(100), nullable=True),
        sa.Column("thread_id", sa.String(100), nullable=True),
        sa.Column(
            "expires_at", sa.DateTime(timezone=True), nullable=False, index=True
        ),
        sa.Column("attempts_remaining", sa.Integer(), nullable=False, default=1),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "consumed_by_message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instagram_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            default=dict,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_instagram_dm_eligibility_tenant_expires",
        "instagram_dm_eligibility",
        ["tenant_id", "expires_at"],
    )
    op.create_index(
        "ix_instagram_dm_eligibility_tenant_comment",
        "instagram_dm_eligibility",
        ["tenant_id", "comment_id"],
        unique=True,
    )

    # instagram_dedupe
    op.create_table(
        "instagram_dedupe",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider_message_id", sa.String(100), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instagram_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_instagram_dedupe_tenant_processed",
        "instagram_dedupe",
        ["tenant_id", "processed_at"],
    )
    op.create_index(
        "ix_instagram_dedupe_tenant_provider",
        "instagram_dedupe",
        ["tenant_id", "provider_message_id"],
        unique=True,
    )

    # Enable RLS and create policies
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
        op.execute(f"REVOKE ALL ON {table} FROM socialerp_app")
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("instagram_dedupe")
    op.drop_table("instagram_dm_eligibility")
    op.drop_table("instagram_messages")
    op.drop_table("instagram_conversations")
    op.drop_table("instagram_contacts")