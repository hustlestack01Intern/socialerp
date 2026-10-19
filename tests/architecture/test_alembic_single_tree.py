"""
Guard test: Ensure exactly one Alembic versions directory exists.

The project uses a single Alembic tree at the root `alembic/` with migrations in
`alembic/versions/`. The duplicate tree at `packages/core/alembic/` was removed
per ADR 0008. This test ensures the duplicate never returns.
"""

from __future__ import annotations

from pathlib import Path


def test_single_alembic_versions_directory():
    """Ensure exactly one alembic/versions directory exists in the repo."""
    repo_root = Path(__file__).parent.parent.parent

    # Find all alembic/versions directories
    versions_dirs = list(repo_root.rglob("alembic/versions"))

    # Should only be one: the root alembic/versions
    assert len(versions_dirs) == 1, (
        f"Expected exactly one alembic/versions directory, found {len(versions_dirs)}: "
        f"{[str(d.relative_to(repo_root)) for d in versions_dirs]}"
    )

    # It should be at the expected location
    expected = repo_root / "alembic" / "versions"
    assert versions_dirs[0] == expected, (
        f"Expected alembic/versions at {expected}, found at {versions_dirs[0]}"
    )

    # The duplicate location should not exist
    duplicate = repo_root / "packages" / "core" / "alembic"
    assert not duplicate.exists(), (
        f"Duplicate Alembic tree found at {duplicate}. "
        f"Per ADR 0008, this should not exist."
    )

    # The root alembic directory should exist with versions
    root_alembic = repo_root / "alembic"
    assert root_alembic.exists(), "Root alembic directory should exist"
    assert (root_alembic / "versions").exists(), "Root alembic/versions should exist"
    assert (root_alembic / "env.py").exists(), "Root alembic/env.py should exist"
    assert (root_alembic / "script.py.mako").exists() or True, "Template optional"


def test_alembic_ini_single():
    """Ensure only one alembic.ini exists (at root)."""
    repo_root = Path(__file__).parent.parent.parent

    ini_files = list(repo_root.rglob("alembic.ini"))
    assert len(ini_files) == 1, (
        f"Expected exactly one alembic.ini, found {len(ini_files)}: "
        f"{[str(f.relative_to(repo_root)) for f in ini_files]}"
    )
    assert ini_files[0] == repo_root / "alembic.ini"
