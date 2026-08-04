"""由成功训练任务和算法适配器驱动的推理执行器。"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import posixpath
import shlex
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from tortoise import connections

from models import InferenceJob, TrainingArtifact, TrainingJob
from services.algorithm_adapters import AlgorithmAdapterError, algorithm_adapter_registry
from services.training_executor_service import (
    ACTIVE_STATUSES,
    TrainingExecutorError,
    _absolute_path,
    _isolated_output_root,
    training_executor_service,
)
from settings import INFERENCE_EXECUTOR_CONFIG


INFERENCE_ACTIVE = {"QUEUED", "STARTING", "RUNNING"}
INFERENCE_TERMINAL = {"SUCCEEDED", "FAILED", "STOPPED", "LOST"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InferenceExecutorError(RuntimeError):
    pass


class InferenceExecutorService:
    def __init__(self) -> None:
        self.config = INFERENCE_EXECUTOR_CONFIG
        self._monitor_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._dispatch_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return training_executor_service.enabled

    async def _resolve_source(
        self,
        source: TrainingJob,
        parameters: dict[str, Any],
    ) -> tuple[Any, Any, dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
        if source.status != "SUCCEEDED":
            raise InferenceExecutorError("只有训练成功的任务可以用于推理")
        if source.cleanup_status != "RETAINED" or not source.remote_run_dir:
            raise InferenceExecutorError("训练产物已清理或运行目录不可用")
        try:
            await training_executor_service.ensure_artifact_catalog(source)
        except TrainingExecutorError as exc:
            raise InferenceExecutorError("无法校验训练 checkpoint") from exc
        if not await TrainingArtifact.filter(
            job_id=source.id,
            artifact_role="BEST_CHECKPOINT",
            downloadable=True,
        ).exists():
            raise InferenceExecutorError("训练任务缺少可用的最佳 checkpoint")
        algorithm, dataset, runtime, dataset_runtime, adapter = (
            await training_executor_service._resolve_whitelisted_runtime(
                source.algorithm_id,
                source.dataset_id,
            )
        )
        if algorithm_adapter_registry.get(adapter.key) is None:
            raise InferenceExecutorError("算法推理适配器不可用")
        training_parameters = (source.config_json or {}).get("parameters") or {}
        try:
            normalized = adapter.validate_inference_parameters(
                parameters,
                training_parameters,
            )
        except AlgorithmAdapterError as exc:
            raise InferenceExecutorError(str(exc)) from exc
        return algorithm, dataset, runtime, dataset_runtime, adapter, normalized

    async def submit_job(
        self,
        owner: dict[str, Any],
        training_job_id: int,
        parameters: dict[str, Any],
        requested_gpu: int | None,
    ) -> InferenceJob:
        source = await TrainingJob.get_or_none(id=training_job_id)
        if source is None:
            raise InferenceExecutorError("训练任务不存在")
        if owner["role"] != "管理员" and (
            source.owner_id != owner["user_id"] or source.owner_role != owner["role"]
        ):
            raise InferenceExecutorError("训练任务不存在")
        _, _, _, _, adapter, normalized = await self._resolve_source(source, parameters)
        if requested_gpu is not None and requested_gpu not in self.config["gpu_allowlist"]:
            raise InferenceExecutorError("请求的 GPU 不在管理员白名单中")
        pending = await InferenceJob.filter(
            owner_id=owner["user_id"],
            owner_role=owner["role"],
            status__in=INFERENCE_ACTIVE,
        ).count()
        if pending >= self.config["max_pending_jobs_per_user"]:
            raise InferenceExecutorError("当前用户的活动推理任务已达上限")
        return await InferenceJob.create(
            job_no=str(uuid.uuid4()),
            owner_id=owner["user_id"],
            owner_role=owner["role"],
            training_job_id=source.id,
            status="QUEUED",
            config_json={
                "parameters": normalized,
                "requested_gpu": requested_gpu,
                "adapter": {"key": adapter.key, "protocol_version": adapter.protocol_version},
            },
        )

    async def _available_gpu(self, requested: int | None) -> int | None:
        free = await training_executor_service._gpu_free_memory()
        training_leases = await TrainingJob.filter(
            status__in=ACTIVE_STATUSES,
            assigned_gpu__isnull=False,
        ).values_list("assigned_gpu", flat=True)
        inference_leases = await InferenceJob.filter(
            status__in=INFERENCE_ACTIVE,
            assigned_gpu__isnull=False,
        ).values_list("assigned_gpu", flat=True)
        leased = set(training_leases) | set(inference_leases)
        candidates = [requested] if requested is not None else sorted(
            self.config["gpu_allowlist"], key=lambda item: free.get(item, -1), reverse=True
        )
        minimum = int(self.config["min_free_gpu_memory_mb"])
        return next(
            (gpu for gpu in candidates if gpu not in leased and free.get(gpu, 0) >= minimum),
            None,
        )

    async def dispatch_job(self, job_id: int) -> InferenceJob:
        async with self._dispatch_lock:
            job = await InferenceJob.get_or_none(id=job_id)
            if job is None:
                raise InferenceExecutorError("推理任务不存在")
            if job.status != "QUEUED":
                return job
            source = await TrainingJob.get(id=job.training_job_id)
            config = job.config_json or {}
            resolved = await self._resolve_source(
                source,
                config.get("parameters") or {},
            )
            algorithm, dataset, runtime, dataset_runtime, adapter, normalized = resolved
            gpu = await self._available_gpu(config.get("requested_gpu"))
            if gpu is None:
                return job
            updated = await InferenceJob.filter(id=job.id, status="QUEUED").update(
                status="STARTING", assigned_gpu=gpu
            )
            if not updated:
                return await InferenceJob.get(id=job.id)
            control_root = _absolute_path(self.config["control_root"], "推理控制目录")
            output_root = _isolated_output_root(
                _absolute_path(self.config["output_root"], "推理输出目录"),
                algorithm_id=algorithm.id,
                algorithm_name=algorithm.abbreviation or algorithm.name,
                dataset_id=dataset.id,
                dataset_name=dataset.name,
            )
            control_dir = posixpath.join(control_root, job.job_no)
            run_dir = posixpath.join(output_root, job.job_no)
            config_path = posixpath.join(control_dir, "config.json")
            bootstrap_log = posixpath.join(control_dir, "bootstrap.log")
            try:
                remote_config = adapter.build_inference_config(
                    runtime=runtime,
                    dataset_name=dataset.name,
                    dataset_runtime=dataset_runtime,
                    training_parameters=(source.config_json or {}).get("parameters") or {},
                    inference_parameters=normalized,
                    gpu_index=gpu,
                    source_run_directory=source.remote_run_dir,
                    output_root=output_root,
                )
                runner = _absolute_path(
                    adapter.inference_runner_path(self.config),
                    f"{adapter.key} 推理 runner",
                )
                python_path = posixpath.join(runtime["conda_env_path"], "bin/python")
                connection = await training_executor_service._connect()
                try:
                    sftp = await connection.start_sftp_client()
                    await sftp.makedirs(control_dir, exist_ok=True)
                    async with sftp.open(config_path, "w") as stream:
                        await stream.write(json.dumps(remote_config, ensure_ascii=False, indent=2) + "\n")
                    argv = [python_path, runner, "--config", config_path, "--run", "--run-id", job.job_no]
                    quoted = " ".join(shlex.quote(item) for item in argv)
                    command = (
                        f"/usr/bin/nohup /usr/bin/setsid {quoted} "
                        f"> {shlex.quote(bootstrap_log)} 2>&1 < /dev/null "
                        "& printf '%s' $!"
                    )
                    result = await training_executor_service._run(connection, command)
                    launcher_pid = int(result.stdout.strip())
                finally:
                    connection.close()
                    await connection.wait_closed()
            except Exception as exc:
                await InferenceJob.filter(id=job.id).update(
                    status="FAILED", failure_reason=str(exc), finished_at=_now()
                )
                raise InferenceExecutorError(str(exc)) from exc
            await InferenceJob.filter(id=job.id).update(
                status="RUNNING",
                launcher_pid=launcher_pid,
                remote_control_dir=control_dir,
                remote_run_dir=run_dir,
                started_at=_now(),
                failure_reason=None,
            )
            return await InferenceJob.get(id=job.id)

    async def reconcile_job(self, job: InferenceJob) -> InferenceJob:
        if job.status not in {"STARTING", "RUNNING"} or not job.remote_run_dir:
            return job
        runtime_seconds = 0.0
        if job.started_at:
            # 与训练执行器保持相同的时间源。use_tz=True 下 MySQL DATETIME
            # 不能和应用进程的本地/UTC datetime 混算，否则会产生 8 小时偏差。
            rows = await connections.get("default").execute_query_dict(
                "SELECT TIMESTAMPDIFF(SECOND, started_at, UTC_TIMESTAMP(6)) AS age "
                "FROM inference_jobs WHERE id=%s",
                [job.id],
            )
            runtime_seconds = max(0.0, float(rows[0]["age"] or 0)) if rows else 0.0
        if runtime_seconds > self.config["max_runtime_seconds"]:
            if job.launcher_pid and job.launcher_pid > 1:
                try:
                    connection = await training_executor_service._connect()
                    try:
                        await training_executor_service._run(
                            connection,
                            f"/bin/kill -TERM -- -{int(job.launcher_pid)}",
                            check=False,
                        )
                    finally:
                        connection.close()
                        await connection.wait_closed()
                except TrainingExecutorError:
                    pass
            await InferenceJob.filter(id=job.id).update(
                status="FAILED",
                failure_reason="推理任务运行超时，已请求停止远程进程组",
                finished_at=_now(),
                assigned_gpu=None,
            )
            return await InferenceJob.get(id=job.id)
        connection = await training_executor_service._connect()
        try:
            sftp = await connection.start_sftp_client()
            manifest_path = posixpath.join(job.remote_run_dir, "manifest.json")
            try:
                async with sftp.open(manifest_path, "r") as stream:
                    manifest = json.loads(await stream.read())
            except FileNotFoundError:
                return job
        finally:
            connection.close()
            await connection.wait_closed()
        status = manifest.get("status")
        if status in {"SUCCEEDED", "FAILED"}:
            await InferenceJob.filter(id=job.id).update(
                status=status,
                exit_code=manifest.get("exit_code"),
                result_json=manifest.get("result"),
                failure_reason=None if status == "SUCCEEDED" else "PBAS 推理进程执行失败，请查看日志",
                finished_at=_now(),
                assigned_gpu=None,
            )
        return await InferenceJob.get(id=job.id)

    async def dispatch_queued_jobs(self) -> None:
        running = await InferenceJob.filter(status__in={"STARTING", "RUNNING"}).count()
        capacity = max(0, self.config["max_concurrent_jobs"] - running)
        jobs = await InferenceJob.filter(status="QUEUED").order_by("submitted_at").limit(capacity)
        for job in jobs:
            try:
                dispatched = await self.dispatch_job(job.id)
                if dispatched.status == "QUEUED":
                    break
            except InferenceExecutorError:
                continue

    async def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            for job in await InferenceJob.filter(status__in={"STARTING", "RUNNING"}):
                try:
                    await self.reconcile_job(job)
                except Exception:
                    # 瞬时 SSH 故障留待下一轮恢复，不能误判任务终态。
                    pass
            await self.dispatch_queued_jobs()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.config["monitor_interval"]
                )
            except asyncio.TimeoutError:
                pass

    async def start_monitor(self) -> None:
        if not self.enabled or self._monitor_task is not None:
            return
        self._stop_event.clear()
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop_monitor(self) -> None:
        self._stop_event.set()
        if self._monitor_task:
            await self._monitor_task
            self._monitor_task = None

    async def read_output(self, job: InferenceJob, relative_path: str) -> tuple[bytes, str]:
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or ".." in pure.parts or not job.remote_run_dir:
            raise InferenceExecutorError("推理输出路径无效")
        allowed = {"raw.log"} | set((job.result_json or {}).get("visualizations") or [])
        if relative_path not in allowed:
            raise InferenceExecutorError("该文件不属于可读取的推理输出")
        remote_path = posixpath.join(job.remote_run_dir, *pure.parts)
        connection = await training_executor_service._connect()
        try:
            sftp = await connection.start_sftp_client()
            attrs = await sftp.stat(remote_path)
            if attrs.size is not None and attrs.size > 20 * 1024 * 1024:
                raise InferenceExecutorError("单个推理输出超过 20 MiB，拒绝在线读取")
            async with sftp.open(remote_path, "rb") as stream:
                content = await stream.read()
        except FileNotFoundError as exc:
            raise InferenceExecutorError("推理输出不存在") from exc
        finally:
            connection.close()
            await connection.wait_closed()
        return content, mimetypes.guess_type(relative_path)[0] or "application/octet-stream"


inference_executor_service = InferenceExecutorService()
