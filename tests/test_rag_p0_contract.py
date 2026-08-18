import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_rag_p0_contract import (  # noqa: E402
    CONTRACT_PATH,
    _load_json,
    check_api_contract,
    check_application_ports,
    check_dataset,
    check_layering,
    check_prompt,
    check_report,
    check_runtime_configuration,
)


class RagP0ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = _load_json(CONTRACT_PATH)

    def test_evaluation_dataset_is_frozen(self):
        self.assertEqual(check_dataset(self.contract), [])

    def test_runtime_retrieval_configuration_is_frozen(self):
        self.assertEqual(check_runtime_configuration(self.contract), [])

    def test_system_prompt_is_frozen(self):
        self.assertEqual(check_prompt(self.contract), [])

    def test_application_port_signatures_are_frozen(self):
        self.assertEqual(check_application_ports(self.contract), [])

    def test_rag_api_routes_and_request_shapes_are_frozen(self):
        self.assertEqual(check_api_contract(self.contract), [])

    def test_application_layer_has_no_vendor_sdk_imports(self):
        self.assertEqual(check_layering(self.contract), [])

    def test_captured_behavior_report_meets_baseline(self):
        report = PROJECT_ROOT / "reports" / "rag_baseline_p0.json"
        if not report.exists():
            self.skipTest("本地联网基线报告未生成；离线契约仍由其他测试覆盖")
        self.assertEqual(check_report(self.contract, report), [])


if __name__ == "__main__":
    unittest.main()
