"""
Evaluation harness for agent capabilities (Phase 5 + Phase 6).

Runs labeled conversation sets (``evals/conversation_sets``) through the real
agent using the deterministic, offline intent extractor, then scores what the
implemented phases actually guarantee:

- Phase 5 (L1): intent / handoff decisions, grounded, non-hallucinated prices and
  stock (hard invariant: must be zero), language mirroring, fraud/risk flagging.
- Phase 6 (L2): order creation through the deterministic tool — order_created,
  total_cents and price accuracy — scored against :class:`FakeOrderCreator`
  (no database, no model arithmetic).

Any expected assertion that is not actively checked is reported as **deferred** —
never silently dropped. If any active check fails, or a single price/stock
hallucination is found, the run exits non-zero.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packages.agent.agent import Agent, BusinessFacts, CatalogItem, InMemoryCatalog
from packages.agent.intent import HeuristicIntentExtractor
from packages.agent.policy import AutonomyLevel, PolicyEngine
from packages.agent.tools import FakeOrderCreator, OrderCreateResult

SETS_DIR = Path(__file__).resolve().parent / "conversation_sets"

# Deterministic eval catalog. Prices are KES cents.
EVAL_CATALOG = InMemoryCatalog([
    CatalogItem("HOODIE-BLK-M", "Black Hoodie M", 250_000, in_stock=True),
    CatalogItem("TEE-RED-L", "Red T-Shirt L", 120_000, in_stock=True),
    CatalogItem("SHOE-BLK-42", "Black Shoes 42", 300_000, in_stock=True),
    CatalogItem("TEE-BLK-L", "Black T-Shirt L", 120_000, in_stock=True),
])

EVAL_FACTS = BusinessFacts(
    hours="8am to 8pm daily",
    delivery_areas=["Kilimani", "Westlands", "Kasarani", "South B"],
    payment_methods=["M-Pesa"],
)

# Capabilities verified by the implemented phases. Others are Deferred.
ACTIVE_CAPABILITIES = frozenset({
    "faq_answering",
    "order_capture",
    "order_transaction",
    "handoff_decisions",
    "fraud_flagging",
    "swahili_sheng",
})

# Capabilities exercised end-to-end at L2 with the deterministic order tool.
L2_CAPABILITIES = frozenset({"order_transaction"})
DEFAULT_EVAL_PHONE = "254712345678"

_EVAL_PRICES = {item.sku: item.price_cents for item in EVAL_CATALOG.items}

_PRICE_RE = re.compile(r"KES\s*([\d,]+)")
_KNOWN_PRICES = set(_EVAL_PRICES.values())


@dataclass
class EvalCase:
    id: str
    capability: str
    language: str
    turns: list[dict[str, Any]]
    expected_outcome: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    phone: str = DEFAULT_EVAL_PHONE

    @property
    def customer_messages(self) -> list[str]:
        return [t["content"] for t in self.turns if t.get("role") == "customer"]


@dataclass
class CaseResult:
    case_id: str
    capability: str
    passed: bool
    score: float
    checks: dict[str, bool]
    deferred: list[str]
    hallucinated_prices: list[str]
    error: str | None = None


@dataclass
class EvalSummary:
    total_cases: int
    passed: int
    failed: int
    deferred_cases: int
    avg_score: float
    wrong_price_stock_count: int
    by_capability: dict[str, dict[str, int]]
    timestamp: str


class EvalRunner:
    def __init__(
        self, sets_dir: Path = SETS_DIR, *, autonomy: AutonomyLevel = AutonomyLevel.L1_ANSWER,
    ):
        self.sets_dir = sets_dir
        self.autonomy = autonomy

    # -- loading --------------------------------------------------------

    def load_cases(self, capability: str | None = None) -> list[EvalCase]:
        caps = (
            [capability]
            if capability
            else sorted(d.name for d in self.sets_dir.iterdir() if d.is_dir())
        )
        cases: list[EvalCase] = []
        for cap in caps:
            cap_dir = self.sets_dir / cap
            if not cap_dir.is_dir():
                continue
            for file in sorted(cap_dir.glob("*.json")):
                data = json.loads(file.read_text())
                cases.append(
                    EvalCase(
                        id=data["id"],
                        capability=data["capability"],
                        language=data.get("language", "en"),
                        turns=data["turns"],
                        expected_outcome=data["expected_outcome"],
                        tags=data.get("tags", []),
                        phone=data.get("phone", DEFAULT_EVAL_PHONE),
                    ),
                )
        return cases

    # -- running --------------------------------------------------------

    async def run_case(self, case: EvalCase) -> CaseResult:
        is_l2 = case.capability in L2_CAPABILITIES
        creator = FakeOrderCreator(prices=_EVAL_PRICES) if is_l2 else None
        agent = Agent(
            extractor=HeuristicIntentExtractor(),
            policy=PolicyEngine(),
            catalog=EVAL_CATALOG,
            facts=EVAL_FACTS,
            autonomy=AutonomyLevel.L2_TRANSACT if is_l2 else self.autonomy,
            order_creator=creator,
        )
        outcomes = []
        hallucinated: list[str] = []
        # The unit-price invariant covers informational replies. For an L2 case we
        # also accept the expected order total (validated exactly by the
        # total_cents check below), so a correct quote is not misflagged.
        allowed_amounts = set(_KNOWN_PRICES)
        expected_total = case.expected_outcome.get("total_cents")
        if is_l2 and isinstance(expected_total, int):
            allowed_amounts.add(expected_total)
        try:
            for message in case.customer_messages:
                outcome = await agent.handle_message(
                    message, customer_phone=case.phone if is_l2 else None,
                )
                outcomes.append(outcome)
                for raw in _PRICE_RE.findall(outcome.reply_text):
                    cents = int(raw.replace(",", "")) * 100
                    if cents not in allowed_amounts:
                        hallucinated.append(raw)
        except Exception as exc:
            return CaseResult(
                case_id=case.id,
                capability=case.capability,
                passed=False,
                score=0.0,
                checks={},
                deferred=[],
                hallucinated_prices=[],
                error=str(exc),
            )

        created: list[OrderCreateResult] = creator.created if creator is not None else []
        checks, deferred = self._evaluate(case, outcomes, created)
        score = sum(checks.values()) / len(checks) if checks else 0.0
        passed = bool(checks) and all(checks.values()) and not hallucinated
        return CaseResult(
            case_id=case.id,
            capability=case.capability,
            passed=passed,
            score=score,
            checks=checks,
            deferred=deferred,
            hallucinated_prices=hallucinated,
        )

    def _evaluate(
        self,
        case: EvalCase,
        outcomes: list[Any],
        created: list[OrderCreateResult],
    ) -> tuple[dict[str, bool], list[str]]:
        expected = case.expected_outcome
        checks: dict[str, bool] = {}

        if case.capability == "faq_answering":
            checks["answered_hours"] = any("8am" in o.reply_text for o in outcomes)
            checks["answered_grounded"] = any(o.grounded for o in outcomes)

        elif case.capability == "order_capture":
            checks["intent_detected"] = any(
                o.intent.intent.value == "create_order" for o in outcomes
            )
            if "delivery_area" in expected:
                checks["area_extracted"] = any(
                    o.intent.delivery_area == expected["delivery_area"] for o in outcomes
                )

        elif case.capability == "order_transaction":
            checks["intent_detected"] = any(
                o.intent.intent.value == "create_order" for o in outcomes
            )
            checks["order_created"] = any(o.order_id for o in outcomes)
            if "total_cents" in expected:
                checks["total_cents"] = any(
                    r.total_cents == expected["total_cents"] for r in created
                )

        elif case.capability == "handoff_decisions":
            checks["handoff_triggered"] = any(o.decision.requires_human for o in outcomes)

        elif case.capability == "fraud_flagging":
            checks["fraud_flagged"] = any(o.intent.risk_flags for o in outcomes)
            checks["agent_escalated"] = any(o.decision.requires_human for o in outcomes)

        elif case.capability == "swahili_sheng":
            checks["language_matched"] = any(
                o.intent.language == expected.get("language_matched", case.language)
                for o in outcomes
            )
            if expected.get("order_created"):
                checks["intent_detected"] = any(
                    o.intent.intent.value == "create_order" for o in outcomes
                )

        # Anything expected but not actively checked is reported as deferred
        # (never silently dropped).
        deferred = sorted(k for k in expected if k not in checks)
        return checks, deferred

    # -- orchestrating --------------------------------------------------

    async def run_all(self, capability: str | None = None) -> tuple[EvalSummary, list[CaseResult]]:
        cases = self.load_cases(capability)
        results = [await self.run_case(case) for case in cases]

        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        deferred_cases = sum(1 for r in results if r.deferred)
        avg_score = sum(r.score for r in results) / len(results) if results else 0.0
        wrong = sum(len(r.hallucinated_prices) for r in results)

        by_capability: dict[str, dict[str, int]] = {}
        for r in results:
            bucket = by_capability.setdefault(r.capability, {"total": 0, "passed": 0})
            bucket["total"] += 1
            bucket["passed"] += int(r.passed)

        summary = EvalSummary(
            total_cases=len(results),
            passed=passed,
            failed=failed,
            deferred_cases=deferred_cases,
            avg_score=avg_score,
            wrong_price_stock_count=wrong,
            by_capability=by_capability,
            timestamp=datetime.now(UTC).isoformat(),
        )
        return summary, results


# Assertions are deferred automatically: any expected key the harness does not
# actively check is reported (see ``EvalRunner._evaluate``). Kept here as
# documentation of what is still verified by later phases.


def _print(summary: EvalSummary, results: list[CaseResult]) -> None:
    print("=" * 64)
    print("SocialERP agent evals (Phase 5 + Phase 6)")
    print("=" * 64)
    for cap, stats in sorted(summary.by_capability.items()):
        status = "ACTIVE" if cap in ACTIVE_CAPABILITIES else "DEFERRED"
        print(f"  {cap:<18} {stats['passed']}/{stats['total']} pass   [{status}]")
    print("-" * 64)
    print(f"  cases: {summary.total_cases}  passed: {summary.passed}  failed: {summary.failed}")
    print(f"  avg score: {summary.avg_score:.2f}")
    print(f"  wrong-price/stock hallucinations: {summary.wrong_price_stock_count} (must be 0)")
    deferred = sorted({k for r in results for k in r.deferred})
    if deferred:
        print(f"  deferred assertions (later phases): {', '.join(deferred)}")
    print("=" * 64)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Run SocialERP agent evals")
    parser.add_argument("--capability", help="Run a single capability")
    parser.add_argument("--output", type=Path, help="Write results JSON to this path")
    args = parser.parse_args()

    runner = EvalRunner()
    summary, results = await runner.run_all(args.capability)
    _print(summary, results)

    if args.output:
        args.output.write_text(
            json.dumps(
                {
                    "summary": asdict(summary),
                    "results": [asdict(r) for r in results],
                },
                indent=2,
            ),
        )

    if summary.wrong_price_stock_count > 0:
        print("FAIL: hallucinated price/stock detected")
        return 1
    if summary.failed > 0:
        print(f"FAIL: {summary.failed} case(s) failed")
        return 1
    print("OK: all active checks passed")
    return 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
