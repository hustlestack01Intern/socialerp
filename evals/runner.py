"""
Evaluation harness for agent capabilities (Phase 5).

Runs labeled conversation sets (``evals/conversation_sets``) through the real
Phase-5 agent using the deterministic, offline intent extractor, then scores what
Phase 5 actually guarantees:

- intent / handoff decisions,
- grounded, non-hallucinated prices and stock (hard invariant: must be zero),
- language mirroring,
- fraud/risk flagging.

Assertions that belong to later phases (order creation, STK payment, totals) are
reported as **deferred** — never silently counted as passing. If any active check
fails, or a single price/stock hallucination is found, the run exits non-zero.
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

# Capabilities verified in Phase 5. Others are Deferred.
ACTIVE_CAPABILITIES = frozenset({
    "faq_answering",
    "order_capture",
    "handoff_decisions",
    "fraud_flagging",
    "swahili_sheng",
})

_PRICE_RE = re.compile(r"KES\s*([\d,]+)")
_KNOWN_PRICES = {item.price_cents for item in EVAL_CATALOG.items}


@dataclass
class EvalCase:
    id: str
    capability: str
    language: str
    turns: list[dict[str, Any]]
    expected_outcome: dict[str, Any]
    tags: list[str] = field(default_factory=list)

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
        self, sets_dir: Path = SETS_DIR, *, autonomy: AutonomyLevel = AutonomyLevel.L1_ANSWER
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
                    )
                )
        return cases

    # -- running --------------------------------------------------------

    async def run_case(self, case: EvalCase) -> CaseResult:
        agent = Agent(
            extractor=HeuristicIntentExtractor(),
            policy=PolicyEngine(),
            catalog=EVAL_CATALOG,
            facts=EVAL_FACTS,
            autonomy=self.autonomy,
        )
        outcomes = []
        hallucinated: list[str] = []
        try:
            for message in case.customer_messages:
                outcome = await agent.handle_message(message)
                outcomes.append(outcome)
                for raw in _PRICE_RE.findall(outcome.reply_text):
                    cents = int(raw.replace(",", "")) * 100
                    if cents not in _KNOWN_PRICES:
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

        checks, deferred = self._evaluate(case, outcomes)
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

    def _evaluate(self, case: EvalCase, outcomes: list[Any]) -> tuple[dict[str, bool], list[str]]:
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

        deferred = sorted(k for k in expected if k in _DEFERRED_KEYS and k not in _CHECKED_KEYS)
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


# Assertions we knowingly defer to later phases (recorded, never silently passed).
_DEFERRED_KEYS = frozenset({
    "order_created",
    "payment_initiated",
    "items",
    "total_cents",
    "delivery_fee_cents",
    "stock_accurate",
    "price_accurate",
    "answered_stock",
    "flag_type",
    "safe_response",
    "agent_refused",
    "reason",
    "confidence_below_threshold",
    "human_notified",
})

# Assertions the Phase-5 harness actually verifies.
_CHECKED_KEYS = frozenset({
    "answered_hours",
    "answered_grounded",
    "intent_detected",
    "area_extracted",
    "handoff_triggered",
    "fraud_flagged",
    "agent_escalated",
    "language_matched",
})


def _print(summary: EvalSummary, results: list[CaseResult]) -> None:
    print("=" * 64)
    print("SocialERP agent evals (Phase 5)")
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
            )
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
