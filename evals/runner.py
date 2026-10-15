"""
Evaluation harness for agent capabilities.

Loads labeled conversation sets, runs agent, scores against rubric.
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class EvalCase:
    """Single evaluation case."""

    id: str
    capability: str  # order_capture, faq_answering, swahili_sheng, fraud_flagging, handoff_decision
    language: str  # en, sw, sheng
    customer_messages: list[str]
    expected_outcome: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalResult:
    """Result of evaluating one case."""

    case_id: str
    capability: str
    passed: bool
    score: float  # 0.0 to 1.0
    details: dict[str, Any]
    latency_ms: int
    tokens_used: int
    error: str | None = None


@dataclass
class EvalSummary:
    """Summary of evaluation run."""

    total_cases: int
    passed: int
    failed: int
    avg_score: float
    by_capability: dict[str, dict]
    timestamp: str


class EvalRunner:
    """Runs evaluation cases against the agent."""

    def __init__(self, cases_dir: Path = Path("evals/cases")):
        self.cases_dir = cases_dir
        self.cases: list[EvalCase] = []

    def load_cases(self, capability: str | None = None) -> list[EvalCase]:
        """Load evaluation cases from JSONL files."""
        self.cases = []

        if capability:
            files = [self.cases_dir / f"{capability}.jsonl"]
        else:
            files = list(self.cases_dir.glob("*.jsonl"))

        for file in files:
            if not file.exists():
                continue
            with Path(file).open() as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        self.cases.append(EvalCase(**data))

        return self.cases

    async def run_case(self, case: EvalCase, agent) -> EvalResult:
        """Run a single evaluation case."""
        import time

        start = time.time()

        try:
            # Run agent on the conversation
            result = await agent.run_conversation(case.customer_messages)

            # Score against expected outcome
            score, details = self._score_result(case, result)
            passed = score >= 0.8  # Threshold

            return EvalResult(
                case_id=case.id,
                capability=case.capability,
                passed=passed,
                score=score,
                details=details,
                latency_ms=int((time.time() - start) * 1000),
                tokens_used=result.get("tokens_used", 0),
            )
        except Exception as e:
            return EvalResult(
                case_id=case.id,
                capability=case.capability,
                passed=False,
                score=0.0,
                details={},
                latency_ms=int((time.time() - start) * 1000),
                tokens_used=0,
                error=str(e),
            )

    def _score_result(self, case: EvalCase, result: dict) -> tuple[float, dict]:
        """Score agent result against expected outcome."""
        expected = case.expected_outcome
        details = {"checks": {}}

        # Check 1: Wrong price/stock rate (must be 0)
        if "price" in expected and "price" in result:
            price_match = result["price"] == expected["price"]
            details["checks"]["price_accuracy"] = price_match
            if not price_match:
                return 0.0, details  # Hard fail

        if "stock" in expected and "stock" in result:
            stock_match = result["stock"] == expected["stock"]
            details["checks"]["stock_accuracy"] = stock_match
            if not stock_match:
                return 0.0, details  # Hard fail

        # Check 2: Intent accuracy
        if "intent" in expected:
            intent_match = result.get("intent") == expected["intent"]
            details["checks"]["intent_accuracy"] = intent_match

        # Check 3: Handoff decision
        if "should_handoff" in expected:
            handoff_match = result.get("handoff") == expected["should_handoff"]
            details["checks"]["handoff_decision"] = handoff_match

        # Check 4: Response quality (for FAQ/answer)
        if "response_contains" in expected:
            response = result.get("response", "").lower()
            contains_all = all(
                phrase.lower() in response for phrase in expected["response_contains"]
            )
            details["checks"]["response_contains"] = contains_all

        # Check 5: Language matching
        if "language" in expected:
            lang_match = result.get("language") == expected["language"]
            details["checks"]["language_match"] = lang_match

        # Calculate overall score
        checks = list(details["checks"].values())
        if not checks:
            return 0.0, details

        score = sum(checks) / len(checks)
        return score, details

    async def run_all(self, agent, capability: str | None = None) -> EvalSummary:
        """Run all evaluation cases."""
        cases = self.load_cases(capability)
        if not cases:
            return EvalSummary(
                total_cases=0,
                passed=0,
                failed=0,
                avg_score=0.0,
                by_capability={},
                timestamp=datetime.utcnow().isoformat(),
            )

        results = []
        for case in cases:
            result = await self.run_case(case, agent)
            results.append(result)

        # Aggregate
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed
        avg_score = sum(r.score for r in results) / len(results)

        by_capability = {}
        for result in results:
            cap = result.capability
            if cap not in by_capability:
                by_capability[cap] = {"total": 0, "passed": 0, "avg_score": 0.0}
            by_capability[cap]["total"] += 1
            if result.passed:
                by_capability[cap]["passed"] += 1

        for cap in by_capability:
            cap_results = [r for r in results if r.capability == cap]
            by_capability[cap]["avg_score"] = sum(r.score for r in cap_results) / len(cap_results)

        return EvalSummary(
            total_cases=len(results),
            passed=passed,
            failed=failed,
            avg_score=avg_score,
            by_capability=by_capability,
            timestamp=datetime.utcnow().isoformat(),
        )

    def print_summary(self, summary: EvalSummary):
        """Print evaluation summary."""
        for _cap, _stats in summary.by_capability.items():
            pass


# =============================================================================
# SAMPLE EVAL CASES (for testing the harness)
# =============================================================================

SAMPLE_CASES = {
    "order_capture": [
        {
            "id": "order_001",
            "capability": "order_capture",
            "language": "en",
            "customer_messages": ["I want to buy 2 black hoodies size M", "Deliver to Kilimani"],
            "expected_outcome": {
                "intent": "create_order",
                "items": [{"sku": "HOODIE-BLK-M", "qty": 2}],
                "delivery_area": "Kilimani",
            },
            "metadata": {"difficulty": "easy"},
        },
        {
            "id": "order_002",
            "capability": "order_capture",
            "language": "sw",
            "customer_messages": ["Ninataka kununua viatu 2 vya nyekundu", "Nipeleke South B"],
            "expected_outcome": {
                "intent": "create_order",
                "items": [{"sku": "SHOE-RED", "qty": 2}],
                "delivery_area": "South B",
            },
            "metadata": {"difficulty": "medium"},
        },
    ],
    "faq_answering": [
        {
            "id": "faq_001",
            "capability": "faq_answering",
            "language": "en",
            "customer_messages": ["What are your delivery hours?"],
            "expected_outcome": {
                "intent": "faq",
                "response_contains": ["8am", "8pm", "delivery"],
            },
            "metadata": {"difficulty": "easy"},
        },
    ],
    "fraud_flagging": [
        {
            "id": "fraud_001",
            "capability": "fraud_flagging",
            "language": "en",
            "customer_messages": ["Send to this number 0712345678, pay to till 123456"],
            "expected_outcome": {
                "intent": "order",
                "should_handoff": True,
                "fraud_flags": ["personal_number_payment", "till_number_mismatch"],
            },
            "metadata": {"difficulty": "hard"},
        },
    ],
}


def create_sample_cases():
    """Create sample evaluation cases for testing."""
    evals_dir = Path("evals/cases")
    evals_dir.mkdir(parents=True, exist_ok=True)

    for capability, cases in SAMPLE_CASES.items():
        file_path = evals_dir / f"{capability}.jsonl"
        with Path(file_path).open("w") as f:
            for case in cases:
                f.write(json.dumps(case) + "\n")


# =============================================================================
# CLI ENTRY POINT
# =============================================================================


async def main():
    """Main entry point for eval runner."""
    import argparse

    parser = argparse.ArgumentParser(description="Run agent evaluations")
    parser.add_argument("--capability", help="Run specific capability")
    parser.add_argument("--create-samples", action="store_true", help="Create sample cases")
    args = parser.parse_args()

    if args.create_samples:
        create_sample_cases()
        return

    runner = EvalRunner()

    # For now, just verify the harness loads
    cases = runner.load_cases(args.capability)

    # Print sample
    for _case in cases[:3]:
        pass


if __name__ == "__main__":
    asyncio.run(main())
