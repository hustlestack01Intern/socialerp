"""Unit tests for the LLM gateway wrappers (no network)."""

from __future__ import annotations

import pytest

from packages.agent.gateway import (
    CachingGateway,
    LLMGateway,
    LLMMessage,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUsage,
    RetryingGateway,
    ScriptedGateway,
    SpendCapError,
    SpendCapGateway,
    build_gateway,
    estimate_cost_usd,
)


def _req(cache_key: str | None = None, tenant_id: str | None = None) -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hello")],
        model="m",
        cache_key=cache_key,
        tenant_id=tenant_id,
    )


class FixedGateway(LLMGateway):
    """Returns a fixed response or raises a fixed error."""

    def __init__(self, *, cost_usd: float = 0.0, error: Exception | None = None) -> None:
        self.cost_usd = cost_usd
        self.error = error
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return LLMResponse(
            text="ok",
            model="m",
            model_version="1",
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
            cost_usd=self.cost_usd,
        )


async def test_scripted_gateway_records_calls() -> None:
    gw = ScriptedGateway(["a", "b"])
    assert (await gw.complete(_req())).text == "a"
    assert (await gw.complete(_req())).text == "b"
    assert len(gw.calls) == 2


async def test_scripted_gateway_raises_configured_error() -> None:
    gw = ScriptedGateway()
    gw.error = LLMTimeoutError("slow")
    with pytest.raises(LLMTimeoutError):
        await gw.complete(_req())


async def test_caching_gateway_caches_by_key() -> None:
    inner = FixedGateway()
    gw = CachingGateway(inner)
    first = await gw.complete(_req(cache_key="k1"))
    second = await gw.complete(_req(cache_key="k1"))
    assert inner.calls == 1
    assert first.cached is False
    assert second.cached is True


async def test_caching_gateway_skips_without_key() -> None:
    inner = FixedGateway()
    gw = CachingGateway(inner)
    await gw.complete(_req())
    await gw.complete(_req())
    assert inner.calls == 2


async def test_retrying_gateway_retries_then_succeeds() -> None:
    class Flaky(FixedGateway):
        async def complete(self, request: LLMRequest) -> LLMResponse:
            self.calls += 1
            if self.calls < 3:
                raise LLMProviderError("boom")
            return LLMResponse(text="ok", model="m", model_version="1")

    delays: list[float] = []

    async def fake_sleep(d: float) -> None:
        delays.append(d)

    wrapped = RetryingGateway(Flaky(), max_attempts=3, base_delay=0.1, sleep=fake_sleep)
    response = await wrapped.complete(_req())
    assert response.text == "ok"
    assert delays == [0.1, 0.2]


async def test_retrying_gateway_exhausts() -> None:
    async def fake_sleep(_: float) -> None: ...

    wrapped = RetryingGateway(
        FixedGateway(error=LLMProviderError("nope")), max_attempts=2, sleep=fake_sleep,
    )
    with pytest.raises(LLMProviderError):
        await wrapped.complete(_req())


async def test_spend_cap_blocks_when_reached() -> None:
    gw = SpendCapGateway(FixedGateway(cost_usd=0.6), daily_cap_usd=0.5)
    await gw.complete(_req(tenant_id="t1"))
    with pytest.raises(SpendCapError):
        await gw.complete(_req(tenant_id="t1"))


async def test_spend_cap_is_per_tenant() -> None:
    gw = SpendCapGateway(FixedGateway(cost_usd=0.6), daily_cap_usd=1.0)
    await gw.complete(_req(tenant_id="t1"))
    await gw.complete(_req(tenant_id="t2"))  # different tenant: allowed
    assert gw.spent_today("t1") == pytest.approx(0.6)


async def test_spend_cap_per_request_limit() -> None:
    gw = SpendCapGateway(FixedGateway(cost_usd=5.0), daily_cap_usd=100.0, max_request_usd=1.0)
    with pytest.raises(SpendCapError):
        await gw.complete(_req(tenant_id="t1"))


async def test_spend_cap_window_rolls_over() -> None:
    clock = {"t": 0.0}
    gw = SpendCapGateway(
        FixedGateway(cost_usd=0.6),
        daily_cap_usd=1.0,
        now=lambda: clock["t"],
        day_seconds=100.0,
    )
    await gw.complete(_req(tenant_id="t1"))
    clock["t"] = 200.0
    await gw.complete(_req(tenant_id="t1"))  # new window: allowed again
    assert gw.spent_today("t1") == pytest.approx(0.6)


def test_estimate_cost() -> None:
    usage = LLMUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert estimate_cost_usd("gpt-4o-mini", usage, {"gpt-4o-mini": (0.15, 0.60)}) == pytest.approx(
        0.75,
    )
    assert estimate_cost_usd("local", usage, {}) == pytest.approx(0.0)


async def test_build_gateway_composes_wrappers() -> None:
    gw = build_gateway(inner=ScriptedGateway(["hi"]), daily_cap_usd=1.0)
    response = await gw.complete(_req(cache_key="k"))
    assert response.text == "hi"
