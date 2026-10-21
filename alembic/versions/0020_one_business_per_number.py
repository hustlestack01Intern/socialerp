"""One business per WhatsApp number (M1.6, U81).

The pricing page asks for "one trial per business", and the invite gate stops
strangers, but nothing stopped a business from opening a second account on the
same WhatsApp number - the number is the business identity in this product
(the Coexistence flow connects the *existing* number to one business), so a
second account on it is a second trial for the same business.

What this migration does:

* ``tenants.owner_phone`` gets a **lookup index** (not unique) so the
  "is this number taken?" check is an index scan, not a table scan.

What this migration deliberately does **not** do:

* it does not make the column UNIQUE. A unique index is the obvious database
  answer, but it makes the shared ``tenants`` table unforgiving for every
  other writer of it - including the tenant-isolation test fixtures in
  ``tests/billing/test_billing_service.py``, which create two tenants with one
  placeholder number. Those tests are a protected zone, and a migration must
  not be made to pass by editing them.

So the rule lives where the business is created, and is made race-free with a
Postgres transaction-scoped advisory lock (``packages/core/auth/
tenant_claims.py``): lock the number, look it up, insert, commit. Two
registrations for the same number serialise on the lock, so the second one
sees the first one's row and is refused with a 409. The check-then-insert is
atomic against other registrations because the lock is held to the end of the
transaction.

The migration still refuses to run when duplicate numbers exist, because a
production database with two businesses on one number is a question for a
human: this migration never picks a winner and never deletes tenant data.

The downgrade drops the index, which restores the old behaviour (a full scan
on the phone lookup). It does not delete data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_one_business_per_number"
down_revision = "0019_invite_code_grants"
branch_labels = None
depends_on = None


def _duplicates() -> list[tuple[str, int]]:
    """The phone numbers that more than one tenant uses."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT owner_phone, count(*) FROM tenants "
            "GROUP BY owner_phone HAVING count(*) > 1 ORDER BY 2 DESC"
        )
    ).fetchall()
    return [(row[0], int(row[1])) for row in rows]


def upgrade() -> None:
    duplicates = _duplicates()
    if duplicates:
        listing = ", ".join(f"{phone} x{count}" for phone, count in duplicates)
        raise RuntimeError(
            "cannot create the one-business-per-number index: these phone "
            f"numbers are used by more than one tenant: {listing}. Resolve the "
            "duplicates by hand (merge or delete the tenant), then re-run the "
            "migration. This migration never deletes tenant data."
        )
    op.create_index(
        "ix_tenants_owner_phone",
        "tenants",
        ["owner_phone"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tenants_owner_phone", table_name="tenants")
