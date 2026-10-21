"""The pricing page must say what the code does (M1.5, corrected 2026-10-21).

A pricing page that drifts from the code is a promise the platform cannot
keep. The page renders ``apps/dashboard/src/lib/pricing.json``; this test
compares that file with the two sources of truth in the backend:

* the published prices (``_PRICE_KES`` in ``apps/api/routes/billing.py``);
* the entitlement matrix (``packages/core/billing/entitlements.py``).

So a plan that gains a channel, loses an agent level or changes price in the
backend fails this test until the page is updated.

**The correction (skeptic, 2026-10-21):** the matrix is *published* but the
per-feature cells (channels beyond WhatsApp, the agent-level ceiling, API
access, seats) are **not enforced** anywhere in the code - only the plan
state gate (expired / cancelled / not started) is. A page that said "every
line is enforced by the system" was therefore making a promise the platform
could not keep. The page now separates the two: what the software enforces
today, and what it only records for now. These tests keep that separation
honest.
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


def test_the_page_separates_enforced_from_recorded() -> None:
    """The page must say what is enforced today AND what is not switched on.

    The matrix is published but not enforced per feature; a page that only
    lists the plan contents would read as a promise.
    """
    data = _pricing()
    assert data["enforced_today"], "the page must state what the system enforces today"
    assert data["not_enforced_yet"], "the page must state what is not enforced yet"
    source = PRICING_PAGE.read_text(encoding="utf-8")
    assert "enforced_today" in source, "the page must render the enforced list"
    assert "not_enforced_yet" in source, "the page must render the not-enforced list"


def test_the_page_may_not_claim_the_matrix_is_enforced() -> None:
    """Banned: telling a buyer that every listed line is enforced."""
    source = PRICING_PAGE.read_text(encoding="utf-8").lower()
    for banned in (
        "actually enforces",
        "is enforced by the system",
        "every line is enforced",
        "what the system actually enforces",
    ):
        assert banned not in source, f"the pricing page claims {banned!r}"


def test_the_page_may_not_promise_one_trial_per_business() -> None:
    """One owner account per email is enforced; one trial per business is not."""
    source = PRICING_PAGE.read_text(encoding="utf-8").lower()
    assert "one trial per business." not in source, (
        "the page states one trial per business as a fact, but only one owner "
        "account per email address is enforced"
    )
