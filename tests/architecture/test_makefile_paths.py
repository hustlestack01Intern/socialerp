from pathlib import Path


def test_makefile_has_no_deleted_paths() -> None:
    makefile = Path("Makefile")
    text = makefile.read_text()
    assert "infra/docker-compose.yml" not in text
    assert "packages/core/alembic.ini" not in text
