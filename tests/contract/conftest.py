"""Contract-test harness.

Starts the canonical fake Meta and fake Daraja servers on the ports the
contract tests target (8081 / 8082) for the whole session, so `make test`
and `make check` are self-contained (no Docker or manually started servers
required). If a healthy server is already listening (e.g. `make fake-all` or
CI's fake profile), it is reused and not restarted.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator

import httpx
import pytest

from tests.integrations.server_harness import run_uvicorn

FAKE_META_PORT = 8081
FAKE_DARAJA_PORT = 8082


def _is_healthy(url: str) -> bool:
    try:
        return httpx.get(f"{url}/health", timeout=1.0).status_code == 200
    except Exception:
        return False


@contextlib.contextmanager
def _ensure_server(module: str, port: int) -> Generator[str]:
    base_url = f"http://127.0.0.1:{port}"
    if _is_healthy(base_url):
        yield base_url
        return
    with run_uvicorn(module, port) as url:
        yield url


@pytest.fixture(scope="session", autouse=True)
def fake_provider_servers() -> Generator[None]:
    """Run fake Meta + Daraja servers for the contract-test session."""
    with (
        _ensure_server("packages.integrations.fake_meta.main", FAKE_META_PORT),
        _ensure_server("packages.integrations.fake_daraja.main", FAKE_DARAJA_PORT),
    ):
        yield
