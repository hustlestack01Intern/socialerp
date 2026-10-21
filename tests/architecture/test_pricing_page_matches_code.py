"""The pricing page must say what the code does (M1.5, corrected 2026-10-21).

A pricing page that drifts from the code is a promise the platform cannot
keep. The page renders ``apps/dashboard/src/lib/pricing.json``; this test
compares that file with the sources of truth in the backend:

* the published prices (``_PRICE_KES`` in ``apps/api/routes/billing.py``);
* the entitlement matrix (``packages/core/billing/entitlements.py``);
* the switches the code actually throws (``ENFORCED_SWITCHES`` and
  ``IMPLEMENTED_CHANNELS`` in ``packages/core/billing/enforcer.py``).

So a plan that gains a channel, loses an agent level or changes price in the
backend fails this test until the page is updated - and a page that claims a
switch the code does not throw fails it too.

**The correction (skeptic, 2026-10-21):** the matrix was *published* but the
per-feature cells (channels beyond WhatsApp, the agent-level ceiling, API
access, seats) were **not enforced** anywhere in the code - only the plan state
gate was. A page that said "every line is enforced by the system" was making a
promise the platform could not keep. M1.6 enforced the cells, so the page now
lists them under "What the system enforces today" - and this test keeps that
list exactly equal to the set the code enforces. What is still *not* built
(the L3 optimisation actions, TikTok, API writes and inbound webhooks) is
listed separately, and the page may not claim it.
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


def test_no_plan_sells_a_channel_the_software_does_not_implement() -> None:
    """TikTok is behind a feature flag: selling it would be a broken promise."""
    from packages.core.billing.enforcer import IMPLEMENTED_CHANNELS

    for plan in _pricing()["plans"]:
        for channel in plan["channels"]:
            assert channel in IMPLEMENTED_CHANNELS, (
                f"the pricing page sells {channel!r} on the {plan['id']} plan, "
                f"but the software only implements {sorted(IMPLEMENTED_CHANNELS)}"
            )


def test_the_enforced_list_is_exactly_what_the_code_enforces() -> None:
    """The page's "enforced today" list == the switches the code throws.

    This is the test that keeps the promise honest in both directions: the
    page may not drop a switch the code enforces, and may not claim a switch
    the code does not.
    """
    from packages.core.billing.enforcer import ENFORCED_SWITCHES

    claimed = {item["feature"] for item in _pricing()["enforced_today"]}
    assert claimed == set(ENFORCED_SWITCHES), (
        "the pricing page's enforced list has drifted from the code: "
        f"page={sorted(claimed)} code={sorted(ENFORCED_SWITCHES)}"
    )


def test_every_enforced_statement_is_written_in_plain_words() -> None:
    """A switch that is listed must say what it does, in words a buyer reads."""
    for item in _pricing()["enforced_today"]:
        statement = item.get("statement", "")
        assert len(statement) > 40, f"{item['feature']} has no real statement"
        assert "This is switched on today." in statement, item["feature"]


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
    """The page must say what is enforced today AND what is not switched on."""
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


def test_the_page_may_not_promise_an_unbuilt_feature() -> None:
    """The things that are recorded but not built must stay in their own list.

    TikTok, the L3 optimisation actions and API writes are not in the
    software. If a plan's own text starts selling them, the page is making a
    promise the code cannot keep.
    """
    from packages.core.billing.enforcer import IMPLEMENTED_CHANNELS

    data = _pricing()
    not_yet = " ".join(data["not_enforced_yet"]).lower()
    for plan in data["plans"]:
        for channel in plan["channels"]:
            if channel not in IMPLEMENTED_CHANNELS:
                pytest.fail(f"the {plan['id']} plan sells {channel}")
    # The L3 claim must be qualified wherever it appears.
    for plan in data["plans"]:
        if plan["agent_level"] == "L3":
            text = f"{plan['summary']} {plan['highlight']}".lower()
            assert "not switched on yet" in text, (
                "the Enterprise plan claims L3 without saying the optimisation "
                "actions are not built yet"
            )
    assert "l3" in not_yet, "the page must say what L3 does not do yet"
    assert "read-only" in not_yet, "the page must say the API is read-only"
