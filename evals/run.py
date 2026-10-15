"""
Evals Runner

Loads labeled conversation sets, runs them through the agent,
and scores accuracy, hallucination rate, wrong-price/wrong-stock rate, etc.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ConversationTurn:
    """Single turn in a conversation."""

    role: str  # "customer" or "agent"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationCase:
    """A labeled conversation test case."""

    id: str
    capability: str  # order_capture, faq_answering, etc.
    turns: list[ConversationTurn]
    expected_outcome: dict[str, Any]  # e.g., {"order_created": true, "items": [...]}
    language: str = "en"  # en, sw, sheng
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    """Result of evaluating one conversation case."""

    case_id: str
    passed: bool
    score: float  # 0.0 to 1.0
    details: dict[str, Any]
    latency_ms: int
    tokens_used: int
    model_version: str


class EvalRunner:
    """Runs evaluation sets against the agent."""

    def __init__(self, sets_dir: Path = Path("evals/conversation_sets")):
        self.sets_dir = sets_dir

    def load_set(self, capability: str) -> list[ConversationCase]:
        """Load all cases for a capability."""
        cases = []
        cap_dir = self.sets_dir / capability
        if not cap_dir.exists():
            return cases

        for file in cap_dir.glob("*.json"):
            with Path(file).open() as f:
                data = json.load(f)
                cases.append(
                    ConversationCase(
                        id=data["id"],
                        capability=data["capability"],
                        turns=[ConversationTurn(**t) for t in data["turns"]],
                        expected_outcome=data["expected_outcome"],
                        language=data.get("language", "en"),
                        tags=data.get("tags", []),
                    )
                )
        return cases

    def run_case(self, case: ConversationCase) -> EvalResult:
        """Run a single case through the agent (stub for M0)."""
        # In M0, this is a stub. Real implementation in M5.
        return EvalResult(
            case_id=case.id,
            passed=False,
            score=0.0,
            details={"note": "M0 stub - not implemented"},
            latency_ms=0,
            tokens_used=0,
            model_version="stub",
        )

    def run_all(self, capabilities: list[str] | None = None) -> dict[str, Any]:
        """Run all evaluation sets."""
        if capabilities is None:
            capabilities = [d.name for d in self.sets_dir.iterdir() if d.is_dir()]

        all_results = []
        summary = {
            "timestamp": datetime.now().isoformat(),
            "capabilities": {},
            "total_cases": 0,
            "passed": 0,
            "failed": 0,
        }

        for cap in capabilities:
            cases = self.load_set(cap)
            cap_results = []
            for case in cases:
                result = self.run_case(case)
                cap_results.append(result)
                all_results.append(result)

            passed = sum(1 for r in cap_results if r.passed)
            total = len(cap_results)
            summary["capabilities"][cap] = {
                "total": total,
                "passed": passed,
                "pass_rate": passed / total if total > 0 else 0,
            }
            summary["total_cases"] += total
            summary["passed"] += passed
            summary["failed"] += total - passed

        summary["overall_pass_rate"] = (
            summary["passed"] / summary["total_cases"] if summary["total_cases"] > 0 else 0
        )

        return {
            "summary": summary,
            "results": all_results,
        }


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Run SocialERP evals")
    parser.add_argument("--capability", action="append", help="Specific capability to run")
    parser.add_argument("--smoke", action="store_true", help="Run smoke test (no agent)")
    parser.add_argument("--output", type=Path, help="Output JSON file")

    args = parser.parse_args()

    runner = EvalRunner()

    if args.smoke:
        # Just verify structure loads
        for cap_dir in runner.sets_dir.iterdir():
            if cap_dir.is_dir():
                runner.load_set(cap_dir.name)
        return 0

    results = runner.run_all(args.capability)

    if args.output:
        with Path(args.output).open("w") as f:
            json.dump(results, f, indent=2, default=str)

    # Print summary

    for _cap, _stats in results["summary"]["capabilities"].items():
        pass

    return 0 if results["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
