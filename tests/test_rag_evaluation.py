import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.evaluate_rag import (  # noqa: E402
    _dense_results_at_threshold,
    _hybrid_results,
    _lexical_score,
)


class RagEvaluationTests(unittest.TestCase):
    def test_dataset_contains_40_well_formed_questions(self):
        dataset = json.loads(
            (PROJECT_ROOT / "config" / "rag_eval_questions.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(len(dataset["questions"]), 40)
        self.assertEqual(len({item["id"] for item in dataset["questions"]}), 40)
        for item in dataset["questions"]:
            self.assertIn(item["category"], {"semantic", "exact"})
            self.assertTrue(item["question"].strip())
            self.assertTrue(item["expected_all"])

    def test_lexical_score_rewards_exact_identifiers(self):
        question = "torch.cuda.is_available() 为什么返回 false？"
        relevant = "请运行 torch.cuda.is_available() 检查 CUDA 是否可用。"
        irrelevant = "请联系管理员检查网络连接。"

        self.assertGreater(
            _lexical_score(question, relevant),
            _lexical_score(question, irrelevant),
        )

    def test_hybrid_can_add_exact_match_missing_from_dense_candidates(self):
        dense = [{
            "content": "一般性的 SSH 登录说明",
            "doc_id": "dense",
            "chunk_index": 0,
            "score": 0.8,
        }]
        records = dense + [{
            "content": "持续刷新命令是 watch -n 2 nvidia-smi",
            "doc_id": "exact",
            "chunk_index": 0,
        }]

        results = _hybrid_results(
            "watch -n 2 nvidia-smi 命令是什么？",
            dense,
            records,
            final_k=4,
        )

        self.assertIn("exact", [item["doc_id"] for item in results])

    def test_dense_threshold_selection_matches_online_deduplication(self):
        dense = [
            {"content": "低分内容", "doc_id": "low", "chunk_index": 0, "score": 0.19},
            {"content": "高分内容", "doc_id": "high", "chunk_index": 0, "score": 0.42},
            {"content": "高分内容", "doc_id": "copy", "chunk_index": 1, "score": 0.40},
        ]

        results = _dense_results_at_threshold(dense, threshold=0.20, final_k=4)

        self.assertEqual([item["doc_id"] for item in results], ["high"])

if __name__ == "__main__":
    unittest.main()
