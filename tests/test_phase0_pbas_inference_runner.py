import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "phase0_pbas_inference_runner.py"
SPEC = importlib.util.spec_from_file_location("pbas_inference_runner", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runner)


class PbasInferenceRunnerTests(unittest.TestCase):
    def test_copies_only_model_artifacts_for_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "models" / "class-a").mkdir(parents=True)
            (source / "models" / "class-a" / "ckpt_best_1.pth").write_bytes(b"model")
            (source / "eval").mkdir()
            (source / "eval" / "001.png").write_bytes(b"image")
            (source / "results.csv").write_text("metric,value\nauc,1\n")

            target = root / "target"
            copied = runner.copy_model_artifacts(source, target)

            self.assertEqual(copied, 1)
            self.assertTrue((target / "models" / "class-a" / "ckpt_best_1.pth").exists())
            self.assertFalse((target / "eval" / "001.png").exists())
            self.assertFalse((target / "results.csv").exists())

    def test_replays_training_command_as_test_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "training"
            source.mkdir()
            original = [
                "/env/bin/python", "/srv/PBAS/main.py",
                "--results_path", str(source / "artifacts"),
                "--test", "ckpt", "net", "dataset", "--batch_size", "8",
                "-d", "screw", "-d", "bottle", "mvtec", "/data/mvtec",
            ]
            (source / "command.json").write_text(
                json.dumps({"argv": original}), encoding="utf-8"
            )
            values = {
                "python": Path("/env/bin/python"),
                "source_dir": Path("/srv/PBAS"),
                "dataset_root": Path("/data/mvtec"),
                "classes": ["screw"],
                "command_path": source / "command.json",
            }
            target = root / "inference" / "artifacts"
            command = runner.build_command(values, target)

            self.assertEqual(command[command.index("--test") + 1], "test")
            self.assertEqual(command[command.index("--results_path") + 1], str(target))
            self.assertIn("screw", command)
            self.assertNotIn("bottle", command)
            self.assertEqual(command[-4:], ["-d", "screw", "mvtec", "/data/mvtec"])
            self.assertEqual(json.loads((source / "command.json").read_text())["argv"], original)

    def test_rejects_runtime_that_differs_from_training_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            command_path = Path(directory) / "command.json"
            command_path.write_text(json.dumps({"argv": ["/evil/python", "/tmp/main.py"]}))
            with self.assertRaises(runner.ConfigError):
                runner.build_command({
                    "python": Path("/env/bin/python"),
                    "source_dir": Path("/srv/PBAS"),
                    "dataset_root": Path("/data/mvtec"),
                    "classes": ["screw"],
                    "command_path": command_path,
                }, Path("/runs/inference/artifacts"))


if __name__ == "__main__":
    unittest.main()
