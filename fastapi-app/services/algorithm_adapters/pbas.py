"""PBAS 算法训练插件。"""

from __future__ import annotations

import csv
import copy
import io
import posixpath
from typing import Any

from services.algorithm_adapters.base import AlgorithmAdapter, AlgorithmAdapterError
from services.training_log_parser import ParsedTrainingLine, parse_training_line


DATASET_PROFILES = {
    "MVTec AD": {
        "loader": "mvtec",
        "classes": (
            "bottle", "cable", "capsule", "carpet", "grid", "hazelnut",
            "leather", "metal_nut", "pill", "screw", "tile", "toothbrush",
            "transistor", "wood", "zipper",
        ),
    },
    "VisA": {
        "loader": "visa",
        "classes": (
            "candle", "capsules", "cashew", "chewinggum", "fryum",
            "macaroni1", "macaroni2", "pcb1", "pcb2", "pcb3", "pcb4",
            "pipe_fryum",
        ),
    },
    "MPDD": {
        "loader": "mpdd",
        "classes": (
            "bracket_black", "bracket_brown", "bracket_white", "connector",
            "metal_plate", "tubes",
        ),
    },
    "BTAD": {
        "loader": "btad",
        "classes": ("01", "02", "03"),
    },
}
ALL_SUPPORTED_CLASSES = {
    class_name
    for profile in DATASET_PROFILES.values()
    for class_name in profile["classes"]
}


