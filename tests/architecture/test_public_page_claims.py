"""The public pages may not claim what the system does not do (M1.5).

Every public page is a promise. The constitution (and plain honesty) says a
page may only state what the code does today. Two false claims were found by
the skeptic on 2026-10-21 and are pinned here:

* ``/security`` said "a query without the right tenant context returns
  nothing". The row-level security policies are installed, but the role the
  API connects with owns the tables, and a table owner is not subject to the
  policies - so the database is not yet the last line of defence.
* the landing page said "Isolation enforced by the database" for the same
  reason.

These tests fail when a page drifts back into claiming enforcement it does
not have. The honest wording is not a promise: it is a statement about the
current state of the build.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO = Path(__file__).resolve().parents[2]
SECURITY_PAGE = REPO / "apps" / "dashboard" / "src" / "app" / "security" / "page.tsx"
LANDING_PAGE = REPO / "apps" / "dashboard" / "src" / "app" / "page.tsx"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """The page text with the JSX line wrapping collapsed.

    A sentence can be split across lines in the markup ("audit\nlog"), so a
    phrase check has to look at the flattened text.
    """
    return " ".join(_source(path).split()).lower()


def test_security_page_does_not_claim_the_database_enforces_isolation() -> None:
    """The owner role bypasses the policies: the page may not say otherwise."""
    source = _flat(SECURITY_PAGE)
    for banned in (
        "a query without the right tenant context returns nothing",
        "isolation enforced by the database",
        "the database enforces",
    ):
        assert banned not in source, f"/security claims {banned!r}"


def test_security_page_states_the_role_hardening_is_pending() -> None:
    """The unfinished hardening must be on the page, in the open."""
    source = _flat(SECURITY_PAGE)
    assert "not done yet" in source, "/security must keep the 'not done yet' section"
    assert "row-level security" in source or "row level security" in source
    assert "owner" in source and "role" in source, (
        "/security must say that the API connects as the table owner, which is "
        "why the policies do not bite yet"
    )


def test_security_page_keeps_the_other_honest_items() -> None:
    """The page is not a disclaimer page: it must keep the real guarantees."""
    source = _flat(SECURITY_PAGE)
    assert "fails closed" in source, "/security must keep the fail-closed statement"
    assert "audit log" in source, "/security must keep the audit-log statement"


def test_landing_page_does_not_claim_database_enforced_isolation() -> None:
    """Same claim, on the landing page."""
    source = _flat(LANDING_PAGE)
    assert "isolation enforced by the database" not in source
    assert "per tenant" in source, "the landing page keeps the per-tenant tile"
