"""通用训练执行器：算法适配器驱动的白名单启动、监控与任务生命周期。"""

from __future__ import annotations

import asyncio
import json
import logging
import posixpath
import re
import shlex
import stat
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from tortoise import connections
from tortoise.transactions import in_transaction

from models import (
    Algorithm,
    Dataset,
    TrainingArtifact,
    TrainingAudit,
    TrainingEvent,
    TrainingJob,
    TrainingJobDeletion,
    TrainingLog,
    TrainingMetric,
    InferenceJob,
)
from settings import TRAINING_EXECUTOR_CONFIG
from services.algorithm_adapters import (
    AlgorithmAdapter,
    AlgorithmAdapterError,
    algorithm_adapter_registry,
)
from services.training_log_parser import ParsedTrainingLine
from services.training_reliability import (
    classify_failure,
    hard_delete_blockers,
    safe_artifact_path,
)

try:
    import asyncssh
except ImportError:  # pragma: no cover - 由部署依赖检查覆盖
    asyncssh = None


logger = logging.getLogger(__name__)
ACTIVE_STATUSES = {"QUEUED", "STARTING", "RUNNING", "STOPPING"}
REMOTE_ACTIVE_STATUSES = {"STARTING", "RUNNING", "STOPPING"}
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "STOPPED", "LOST"}
class TrainingExecutorError(RuntimeError):
    pass