class PbasAlgorithmAdapter(AlgorithmAdapter):
    key = "PBAS"
    protocol_version = "phase0-v1"
    supports_inference = True

    def parse_log_line(self, raw: str) -> ParsedTrainingLine | None:
        return parse_training_line(raw)

    def validate_dataset(
        self,
        dataset_name: str,
        dataset_runtime: dict[str, Any],
    ) -> None:
        super().validate_dataset(dataset_name, dataset_runtime)
        if dataset_name not in DATASET_PROFILES:
            raise AlgorithmAdapterError(
                f"PBAS 尚未适配数据集 {dataset_name}；"
                f"当前支持：{', '.join(DATASET_PROFILES)}"
            )

    def validate_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        return self._validate_parameters(
            parameters,
            allowed_classes=ALL_SUPPORTED_CLASSES,
            dataset_name="PBAS 支持的数据集",
        )

    def validate_job_parameters(
        self,
        parameters: dict[str, Any],
        dataset_name: str,
        dataset_runtime: dict[str, Any],
    ) -> dict[str, Any]:
        self.validate_dataset(dataset_name, dataset_runtime)
        normalized = self._validate_parameters(
            parameters,
            allowed_classes=set(DATASET_PROFILES[dataset_name]["classes"]),
            dataset_name=dataset_name,
        )
        # 用户提交空数组表示"全选该数据集的类别"；展开后写入 config_json，
        # 让重试、审计、远程 runner 都看到完整类别列表，避免下游二义性。
        if not normalized["classes"]:
            normalized["classes"] = list(DATASET_PROFILES[dataset_name]["classes"])
        return normalized

    @staticmethod
    def _validate_parameters(
        parameters: dict[str, Any],
        *,
        allowed_classes: set[str],
        dataset_name: str,
    ) -> dict[str, Any]:
        allowed = {
            "classes", "epochs", "batch_size", "num_workers", "resize",
            "image_size", "seed", "eval_every", "learning_rate",
        }
        unknown = set(parameters) - allowed
        if unknown:
            raise AlgorithmAdapterError(
                f"存在未支持的训练参数: {', '.join(sorted(unknown))}"
            )
        classes = parameters.get("classes", [])
        if (
            not isinstance(classes, list)
            or len(set(classes)) != len(classes)
            or any(
                not isinstance(item, str) or item not in allowed_classes
                for item in classes
            )
        ):
            raise AlgorithmAdapterError(f"{dataset_name} 类别参数无效")

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            value = parameters.get(name, default)
            if isinstance(value, bool) or not isinstance(value, int):
                raise AlgorithmAdapterError(f"{name} 必须是整数")
            if not minimum <= value <= maximum:
                raise AlgorithmAdapterError(f"{name} 超出允许范围")
            return value

        epochs = integer("epochs", 5, 1, 10)
        learning_rate = parameters.get("learning_rate", 0.0001)
        if (
            isinstance(learning_rate, bool)
            or not isinstance(learning_rate, (int, float))
            or not 0 < float(learning_rate) <= 1
        ):
            raise AlgorithmAdapterError("learning_rate 超出允许范围")
        return {
            "classes": list(classes),
            "epochs": epochs,
            "batch_size": integer("batch_size", 8, 1, 64),
            "num_workers": integer("num_workers", 4, 0, 32),
            "resize": integer("resize", 288, 64, 1024),
            "image_size": integer("image_size", 288, 64, 1024),
            "seed": integer("seed", 0, 0, 2**31 - 1),
            "eval_every": integer("eval_every", 1, 1, epochs),
            "learning_rate": float(learning_rate),
        }

    def parameter_schema_for_dataset(
        self,
        parameter_schema: dict[str, Any] | None,
        dataset_name: str,
    ) -> dict[str, Any]:
        schema = copy.deepcopy(parameter_schema or {})
        profile = DATASET_PROFILES.get(dataset_name)
        if profile is None:
            return schema
        properties = schema.setdefault("properties", {})
        # 直接赋值而非 setdefault：管理员在 parameter_schema_json 里写死的
        # title/default 必须让位于"按数据集动态化"的语义，否则前端切数据集时
        # 字段标题永远是历史值（例如 "MVTec 类别"），且会预选首个类别。
        classes_schema = properties.setdefault("classes", {})
        classes_schema["type"] = "array"
        classes_schema["title"] = f"{dataset_name} 类别"
        classes_schema["default"] = []
        items_schema = classes_schema.setdefault("items", {"type": "string"})
        items_schema["enum"] = list(profile["classes"])
        required = schema.get("required")
        if isinstance(required, list) and "classes" in required:
            schema["required"] = [name for name in required if name != "classes"]
        return schema

    def build_remote_config(
        self,
        *,
        runtime: dict[str, Any],
        dataset_name: str,
        dataset_runtime: dict[str, Any],
        parameters: dict[str, Any],
        gpu_index: int,
        output_root: str,
    ) -> dict[str, Any]:
        self.validate_dataset(dataset_name, dataset_runtime)
        return {
            "version": 1,
            "algorithm": self.key,
            "runtime": {
                "python_executable": posixpath.join(
                    runtime["conda_env_path"], "bin/python"
                ),
                "source_directory": runtime["source_directory"],
                "entrypoint": runtime["entrypoint"],
            },
            "dataset": {
                "name": dataset_name,
                "root_directory": dataset_runtime["root_directory"],
                "classes": parameters["classes"],
            },
            "resources": {"gpu_index": gpu_index},
            "training": {
                key: value for key, value in parameters.items() if key != "classes"
            },
            "output": {"root_directory": output_root},
        }

    def extract_final_metrics(
        self,
        artifacts: dict[str, str],
    ) -> list[tuple[str, float, int | None]]:
        results_text = artifacts.get("artifacts/results.csv")
        if not results_text:
            return []
        rows = list(csv.DictReader(io.StringIO(results_text)))
        if not rows:
            return []
        row = rows[0]
        epoch = int(float(row["best_epoch"])) if row.get("best_epoch") else None
        metrics = []
        for name in (
            "image_auroc", "image_ap", "pixel_auroc", "pixel_ap", "pixel_pro"
        ):
            if row.get(name):
                metrics.append((name, float(row[name]), epoch))
        return metrics

    def metric_artifact_paths(self) -> tuple[str, ...]:
        return ("artifacts/results.csv",)

    def validate_inference_parameters(
        self,
        parameters: dict[str, Any],
        training_parameters: dict[str, Any],
    ) -> dict[str, Any]:
        unknown = set(parameters) - {"classes"}
        if unknown:
            raise AlgorithmAdapterError(
                f"存在未支持的推理参数: {', '.join(sorted(unknown))}"
            )
        trained_classes = training_parameters.get("classes") or []
        classes = parameters.get("classes") or trained_classes
        if (
            not isinstance(classes, list)
            or not classes
            or len(set(classes)) != len(classes)
            or any(item not in trained_classes for item in classes)
        ):
            raise AlgorithmAdapterError("推理类别必须来自该训练任务已训练的类别")
        return {"classes": list(classes)}

    def build_inference_config(
        self,
        *,
        runtime: dict[str, Any],
        dataset_name: str,
        dataset_runtime: dict[str, Any],
        training_parameters: dict[str, Any],
        inference_parameters: dict[str, Any],
        gpu_index: int,
        source_run_directory: str,
        output_root: str,
    ) -> dict[str, Any]:
        self.validate_dataset(dataset_name, dataset_runtime)
        selected = self.validate_inference_parameters(
            inference_parameters,
            training_parameters,
        )
        return {
            "version": 1,
            "algorithm": self.key,
            "runtime": {
                "python_executable": posixpath.join(
                    runtime["conda_env_path"], "bin/python"
                ),
                "source_directory": runtime["source_directory"],
                # 官方 PBAS 通过 main.py 的 --test test 进入 checkpoint 测试。
                "entrypoint": runtime["entrypoint"],
            },
            "dataset": {
                "name": dataset_name,
                "root_directory": dataset_runtime["root_directory"],
                "classes": selected["classes"],
            },
            "resources": {"gpu_index": gpu_index},
            "training": {
                key: value
                for key, value in training_parameters.items()
                if key != "classes"
            },
            "source": {"run_directory": source_run_directory},
            "output": {"root_directory": output_root},
        }


pbas_algorithm_adapter = PbasAlgorithmAdapter()
