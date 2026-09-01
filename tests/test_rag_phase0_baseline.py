import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from rag_phase0_baseline import (  # noqa: E402
    _pending_evaluation,
    _release_snapshot,
    _sections,
    _yaml_lines,
    ensure_baseline_writable,
)
from evaluate_rag_phase0 import _ndcg, _question_fingerprint  # noqa: E402
from measure_rag_phase0_latency import stratified_sample  # noqa: E402


class RagPhase0ProductionBaselineTests(unittest.TestCase):
    def test_sections_extract_stable_kb_locators(self):
        text = "KB-SRV-01 1. 接入流程\n正文 A\nKB-IAD-02 2. 任务定义\n正文 B"
        self.assertEqual(
            [item["source_locator"] for item in _sections(text)],
            ["KB-SRV-01", "KB-IAD-02"],
        )

    def test_missing_release_is_reported_without_creating_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = _release_snapshot(root)
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(list(root.iterdir()), [])

    def test_unreviewed_dataset_cannot_emit_fake_scores(self):
        dataset = {
            "name": "draft",
            "cases": [{
                "id": "case-1",
                "category": "exact_fact",
                "review": {"status": "pending"},
            }],
        }
        evaluation, metrics = _pending_evaluation(dataset)
        self.assertEqual(evaluation["status"], "pending_human_review")
        self.assertIsNone(metrics["retrieval"]["recall_at_5"])
        self.assertFalse(metrics["acceptance"]["passed"])

    def test_yaml_writer_quotes_strings_and_preserves_null(self):
        rendered = "\n".join(_yaml_lines({"version": "V0", "value": None}))
        self.assertIn('version: "V0"', rendered)
        self.assertIn("value: null", rendered)

    def test_signed_baseline_cannot_be_overwritten_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "baseline_metrics.json").write_text(
                '{"status":"signed"}', encoding="utf-8"
            )
            with self.assertRaises(RuntimeError):
                ensure_baseline_writable(output)
            ensure_baseline_writable(output, allow_replace=True)

    def test_generated_draft_has_phase0_schema_shape(self):
        draft_path = PROJECT_ROOT / "config" / "rag_golden_dataset_v0.draft.json"
        if not draft_path.exists():
            self.skipTest("候选集尚未生成")
        dataset = json.loads(draft_path.read_text(encoding="utf-8"))
        self.assertEqual(dataset["status"], "pending_human_review")
        self.assertGreaterEqual(len(dataset["cases"]), 200)
        self.assertEqual(
            len({case["id"] for case in dataset["cases"]}),
            len(dataset["cases"]),
        )
        required = {
            "id", "question", "category", "expected_mode",
            "allowed_doc_ids", "expected_evidence", "expected_answer_points",
            "must_not_include", "requires_refusal", "review",
        }
        for case in dataset["cases"]:
            self.assertTrue(required.issubset(case))

    def test_question_fingerprint_ignores_review_metadata(self):
        left = {"cases": [{
            "id": "one", "question": "问题", "category": "exact_fact",
            "requires_refusal": False, "review": {"status": "pending"},
        }]}
        right = json.loads(json.dumps(left, ensure_ascii=False))
        right["cases"][0]["review"]["status"] = "approved"
        self.assertEqual(_question_fingerprint(left), _question_fingerprint(right))

    def test_ndcg_rewards_relevant_node_at_higher_rank(self):
        expected = {"relevant"}
        self.assertGreater(
            _ndcg(["relevant", "other"], expected, 2),
            _ndcg(["other", "relevant"], expected, 2),
        )

    def test_latency_sample_is_stratified_by_category(self):
        cases = [
            {"id": f"a-{index}", "category": "a"} for index in range(5)
        ] + [
            {"id": f"b-{index}", "category": "b"} for index in range(3)
        ]
        sample = stratified_sample(cases, per_category=2)
        self.assertEqual(
            {category: sum(case["category"] == category for case in sample)
             for category in ("a", "b")},
            {"a": 2, "b": 2},
        )


if __name__ == "__main__":
    unittest.main()
