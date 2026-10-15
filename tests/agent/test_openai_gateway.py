"""Tests for the OpenAI-compatible HTTP gateway using a mock transport."""

from __future__ import annotations

import httpx
import pytest

from packages.agent.gateway import LLMMessage, LLMProviderError, LLMRequest, LLMTimeoutError
from packages.agent.http_gateway import OpenAICompatibleGateway


def _request(timeout: float = 5.0) -> LLMRequest:
    return LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        model="llama3.1:8b",
        timeout_seconds=timeout,
    )


def _gateway(handler) -> OpenAICompatibleGateway:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleGateway(base_url="http://localhost:11434/v1", api_key="k", client=client)


async def test_success_parses_content_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(
            200,
            json={
                "model": "llama3.1:8b",
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    response = await _gateway(handler).complete(_request())
    assert response.text == "hello"
    assert response.model == "llama3.1:8b"
    assert response.model_version == "llama3.1:8b"
    assert response.usage.total_tokens == 5


async def test_response_schema_passed_as_response_format() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
        )

    req = _request()
    gw = _gateway(handler)
    await gw.complete(
        LLMRequest(
            messages=req.messages,
            model=req.model,
            response_schema={"type": "object", "properties": {}},
        )
    )
    assert captured["response_format"]["type"] == "json_schema"


async def test_timeout_raises_llm_timeout_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    with pytest.raises(LLMTimeoutError):
        await _gateway(handler).complete(_request())


async def test_http_error_status_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="kaboom")

    with pytest.raises(LLMProviderError):
        await _gateway(handler).complete(_request())


async def test_malformed_response_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(LLMProviderError):
        await _gateway(handler).complete(_request())


async def test_transport_error_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMProviderError):
        await _gateway(handler).complete(_request())
