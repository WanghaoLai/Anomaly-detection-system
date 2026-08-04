import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.experiment_result_service import _visualization_items  # noqa: E402


class ExperimentResultServiceTests(unittest.TestCase):
    def test_normalizes_legacy_and_structured_inference_images(self):
        legacy = SimpleNamespace(result_json={"visualizations": ["work/results/001.png"]})
        structured = SimpleNamespace(result_json={
            "visualizationItems": [
                {"path": "work/results/002.png", "sizeBytes": 128},
            ],
        })

        self.assertEqual(
            _visualization_items(legacy),
            [{"path": "work/results/001.png", "sizeBytes": 0}],
        )
        self.assertEqual(
            _visualization_items(structured),
            [{"path": "work/results/002.png", "sizeBytes": 128}],
        )

    def test_rejects_unsafe_manifest_paths(self):
        job = SimpleNamespace(result_json={
            "visualizations": ["../escape.png", "/etc/passwd", "safe/001.png"],
        })
        self.assertEqual(
            _visualization_items(job),
            [{"path": "safe/001.png", "sizeBytes": 0}],
        )


if __name__ == "__main__":
    unittest.main()
