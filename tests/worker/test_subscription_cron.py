"""M1.3: the subscription-expiry cron is registered with the worker.

Importing ``apps.worker.main`` instantiates ``WorkerSettings`` at class-definition
time, which requires a full Settings object, so we set the required env var
before importing. The assertion is that the cron function is declared in the
worker's ``functions`` and ``cron_jobs`` lists (a wiring regression there would
silently stop trial expiries).
"""

from __future__ import annotations

import pytest


def test_expire_subscriptions_cron_is_registered(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "x" * 32)
    monkeypatch.setenv("ENVIRONMENT", "development")

    from apps.worker import main as worker

    func_names = [getattr(f, "__name__", str(f)) for f in worker.WorkerSettings.functions]
    assert "expire_subscriptions" in func_names

    cron_funcs = {job["func"] for job in worker.WorkerSettings.cron_jobs}
    assert "expire_subscriptions" in cron_funcs


@pytest.mark.integration
def test_expire_subscriptions_is_callable():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("ENCRYPTION_KEY", "x" * 32)
    monkeypatch.setenv("ENVIRONMENT", "development")
    try:
        from apps.worker.main import expire_subscriptions

        assert callable(expire_subscriptions)
    finally:
        monkeypatch.undo()
