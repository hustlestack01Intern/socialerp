"""A small, honest rate limiter for the auth endpoints (M1.5).

The constitution asks for rate limits and `packages/core/config.py` has
carried `RATE_LIMIT_REQUESTS_PER_MINUTE` since the beginning without
anything reading it. The invite gate made that gap concrete: the status
code of a registration tells an attacker whether a code is valid, so the
practical mitigation is to make probing expensive.

What this is
------------
A fixed-window counter, per client IP, per endpoint group, in process
memory. It answers 429 with a `Retry-After` header.

What this is NOT (say it out loud, do not pretend)
--------------------------------------------------
* It is **per process**. With N API workers the effective limit is N
  times the configured one, and a restart clears the counters. It stops a
  script, not a distributed attack.
* It is **not durable**. A shared limiter (Redis is already a dependency)
  is the real answer; this is the honest first layer, and the window it
  opens is recorded in the uncertainty ledger.
* It only covers the paths it is given. The API is not rate limited
  everywhere yet.

The limit is passed in, not read from the environment here, so the
middleware never keeps its own copy of the settings (the settings object
is cached per import path, and a second copy is a second truth).
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# The endpoints that are worth guessing at: a registration (an invite code)
# and a login (a password).
DEFAULT_PROTECTED_PATHS: tuple[str, ...] = (
    "/api/v1/auth/register",
    "/api/v1/auth/login",
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window, per-IP rate limit for a set of paths."""

    def __init__(
        self,
        app: object,
        *,
        limit: int,
        window_seconds: int = 60,
        paths: tuple[str, ...] = DEFAULT_PROTECTED_PATHS,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.limit = limit
        self.window_seconds = window_seconds
        self.paths = paths
        self._hits: dict[str, list[float]] = defaultdict(list)

    def _client_key(self, request: Request) -> str:
        client = request.client
        return client.host if client is not None else "unknown"

    def _prune(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._hits[key] if now - t < self.window_seconds]
        self._hits[key] = hits
        return hits

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        path = request.url.path
        if self.limit <= 0 or path not in self.paths:
            return await call_next(request)

        key = f"{path}|{self._client_key(request)}"
        now = time.monotonic()
        hits = self._prune(key, now)

        if len(hits) >= self.limit:
            oldest = hits[0]
            retry_after = max(1, int(self.window_seconds - (now - oldest)))
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": (
                        "Too many attempts from this address. "
                        f"Try again in {retry_after} seconds."
                    )
                },
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)
        self._hits[key] = hits
        return await call_next(request)


__all__ = ["DEFAULT_PROTECTED_PATHS", "RateLimitMiddleware"]
