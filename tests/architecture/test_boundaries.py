"""
Dependency-rule (boundary) tests — constitution §3 Principle 3.

The canonical dependency direction is ``delivery -> orders`` (delivery is the
downstream physical lifecycle of an order). ``orders`` must NOT import the
``delivery`` package at module scope: doing so created the import cycle that
broke test collection (ADR 0015). A deferred (function-scope) import is allowed.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ORDERS_DIR = REPO_ROOT / "packages" / "core" / "orders"


def _module_level_imports(path: Path) -> Iterator[str]:
    """Yield the module names imported at the top level of ``path``.

    Only ``Module.body`` statements are inspected, so imports inside functions
    (the sanctioned deferred-import escape hatch) are ignored.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            yield node.module or ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name


def test_orders_package_does_not_import_delivery_at_module_scope() -> None:
    offenders: list[tuple[str, str]] = []
    for py in sorted(ORDERS_DIR.rglob("*.py")):
        for module in _module_level_imports(py):
            if module == "packages.core.delivery" or module.startswith("packages.core.delivery."):
                offenders.append((py.name, module))
    assert not offenders, (
        "orders must not import delivery at module scope (use an injected lookup "
        f"or a deferred import); found {offenders}"
    )


def test_delivery_package_may_import_orders() -> None:
    """The allowed direction: delivery depends on orders (never the reverse)."""
    modules = {
        module
        for py in sorted((REPO_ROOT / "packages" / "core" / "delivery").rglob("*.py"))
        for module in _module_level_imports(py)
    }
    assert any(module.startswith("packages.core.orders") for module in modules)
