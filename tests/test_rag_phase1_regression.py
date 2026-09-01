import sys
import unittest
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from compare_rag_phase1 import compare  # noqa: E402
from compare_rag_phase1_multirun import aggregate  # noqa: E402
from compare_rag_phase1_v5_multirun import (  # noqa: E402
    aggregate_v5,
    fixed_baseline,
)


def metrics():
    return {
        "baseline_version": "V0",
        "answer": {
            "citation_accuracy": 0.80,
            "faithfulness": 0.92,
            "refusal_accuracy": 0.94,
            "unexpected_refusal_rate": 0.05,
            "terminal_generation_error_cases": 10,
        },
        "security": {
            "prompt_injection_refusal_rate": 1.0,
            "unauthorized_citation_rate": 0.0,
        },
    }


class RagPhase1RegressionTests(unittest.TestCase):
    def test_v5_fixed_denominator_counts_refusal_as_citation_miss(self):
        baseline_metrics = metrics()
        baseline_evaluation = {"cases": [
            {
                "expected_evidence": ["node-1"],
                "answer": {
                    "expected_refusal": False,
                    "refusal": False,
                    "citation_hits_expected_evidence": True,
                    "error": None,
                },
            },
            {
                "expected_evidence": ["node-2"],
                "answer": {
                    "expected_refusal": False,
                    "refusal": True,
                    "citation_hits_expected_evidence": None,
                    "error": "GroundingValidationError",
                },
            },
        ]}

        result = fixed_baseline(baseline_metrics, baseline_evaluation)

        self.assertEqual(result["citation_expected_evidence_success_rate"], 0.5)
        self.assertEqual(result["expected_answer_publication_rate"], 0.5)
        self.assertEqual(result["unexpected_terminal_generation_error_cases"], 1)

    def test_v5_multirun_uses_confirmed_fixed_denominator_gates(self):
        baseline_metrics = metrics()
        baseline_evaluation = {"cases": [{
            "expected_evidence": ["node-1"],
            "answer": {
                "expected_refusal": False,
                "refusal": False,
                "citation_hits_expected_evidence": True,
                "error": None,
            },
        }]}
        runs = []
        for index in range(3):
            candidate = deepcopy(baseline_metrics)
            candidate["baseline_version"] = f"V5-R{index + 1}"
            candidate["answer"].update({
                "citation_expected_evidence_success_rate": 1.0,
                "expected_answer_publication_rate": 1.0,
                "unexpected_terminal_generation_error_cases": 0,
                "answer_completeness_proxy": 0.95,
                "expected_safe_grounding_refusal_cases": 0,
            })
            runs.append(candidate)

        result = aggregate_v5(
            baseline_metrics,
            baseline_evaluation,
            runs,
            [{"cases": []} for _ in range(3)],
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["decision"], "accept_phase1_v5")

    def test_multirun_uses_median_and_all_run_security(self):
        baseline = metrics()
        runs = [deepcopy(baseline) for _ in range(3)]
        runs[0]["answer"]["citation_accuracy"] = 0.79
        runs[1]["answer"]["citation_accuracy"] = 0.80
        runs[2]["answer"]["citation_accuracy"] = 0.81
        result = aggregate(
            baseline,
            runs,
            [{"cases": []} for _ in range(3)],
            require_faithfulness_retry=False,
            require_claim_limit_retry=False,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["gates"]["citation_accuracy"]["actual"], 0.8)

    def test_v4a_can_skip_not_yet_implemented_behavior_gates(self):
        baseline = metrics()
        candidate = deepcopy(baseline)
        result = compare(
            baseline,
            candidate,
            {"cases": [{
                "id": "low",
                "answer": {"refusal": False, "faithfulness": 0.89, "attempts": 1},
            }]},
            require_faithfulness_retry=False,
            require_claim_limit_retry=False,
        )

        self.assertTrue(result["passed"])
        self.assertNotIn("faithfulness_retry_signal_gate", result["gates"])
        self.assertNotIn("claim_limit_retry_signal_gate", result["gates"])

    def test_equal_candidate_passes_all_zero_degradation_gates(self):
        baseline = metrics()
        candidate = deepcopy(baseline)
        candidate["baseline_version"] = "P1-CANDIDATE"
        result = compare(baseline, candidate, {"cases": []})

        self.assertTrue(result["passed"])
        self.assertEqual(result["decision"], "accept_phase1")

    def test_low_faithfulness_without_retry_forces_rollback(self):
        baseline = metrics()
        candidate = deepcopy(baseline)
        result = compare(baseline, candidate, {"cases": [{
            "id": "bad",
            "answer": {
                "refusal": False,
                "faithfulness": 0.89,
                "attempts": 1,
                "faithfulness_retry_triggered": False,
            },
        }]})

        self.assertFalse(result["passed"])
        self.assertEqual(result["decision"], "rollback_to_v0")
        self.assertEqual(
            result["gates"]["faithfulness_retry_signal_gate"]["case_ids"],
            ["bad"],
        )

    def test_low_faithfulness_after_controlled_retry_is_allowed(self):
        baseline = metrics()
        candidate = deepcopy(baseline)
        result = compare(baseline, candidate, {"cases": [{
            "id": "safe-partial",
            "answer": {
                "refusal": False,
                "faithfulness": 0.89,
                "attempts": 2,
                "faithfulness_retry_triggered": True,
            },
        }]})

        self.assertTrue(result["passed"])

    def test_claim_limit_without_retry_forces_rollback(self):
        baseline = metrics()
        candidate = deepcopy(baseline)
        result = compare(baseline, candidate, {"cases": [{
            "id": "overflow",
            "answer": {"claims_raw": 13, "attempts": 1},
        }]})

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["gates"]["claim_limit_retry_signal_gate"]["case_ids"],
            ["overflow"],
        )


if __name__ == "__main__":
    unittest.main()
