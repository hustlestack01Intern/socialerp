"""M1.4 regression: the LLM request must carry tenant_id and a cache_key.

Before this fix two safety wrappers were inert on the real path (U64/U65):
- ``SpendCapGateway`` bucketed every tenant under ``""`` because
  ``LLMIntentExtractor`` never passed ``tenant_id`` — so one tenant's spend
  capped/uncapped every other tenant.
- ``CachingGateway`` never hit because no ``cache_key`` was set.
"""

from __future__ import annotations

from packages.agent.gateway import ScriptedGateway, SpendCapGateway, build_gateway
from packages.agent.intent import LLMIntentExtractor, _extraction_cache_key


def _valid_intent_json() -> str:
    return (
        '{"intent": "greet", "confidence": 0.9, "language": "en", "product_query": null,'
        ' "items": [], "delivery_area": null, "injection_detected": false, "risk_flags": []}'
    )


async def test_extractor_passes_tenant_id() -> None:
    inner = ScriptedGateway([_valid_intent_json()])
    extractor = LLMIntentExtractor(inner, model="scripted", tenant_id="tenant-abc")

    await extractor.extract("hi")

    assert len(inner.calls) == 1
    assert inner.calls[0].tenant_id == "tenant-abc"


async def test_extractor_sets_cache_key() -> None:
    inner = ScriptedGateway([_valid_intent_json()])
    extractor = LLMIntentExtractor(inner, model="scripted", tenant_id="tenant-abc")

    await extractor.extract("hi")

    assert inner.calls[0].cache_key is not None


async def test_cache_key_is_tenant_scoped() -> None:
    """Same message, different tenant → different key (no cross-tenant leak)."""
    a = _extraction_cache_key("tenant-a", "m", "hello")
    b = _extraction_cache_key("tenant-b", "m", "hello")
    assert a != b
    # Same inputs → stable key (cacheable).
    assert a == _extraction_cache_key("tenant-a", "m", "hello")


async def test_real_gateway_composition_caches_and_scopes_spend() -> None:
    """The composed gateway actually caches and caps per tenant now."""
    inner = ScriptedGateway([_valid_intent_json()])
    gateway = build_gateway(inner=inner, daily_cap_usd=100.0, cache=True)
    # build_gateway returns CachingGateway(SpendCapGateway(RetryingGateway(inner)));
    # assert the inner-most gateway saw exactly one call for two identical requests.
    extractor = LLMIntentExtractor(gateway, model="scripted", tenant_id="tenant-x")

    await extractor.extract("hi")
    await extractor.extract("hi")

    assert len(inner.calls) == 1  # second was a cache hit


async def test_spend_cap_buckets_are_per_tenant() -> None:
    inner = ScriptedGateway([_valid_intent_json(), _valid_intent_json()])
    cap = SpendCapGateway(inner, daily_cap_usd=100.0)

    from packages.agent.gateway import LLMMessage, LLMRequest

    await cap.complete(LLMRequest(messages=[LLMMessage("user", "a")], model="m", tenant_id="t1"))
    await cap.complete(LLMRequest(messages=[LLMMessage("user", "b")], model="m", tenant_id="t2"))
    # One bucket per tenant; the shared "" bucket must NOT be used.
    assert set(cap._spend) == {"t1", "t2"}
    assert "" not in cap._spend
