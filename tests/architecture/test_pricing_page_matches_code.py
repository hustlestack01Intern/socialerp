"""The pricing page must say what the code enforces (M1.5).

A pricing page that drifts from the code is a promise the platform cannot
keep. The page renders ``apps/dashboard/src/lib/pricing.json``; this test
compares that file with the two sources of truth in the backend:

* the published prices (``_PRICE_KES`` in ``apps/api/routes/billing.py``);
* the entitlement matrix (``packages/core/billing/entitlements.py``).

So a plan that gains a channel, loses an agent level or changes price in the
backend fails this test until the page is updated. The page may only claim
what the matrix enforces; anything else has to sit in ``sold_separately``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

REPO = Path(__file__).resolve().parents[2]
PRICING_JSON = REPO / "apps" / "dashboard" / "src" / "lib" / "pricing.json"
PRICING_PAGE = REPO / "apps" / "dashboard" / "src" / "app" / "pricing" / "page.tsx"


def _pricing() -> dict:
    return json.loads(PRICING_JSON.read_text(encoding="utf-8"))


def _code_prices() -> dict[str, int]:
    """The published prices, read from the API module (no copy-paste)."""
    import ast

    source = (REPO / "apps" / "api" / "routes" / "billing.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    prices: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "_PRICE_KES":
            continue
        assert isinstance(node.value, ast.Dict)
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            assert isinstance(key, ast.Attribute)  # PlanTier.STARTER
            assert isinstance(value, ast.Constant)
            prices[key.attr.lower()] = value.value
    assert prices, "could not read _PRICE_KES from apps/api/routes/billing.py"
    return prices


def test_pricing_file_exists() -> None:
    assert PRICING_JSON.exists(), f"missing {PRICING_JSON}"


def test_prices_match_the_code() -> None:
    """Every price on the page is the price the API charges."""
    code = _code_prices()
    page = {plan["id"]: plan["price"] for plan in _pricing()["plans"]}
    assert page == code, f"the pricing page and the API disagree: {page} vs {code}"


def test_features_match_the_entitlement_matrix() -> None:
    """The page may only claim what the matrix grants."""
    from packages.core.billing.entitlements import entitlements_for

    for plan in _pricing()["plans"]:
        ents = entitlements_for(plan["id"])
        assert sorted(plan["channels"]) == sorted(ents.channels), plan["id"]
        assert plan["agent_level"] == ents.agent_level.name, plan["id"]
        assert plan["insights"] is ents.insights, plan["id"]
        assert plan["api_access"] == _api_access_label(ents.api_access.name), plan["id"]
        assert plan["seats"] == ents.seats, plan["id"]


def _api_access_label(name: str) -> str:
    return {
        "NONE": "none",
        "API": "api",
        "API_AND_WEBHOOKS": "api + webhooks",
    }[name]


def test_the_page_renders_the_data_file() -> None:
    """The page must read the JSON, not carry its own copy of the numbers."""
    source = PRICING_PAGE.read_text(encoding="utf-8")
    assert "pricing.json" in source, "the pricing page must import pricing.json"
    # The price is rendered from the data file, not written into the markup.
    assert "price_label" in source, "the pricing page must render plan.price_label"
    for plan in _pricing()["plans"]:
        assert plan["price_label"], f"{plan['id']} has no price label"


def test_unenforced_costs_are_labelled_sold_separately() -> None:
    """Anything we do not enforce must be sold separately, not implied."""
    data = _pricing()
    assert data["sold_separately"], "the pricing page must say what is NOT included"
    source = PRICING_PAGE.read_text(encoding="utf-8").lower()
    assert "sold separately" in source or "not included" in source


def test_no_absolute_claims() -> None:
    """A pricing page may not promise what the system cannot promise."""
    source = PRICING_PAGE.read_text(encoding="utf-8").lower()
    for banned in ("100%", "guaranteed", "instant setup", "no limits"):
        assert banned not in source, f"the pricing page claims {banned!r}"
