"""
HTTP gateway for any OpenAI-compatible chat API.

Ollama exposes an OpenAI-compatible endpoint at ``/v1`` (default
``http://localhost:11434/v1``), and hosted providers share the same shape, so a
single implementation serves both. The transport is injectable for tests.
"""

from __future__ import annotations

import httpx

from packages.agent.gateway import (
    DEFAULT_PRICES,
    LLMGateway,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUsage,
    estimate_cost_usd,
)


class OpenAICompatibleGateway(LLMGateway):
    """Chat completions against an OpenAI-compatible ``/chat/completions`` API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        client: httpx.AsyncClient | None = None,
        prices: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client
        self._prices = prices if prices is not None else DEFAULT_PRICES

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": request.response_schema},
            }

        client = self._client or httpx.AsyncClient(timeout=request.timeout_seconds)
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                timeout=request.timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                f"LLM request timed out after {request.timeout_seconds}s",
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(f"LLM transport error: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()

        if response.status_code >= 400:
            raise LLMProviderError(
                f"LLM provider returned {response.status_code}: {response.text[:200]}",
            )

        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
            usage_raw = data.get("usage") or {}
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMProviderError("Malformed LLM response") from exc

        if not isinstance(text, str):
            raise LLMProviderError("LLM response content was not a string")

        usage = LLMUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
        )
        model = str(data.get("model", request.model))
        return LLMResponse(
            text=text,
            model=model,
            model_version=model,
            usage=usage,
            cost_usd=estimate_cost_usd(model, usage, self._prices),
            finish_reason=choice.get("finish_reason"),
        )


__all__ = ["OpenAICompatibleGateway"]
