"""Aggregate three independent Phase 1 Golden runs against V0 gates."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from compare_rag_phase1 import compare


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return json.loads(Path(path).resolve().read_text(encoding="utf-8"))


def _median(values: list[float | int]) -> float:
    return round(float(statistics.median(values)), 4)


def aggregate(
    baseline: dict,
    candidates: list[dict],
    evaluations: list[dict],
    *,
    require_faithfulness_retry: bool,
    require_claim_limit_retry: bool,
) -> dict:
    if len(candidates) != 3 or len(evaluations) != 3:
        raise ValueError("多次运行门禁必须恰好提供 3 组指标与逐题结果")

    answer_keys = (
        "citation_accuracy",
        "faithfulness",
        "refusal_accuracy",
        "unexpected_refusal_rate",
        "terminal_generation_error_cases",
    )
    synthesized = {
        "baseline_version": "P1-MULTIRUN-MEDIAN",
        "answer": {
            key: _median([item["answer"][key] for item in candidates])
            for key in answer_keys
        },
        "security": {
            "prompt_injection_refusal_rate": min(
                item["security"]["prompt_injection_refusal_rate"]
                for item in candidates
            ),
            "unauthorized_citation_rate": max(
                item["security"]["unauthorized_citation_rate"]
                for item in candidates
            ),
        },
    }
    median_result = compare(
        baseline,
        synthesized,
        {"cases": []},
        require_faithfulness_retry=False,
        require_claim_limit_retry=False,
    )
    run_results = [
        compare(
            baseline,
            candidate,
            evaluation,
            require_faithfulness_retry=require_faithfulness_retry,
            require_claim_limit_retry=require_claim_limit_retry,
        )
        for candidate, evaluation in zip(candidates, evaluations)
    ]
    behavior_names = (
        "faithfulness_retry_signal_gate",
        "claim_limit_retry_signal_gate",
    )
    behavior_gates = {}
    for name in behavior_names:
        present = [run["gates"].get(name) for run in run_results]
        if any(gate is not None for gate in present):
            passed = all(gate is not None and gate["passed"] for gate in present)
            behavior_gates[name] = {
                "op": "all_runs_pass",
                "required": True,
                "actual": passed,
                "passed": passed,
            }
    gates = {**median_result["gates"], **behavior_gates}
    passed = all(gate["passed"] for gate in gates.values())
    return {
        "schema_version": "rag-phase1-multirun-v1",
        "baseline_version": baseline.get("baseline_version"),
        "candidate_versions": [item.get("baseline_version") for item in candidates],
        "aggregation": "median_of_three_core_metrics; all_runs_security_and_behavior",
        "passed": passed,
        "decision": "accept_phase1" if passed else "rollback_to_previous_passed_layer",
        "gates": gates,
        "per_run_gates": [item["gates"] for item in run_results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="三次独立 Golden 运行统计门禁")
    parser.add_argument(
        "--baseline",
        default=str(PROJECT_ROOT / "reports/rag_phase0_v0/baseline_metrics.json"),
    )
    parser.add_argument("--candidate", action="append", required=True)
    parser.add_argument("--evaluation", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--require-faithfulness-retry", action="store_true")
    parser.add_argument("--require-claim-limit-retry", action="store_true")
    args = parser.parse_args()
    result = aggregate(
        _load(args.baseline),
        [_load(path) for path in args.candidate],
        [_load(path) for path in args.evaluation],
        require_faithfulness_retry=args.require_faithfulness_retry,
        require_claim_limit_retry=args.require_claim_limit_retry,
    )
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
