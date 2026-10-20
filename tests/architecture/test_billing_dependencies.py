"""
Dependency rule: ``packages.core.billing`` must not import ``packages.core.payments``.

Rationale (plan M1.2): our SaaS subscription billing is a different money
domain from the tenant's customer payments (M-Pesa STK). Mixing them risks
co-mingling tenant funds with our own subscription revenue — a compliance and
correctness hazard. Billing is tenant-scoped bookkeeping; it must stay free of
the payment provider machinery.

We scan at *module scope* (top-level imports). A deferred function import is
also disallowed here because the two domains should have no coupling at all.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BILLING_DIR = REPO_ROOT / "packages" / "core" / "billing"


def _all_imports(path: Path) -> Iterator[str]:
    """Yield every imported module name in ``path`` (module and function scope)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            yield node.module or ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


def test_billing_does_not_import_payments() -> None:
    offenders: list[tuple[str, str]] = []
    for py in sorted(BILLING_DIR.rglob("*.py")):
        for module in _all_imports(py):
            if module == "packages.core.payments" or module.startswith("packages.core.payments."):
                offenders.append((py.name, module))
    assert not offenders, (
        "packages.core.billing must not import packages.core.payments "
        f"(SaaS billing is separate from tenant payments); found {offenders}"
    )
