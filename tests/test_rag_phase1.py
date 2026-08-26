import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_rag_phase1 import (  # noqa: E402
    CORPUS_PATH,
    SUMMARY_PATH,
    validate_static,
)


class RagPhase1ContractTests(unittest.TestCase):
    def test_frozen_phase1_summary_is_consistent(self):
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(validate_static(summary, corpus), [])
        self.assertEqual(summary["quality"]["documents"], 15)
        self.assertEqual(summary["quality"]["blocked_documents"], 0)
        self.assertTrue(summary["invariants"]["active_release_unchanged"])

    def test_known_font_mapping_problem_requires_manual_review(self):
        summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            summary["quality"]["manual_review_work_ids"],
            ["self-training-survey-2021"],
        )


if __name__ == "__main__":
    unittest.main()
