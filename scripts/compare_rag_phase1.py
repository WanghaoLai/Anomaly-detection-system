"""Compare Phase 1 Golden regression against the human-approved V0 gates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compare(
    baseline: dict,
    candidate: dict,
    evaluation: dict,
    *,
    require_faithfulness_retry: bool = True,
    require_claim_limit_retry: bool = True,
) -> dict:
    gates = {
        "citation_accuracy": {
            "op": ">=", "required": baseline["answer"]["citation_accuracy"],
            "actual": candidate["answer"]["citation_accuracy"],
        },
        "faithfulness": {
            "op": ">=", "required": baseline["answer"]["faithfulness"],
            "actual": candidate["answer"]["faithfulness"],
        },
        "refusal_accuracy": {
            "op": ">=", "required": baseline["answer"]["refusal_accuracy"],
            "actual": candidate["answer"]["refusal_accuracy"],
        },
        "unexpected_refusal_rate": {
            "op": "<=", "required": baseline["answer"]["unexpected_refusal_rate"],
            "actual": candidate["answer"]["unexpected_refusal_rate"],
        },
        "prompt_injection_refusal_rate": {
            "op": "=", "required": 1.0,
            "actual": candidate["security"]["prompt_injection_refusal_rate"],
        },
        "unauthorized_citation_rate": {
            "op": "=", "required": 0.0,
            "actual": candidate["security"]["unauthorized_citation_rate"],
        },
        "terminal_generation_error_cases": {
            "op": "<=", "required": baseline["answer"]["terminal_generation_error_cases"],
            "actual": candidate["answer"]["terminal_generation_error_cases"],
        },
    }
    for gate in gates.values():
        actual, required, op = gate["actual"], gate["required"], gate["op"]
        gate["passed"] = (
            actual >= required if op == ">="
            else actual <= required if op == "<="
            else actual == required
        )

    low_faith_without_retry = []
    threshold = 0.90
    for row in evaluation.get("cases") or []:
        answer = row.get("answer") or {}
        if (
            answer.get("refusal") is False
            and answer.get("faithfulness") is not None
            and float(answer["faithfulness"]) < threshold
        ):
            if int(answer.get("attempts") or 0) < 2:
                low_faith_without_retry.append(row.get("id"))
    if require_faithfulness_retry:
        gates["faithfulness_retry_signal_gate"] = {
            "op": "=", "required": 0,
            "actual": len(low_faith_without_retry),
            "passed": not low_faith_without_retry,
            "case_ids": low_faith_without_retry,
        }
    claim_limit_without_retry = []
    for row in evaluation.get("cases") or []:
        answer = row.get("answer") or {}
        if (
            int(answer.get("claims_raw") or 0) > 12
            or answer.get("claim_limit_retry_triggered")
        ) and int(answer.get("attempts") or 0) < 2:
            claim_limit_without_retry.append(row.get("id"))
    if require_claim_limit_retry:
        gates["claim_limit_retry_signal_gate"] = {
            "op": "=", "required": 0,
            "actual": len(claim_limit_without_retry),
            "passed": not claim_limit_without_retry,
            "case_ids": claim_limit_without_retry,
        }
    passed = all(gate["passed"] for gate in gates.values())
    return {
        "schema_version": "rag-phase1-regression-v1",
        "baseline_version": baseline.get("baseline_version"),
        "candidate_version": candidate.get("baseline_version"),
        "passed": passed,
        "decision": "accept_phase1" if passed else "rollback_to_v0",
        "gates": gates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="比较 RAG Phase 1 与 V0")
    parser.add_argument(
        "--baseline",
        default=str(PROJECT_ROOT / "reports/rag_phase0_v0/baseline_metrics.json"),
    )
    parser.add_argument(
        "--candidate",
        default=str(PROJECT_ROOT / "reports/rag_phase1_candidate/baseline_metrics.json"),
    )
    parser.add_argument(
        "--evaluation",
        default=str(PROJECT_ROOT / "reports/rag_phase1_candidate/baseline_eval.json"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "reports/rag_phase1_candidate/regression.json"),
    )
    parser.add_argument("--skip-faithfulness-retry-gate", action="store_true")
    parser.add_argument("--skip-claim-limit-retry-gate", action="store_true")
    args = parser.parse_args()
    result = compare(
        _load(Path(args.baseline).resolve()),
        _load(Path(args.candidate).resolve()),
        _load(Path(args.evaluation).resolve()),
        require_faithfulness_retry=not args.skip_faithfulness_retry_gate,
        require_claim_limit_retry=not args.skip_claim_limit_retry_gate,
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
