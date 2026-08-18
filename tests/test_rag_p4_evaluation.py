import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from evaluate_rag_context import _contains_any_expected, context_precision  # noqa: E402


class RagP4EvaluationTests(unittest.TestCase):
    def test_context_precision_uses_relevant_rank_precision(self):
        self.assertEqual(context_precision([]), 0.0)
        self.assertEqual(context_precision([False, False]), 0.0)
        self.assertEqual(context_precision([True, False, False]), 1.0)
        self.assertAlmostEqual(
            context_precision([False, True, True]),
            (1 / 2 + 2 / 3) / 2,
        )

    def test_partial_evidence_is_useful_for_precision(self):
        case = {"expected_all": ["设备 ID", "防火墙"]}

        self.assertTrue(_contains_any_expected("请核对设备 ID", case))
        self.assertTrue(_contains_any_expected("检查防火墙", case))
        self.assertFalse(_contains_any_expected("检查磁盘空间", case))


if __name__ == "__main__":
    unittest.main()
