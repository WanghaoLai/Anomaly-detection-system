import sys
import unittest
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_rag_multi_paper_phase0 import (  # noqa: E402
    CONTRACT_PATH,
    CORPUS_PATH,
    EVAL_PATH,
    REQUIRED_CATEGORIES,
    _load_json,
    validate_contract,
    validate_static,
)
from scripts.check_rag_multi_paper_baseline_v1 import (  # noqa: E402
    BASELINE_PATH,
    validate_baseline,
)


class RagMultiPaperPhase0Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = _load_json(CORPUS_PATH)
        cls.dataset = _load_json(EVAL_PATH)
        cls.contract = _load_json(CONTRACT_PATH)

    def test_frozen_artifacts_match_contract(self):
        self.assertEqual(validate_static(self.corpus, self.dataset), [])
        self.assertEqual(
            validate_contract(self.corpus, self.dataset, self.contract), []
        )

    def test_all_fifteen_papers_have_gold_evidence(self):
        covered = {
            work_id
            for case in self.dataset["questions"]
            for work_id in case["relevant_work_ids"]
        }
        frozen = {doc["work_id"] for doc in self.corpus["documents"]}
        self.assertEqual(covered, frozen)

    def test_every_required_evaluation_category_is_present(self):
        counts = Counter(case["category"] for case in self.dataset["questions"])
        self.assertEqual(set(counts), REQUIRED_CATEGORIES)
        self.assertTrue(all(count > 0 for count in counts.values()))

    def test_permission_cases_cover_denial_and_authorized_retrieval(self):
        permission_cases = {
            case["id"]: case
            for case in self.dataset["questions"]
            if case["category"] == "permission"
        }
        self.assertEqual(set(permission_cases), {"mpq039", "mpq041"})
        self.assertEqual(permission_cases["mpq039"]["relevant_work_ids"], [])
        self.assertEqual(
            permission_cases["mpq041"]["relevant_work_ids"], ["realnet-2024"]
        )

    def test_final_baseline_v1_is_frozen_and_self_consistent(self):
        baseline = _load_json(BASELINE_PATH)
        self.assertEqual(validate_baseline(baseline), [])
        self.assertEqual(
            baseline["release"]["release_id"],
            "b17672e25ed44ee793a8799def2d968e",
        )


if __name__ == "__main__":
    unittest.main()
