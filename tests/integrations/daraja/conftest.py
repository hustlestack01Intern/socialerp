"""
Fixtures for Fake Daraja server integration tests.

Starts the test fake Daraja server (packages.integrations.daraja.fakes.main)
on port 8081 for the duration of the session, matching the base_url and the
shortcode / passkey used by the tests.
"""

from __future__ import annotations

import pytest

from tests.integrations.server_harness import run_uvicorn


@pytest.fixture(scope="session", autouse=True)
def fake_daraja_server():
    """Run the fake Daraja server on port 8081 for the whole session."""
    with run_uvicorn(
        "packages.integrations.daraja.fakes.main",
        port=8081,
        cleanup_paths=["/tmp/fake_daraja.db"],
    ) as base_url:
        yield base_url
