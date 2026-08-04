import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "phase0_pbas_runner.py"
SPEC = importlib.util.spec_from_file_location("phase0_pbas_runner", SCRIPT_PATH)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


def valid_config() -> dict:
    return {
        "version": 1,
        "algorithm": "PBAS",
        "runtime": {
            "python_executable": "/env/bin/python",
            "source_directory": "/srv/PBAS",
            "entrypoint": "main.py",
        },
        "dataset": {
            "name": "MVTec AD",
            "root_directory": "/data/mvtec_ad",
            "classes": ["screw"],
        },
        "resources": {"gpu_index": 2},
        "training": {
            "epochs": 5,
            "batch_size": 8,
            "num_workers": 4,
            "resize": 288,
            "image_size": 288,
            "seed": 0,
            "eval_every": 1,
            "learning_rate": 0.0001,
        },
        "output": {"root_directory": "/runs/phase0"},
    }


class Phase0RunnerTests(unittest.TestCase):
    def test_builds_fixed_command_without_shell_fragments(self):
        values = runner.validate_config(valid_config(), check_files=False)
        command = runner.build_command(values, Path("/runs/phase0/job-1"))
        self.assertEqual(command[0], "/env/bin/python")
        self.assertEqual(command[1], "/srv/PBAS/main.py")
        self.assertIn("cuda:0", command)
        self.assertIn("screw", command)
        self.assertNotIn("bash", command)
        self.assertNotIn("conda", command)

    def test_rejects_unknown_dataset_class(self):
        config = valid_config()
        config["dataset"]["classes"] = ["../escape"]
        with self.assertRaises(runner.ConfigError):
            runner.validate_config(config, check_files=False)

    def test_builds_visa_loader_command(self):
        config = valid_config()
        config["dataset"] = {
            "name": "VisA",
            "root_directory": "/data/visa",
            "classes": ["pcb1"],
        }

        values = runner.validate_config(config, check_files=False)
        command = runner.build_command(values, Path("/runs/PBAS/VisA/job-1"))

        self.assertEqual(command[-2:], ["visa", "/data/visa"])
        self.assertIn("pcb1", command)

    def test_rejects_class_from_another_supported_dataset(self):
        config = valid_config()
        config["dataset"] = {
            "name": "VisA",
            "root_directory": "/data/visa",
            "classes": ["screw"],
        }

        with self.assertRaises(runner.ConfigError):
            runner.validate_config(config, check_files=False)

    def test_rejects_more_than_ten_epochs(self):
        config = valid_config()
        config["training"]["epochs"] = 11
        with self.assertRaises(runner.ConfigError):
            runner.validate_config(config, check_files=False)

    def test_rejects_arbitrary_entrypoint(self):
        config = valid_config()
        config["runtime"]["entrypoint"] = "anything.py"
        with self.assertRaises(runner.ConfigError):
            runner.validate_config(config, check_files=False)

    def test_load_config_rejects_unknown_top_level_field(self):
        config = valid_config()
        config["command"] = "rm -rf /"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(runner.ConfigError):
                runner.load_config(path)


if __name__ == "__main__":
    unittest.main()
