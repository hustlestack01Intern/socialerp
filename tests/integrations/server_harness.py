"""
Server harness for integration tests.

Starts fake provider servers (Meta, Daraja) as uvicorn subprocesses for the
duration of a test session, so integration tests are self-contained and do
not require the developer to run the fake servers manually.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from collections.abc import Generator, Sequence
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]


@contextlib.contextmanager
def run_uvicorn(
    module: str,
    port: int,
    health_path: str = "/health",
    cleanup_paths: Sequence[str] = (),
) -> Generator[str, None, None]:
    """Run a uvicorn app in a subprocess until the context exits.

    Waits for the health endpoint before yielding. Raises RuntimeError with
    captured output if the server fails to start. ``cleanup_paths`` (e.g.
    SQLite state files) are removed before start for a deterministic session.
    """
    for path in cleanup_paths:
        Path(path).unlink(missing_ok=True)

    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            f"{module}:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        deadline = time.monotonic() + 30.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(
                    f"Fake server {module} exited early (code {proc.returncode}):\n{output}"
                )
            try:
                response = httpx.get(f"{base_url}{health_path}", timeout=1.0)
                if response.status_code == 200:
                    break
            except Exception as exc:  # server not up yet
                last_error = exc
            time.sleep(0.2)
        else:
            raise RuntimeError(
                f"Fake server {module} did not become healthy at {base_url} "
                f"within 30s (last error: {last_error})"
            )

        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
