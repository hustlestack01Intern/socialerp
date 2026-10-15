"""Align drifted ``metadata`` columns with the ORM as ``metadata_json``.

Migrations 0002/0003/0005/0006 created the tenant tables' JSONB metadata column
as ``metadata``, but every ORM model maps the attribute ``metadata_json`` (the
convention already used by ``audit_log`` and ``payments``). Against a migrated
database the ORM therefore referenced a column that does not exist, so any
table load raised. This migration renames ``metadata`` -> ``metadata_json`` on
the 13 affected tables and renames them back on downgrade.

Note: the test schema is built from ``Base.metadata.create_all`` (which already
uses ``metadata_json``), so this migration only matters for the migrated
database; it is a no-op if the column is already named ``metadata_json``.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0009_metadata_rename"
down_revision = "0008_payments"
branch_labels = None
depends_on = None

# Tables whose JSONB metadata column was created as ``metadata``; the ORM maps
# all of them as ``metadata_json``.
TABLES = (
    "categories",
    "customers",
    "locations",
    "order_items",
    "orders",
    "product_variants",
    "products",
    "reservations",
    "suppliers",
    "whatsapp_contacts",
    "whatsapp_conversations",
    "whatsapp_messages",
    "whatsapp_templates",
)


def _column_exists(table: str, column: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t AND column_name = :c"
        ),
        {"t": table, "c": column},
    )
    return result.first() is not None


def upgrade() -> None:
    for table in TABLES:
        if _column_exists(table, "metadata"):
            op.alter_column(table, "metadata", new_column_name="metadata_json")


def downgrade() -> None:
    for table in TABLES:
        if _column_exists(table, "metadata_json"):
            op.alter_column(table, "metadata_json", new_column_name="metadata")