class NoGpuAvailableError(TrainingExecutorError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _absolute_path(value: str, name: str) -> str:
    normalized = posixpath.normpath(value)
    if not value or not posixpath.isabs(normalized):
        raise TrainingExecutorError(f"{name} 必须是绝对路径")
    return normalized


def _safe_directory_segment(value: str, prefix: str, record_id: int) -> str:
    """生成不可逃逸且在重命名后仍能通过 ID 区分的目录段。"""
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    normalized = normalized.strip("._-")[:48] or "unnamed"
    return f"{prefix}-{record_id}-{normalized}"


def _isolated_output_root(
    output_root: str,
    *,
    algorithm_id: int,
    algorithm_name: str,
    dataset_id: int,
    dataset_name: str,
) -> str:
    root = _absolute_path(output_root, "输出目录")
    return posixpath.join(
        root,
        _safe_directory_segment(algorithm_name, "algorithm", algorithm_id),
        _safe_directory_segment(dataset_name, "dataset", dataset_id),
    )


def _load_allowlist(raw: str, name: str) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TrainingExecutorError(f"{name} 不是有效 JSON") from exc
    if not isinstance(value, dict) or not value:
        raise TrainingExecutorError(f"{name} 必须是非空 JSON 对象")
    return value


class TrainingExecutorService:
    def __init__(self) -> None:
        self.config = TRAINING_EXECUTOR_CONFIG
        self._monitor_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._reconcile_lock = asyncio.Lock()
        self._dispatch_lock = asyncio.Lock()
        self._log_sync_locks: dict[int, asyncio.Lock] = {}

    @property
    def enabled(self) -> bool:
        return bool(
            self.config["enabled"]
            and self.config["host"]
            and self.config["ssh_user"]
            and self.config["private_key_path"]
        )

    def _connection_options(self) -> dict[str, Any]:
        options = {
            "host": self.config["host"],
            "port": self.config["port"],
            "username": self.config["ssh_user"],
            "client_keys": [self.config["private_key_path"]],
        }
        if self.config["known_hosts_path"]:
            options["known_hosts"] = self.config["known_hosts_path"]
        return options

    async def _connect(self):
        if asyncssh is None:
            raise TrainingExecutorError("后端缺少 asyncssh 依赖")
        if not self.enabled:
            raise TrainingExecutorError("训练执行器未启用或配置不完整")
        try:
            return await asyncio.wait_for(
                asyncssh.connect(**self._connection_options()),
                timeout=self.config["connect_timeout"],
            )
        except Exception as exc:
            raise TrainingExecutorError("无法连接低权限训练账号") from exc

    async def _run(self, connection, command: str, check: bool = True):
        try:
            return await connection.run(
                command,
                check=check,
                timeout=self.config["command_timeout"],
            )
        except Exception as exc:
            raise TrainingExecutorError("远程训练控制命令执行失败") from exc

    async def _event(
        self,
        job_id: int,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        latest = await TrainingEvent.filter(job_id=job_id).order_by(
            "-sequence"
        ).first()
        sequence = (latest.sequence + 1) if latest else 1
        await TrainingEvent.create(
            job_id=job_id,
            sequence=sequence,
            event_type=event_type,
            message=message,
            payload_json=payload,
        )

    async def audit(
        self,
        job_id: int,
        action: str,
        actor: dict[str, Any] | None = None,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
        result: str = "SUCCESS",
    ) -> None:
        await TrainingAudit.create(
            job_id=job_id,
            actor_id=actor.get("user_id") if actor else None,
            actor_role=actor.get("role") if actor else "系统",
            action=action,
            result=result,
            message=message,
            payload_json=payload,
        )

    async def build_algorithm_allowlist(self) -> dict[str, dict[str, Any]]:
        algorithms = await Algorithm.filter(
            deleted_at__isnull=True,
        ).prefetch_related("algorithm_infos")
        allowlist: dict[str, dict[str, Any]] = {}
        for algo in algorithms:
            if not algo.algorithm_infos:
                continue
            key = (algo.abbreviation or "").upper()
            adapter = algorithm_adapter_registry.get(key)
            if adapter is None:
                continue
            info = algo.algorithm_infos[0]
            resource_spec = info.resource_spec_json or {}
            configured_minimum = resource_spec.get(
                "min_free_gpu_memory_mb",
                self.config.get("min_free_gpu_memory_mb", 8000),
            )
            try:
                minimum_memory = max(0, int(configured_minimum))
            except (TypeError, ValueError):
                logger.warning(
                    "算法 %s 的 min_free_gpu_memory_mb 无效，使用系统默认值",
                    key,
                )
                minimum_memory = int(
                    self.config.get("min_free_gpu_memory_mb", 8000)
                )
            allowlist[key] = {
                "conda_env_path": info.conda_env_path or "",
                "source_directory": info.working_directory or "",
                "entrypoint": info.train_entrypoint or "",
                "min_free_gpu_memory_mb": minimum_memory,
                "adapter_key": adapter.key,
                "adapter_protocol_version": adapter.protocol_version,
            }
        return allowlist

    async def build_dataset_allowlist(self) -> dict[str, dict[str, Any]]:
        datasets = await Dataset.filter(
            deleted_at__isnull=True,
        ).prefetch_related("dataset_infos")
        allowlist: dict[str, dict[str, Any]] = {}
        for ds in datasets:
            if not ds.dataset_infos:
                continue
            info = ds.dataset_infos[0]
            allowlist[ds.name] = {
                "root_directory": info.root_directory or "",
            }
        return allowlist

    async def _resolve_whitelisted_runtime(
        self,
        algorithm_id: int,
        dataset_id: int,
    ) -> tuple[
        Algorithm,
        Dataset,
        dict[str, Any],
        dict[str, Any],
        AlgorithmAdapter,
    ]:
        algorithm = await Algorithm.filter(
            id=algorithm_id,
            deleted_at__isnull=True,
        ).prefetch_related("algorithm_infos").first()
        dataset = await Dataset.filter(
            id=dataset_id,
            deleted_at__isnull=True,
        ).prefetch_related("dataset_infos").first()
        if algorithm is None or not algorithm.algorithm_infos:
            raise TrainingExecutorError("算法不存在或缺少运行配置")
        if dataset is None or not dataset.dataset_infos:
            raise TrainingExecutorError("数据集不存在或缺少路径配置")

        algorithm_allowlist = await self.build_algorithm_allowlist()
        dataset_allowlist = await self.build_dataset_allowlist()
        algorithm_key = (algorithm.abbreviation or "").upper()
        adapter = algorithm_adapter_registry.get(algorithm_key)
        if adapter is None:
            raise TrainingExecutorError(
                f"算法 {algorithm_key or algorithm.name} 尚未安装训练适配器"
            )
        runtime = algorithm_allowlist.get(algorithm_key)
        dataset_runtime = dataset_allowlist.get(dataset.name)
        if not isinstance(runtime, dict):
            raise TrainingExecutorError("算法未进入训练白名单")
        if not isinstance(dataset_runtime, dict):
            raise TrainingExecutorError("数据集未进入训练白名单")

        info = algorithm.algorithm_infos[0]
        expected_env = _absolute_path(str(runtime.get("conda_env_path", "")), "Conda 环境")
        expected_source = _absolute_path(
            str(runtime.get("source_directory", "")),
            "算法源码目录",
        )
        expected_entrypoint = str(runtime.get("entrypoint", ""))
        expected_dataset = _absolute_path(
            str(dataset_runtime.get("root_directory", "")),
            "数据集目录",
        )
        if (
            posixpath.normpath(info.conda_env_path or "") != expected_env
            or posixpath.normpath(info.working_directory or "") != expected_source
            or info.train_entrypoint != expected_entrypoint
        ):
            raise TrainingExecutorError("数据库算法运行配置与管理员白名单不一致")
        dataset_info = dataset.dataset_infos[0]
        if posixpath.normpath(dataset_info.root_directory or "") != expected_dataset:
            raise TrainingExecutorError("数据库数据集路径与管理员白名单不一致")
        return algorithm, dataset, runtime, dataset_runtime, adapter

    @staticmethod
    def _validated_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
        """PBAS 旧调用入口；新代码必须通过当前算法适配器校验。"""
        try:
            return algorithm_adapter_registry.require("PBAS").validate_parameters(
                parameters
            )
        except (AlgorithmAdapterError, KeyError) as exc:
            raise TrainingExecutorError(str(exc)) from exc

    async def _gpu_free_memory(self) -> dict[int, int]:
        connection = await self._connect()
        try:
            result = await self._run(
                connection,
                "nvidia-smi --query-gpu=index,memory.free "
                "--format=csv,noheader,nounits",
            )
        finally:
            connection.close()
            await connection.wait_closed()
        available = {}
        for line in result.stdout.splitlines():
            parts = [item.strip() for item in line.split(",")]
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                available[int(parts[0])] = int(parts[1])
        return available

    async def _allocate_gpu(
        self,
        job_id: int,
        requested_gpu: int | None,
        minimum_free_memory_mb: int,
    ) -> int:
        allowed = sorted(set(self.config["gpu_allowlist"]))
        if requested_gpu is not None and requested_gpu not in allowed:
            raise TrainingExecutorError("请求的 GPU 不在管理员白名单中")
        free_memory = await self._gpu_free_memory()
        async with in_transaction() as connection:
            await TrainingJob.filter(status__in=ACTIVE_STATUSES).using_db(
                connection
            ).select_for_update()
            leased = set(
                await TrainingJob.filter(
                    status__in=ACTIVE_STATUSES,
                    assigned_gpu__isnull=False,
                ).using_db(connection).values_list("assigned_gpu", flat=True)
            )
            leased.update(
                await InferenceJob.filter(
                    status__in={"QUEUED", "STARTING", "RUNNING"},
                    assigned_gpu__isnull=False,
                ).using_db(connection).values_list("assigned_gpu", flat=True)
            )
            candidates = [requested_gpu] if requested_gpu is not None else sorted(
                allowed,
                key=lambda gpu: free_memory.get(gpu, -1),
                reverse=True,
            )
            selected = next(
                (
                    gpu for gpu in candidates
                    if gpu not in leased
                    and free_memory.get(gpu, 0) >= minimum_free_memory_mb
                ),
                None,
            )
            if selected is None:
                raise NoGpuAvailableError(
                    f"暂无满足 {minimum_free_memory_mb} MiB 剩余显存的可用 GPU"
                )
            updated = await TrainingJob.filter(
                id=job_id,
                status="QUEUED",
            ).using_db(connection).update(
                assigned_gpu=selected,
                status="STARTING",
            )
            if updated != 1:
                raise TrainingExecutorError("任务状态已变化，停止调度")
            return selected

    def _build_remote_config(
        self,
        runtime: dict[str, Any],
        dataset_runtime: dict[str, Any],
        parameters: dict[str, Any],
        gpu_index: int,
    ) -> dict[str, Any]:
        """PBAS 旧调用入口，保留既有单元测试和内部调用兼容性。"""
        adapter = algorithm_adapter_registry.require("PBAS")
        return adapter.build_remote_config(
            runtime=runtime,
            dataset_name="MVTec AD",
            dataset_runtime=dataset_runtime,
            parameters=parameters,
            gpu_index=gpu_index,
            output_root=self.config["output_root"],
        )

    @staticmethod
    def _validate_adapter_parameters(
        adapter: AlgorithmAdapter,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return adapter.validate_parameters(parameters)
        except AlgorithmAdapterError as exc:
            raise TrainingExecutorError(str(exc)) from exc

    @staticmethod
    def _validate_adapter_job_parameters(
        adapter: AlgorithmAdapter,
        parameters: dict[str, Any],
        dataset_name: str,
        dataset_runtime: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return adapter.validate_job_parameters(
                parameters,
                dataset_name,
                dataset_runtime,
            )
        except AlgorithmAdapterError as exc:
            raise TrainingExecutorError(str(exc)) from exc

    async def submit_job(
        self,
        owner: dict[str, Any],
        algorithm_id: int,
        dataset_id: int,
        parameters: dict[str, Any],
        requested_gpu: int | None = None,
        retry_of_job_id: int | None = None,
        attempt: int = 1,
    ) -> TrainingJob:
        algorithm, dataset, runtime, dataset_runtime, adapter = (
            await self._resolve_whitelisted_runtime(algorithm_id, dataset_id)
        )
        del runtime
        validated = self._validate_adapter_job_parameters(
            adapter,
            parameters,
            dataset.name,
            dataset_runtime,
        )
        if requested_gpu is not None and requested_gpu not in self.config["gpu_allowlist"]:
            raise TrainingExecutorError("请求的 GPU 不在管理员白名单中")
        pending_count = await TrainingJob.filter(
            owner_id=owner["user_id"],
            owner_role=owner["role"],
            status__in=ACTIVE_STATUSES,
        ).count()
        if pending_count >= self.config["max_pending_jobs_per_user"]:
            raise TrainingExecutorError(
                f"每个用户最多保留 {self.config['max_pending_jobs_per_user']} 个活动或排队任务"
            )
        job_no = str(uuid.uuid4())
        job = await TrainingJob.create(
            job_no=job_no,
            owner_id=owner["user_id"],
            owner_role=owner["role"],
            algorithm_id=algorithm.id,
            dataset_id=dataset.id,
            status="QUEUED",
            config_json={
                "parameters": validated,
                "requested_gpu": requested_gpu,
                "adapter": {
                    "key": adapter.key,
                    "protocol_version": adapter.protocol_version,
                },
            },
            retry_of_job_id=retry_of_job_id,
            attempt=attempt,
            total_epochs=adapter.total_epochs(validated),
            timeout_seconds=self.config["max_runtime_seconds"],
        )
        await self._event(
            job.id,
            "JOB_CREATED",
            "训练任务已进入队列",
            {"retry_of_job_id": retry_of_job_id, "attempt": attempt},
        )
        await self.audit(
            job.id,
            "JOB_CREATE",
            owner,
            "创建训练任务",
            {
                "algorithm_id": algorithm.id,
                "dataset_id": dataset.id,
                "requested_gpu": requested_gpu,
            },
        )
        return job

    async def dispatch_job(self, job_id: int) -> TrainingJob:
        job = await TrainingJob.get_or_none(id=job_id)
        if job is None:
            raise TrainingExecutorError("训练任务不存在")
        if job.status != "QUEUED":
            return job
        algorithm, dataset, runtime, dataset_runtime, adapter = (
            await self._resolve_whitelisted_runtime(job.algorithm_id, job.dataset_id)
        )
        config = job.config_json or {}
        configured_adapter = (config.get("adapter") or {}).get("key")
        if (
            configured_adapter
            and str(configured_adapter).strip().upper() != adapter.key
        ):
            raise TrainingExecutorError("任务记录的算法适配器与当前算法不一致")
        validated = self._validate_adapter_job_parameters(
            adapter,
            config.get("parameters") or {},
            dataset.name,
            dataset_runtime,
        )
        requested_gpu = config.get("requested_gpu")
        minimum_memory = max(
            int(self.config["min_free_gpu_memory_mb"]),
            int(runtime.get("min_free_gpu_memory_mb") or 0),
        )
        try:
            gpu_index = await self._allocate_gpu(
                job.id,
                requested_gpu,
                minimum_memory,
            )
            await self._event(
                job.id,
                "GPU_ALLOCATED",
                f"已租用 GPU {gpu_index}",
                {"gpu_index": gpu_index},
            )
            await self._launch(
                job,
                algorithm,
                dataset,
                runtime,
                dataset_runtime,
                adapter,
                validated,
                gpu_index,
            )
        except NoGpuAvailableError:
            await TrainingJob.filter(id=job.id, status="QUEUED").update(
                assigned_gpu=None,
            )
            raise
        except Exception as exc:
            await TrainingJob.filter(id=job.id).update(
                status="FAILED",
                failure_code="LAUNCH_FAILED",
                failure_reason=str(exc),
                finished_at=_utc_now(),
            )
            await self._event(job.id, "LAUNCH_FAILED", str(exc))
            await self.audit(
                job.id,
                "LAUNCH_FAILED",
                message=str(exc),
                result="FAILED",
            )
            raise
        return await TrainingJob.get(id=job.id)

    async def create_job(
        self,
        owner: dict[str, Any],
        algorithm_id: int,
        dataset_id: int,
        parameters: dict[str, Any],
        requested_gpu: int | None = None,
    ) -> TrainingJob:
        """阶段 1 兼容入口：提交后立即尝试调度。"""
        job = await self.submit_job(
            owner,
            algorithm_id,
            dataset_id,
            parameters,
            requested_gpu,
        )
        return await self.dispatch_job(job.id)

    async def cancel_job(self, job_id: int) -> TrainingJob:
        job = await TrainingJob.get_or_none(id=job_id)
        if job is None:
            raise TrainingExecutorError("训练任务不存在")
        if job.status == "QUEUED":
            updated = await TrainingJob.filter(id=job.id, status="QUEUED").update(
                status="STOPPED",
                failure_code="CANCELED",
                failure_reason="任务在排队阶段被取消",
                finished_at=_utc_now(),
            )
            if updated:
                await self._event(job.id, "JOB_CANCELED", "排队任务已取消")
            return await TrainingJob.get(id=job.id)
        if job.status in {"STARTING", "RUNNING", "STOPPING"}:
            return await self.stop_job(job.id)
        return job

    async def retry_job(
        self,
        job_id: int,
        owner: dict[str, Any],
    ) -> TrainingJob:
        source = await TrainingJob.get_or_none(id=job_id)
        if source is None:
            raise TrainingExecutorError("训练任务不存在")
        if source.status not in TERMINAL_STATUSES:
            raise TrainingExecutorError("只有已结束的任务可以重试")
        config = source.config_json or {}
        return await self.submit_job(
            owner=owner,
            algorithm_id=source.algorithm_id,
            dataset_id=source.dataset_id,
            parameters=config.get("parameters") or {},
            requested_gpu=config.get("requested_gpu"),
            retry_of_job_id=source.id,
            attempt=source.attempt + 1,
        )

    async def _launch(
        self,
        job: TrainingJob,
        algorithm: Algorithm,
        dataset: Dataset,
        runtime: dict[str, Any],
        dataset_runtime: dict[str, Any],
        adapter: AlgorithmAdapter,
        parameters: dict[str, Any],
        gpu_index: int,
    ) -> None:
        control_root = _absolute_path(self.config["control_root"], "控制目录")
        output_root = _absolute_path(self.config["output_root"], "输出目录")
        isolated_output_root = _isolated_output_root(
            output_root,
            algorithm_id=algorithm.id,
            algorithm_name=algorithm.abbreviation or algorithm.name,
            dataset_id=dataset.id,
            dataset_name=dataset.name,
        )
        runner_path = _absolute_path(
            adapter.runner_path(self.config),
            f"{adapter.key} 训练适配器",
        )
        control_dir = posixpath.join(control_root, job.job_no)
        config_path = posixpath.join(control_dir, "config.json")
        bootstrap_log = posixpath.join(control_dir, "bootstrap.log")
        remote_run_dir = posixpath.join(isolated_output_root, job.job_no)
        try:
            remote_config = adapter.build_remote_config(
                runtime=runtime,
                dataset_name=dataset.name,
                dataset_runtime=dataset_runtime,
                parameters=parameters,
                gpu_index=gpu_index,
                output_root=isolated_output_root,
            )
        except AlgorithmAdapterError as exc:
            raise TrainingExecutorError(str(exc)) from exc
        python_path = posixpath.join(runtime["conda_env_path"], "bin/python")

        connection = await self._connect()
        try:
            sftp = await connection.start_sftp_client()
            await sftp.makedirs(control_dir, exist_ok=True)
            async with sftp.open(config_path, "w") as config_file:
                await config_file.write(
                    json.dumps(remote_config, ensure_ascii=False, indent=2) + "\n"
                )
            argv = [
                python_path,
                runner_path,
                "--config",
                config_path,
                "--run",
                "--run-id",
                job.job_no,
            ]
            quoted = " ".join(shlex.quote(item) for item in argv)
            command = (
                f"/usr/bin/nohup /usr/bin/setsid {quoted} "
                f"> {shlex.quote(bootstrap_log)} 2>&1 < /dev/null "
                "& printf '%s' $!"
            )
            result = await self._run(connection, command)
            launcher_pid = int(result.stdout.strip())
        finally:
            connection.close()
            await connection.wait_closed()

        snapshot = {
            "executor_protocol": "adapter-v1",
            "ssh_account": self.config["ssh_user"],
            "algorithm": adapter.key,
            "algorithm_id": algorithm.id,
            "adapter_key": adapter.key,
            "adapter_protocol_version": adapter.protocol_version,
            "runner_path": runner_path,
            "conda_env_path": runtime["conda_env_path"],
            "source_directory": runtime["source_directory"],
            "entrypoint": runtime["entrypoint"],
            "dataset": dataset.name,
            "dataset_id": dataset.id,
            "dataset_root": dataset_runtime["root_directory"],
            "isolated_output_root": isolated_output_root,
            "gpu_index": gpu_index,
            "launcher": "nohup+setsid",
        }
        await TrainingJob.filter(id=job.id).update(
            status="RUNNING",
            launcher_pid=launcher_pid,
            remote_control_dir=control_dir,
            remote_run_dir=remote_run_dir,
            runtime_snapshot_json=snapshot,
            started_at=_utc_now(),
            finished_at=None,
            failure_reason=None,
            exit_code=None,
            last_reconciled_at=_utc_now(),
        )
        await self._event(
            job.id,
            "PROCESS_STARTED",
            f"远程训练进程已启动，PID {launcher_pid}",
            {"launcher_pid": launcher_pid, "remote_run_dir": remote_run_dir},
        )
        await self.audit(
            job.id,
            "PROCESS_START",
            message=f"远程训练进程已启动，PID {launcher_pid}",
            payload={"gpu_index": gpu_index, "launcher_pid": launcher_pid},
        )

    async def _read_text(self, sftp, path: str) -> str | None:
        try:
            async with sftp.open(path, "r") as remote_file:
                return await remote_file.read()
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile):
            return None

    async def _adapter_for_job(self, job: TrainingJob) -> AlgorithmAdapter:
        """优先使用任务快照，兼容适配器重构前创建的历史 PBAS 任务。"""
        config = job.config_json or {}
        snapshot = job.runtime_snapshot_json or {}
        key = (
            snapshot.get("adapter_key")
            or snapshot.get("algorithm")
            or (config.get("adapter") or {}).get("key")
        )
        if not key:
            algorithm = await Algorithm.get_or_none(id=job.algorithm_id)
            key = algorithm.abbreviation if algorithm else ""
        adapter = algorithm_adapter_registry.get(str(key))
        if adapter is None:
            raise TrainingExecutorError(f"任务对应的算法适配器不可用: {key or '<empty>'}")
        return adapter

    async def _upsert_metric(
        self,
        job_id: int,
        name: str,
        value: float,
        epoch: int | None,
    ) -> None:
        existing = await TrainingMetric.filter(
            job_id=job_id,
            metric_name=name,
            epoch=epoch,
            step__isnull=True,
        ).first()
        if existing:
            await TrainingMetric.filter(id=existing.id).update(metric_value=value)
        else:
            await TrainingMetric.create(
                job_id=job_id,
                metric_name=name,
                metric_value=value,
                epoch=epoch,
            )

    async def _persist_parsed_log_lines(
        self,
        job: TrainingJob,
        parsed_lines: list[ParsedTrainingLine],
        remote_offset: int,
    ) -> None:
        latest = await TrainingLog.filter(job_id=job.id).order_by("-sequence").first()
        sequence = latest.sequence if latest else 0
        pending_logs = []
        latest_live_progress = None
        has_final_metrics = False
        progress_updates: dict[str, Any] = {
            "progress_percent": float(job.progress_percent or 0),
            "current_epoch": int(job.current_epoch or 0),
        }

        for parsed in parsed_lines:
            if parsed.progress_percent is not None:
                progress_updates["progress_percent"] = min(
                    100.0,
                    max(
                        float(progress_updates["progress_percent"]),
                        parsed.progress_percent,
                    ),
                )
            if parsed.current_epoch is not None:
                progress_updates["current_epoch"] = max(
                    int(progress_updates["current_epoch"]),
                    parsed.current_epoch,
                )
            if parsed.total_epochs is not None:
                progress_updates["total_epochs"] = parsed.total_epochs
            for name, value, epoch in parsed.metrics:
                await self._upsert_metric(job.id, name, value, epoch)
                if "/" not in name:
                    has_final_metrics = True

            if parsed.persist:
                sequence += 1
                pending_logs.append(TrainingLog(
                    job_id=job.id,
                    sequence=sequence,
                    stream=parsed.stream,
                    content=parsed.content,
                    remote_offset=remote_offset,
                ))
            elif parsed.stream == "PROGRESS" and parsed.content.startswith("epoch:"):
                latest_live_progress = parsed

        if latest_live_progress is not None and not has_final_metrics:
            sequence += 1
            pending_logs.append(TrainingLog(
                job_id=job.id,
                sequence=sequence,
                stream="PROGRESS",
                content=latest_live_progress.content,
                remote_offset=remote_offset,
            ))
        if pending_logs:
            await TrainingLog.bulk_create(pending_logs)

        await TrainingJob.filter(id=job.id).update(
            log_offset=remote_offset,
            **progress_updates,
        )

    async def _sync_remote_log_with_sftp(
        self,
        job: TrainingJob,
        sftp,
        adapter: AlgorithmAdapter,
    ) -> None:
        if not job.remote_run_dir:
            return
        raw_log_path = posixpath.join(job.remote_run_dir, "raw.log")
        offset = int(job.log_offset or 0)
        parsed_lines: list[ParsedTrainingLine] = []
        max_chunks = 8
        for _ in range(max_chunks):
            try:
                async with sftp.open(raw_log_path, "rb") as remote_file:
                    await remote_file.seek(offset)
                    raw = await remote_file.read(512 * 1024)
            except (FileNotFoundError, asyncssh.SFTPNoSuchFile):
                return
            if not raw:
                break
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            last_delimiter = max(raw.rfind(b"\n"), raw.rfind(b"\r"))
            if last_delimiter < 0:
                break
            consumed = raw[:last_delimiter + 1]
            next_offset = offset + len(consumed)
            for fragment in re.split(rb"[\r\n]+", consumed):
                if not fragment:
                    continue
                parsed = adapter.parse_log_line(
                    fragment.decode("utf-8", errors="replace")
                )
                if parsed is not None:
                    parsed_lines.append(parsed)
            offset = next_offset
            if len(raw) < 512 * 1024:
                break
        if offset > int(job.log_offset or 0):
            await self._persist_parsed_log_lines(job, parsed_lines, offset)

    async def sync_job_output(self, job: TrainingJob) -> TrainingJob:
        """按持久化字节游标增量同步远程日志、进度和过程指标。"""
        if not job.remote_run_dir:
            return job
        lock = self._log_sync_locks.setdefault(job.id, asyncio.Lock())
        async with lock:
            job = await TrainingJob.get(id=job.id)
            adapter = await self._adapter_for_job(job)
            connection = await self._connect()
            try:
                sftp = await connection.start_sftp_client()
                await self._sync_remote_log_with_sftp(job, sftp, adapter)
            finally:
                connection.close()
                await connection.wait_closed()
            return await TrainingJob.get(id=job.id)

    async def _collect_terminal_data(
        self,
        job: TrainingJob,
        sftp,
        manifest: dict[str, Any],
    ) -> None:
        adapter = await self._adapter_for_job(job)
        text_artifacts: dict[str, str] = {}
        for relative in adapter.metric_artifact_paths():
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts:
                raise TrainingExecutorError("适配器声明了不安全的指标产物路径")
            value = await self._read_text(
                sftp,
                posixpath.join(job.remote_run_dir, relative),
            )
            if value is not None:
                text_artifacts[relative] = value
        final_metrics = adapter.extract_final_metrics(text_artifacts)
        if final_metrics:
            metric_names = {name for name, _, _ in final_metrics}
            await TrainingMetric.filter(
                job_id=job.id,
                metric_name__in=metric_names,
            ).delete()
            await TrainingMetric.bulk_create([
                TrainingMetric(
                    job_id=job.id,
                    metric_name=name,
                    metric_value=value,
                    epoch=epoch,
                )
                for name, value, epoch in final_metrics
            ])

        artifact_items = {
            str(item.get("path", "")): {
                "path": str(item.get("path", "")),
                "size_bytes": int(item.get("size_bytes") or 0),
            }
            for item in manifest.get("artifacts", [])
            if item.get("path")
        }
        for relative in ("raw.log", "config.json", "command.json", "manifest.json"):
            remote_path = posixpath.join(job.remote_run_dir, relative)
            try:
                attrs = await sftp.stat(remote_path)
            except (FileNotFoundError, asyncssh.SFTPNoSuchFile):
                continue
            if stat.S_ISREG(attrs.permissions):
                artifact_items[relative] = {
                    "path": relative,
                    "size_bytes": int(attrs.size or 0),
                }

        await TrainingArtifact.filter(job_id=job.id).delete()
        for item in artifact_items.values():
            relative = str(item.get("path", ""))
            if (
                not relative
                or PurePosixPath(relative).is_absolute()
                or ".." in PurePosixPath(relative).parts
            ):
                continue
            artifact_type, artifact_role, downloadable = (
                adapter.describe_artifact(relative)
            )
            await TrainingArtifact.create(
                job_id=job.id,
                artifact_type=artifact_type,
                artifact_role=artifact_role,
                name=posixpath.basename(relative),
                remote_path=posixpath.join(job.remote_run_dir, relative),
                size_bytes=int(item.get("size_bytes") or 0),
                downloadable=downloadable,
            )

    async def ensure_artifact_catalog(self, job: TrainingJob) -> None:
        if (
            job.cleanup_status == "CLEANED"
            or not job.remote_run_dir
            or await TrainingArtifact.filter(
                job_id=job.id,
                artifact_role="TRAIN_LOG",
            ).exists()
        ):
            return
        connection = await self._connect()
        try:
            sftp = await connection.start_sftp_client()
            manifest_text = await self._read_text(
                sftp,
                posixpath.join(job.remote_run_dir, "manifest.json"),
            )
            if manifest_text:
                await self._collect_terminal_data(
                    job,
                    sftp,
                    json.loads(manifest_text),
                )
        finally:
            connection.close()
            await connection.wait_closed()

    async def _recent_log_text(self, job_id: int) -> str:
        logs = await TrainingLog.filter(job_id=job_id).order_by("-id").limit(300)
        return "\n".join(item.content for item in reversed(logs))

    async def _database_runtime_seconds(self, job_id: int) -> float:
        database = connections.get("default")
        rows = await database.execute_query_dict(
            # Tortoise use_tz=True 将 DATETIME 按 UTC 写入 MySQL；必须使用
            # UTC_TIMESTAMP 比较，不能混用数据库服务器的 Asia/Shanghai NOW。
            "SELECT TIMESTAMPDIFF(SECOND, started_at, UTC_TIMESTAMP(6)) AS age "
            "FROM training_jobs WHERE id=%s AND started_at IS NOT NULL",
            [job_id],
        )
        return float(rows[0]["age"] or 0) if rows else 0

    async def artifact_size(
        self,
        job: TrainingJob,
        artifact: TrainingArtifact,
    ) -> int:
        if (
            job.cleanup_status == "CLEANED"
            or not artifact.downloadable
            or not job.remote_run_dir
            or not safe_artifact_path(job.remote_run_dir, artifact.remote_path)
        ):
            raise TrainingExecutorError("产物不可下载或路径不安全")
        connection = await self._connect()
        try:
            sftp = await connection.start_sftp_client()
            attrs = await sftp.stat(artifact.remote_path)
            if not stat.S_ISREG(attrs.permissions):
                raise TrainingExecutorError("产物不是普通文件")
            return int(attrs.size or 0)
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile) as exc:
            raise TrainingExecutorError("远程产物已不存在") from exc
        finally:
            connection.close()
            await connection.wait_closed()

    async def stream_artifact(self, artifact: TrainingArtifact):
        connection = await self._connect()
        try:
            sftp = await connection.start_sftp_client()
            async with sftp.open(artifact.remote_path, "rb") as remote_file:
                while True:
                    chunk = await remote_file.read(1024 * 1024)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    yield chunk
        finally:
            connection.close()
            await connection.wait_closed()

    async def cleanup_job_artifacts(
        self,
        job: TrainingJob,
        actor: dict[str, Any],
    ) -> TrainingJob:
        if job.status not in TERMINAL_STATUSES:
            raise TrainingExecutorError("只能清理已结束任务")
        if await InferenceJob.filter(
            training_job_id=job.id,
            status__in={"QUEUED", "STARTING", "RUNNING"},
        ).exists():
            raise TrainingExecutorError("该训练任务仍有活动推理任务，暂不能清理产物")
        if job.cleanup_status == "CLEANED":
            return job
        output_root = _absolute_path(self.config["output_root"], "输出目录")
        algorithm = await Algorithm.get_or_none(id=job.algorithm_id)
        dataset = await Dataset.get_or_none(id=job.dataset_id)
        allowed_runs = {posixpath.join(output_root, job.job_no)}
        if algorithm is not None and dataset is not None:
            allowed_runs.add(
                posixpath.join(
                    _isolated_output_root(
                        output_root,
                        algorithm_id=algorithm.id,
                        algorithm_name=algorithm.abbreviation or algorithm.name,
                        dataset_id=dataset.id,
                        dataset_name=dataset.name,
                    ),
                    job.job_no,
                )
            )
        actual_run = posixpath.normpath(job.remote_run_dir or "")
        expected_run = next(
            (
                candidate
                for candidate in allowed_runs
                if posixpath.normpath(candidate) == actual_run
            ),
            None,
        )
        expected_control = posixpath.join(
            _absolute_path(self.config["control_root"], "控制目录"),
            job.job_no,
        )
        if (
            expected_run is None
            or posixpath.normpath(job.remote_control_dir or "") != expected_control
        ):
            raise TrainingExecutorError("任务目录不符合受控清理边界")
        connection = await self._connect()
        try:
            command = (
                f"/bin/rm -rf -- {shlex.quote(expected_run)} "
                f"{shlex.quote(expected_control)}"
            )
            await self._run(connection, command)
        finally:
            connection.close()
            await connection.wait_closed()
        await TrainingArtifact.filter(job_id=job.id).update(downloadable=False)
        await TrainingJob.filter(id=job.id).update(
            cleanup_status="CLEANED",
            cleaned_at=_utc_now(),
        )
        await self._event(job.id, "ARTIFACTS_CLEANED", "远程训练目录已清理")
        await self.audit(
            job.id,
            "ARTIFACT_CLEANUP",
            actor,
            "管理员清理远程训练目录",
        )
        return await TrainingJob.get(id=job.id)

    async def archive_job(
        self,
        job: TrainingJob,
        actor: dict[str, Any],
    ) -> TrainingJob:
        if job.status not in TERMINAL_STATUSES:
            raise TrainingExecutorError("只能归档已结束任务")
        if job.archived_at is not None:
            return job
        archived_at = _utc_now()
        await TrainingJob.filter(id=job.id, archived_at__isnull=True).update(
            archived_at=archived_at,
            archived_by=actor["user_id"],
        )
        await self.audit(
            job.id,
            "JOB_ARCHIVE",
            actor,
            "管理员归档训练任务",
        )
        return await TrainingJob.get(id=job.id)

    async def restore_archived_job(
        self,
        job: TrainingJob,
        actor: dict[str, Any],
    ) -> TrainingJob:
        if job.archived_at is None:
            return job
        await self.audit(
            job.id,
            "JOB_RESTORE",
            actor,
            "管理员恢复已归档训练任务",
        )
        await TrainingJob.filter(id=job.id).update(
            archived_at=None,
            archived_by=None,
        )
        return await TrainingJob.get(id=job.id)

    async def _remote_deletion_state(
        self,
        job: TrainingJob,
    ) -> tuple[bool, bool]:
        """确认远程目录和包含任务 UUID 的进程均已释放。"""
        connection = await self._connect()
        try:
            sftp = await connection.start_sftp_client()
            remote_paths_exist = False
            for path in (job.remote_run_dir, job.remote_control_dir):
                if not path:
                    continue
                try:
                    await sftp.stat(path)
                    remote_paths_exist = True
                except (FileNotFoundError, asyncssh.SFTPNoSuchFile):
                    pass
            result = await self._run(
                connection,
                "/bin/ps -eo pid=,pgid=,user=,args=",
            )
            remote_process_exists = any(
                job.job_no in line
                for line in result.stdout.splitlines()
            )
            return remote_paths_exist, remote_process_exists
        finally:
            connection.close()
            await connection.wait_closed()

    async def hard_delete_job(
        self,
        job: TrainingJob,
        actor: dict[str, Any],
        confirmation: str,
        reason: str,
    ) -> None:
        if confirmation != job.job_no:
            raise TrainingExecutorError("确认任务编号不匹配")
        reason = reason.strip()
        if len(reason) < 3:
            raise TrainingExecutorError("请填写至少 3 个字符的删除原因")

        has_retry_children = await TrainingJob.filter(
            retry_of_job_id=job.id,
        ).exists()
        if await InferenceJob.filter(training_job_id=job.id).exists():
            raise TrainingExecutorError("仍有推理任务引用当前训练任务")
        database_blockers = hard_delete_blockers(
            status=job.status,
            archived=job.archived_at is not None,
            cleanup_status=job.cleanup_status,
            has_retry_children=has_retry_children,
            remote_paths_exist=False,
            remote_process_exists=False,
        )
        if database_blockers:
            raise TrainingExecutorError("；".join(database_blockers))

        remote_paths_exist, remote_process_exists = (
            await self._remote_deletion_state(job)
        )
        remote_blockers = hard_delete_blockers(
            status=job.status,
            archived=True,
            cleanup_status=job.cleanup_status,
            has_retry_children=False,
            remote_paths_exist=remote_paths_exist,
            remote_process_exists=remote_process_exists,
        )
        if remote_blockers:
            raise TrainingExecutorError("；".join(remote_blockers))

        async with in_transaction() as connection:
            locked = await TrainingJob.filter(id=job.id).using_db(
                connection
            ).select_for_update().first()
            if locked is None:
                raise TrainingExecutorError("训练任务已不存在")
            if await TrainingJob.filter(
                retry_of_job_id=job.id,
            ).using_db(connection).exists():
                raise TrainingExecutorError("仍有重试任务引用当前任务")
            if await InferenceJob.filter(
                training_job_id=job.id,
            ).using_db(connection).exists():
                raise TrainingExecutorError("仍有推理任务引用当前训练任务")
            locked_blockers = hard_delete_blockers(
                status=locked.status,
                archived=locked.archived_at is not None,
                cleanup_status=locked.cleanup_status,
                has_retry_children=False,
                remote_paths_exist=False,
                remote_process_exists=False,
            )
            if locked_blockers:
                raise TrainingExecutorError("；".join(locked_blockers))
            await TrainingAudit.create(
                job_id=locked.id,
                actor_id=actor["user_id"],
                actor_role=actor["role"],
                action="JOB_HARD_DELETE",
                result="SUCCESS",
                message=f"管理员彻底删除训练任务：{reason}",
                using_db=connection,
            )
            audits = await TrainingAudit.filter(
                job_id=locked.id,
            ).using_db(connection).order_by("id")
            snapshot = {
                "job": {
                    "id": locked.id,
                    "job_no": locked.job_no,
                    "owner_id": locked.owner_id,
                    "owner_role": locked.owner_role,
                    "algorithm_id": locked.algorithm_id,
                    "dataset_id": locked.dataset_id,
                    "status": locked.status,
                    "failure_code": locked.failure_code,
                    "cleanup_status": locked.cleanup_status,
                    "submitted_at": (
                        locked.submitted_at.isoformat()
                        if locked.submitted_at else None
                    ),
                    "finished_at": (
                        locked.finished_at.isoformat()
                        if locked.finished_at else None
                    ),
                    "archived_at": (
                        locked.archived_at.isoformat()
                        if locked.archived_at else None
                    ),
                },
                "audits": [
                    {
                        "actor_id": audit.actor_id,
                        "actor_role": audit.actor_role,
                        "action": audit.action,
                        "result": audit.result,
                        "message": audit.message,
                        "payload": audit.payload_json,
                        "created_at": (
                            audit.created_at.isoformat()
                            if audit.created_at else None
                        ),
                    }
                    for audit in audits
                ],
            }
            await TrainingJobDeletion.create(
                original_job_id=locked.id,
                job_no=locked.job_no,
                owner_id=locked.owner_id,
                owner_role=locked.owner_role,
                algorithm_id=locked.algorithm_id,
                dataset_id=locked.dataset_id,
                terminal_status=locked.status,
                actor_id=actor["user_id"],
                actor_role=actor["role"],
                reason=reason,
                snapshot_json=snapshot,
                using_db=connection,
            )
            await locked.delete(using_db=connection)

    async def reconcile_job(self, job: TrainingJob) -> TrainingJob:
        if job.status in TERMINAL_STATUSES or job.status == "QUEUED":
            return job
        async with self._reconcile_lock:
            job = await TrainingJob.get(id=job.id)
            if job.status in TERMINAL_STATUSES or job.status == "QUEUED":
                return job
            if job.status == "STARTING" and not job.launcher_pid:
                database = connections.get("default")
                rows = await database.execute_query_dict(
                    "SELECT TIMESTAMPDIFF("
                    "SECOND, updated_at, UTC_TIMESTAMP(6)"
                    ") AS age "
                    "FROM training_jobs WHERE id=%s",
                    [job.id],
                )
                age = float(rows[0]["age"] or 0) if rows else 0
                startup_grace = max(60.0, self.config["command_timeout"] * 3)
                if age <= startup_grace:
                    await TrainingJob.filter(id=job.id).update(
                        last_reconciled_at=_utc_now(),
                    )
                    return await TrainingJob.get(id=job.id)
                await TrainingJob.filter(id=job.id, status="STARTING").update(
                    status="LOST",
                    failure_code="EXECUTOR_LOST",
                    failure_reason="远程启动超时且未记录 launcher PID",
                    finished_at=_utc_now(),
                    last_reconciled_at=_utc_now(),
                )
                await self._event(job.id, "PROCESS_LOST", "远程训练启动超时")
                await self.audit(
                    job.id,
                    "PROCESS_LOST",
                    message="远程启动超时且未记录 launcher PID",
                    result="FAILED",
                )
                return await TrainingJob.get(id=job.id)
            if (
                job.status == "RUNNING"
                and job.timeout_seconds
                and await self._database_runtime_seconds(job.id) > job.timeout_seconds
            ):
                await self.stop_job(
                    job.id,
                    failure_code="TIMEOUT",
                    failure_reason="训练超过管理员设置的最长运行时间，已自动停止",
                )
                await self.audit(
                    job.id,
                    "TIMEOUT",
                    message="训练运行超时，执行器已请求停止",
                    payload={"timeout_seconds": job.timeout_seconds},
                    result="FAILED",
                )
                return await TrainingJob.get(id=job.id)
            connection = await self._connect()
            try:
                sftp = await connection.start_sftp_client()
                log_lock = self._log_sync_locks.setdefault(job.id, asyncio.Lock())
                async with log_lock:
                    job = await TrainingJob.get(id=job.id)
                    adapter = await self._adapter_for_job(job)
                    await self._sync_remote_log_with_sftp(job, sftp, adapter)
                job = await TrainingJob.get(id=job.id)
                manifest_text = await self._read_text(
                    sftp,
                    posixpath.join(job.remote_run_dir or "", "manifest.json"),
                )
                runtime_text = await self._read_text(
                    sftp,
                    posixpath.join(job.remote_run_dir or "", "runtime.json"),
                )
                runtime = json.loads(runtime_text) if runtime_text else {}
                runtime_updates = {
                    key: int(runtime[key])
                    for key in ("worker_pid", "process_pid", "process_pgid")
                    if runtime.get(key) is not None
                }
                if manifest_text:
                    manifest = json.loads(manifest_text)
                    remote_status = manifest.get("status")
                    if remote_status in {"SUCCEEDED", "FAILED"}:
                        status = (
                            "STOPPED"
                            if job.status == "STOPPING"
                            else remote_status
                        )
                        await self._collect_terminal_data(job, sftp, manifest)
                        failure_code = job.failure_code
                        failure_reason = job.failure_reason
                        if status == "SUCCEEDED":
                            failure_code = None
                            failure_reason = None
                        elif status == "FAILED":
                            failure_code, failure_reason = classify_failure(
                                manifest.get("exit_code"),
                                await self._recent_log_text(job.id),
                                job.failure_code,
                            )
                            if failure_code is None:
                                failure_code = "UNKNOWN_FAILURE"
                                failure_reason = "训练失败，但日志中没有匹配到已知错误类型"
                        await TrainingJob.filter(id=job.id).update(
                            status=status,
                            exit_code=manifest.get("exit_code"),
                            failure_code=failure_code,
                            failure_reason=failure_reason,
                            reconcile_failures=0,
                            progress_percent=(
                                100 if status == "SUCCEEDED" else job.progress_percent
                            ),
                            finished_at=_utc_now(),
                            last_reconciled_at=_utc_now(),
                            **runtime_updates,
                        )
                        await self._event(
                            job.id,
                            "PROCESS_FINISHED",
                            f"远程训练结束：{status}",
                            {"exit_code": manifest.get("exit_code")},
                        )
                        await self.audit(
                            job.id,
                            "PROCESS_FINISH",
                            message=f"远程训练结束：{status}",
                            payload={
                                "exit_code": manifest.get("exit_code"),
                                "failure_code": failure_code,
                            },
                            result="SUCCESS" if status == "SUCCEEDED" else "FAILED",
                        )
                        return await TrainingJob.get(id=job.id)

                alive = False
                if job.launcher_pid:
                    result = await self._run(
                        connection,
                        f"kill -0 {int(job.launcher_pid)}",
                        check=False,
                    )
                    alive = result.exit_status == 0
                if not alive and not manifest_text:
                    await TrainingJob.filter(id=job.id).update(
                        status="LOST",
                        failure_code="EXECUTOR_LOST",
                        failure_reason="远程进程已消失且未生成最终 manifest",
                        finished_at=_utc_now(),
                        last_reconciled_at=_utc_now(),
                        **runtime_updates,
                    )
                    await self._event(job.id, "PROCESS_LOST", "远程训练进程不可追踪")
                    await self.audit(
                        job.id,
                        "PROCESS_LOST",
                        message="远程进程已消失且未生成最终 manifest",
                        result="FAILED",
                    )
                else:
                    await TrainingJob.filter(id=job.id).update(
                        status="RUNNING" if job.status != "STOPPING" else "STOPPING",
                        last_reconciled_at=_utc_now(),
                        reconcile_failures=0,
                        **runtime_updates,
                    )
            finally:
                connection.close()
                await connection.wait_closed()
            return await TrainingJob.get(id=job.id)

    async def stop_job(
        self,
        job_id: int,
        failure_code: str = "USER_STOPPED",
        failure_reason: str = "用户主动停止训练",
    ) -> TrainingJob:
        job = await TrainingJob.get_or_none(id=job_id)
        if job is None:
            raise TrainingExecutorError("训练任务不存在")
        if job.status in TERMINAL_STATUSES:
            return job
        await TrainingJob.filter(id=job.id).update(
            status="STOPPING",
            failure_code=failure_code,
            failure_reason=failure_reason,
        )
        await self._event(job.id, "STOP_REQUESTED", "已请求停止远程训练")
        connection = await self._connect()
        try:
            sftp = await connection.start_sftp_client()
            runtime_text = await self._read_text(
                sftp,
                posixpath.join(job.remote_run_dir or "", "runtime.json"),
            )
            runtime = json.loads(runtime_text) if runtime_text else {}
            process_pgid = runtime.get("process_pgid") or job.process_pgid
            worker_pid = runtime.get("worker_pid") or job.worker_pid or job.launcher_pid
            if process_pgid and int(process_pgid) > 1:
                await self._run(
                    connection,
                    f"kill -TERM -- -{int(process_pgid)}",
                    check=False,
                )
            if worker_pid and int(worker_pid) > 1:
                await self._run(
                    connection,
                    f"kill -TERM {int(worker_pid)}",
                    check=False,
                )
        finally:
            connection.close()
            await connection.wait_closed()
        return await TrainingJob.get(id=job.id)

    async def recover_active_jobs(self) -> None:
        if not self.enabled:
            logger.info("Training executor is disabled")
            return
        jobs = await TrainingJob.filter(status__in=REMOTE_ACTIVE_STATUSES)
        for job in jobs:
            try:
                await self.reconcile_job(job)
            except TrainingExecutorError as exc:
                failures = int(job.reconcile_failures or 0) + 1
                updates: dict[str, Any] = {"reconcile_failures": failures}
                if failures >= 3:
                    updates.update({
                        "status": "LOST",
                        "failure_code": "EXECUTOR_LOST",
                        "failure_reason": "连续 3 次无法连接训练执行器",
                        "finished_at": _utc_now(),
                    })
                await TrainingJob.filter(id=job.id).update(**updates)
                if failures >= 3:
                    await self._event(
                        job.id,
                        "EXECUTOR_UNREACHABLE",
                        "连续 3 次无法连接训练执行器，任务标记为失联",
                    )
                    await self.audit(
                        job.id,
                        "PROCESS_LOST",
                        message=str(exc),
                        payload={"consecutive_failures": failures},
                        result="FAILED",
                    )
                logger.warning(
                    "Training executor unavailable for %s (%s/3): %s",
                    job.job_no,
                    failures,
                    exc,
                )
            except Exception:
                logger.exception("Training job recovery failed: %s", job.job_no)

    async def dispatch_queued_jobs(self) -> None:
        if not self.enabled:
            return
        async with self._dispatch_lock:
            active_count = await TrainingJob.filter(
                status__in=REMOTE_ACTIVE_STATUSES,
            ).count()
            available_slots = max(
                0,
                int(self.config["max_concurrent_jobs"]) - active_count,
            )
            if available_slots == 0:
                return
            jobs = await TrainingJob.filter(status="QUEUED").order_by(
                "submitted_at",
                "id",
            ).limit(available_slots)
            for job in jobs:
                try:
                    await self.dispatch_job(job.id)
                except NoGpuAvailableError:
                    # 保持队列状态，下一轮根据显存和系统租约重试。
                    continue
                except Exception:
                    logger.exception("Queued training job launch failed: %s", job.job_no)

    async def _monitor(self) -> None:
        while not self._stop_event.is_set():
            await self.recover_active_jobs()
            await self.dispatch_queued_jobs()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config["monitor_interval"],
                )
            except asyncio.TimeoutError:
                pass

    async def start_monitor(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._stop_event.clear()
            await self.recover_active_jobs()
            await self.dispatch_queued_jobs()
            self._monitor_task = asyncio.create_task(
                self._monitor(),
                name="training-job-monitor",
            )

    async def stop_monitor(self) -> None:
        self._stop_event.set()
        if self._monitor_task is not None:
            await self._monitor_task
            self._monitor_task = None


training_executor_service = TrainingExecutorService()
