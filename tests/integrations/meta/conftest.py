"""
Fixtures for Fake Meta server integration tests.

Starts the test fake Meta server (packages.integrations.meta.fakes.main) on
port 8080 for the duration of the session, matching the base_url and the
webhook verify token / app secret used by the tests.
"""

from __future__ import annotations

import pytest

from tests.integrations.server_harness import run_uvicorn


@pytest.fixture(scope="session", autouse=True)
def fake_meta_server():
    """Run the fake Meta server on port 8080 for the whole session."""
    with run_uvicorn(
        "packages.integrations.meta.fakes.main",
        port=8080,
        cleanup_paths=["/tmp/fake_meta.db"],
    ) as base_url:
        yield base_url
