import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.algorithm_adapters import (  # noqa: E402
    AlgorithmAdapter,
    AlgorithmAdapterRegistry,
    algorithm_adapter_registry,
)


class AlgorithmAdapterTests(unittest.TestCase):
    def test_pbas_is_exposed_as_registered_adapter(self):
        adapter = algorithm_adapter_registry.require("pbas")
        self.assertEqual(adapter.key, "PBAS")
        self.assertTrue(adapter.supports_inference)
        self.assertEqual(adapter.validate_parameters({"epochs": 1})["epochs"], 1)

    def test_pbas_remote_config_remains_legacy_runner_compatible(self):
        adapter = algorithm_adapter_registry.require("PBAS")
        parameters = adapter.validate_parameters({"classes": ["screw"]})
        config = adapter.build_remote_config(
            runtime={
                "conda_env_path": "/envs/pbas",
                "source_directory": "/srv/PBAS",
                "entrypoint": "main.py",
            },
            dataset_name="MVTec AD",
            dataset_runtime={"root_directory": "/data/mvtec"},
            parameters=parameters,
            gpu_index=2,
            output_root="/runs",
        )
        self.assertEqual(config["algorithm"], "PBAS")
        self.assertEqual(config["runtime"]["python_executable"], "/envs/pbas/bin/python")
        self.assertNotIn("adapter", config)
        self.assertNotIn("command", config)

    def test_pbas_exposes_dataset_specific_class_schema(self):
        adapter = algorithm_adapter_registry.require("PBAS")
        base_schema = {
            "type": "object",
            "properties": {
                "classes": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["screw"]},
                    "default": ["screw"],
                    "title": "MVTec 类别",
                }
            },
            "required": ["classes", "epochs"],
        }

        visa_schema = adapter.parameter_schema_for_dataset(base_schema, "VisA")

        self.assertIn(
            "pcb1",
            visa_schema["properties"]["classes"]["items"]["enum"],
        )
        # title/default 必须被适配器覆盖，否则前端切数据集时字段标题与预选类别
        # 会停留在管理员 schema 里的历史值（例如 "MVTec 类别" / 首类）。
        self.assertEqual(
            visa_schema["properties"]["classes"]["title"],
            "VisA 类别",
        )
        self.assertEqual(
            visa_schema["properties"]["classes"]["default"],
            [],
        )
        # "classes" 必须从 required 中剔除，因为空数组表示全选。
        self.assertNotIn("classes", visa_schema.get("required", []))
        self.assertIn("epochs", visa_schema["required"])
        # base_schema 不可变（深拷贝契约）。
        self.assertEqual(
            base_schema["properties"]["classes"]["items"]["enum"],
            ["screw"],
        )
        self.assertEqual(
            base_schema["properties"]["classes"]["title"],
            "MVTec 类别",
        )

    def test_pbas_schema_title_follows_dataset_name(self):
        adapter = algorithm_adapter_registry.require("PBAS")

        mvtec_schema = adapter.parameter_schema_for_dataset({}, "MVTec AD")
        self.assertEqual(
            mvtec_schema["properties"]["classes"]["title"],
            "MVTec AD 类别",
        )
        self.assertEqual(
            len(mvtec_schema["properties"]["classes"]["items"]["enum"]),
            15,
        )

        btad_schema = adapter.parameter_schema_for_dataset({}, "BTAD")
        self.assertEqual(
            btad_schema["properties"]["classes"]["title"],
            "BTAD 类别",
        )

    def test_pbas_schema_unknown_dataset_returns_base_untouched(self):
        adapter = algorithm_adapter_registry.require("PBAS")
        base_schema = {"properties": {"classes": {"title": "自定义"}}}
        result = adapter.parameter_schema_for_dataset(base_schema, "Unknown")
        self.assertEqual(result["properties"]["classes"]["title"], "自定义")

    def test_pbas_validate_job_parameters_expands_empty_classes_to_all(self):
        adapter = algorithm_adapter_registry.require("PBAS")

        values = adapter.validate_job_parameters(
            {"classes": [], "epochs": 1},
            "MVTec AD",
            {"root_directory": "/data/mvtec"},
        )

        # 空数组在 job 入口展开为该数据集的全部类别，下游 runner/审计/重试
        # 都看到完整列表，避免空数组在远程被解释为"不训练任何类别"。
        self.assertEqual(len(values["classes"]), 15)
        self.assertIn("screw", values["classes"])
        self.assertIn("zipper", values["classes"])

    def test_pbas_validate_job_parameters_keeps_explicit_classes(self):
        adapter = algorithm_adapter_registry.require("PBAS")

        values = adapter.validate_job_parameters(
            {"classes": ["screw", "bottle"], "epochs": 1},
            "MVTec AD",
            {"root_directory": "/data/mvtec"},
        )
        self.assertEqual(values["classes"], ["screw", "bottle"])

    def test_pbas_validate_job_parameters_rejects_unknown_class(self):
        adapter = algorithm_adapter_registry.require("PBAS")
        with self.assertRaises(ValueError):
            adapter.validate_job_parameters(
                {"classes": ["nonexistent"], "epochs": 1},
                "MVTec AD",
                {"root_directory": "/data/mvtec"},
            )

    def test_pbas_validates_classes_against_selected_dataset(self):
        adapter = algorithm_adapter_registry.require("PBAS")

        values = adapter.validate_job_parameters(
            {"classes": ["pcb1"], "epochs": 1},
            "VisA",
            {"root_directory": "/data/visa"},
        )

        self.assertEqual(values["classes"], ["pcb1"])
        with self.assertRaises(ValueError):
            adapter.validate_job_parameters(
                {"classes": ["screw"], "epochs": 1},
                "VisA",
                {"root_directory": "/data/visa"},
            )

    def test_pbas_inference_can_only_select_trained_classes(self):
        adapter = algorithm_adapter_registry.require("PBAS")
        trained = {"classes": ["screw", "bottle"], "epochs": 1}

        self.assertEqual(
            adapter.validate_inference_parameters({}, trained),
            {"classes": ["screw", "bottle"]},
        )
        self.assertEqual(
            adapter.validate_inference_parameters({"classes": ["screw"]}, trained),
            {"classes": ["screw"]},
        )
        with self.assertRaises(ValueError):
            adapter.validate_inference_parameters({"classes": ["cable"]}, trained)

    def test_pbas_inference_config_reuses_training_runtime_and_artifacts(self):
        adapter = algorithm_adapter_registry.require("PBAS")
        config = adapter.build_inference_config(
            runtime={
                "conda_env_path": "/envs/pbas",
                "source_directory": "/srv/PBAS",
                "entrypoint": "main.py",
            },
            dataset_name="MVTec AD",
            dataset_runtime={"root_directory": "/data/mvtec"},
            training_parameters={"classes": ["screw"], "epochs": 1},
            inference_parameters={"classes": ["screw"]},
            gpu_index=2,
            source_run_directory="/runs/training/job-1",
            output_root="/runs/inference",
        )
        self.assertEqual(config["source"]["run_directory"], "/runs/training/job-1")
        self.assertEqual(config["dataset"]["classes"], ["screw"])
        self.assertEqual(config["resources"]["gpu_index"], 2)

    def test_new_algorithm_can_be_added_without_executor_changes(self):
        class DemoAdapter(AlgorithmAdapter):
            key = "DEMO"

            def validate_parameters(self, parameters):
                return {"epochs": int(parameters.get("epochs", 1))}

            def build_remote_config(
                self,
                *,
                runtime,
                dataset_name,
                dataset_runtime,
                parameters,
                gpu_index,
                output_root,
            ):
                return {
                    "version": 1,
                    "algorithm": self.key,
                    "runtime": runtime,
                    "dataset": {
                        "name": dataset_name,
                        "root_directory": dataset_runtime["root_directory"],
                    },
                    "resources": {"gpu_index": gpu_index},
                    "training": parameters,
                    "output": {"root_directory": output_root},
                }

        registry = AlgorithmAdapterRegistry()
        registry.register(DemoAdapter())
        adapter = registry.require("demo")
        self.assertFalse(adapter.supports_inference)
        config = adapter.build_remote_config(
            runtime={"entrypoint": "train.py"},
            dataset_name="Demo",
            dataset_runtime={"root_directory": "/data/demo"},
            parameters=adapter.validate_parameters({}),
            gpu_index=0,
            output_root="/runs",
        )
        self.assertEqual(config["algorithm"], "DEMO")
        self.assertEqual(config["training"]["epochs"], 1)

    def test_registry_rejects_duplicate_keys(self):
        registry = AlgorithmAdapterRegistry()

        class DemoAdapter(AlgorithmAdapter):
            key = "demo"

            def validate_parameters(self, parameters):
                return parameters

            def build_remote_config(self, **kwargs):
                return kwargs

        registry.register(DemoAdapter())
        with self.assertRaises(ValueError):
            registry.register(DemoAdapter())


if __name__ == "__main__":
    unittest.main()
