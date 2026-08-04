"""算法训练适配器的最小稳定契约。"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import Any

from services.training_log_parser import ParsedTrainingLine, clean_log_line
from services.training_reliability import artifact_descriptor


class AlgorithmAdapterError(ValueError):
    """适配器拒绝了不安全或不完整的算法配置。"""


class AlgorithmAdapter(ABC):
    """把算法差异隔离在插件内，执行器只管理训练生命周期。"""

    key: str
    protocol_version = "1.0"
    supports_inference = False

    @abstractmethod
    def validate_parameters(self, parameters: dict[str, Any]) -> dict[str, Any]:
        """校验用户参数并返回包含默认值的规范化副本。"""

    @abstractmethod
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
        """构造传给远程 runner 的纯数据配置，禁止返回 shell 命令。"""

    def parse_log_line(self, raw: str) -> ParsedTrainingLine | None:
        """解释算法日志；默认实现只做安全清洗，不猜测指标语义。"""
        line = clean_log_line(raw)
        if not line:
            return None
        is_error = any(
            marker in line
            for marker in (
                "ERROR",
                "RuntimeError:",
                "OSError:",
                "Traceback (most recent call last)",
            )
        )
        return ParsedTrainingLine(
            content=line[:4000],
            stream="ERROR" if is_error else "STDOUT",
        )

    def describe_artifact(self, relative_path: str) -> tuple[str, str, bool]:
        """返回产物类型、角色和是否允许下载。"""
        return artifact_descriptor(relative_path)

    def extract_final_metrics(
        self,
        artifacts: dict[str, str],
    ) -> list[tuple[str, float, int | None]]:
        """从已读取的文本产物提取最终指标。"""
        return []

    def metric_artifact_paths(self) -> tuple[str, ...]:
        """声明需要读取为文本并交给 extract_final_metrics 的小型指标产物。"""
        return ()

    def validate_dataset(
        self,
        dataset_name: str,
        dataset_runtime: dict[str, Any],
    ) -> None:
        """在任务入队前校验算法与数据集兼容性。"""
        if not dataset_name or not dataset_runtime.get("root_directory"):
            raise AlgorithmAdapterError("数据集名称或根目录不完整")

    def validate_job_parameters(
        self,
        parameters: dict[str, Any],
        dataset_name: str,
        dataset_runtime: dict[str, Any],
    ) -> dict[str, Any]:
        """结合所选数据集校验并规范化任务参数。"""
        self.validate_dataset(dataset_name, dataset_runtime)
        return self.validate_parameters(parameters)

    def parameter_schema_for_dataset(
        self,
        parameter_schema: dict[str, Any] | None,
        dataset_name: str,
    ) -> dict[str, Any]:
        """返回所选数据集使用的参数 Schema，默认保持算法基础 Schema。"""
        del dataset_name
        return copy.deepcopy(parameter_schema or {})

    def runner_path(self, executor_config: dict[str, Any]) -> str:
        """返回受管理员控制的远程插件 runner 路径。"""
        return str(executor_config["runner_path"])

    def total_epochs(self, parameters: dict[str, Any]) -> int | None:
        value = parameters.get("epochs")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def validate_inference_parameters(
        self,
        parameters: dict[str, Any],
        training_parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """校验推理参数。未显式适配的算法默认不可推理。"""
        del parameters, training_parameters
        raise AlgorithmAdapterError(f"算法 {self.key} 尚未适配推理流程")

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
        """构造远程推理 runner 的纯数据配置。"""
        del (
            runtime, dataset_name, dataset_runtime, training_parameters,
            inference_parameters, gpu_index, source_run_directory, output_root,
        )
        raise AlgorithmAdapterError(f"算法 {self.key} 尚未适配推理流程")

    def inference_runner_path(self, executor_config: dict[str, Any]) -> str:
        return str(executor_config["runner_path"])
