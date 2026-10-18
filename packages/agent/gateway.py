"""
Model-agnostic LLM gateway (Phase 5, ADR 0012).

One interface, many backends. The gateway is the only code that knows a
provider's wire format. It never does business arithmetic; callers pass strict
schemas and validate the output as untrusted input.

Cross-cutting concerns (spend caps, retries, caching, recording) are wrappers
that implement the same :class:`LLMGateway` interface, so each is independently
testable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# USD per 1M tokens (input, output). Local models are free; used to price hosted ones.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


@dataclass(frozen=True)
class LLMMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str


@dataclass(frozen=True)
class LLMRequest:
    messages: list[LLMMessage]
    model: str
    temperature: float = 0.1
    max_tokens: int = 1024
    response_schema: dict[str, Any] | None = None
    timeout_seconds: float = 30.0
    cache_key: str | None = None
    tenant_id: str | None = None


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    model_version: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    cost_usd: float = 0.0
    cached: bool = False
    finish_reason: str | None = None


# --- errors ----------------------------------------------------------------


class LLMError(Exception):
    """Base error for LLM gateway operations."""


class LLMTimeoutError(LLMError):
    """The provider did not respond within the timeout."""


class LLMProviderError(LLMError):
    """The provider returned an error or an unparseable response."""


class SpendCapError(LLMError):
    """A per-tenant or per-request spend cap would be exceeded."""


class LLMGateway(ABC):
    """Abstract, provider-neutral LLM gateway."""

    @abstractmethod
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Run one completion. Callers must treat the output as untrusted."""


def estimate_cost_usd(model: str, usage: LLMUsage, prices: dict[str, tuple[float, float]]) -> float:
    """Estimate USD cost from token usage (0 for unknown/local models)."""
    price = prices.get(model)
    if price is None:
        return 0.0
    in_price, out_price = price
    return (usage.prompt_tokens * in_price + usage.completion_tokens * out_price) / 1_000_000


class ScriptedGateway(LLMGateway):
    """Deterministic gateway for tests and CI. No network."""

    def __init__(self, responses: list[str] | None = None, *, model: str = "scripted") -> None:
        self.responses = list(responses or [])
        self.model = model
        self.calls: list[LLMRequest] = []
        self.error: Exception | None = None
        self.delay_seconds: float = 0.0

    def push(self, text: str) -> None:
        self.responses.append(text)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise self.error
        text = self.responses.pop(0) if self.responses else ""
        return LLMResponse(
            text=text,
            model=self.model,
            model_version="0",
            usage=LLMUsage(
                prompt_tokens=len(request.messages), completion_tokens=len(text.split()),
            ),
            finish_reason="stop",
        )


class CachingGateway(LLMGateway):
    """Cache completions by ``LLMRequest.cache_key`` (in-memory, per process)."""

    def __init__(self, inner: LLMGateway) -> None:
        self.inner = inner
        self._cache: dict[str, LLMResponse] = {}

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if request.cache_key is None:
            return await self.inner.complete(request)
        key = self._cache_key(request)
        hit = self._cache.get(key)
        if hit is not None:
            return LLMResponse(
                text=hit.text,
                model=hit.model,
                model_version=hit.model_version,
                usage=hit.usage,
                cost_usd=hit.cost_usd,
                cached=True,
                finish_reason=hit.finish_reason,
            )
        response = await self.inner.complete(request)
        self._cache[key] = response
        return response

    @staticmethod
    def _cache_key(request: LLMRequest) -> str:
        raw = json.dumps(
            {
                "key": request.cache_key,
                "model": request.model,
                "temperature": request.temperature,
                "messages": [(m.role, m.content) for m in request.messages],
                "schema": request.response_schema,
            },
            sort_keys=True,
        )
        return hashlib.sha256(raw.encode()).hexdigest()


class RetryingGateway(LLMGateway):
    """Retry transient provider errors with bounded backoff."""

    def __init__(
        self,
        inner: LLMGateway,
        *,
        max_attempts: int = 3,
        base_delay: float = 0.1,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.inner = inner
        self.max_attempts = max(1, max_attempts)
        self.base_delay = base_delay
        self._sleep = sleep

    async def complete(self, request: LLMRequest) -> LLMResponse:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self.inner.complete(request)
            except (LLMProviderError, LLMTimeoutError):
                if attempt >= self.max_attempts:
                    raise
                await self._sleep(self.base_delay * (2 ** (attempt - 1)))


class SpendCapGateway(LLMGateway):
    """Enforce per-tenant and per-request spend caps (fail closed)."""

    def __init__(
        self,
        inner: LLMGateway,
        *,
        daily_cap_usd: float,
        max_request_usd: float | None = None,
        now: Callable[[], float] = time.monotonic,
        day_seconds: float = 86_400.0,
    ) -> None:
        self.inner = inner
        self.daily_cap_usd = daily_cap_usd
        self.max_request_usd = max_request_usd
        self._now = now
        self._day_seconds = day_seconds
        self._spend: dict[str, float] = {}
        self._window_start: dict[str, float] = {}

    def spent_today(self, tenant_id: str | None) -> float:
        return self._spend.get(tenant_id or "", 0.0)

    def _roll_window(self, tenant_id: str) -> None:
        start = self._window_start.get(tenant_id)
        now = self._now()
        if start is None or now - start >= self._day_seconds:
            self._window_start[tenant_id] = now
            self._spend[tenant_id] = 0.0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        tenant = request.tenant_id or ""
        self._roll_window(tenant)
        if self.spent_today(tenant) >= self.daily_cap_usd:
            raise SpendCapError(f"Daily LLM spend cap reached for tenant {tenant!r}")
        response = await self.inner.complete(request)
        if self.max_request_usd is not None and response.cost_usd > self.max_request_usd:
            raise SpendCapError("Single request exceeded the per-request spend cap")
        self._spend[tenant] = self.spent_today(tenant) + response.cost_usd
        return response


def build_gateway(
    *,
    inner: LLMGateway,
    daily_cap_usd: float,
    max_attempts: int = 3,
    cache: bool = True,
) -> LLMGateway:
    """Compose the standard safety wrappers around a base gateway."""
    gateway: LLMGateway = inner
    gateway = RetryingGateway(gateway, max_attempts=max_attempts)
    gateway = SpendCapGateway(gateway, daily_cap_usd=daily_cap_usd)
    if cache:
        gateway = CachingGateway(gateway)
    return gateway


__all__ = [
    "DEFAULT_PRICES",
    "CachingGateway",
    "LLMError",
    "LLMGateway",
    "LLMMessage",
    "LLMProviderError",
    "LLMRequest",
    "LLMResponse",
    "LLMTimeoutError",
    "LLMUsage",
    "RetryingGateway",
    "ScriptedGateway",
    "SpendCapError",
    "SpendCapGateway",
    "build_gateway",
    "estimate_cost_usd",
]
