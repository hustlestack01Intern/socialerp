"""
M2: Add WhatsApp tables with Row-Level Security (RLS)

This migration creates:
- whatsapp_contacts table
- whatsapp_conversations table
- whatsapp_messages table
- whatsapp_templates table
- whatsapp_dedupe table

And enables RLS on all tables with policies that enforce tenant isolation.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_whatsapp_tables"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # WHATSAPP CONTACTS TABLE (no dependencies)
    # =========================================================================
    op.create_table(
        "whatsapp_contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("wa_id", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("profile_picture_url", sa.Text, nullable=True),
        sa.Column("last_customer_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opted_in_marketing", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("opted_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_reason", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_whatsapp_contacts_tenant_id", "whatsapp_contacts", ["tenant_id"])
    op.create_index("ix_whatsapp_contacts_tenant_window", "whatsapp_contacts", ["tenant_id", "window_expires_at"])
    op.create_index("ix_whatsapp_contacts_tenant_blocked", "whatsapp_contacts", ["tenant_id", "blocked"])
    op.create_unique_constraint("uq_whatsapp_contacts_tenant_wa_id", "whatsapp_contacts", ["tenant_id", "wa_id"])
    op.create_unique_constraint("uq_whatsapp_contacts_tenant_phone", "whatsapp_contacts", ["tenant_id", "phone"])

    # Enable RLS on whatsapp_contacts
    op.execute("ALTER TABLE whatsapp_contacts ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY whatsapp_contact_isolation ON whatsapp_contacts
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # WHATSAPP CONVERSATIONS TABLE (depends on whatsapp_contacts)
    # =========================================================================
    op.create_table(
        "whatsapp_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(30), nullable=False, server_default=sa.text("'open'")),
        sa.Column("handoff_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handoff_reason", sa.Text, nullable=True),
        sa.Column("handoff_assigned_to", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("handoff_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_agent_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_customer_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("tags", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["whatsapp_contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["handoff_assigned_to"], ["users.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_whatsapp_conversations_tenant_id", "whatsapp_conversations", ["tenant_id"])
    op.create_index("ix_whatsapp_conversations_tenant_state", "whatsapp_conversations", ["tenant_id", "state"])
    op.create_index("ix_whatsapp_conversations_tenant_contact", "whatsapp_conversations", ["tenant_id", "contact_id"])
    op.create_index("ix_whatsapp_conversations_tenant_handoff", "whatsapp_conversations", ["tenant_id", "handoff_requested_at"])
    op.create_index("ix_whatsapp_conversations_tenant_sla", "whatsapp_conversations", ["tenant_id", "first_response_due_at"])
    op.create_index("ix_whatsapp_conversations_contact_id", "whatsapp_conversations", ["contact_id"])

    # Enable RLS on whatsapp_conversations
    op.execute("ALTER TABLE whatsapp_conversations ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY whatsapp_conversation_isolation ON whatsapp_conversations
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # WHATSAPP MESSAGES TABLE (depends on whatsapp_contacts and whatsapp_conversations)
    # =========================================================================
    op.create_table(
        "whatsapp_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("message_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'sent'")),
        sa.Column("text_body", sa.Text, nullable=True),
        sa.Column("media_url", sa.Text, nullable=True),
        sa.Column("media_mime_type", sa.String(100), nullable=True),
        sa.Column("media_sha256", sa.String(64), nullable=True),
        sa.Column("media_caption", sa.Text, nullable=True),
        sa.Column("interactive_data", postgresql.JSONB, nullable=True),
        sa.Column("template_name", sa.String(512), nullable=True),
        sa.Column("template_language", sa.String(10), nullable=True),
        sa.Column("template_components", postgresql.JSONB, nullable=True),
        sa.Column("context_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("referenced_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("meta_message_id", sa.String(100), nullable=True),
        sa.Column("meta_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_echo", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("processing_error", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["whatsapp_contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["whatsapp_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["context_message_id"], ["whatsapp_messages.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_whatsapp_messages_tenant_id", "whatsapp_messages", ["tenant_id"])
    op.create_index("ix_whatsapp_messages_tenant_created", "whatsapp_messages", ["tenant_id", "created_at"])
    op.create_index("ix_whatsapp_messages_tenant_conversation", "whatsapp_messages", ["tenant_id", "conversation_id"])
    op.create_index("ix_whatsapp_messages_tenant_meta_id", "whatsapp_messages", ["tenant_id", "meta_message_id"])
    op.create_index("ix_whatsapp_messages_tenant_status", "whatsapp_messages", ["tenant_id", "status"])
    op.create_index("ix_whatsapp_messages_contact_id", "whatsapp_messages", ["contact_id"])
    op.create_index("ix_whatsapp_messages_conversation_id", "whatsapp_messages", ["conversation_id"])
    op.create_index("ix_whatsapp_messages_meta_message_id", "whatsapp_messages", ["meta_message_id"])

    # Enable RLS on whatsapp_messages
    op.execute("ALTER TABLE whatsapp_messages ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY whatsapp_message_isolation ON whatsapp_messages
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # WHATSAPP TEMPLATES TABLE (depends only on tenants)
    # =========================================================================
    op.create_table(
        "whatsapp_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("header", postgresql.JSONB, nullable=True),
        sa.Column("body", postgresql.JSONB, nullable=False),
        sa.Column("footer", postgresql.JSONB, nullable=True),
        sa.Column("buttons", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("meta_template_id", sa.String(100), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )

    op.create_index("ix_whatsapp_templates_tenant_id", "whatsapp_templates", ["tenant_id"])
    op.create_index("ix_whatsapp_templates_tenant_status", "whatsapp_templates", ["tenant_id", "status"])
    op.create_unique_constraint("uq_whatsapp_templates_tenant_name_lang", "whatsapp_templates", ["tenant_id", "name", "language"])

    # Enable RLS on whatsapp_templates
    op.execute("ALTER TABLE whatsapp_templates ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY whatsapp_template_isolation ON whatsapp_templates
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # WHATSAPP DEDUPE TABLE (depends on tenants and whatsapp_messages)
    # =========================================================================
    op.create_table(
        "whatsapp_dedupe",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_message_id", sa.String(100), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["whatsapp_messages.id"], ondelete="SET NULL"),
    )

    op.create_index("ix_whatsapp_dedupe_tenant_id", "whatsapp_dedupe", ["tenant_id"])
    op.create_index("ix_whatsapp_dedupe_tenant_processed", "whatsapp_dedupe", ["tenant_id", "processed_at"])
    op.create_unique_constraint("uq_whatsapp_dedupe_tenant_provider_id", "whatsapp_dedupe", ["tenant_id", "provider_message_id"])

    # Enable RLS on whatsapp_dedupe
    op.execute("ALTER TABLE whatsapp_dedupe ENABLE ROW LEVEL SECURITY")

    op.execute("""
        CREATE POLICY whatsapp_dedupe_isolation ON whatsapp_dedupe
        FOR ALL
        USING (tenant_id = app.current_tenant_id())
        WITH CHECK (tenant_id = app.current_tenant_id())
    """)

    # =========================================================================
    # GRANTS FOR APPLICATION ROLE
    # =========================================================================
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp_contacts TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp_conversations TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp_messages TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp_templates TO socialerp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON whatsapp_dedupe TO socialerp_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO socialerp_app")


def downgrade() -> None:
    # Revoke grants
    op.execute("REVOKE ALL ON whatsapp_contacts FROM socialerp_app")
    op.execute("REVOKE ALL ON whatsapp_conversations FROM socialerp_app")
    op.execute("REVOKE ALL ON whatsapp_messages FROM socialerp_app")
    op.execute("REVOKE ALL ON whatsapp_templates FROM socialerp_app")
    op.execute("REVOKE ALL ON whatsapp_dedupe FROM socialerp_app")

    # Drop policies
    op.execute("DROP POLICY IF EXISTS whatsapp_contact_isolation ON whatsapp_contacts")
    op.execute("DROP POLICY IF EXISTS whatsapp_conversation_isolation ON whatsapp_conversations")
    op.execute("DROP POLICY IF EXISTS whatsapp_message_isolation ON whatsapp_messages")
    op.execute("DROP POLICY IF EXISTS whatsapp_template_isolation ON whatsapp_templates")
    op.execute("DROP POLICY IF EXISTS whatsapp_dedupe_isolation ON whatsapp_dedupe")

    # Drop tables in reverse dependency order
    op.drop_table("whatsapp_dedupe")
    op.drop_table("whatsapp_templates")
    op.drop_table("whatsapp_messages")
    op.drop_table("whatsapp_conversations")
    op.drop_table("whatsapp_contacts")
