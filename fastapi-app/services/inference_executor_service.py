"""由成功训练任务和算法适配器驱动的推理执行器。"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import posixpath
import shlex
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from models import Admin, InferenceJob, TrainingArtifact, TrainingJob, User
from settings import INFERENCE_EXECUTOR_CONFIG
from tortoise.transactions import in_transaction

from services.algorithm_adapters import (
    AlgorithmAdapterError,
    algorithm_adapter_registry,
)
from services.gpu_lease_service import gpu_lease_coordinator
from services.training_executor_service import (
    TrainingExecutorError,
    _absolute_path,
    _isolated_output_root,
    _load_json_object,
    training_executor_service,
)

try:
    import asyncssh
except ImportError:  # pragma: no cover - 与训练执行器一致的依赖保护
    asyncssh = None


logger = logging.getLogger(__name__)

INFERENCE_ACTIVE = {"QUEUED", "STARTING", "RUNNING"}
INFERENCE_TERMINAL = {"SUCCEEDED", "FAILED", "STOPPED", "LOST"}
INFERENCE_MANIFEST_TERMINAL = {"SUCCEEDED", "FAILED"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_seconds(value: datetime | None) -> float:
    """以应用时钟计算时间戳距现在的秒数。

    实测（MySQL 9.5 + 本项目 use_tz=True/timezone=Asia/Shanghai 配置）：
    Tortoise 读回的是带正确时区的 aware datetime，Python 侧差值即为真实
    间隔；而库内 TIMESTAMPDIFF(..., UTC_TIMESTAMP()) 会与本地墙钟存储
    相差 8 小时、被 max(0, ...) 钳为 0，导致超时判定永不触发。数据库
    时间字段的时长计算统一走本函数，不使用 SQL 方言的时间函数。
    """
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (_now() - value).total_seconds())


class InferenceExecutorError(RuntimeError):
    pass


class InferencePermanentError(InferenceExecutorError):
    """排队期间已永久失效（训练产物被清理、适配器移除等）。

    与可重试的基础设施故障（SSH 瞬断等）区分开：永久失效的任务必须
    落到 FAILED 终态，否则会永远留在 QUEUED 每轮重试并占用并发名额。
    """


class InferenceExecutorService:
    def __init__(self) -> None:
        self.config = INFERENCE_EXECUTOR_CONFIG
        self._monitor_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._dispatch_lock = asyncio.Lock()
        self._submit_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return training_executor_service.enabled

    async def _resolve_source(
        self,
        source: TrainingJob,
        parameters: dict[str, Any],
    ) -> tuple[Any, Any, dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
        if source.status != "SUCCEEDED":
            raise InferencePermanentError("只有训练成功的任务可以用于推理")
        config_server_id = str((source.config_json or {}).get("server_id") or source.server_id)
        if config_server_id != source.server_id:
            raise InferencePermanentError("训练任务的服务器快照与任务归属不一致")
        if source.server_id != training_executor_service.server_id:
            raise InferencePermanentError("来源训练任务所属服务器的推理路由尚未启用")
        if source.cleanup_status != "RETAINED" or not source.remote_run_dir:
            raise InferencePermanentError("训练产物已清理或运行目录不可用")
        try:
            await training_executor_service.ensure_artifact_catalog(source)
        except TrainingExecutorError as exc:
            # SSH/远端故障是瞬时的：保持 QUEUED 留待下一轮重试。
            raise InferenceExecutorError("无法校验训练 checkpoint") from exc
        if not await TrainingArtifact.filter(
            job_id=source.id,
            artifact_role="BEST_CHECKPOINT",
            downloadable=True,
        ).exists():
            raise InferencePermanentError("训练任务缺少可用的最佳 checkpoint")
        algorithm, dataset, runtime, dataset_runtime, adapter = (
            await training_executor_service._resolve_whitelisted_runtime(
                source.algorithm_id,
                source.dataset_id,
                source.server_id,
            )
        )
        if algorithm_adapter_registry.get(adapter.key) is None:
            raise InferencePermanentError("算法推理适配器不可用")
        training_parameters = (source.config_json or {}).get("parameters") or {}
        try:
            normalized = adapter.validate_inference_parameters(
                parameters,
                training_parameters,
            )
        except AlgorithmAdapterError as exc:
            raise InferencePermanentError(str(exc)) from exc
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
        owner_model = Admin if owner["role"] == "管理员" else User
        async with self._submit_lock:
            async with in_transaction() as connection:
                principal = await owner_model.filter(id=owner["user_id"]).using_db(
                    connection
                ).select_for_update().first()
                if principal is None:
                    raise InferenceExecutorError("任务所有者不存在或已被删除")
                active_query = InferenceJob.filter(
                    server_id=source.server_id,
                    status__in=INFERENCE_ACTIVE,
                ).using_db(connection)
                total_pending = await active_query.count()
                if total_pending >= self.config["max_pending_jobs_total"]:
                    raise InferenceExecutorError("系统推理队列已满，请稍后再试")
                pending = await active_query.filter(
                    owner_id=owner["user_id"],
                    owner_role=owner["role"],
                ).count()
                if pending >= self.config["max_pending_jobs_per_user"]:
                    raise InferenceExecutorError("当前用户的活动推理任务已达上限")
                return await InferenceJob.create(
                    using_db=connection,
                    job_no=str(uuid.uuid4()),
                    owner_id=owner["user_id"],
                    owner_role=owner["role"],
                    server_id=source.server_id,
                    training_job_id=source.id,
                    status="QUEUED",
                    config_json={
                        "server_id": source.server_id,
                        "parameters": normalized,
                        "requested_gpu": requested_gpu,
                        "adapter": {
                            "key": adapter.key,
                            "protocol_version": adapter.protocol_version,
                        },
                    },
                )

    async def _gpu_candidates(self, requested: int | None) -> list[int]:
        free = await training_executor_service._gpu_free_memory()
        candidates = [requested] if requested is not None else sorted(
            self.config["gpu_allowlist"], key=lambda item: free.get(item, -1), reverse=True
        )
        minimum = int(self.config["min_free_gpu_memory_mb"])
        return [gpu for gpu in candidates if free.get(gpu, 0) >= minimum]

    async def _available_gpu(self, requested: int | None) -> int | None:
        """兼容旧调用；实际调度会把全部合格候选交给数据库租约层。"""
        candidates = await self._gpu_candidates(requested)
        return candidates[0] if candidates else None

    async def dispatch_job(self, job_id: int) -> InferenceJob:
        async with self._dispatch_lock:
            job = await InferenceJob.get_or_none(id=job_id)
            if job is None:
                raise InferenceExecutorError("推理任务不存在")
            if job.status != "QUEUED":
                return job
            source = await TrainingJob.get_or_none(id=job.training_job_id)
            if source is None:
                await self._fail_permanent_dispatch(job, "训练任务已被删除，推理无法执行")
                raise InferencePermanentError("训练任务已被删除，推理无法执行")
            config = job.config_json or {}
            launcher_pid: int | None = None
            lease_acquired = False
            try:
                try:
                    resolved = await self._resolve_source(
                        source,
                        config.get("parameters") or {},
                    )
                except InferencePermanentError as exc:
                    await self._fail_permanent_dispatch(job, str(exc))
                    raise
                algorithm, dataset, runtime, dataset_runtime, adapter, normalized = resolved
                gpu_candidates = await self._gpu_candidates(config.get("requested_gpu"))
                if not gpu_candidates:
                    return job
                gpu = await gpu_lease_coordinator.acquire(
                    workload_type="INFERENCE",
                    workload_id=job.id,
                    candidates=gpu_candidates,
                    server_id=source.server_id,
                )
                if gpu is None:
                    return job
                lease_acquired = True
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
                # 条件更新 + 行数校验：若启动耗时超过宽限期、reconcile 已把
                # 任务收敛为 LOST 并释放租约，这里绝不能把终态覆盖回 RUNNING。
                updated = await InferenceJob.filter(
                    id=job.id,
                    status="STARTING",
                ).update(
                    status="RUNNING",
                    launcher_pid=launcher_pid,
                    remote_control_dir=control_dir,
                    remote_run_dir=run_dir,
                    started_at=_now(),
                    failure_reason=None,
                )
                if not updated:
                    # 远程进程可能已启动：尽力回收，避免孤儿进程永久占卡。
                    await self._terminate_launcher(launcher_pid)
                    raise InferenceExecutorError(
                        "推理任务启动耗时超过宽限期，已被收敛为 LOST"
                    )
                return await InferenceJob.get(id=job.id)
            except asyncio.CancelledError:
                # 请求取消/服务停机：CancelledError 不是 Exception 的子类，
                # 若不在此清理，任务将永久停留在 STARTING 且租约持续占用
                # GPU。即使本清理本身再被取消，reconcile 的启动宽限期
                # 收敛也会在下一轮把残留状态回收。
                if lease_acquired:
                    await self._abandon_launch(job, launcher_pid, "推理任务启动被取消")
                raise
            except InferenceExecutorError:
                raise
            except Exception as exc:
                if lease_acquired:
                    await self._abandon_launch(job, launcher_pid, str(exc))
                raise InferenceExecutorError(str(exc)) from exc

    async def reconcile_job(
        self,
        job: InferenceJob,
        connection=None,
    ) -> InferenceJob:
        if job.status not in {"STARTING", "RUNNING"}:
            return job
        if not job.remote_run_dir:
            # dispatch 在租约 acquire 与 RUNNING 落库之间被取消/崩溃时，
            # 任务停留在 STARTING 且没有远端运行目录：交给启动宽限期收敛，
            # 否则租约会被永久占用（GPU 永久泄漏，重启也不能恢复）。
            return await self._expire_stale_starting(job)
        runtime_seconds = _age_seconds(job.started_at)
        if runtime_seconds > self.config["max_runtime_seconds"]:
            if job.launcher_pid and job.launcher_pid > 1:
                try:
                    await self._terminate_launcher(job.launcher_pid)
                except TrainingExecutorError:
                    pass
            await InferenceJob.filter(id=job.id).update(
                status="FAILED",
                failure_reason="推理任务运行超时，已请求停止远程进程组",
                finished_at=_now(),
                assigned_gpu=None,
            )
            await gpu_lease_coordinator.release("INFERENCE", job.id)
            return await InferenceJob.get(id=job.id)
        owns_connection = connection is None
        if owns_connection:
            connection = await training_executor_service._connect()
        try:
            sftp = await connection.start_sftp_client()
            manifest_path = posixpath.join(job.remote_run_dir, "manifest.json")
            manifest = None
            try:
                async with sftp.open(manifest_path, "r") as stream:
                    manifest = _load_json_object(await stream.read())
            except (FileNotFoundError, asyncssh.SFTPNoSuchFile):
                # 远程 manifest 未生成属于运行中的正常状态，等待下一轮。
                manifest = None
            if (
                not isinstance(manifest, dict)
                or manifest.get("status") not in INFERENCE_MANIFEST_TERMINAL
            ):
                # manifest 缺失或停留在 RUNNING：进程仍在则等待下一轮，
                # 进程已消失且无终态 manifest 时按失联处理（对齐训练执行器）。
                return await self._mark_lost_if_process_gone(job, connection)
            status = manifest.get("status")
            # 适配器键来自任务自身的快照，避免推理层硬编码具体算法名。
            adapter_key = str(
                ((job.config_json or {}).get("adapter") or {}).get("key")
                or "算法"
            )
            await InferenceJob.filter(id=job.id).update(
                status=status,
                exit_code=manifest.get("exit_code"),
                result_json=manifest.get("result"),
                failure_reason=(
                    None
                    if status == "SUCCEEDED"
                    else f"{adapter_key} 推理进程执行失败，请查看日志"
                ),
                finished_at=_now(),
                assigned_gpu=None,
            )
            await gpu_lease_coordinator.release("INFERENCE", job.id)
            return await InferenceJob.get(id=job.id)
        finally:
            if owns_connection:
                connection.close()
                await connection.wait_closed()

    async def _mark_lost_if_process_gone(
        self,
        job: InferenceJob,
        connection,
    ) -> InferenceJob:
        """runner 在写出终态 manifest 前死亡时把任务收敛为 LOST。"""
        if not job.launcher_pid or int(job.launcher_pid) <= 1:
            return job
        result = await training_executor_service._run(
            connection,
            f"/bin/kill -0 {int(job.launcher_pid)}",
            check=False,
        )
        if result.exit_status == 0:
            return job
        await InferenceJob.filter(
            id=job.id,
            status__in={"STARTING", "RUNNING"},
        ).update(
            status="LOST",
            failure_reason="推理进程已消失且未生成最终 manifest",
            finished_at=_now(),
            assigned_gpu=None,
        )
        await gpu_lease_coordinator.release("INFERENCE", job.id)
        return await InferenceJob.get(id=job.id)

    async def _expire_stale_starting(self, job: InferenceJob) -> InferenceJob:
        """把卡在启动阶段（STARTING 且无远端运行目录）的任务超时收敛。

        以进入 STARTING 的时刻（dispatch 显式写入的 started_at，历史
        数据回退 updated_at）为基准；超过启动宽限期仍无运行目录即判
        LOST 并释放租约。条件更新保证不会覆盖已推进到 RUNNING 的任务。
        """
        age = _age_seconds(job.started_at or job.updated_at)
        grace = max(60.0, float(self.config["command_timeout"]) * 3.0)
        if age <= grace:
            return job
        updated = await InferenceJob.filter(
            id=job.id,
            status="STARTING",
            remote_run_dir__isnull=True,
        ).update(
            status="LOST",
            failure_reason="推理任务启动阶段失联（超过启动宽限期），已释放 GPU 租约",
            finished_at=_now(),
            assigned_gpu=None,
        )
        if updated:
            await gpu_lease_coordinator.release("INFERENCE", job.id)
        return await InferenceJob.get(id=job.id)

    @staticmethod
    async def _fail_permanent_dispatch(job: InferenceJob, reason: str) -> None:
        """排队期间已永久失效的任务落到 FAILED 终态（发生在租约获取之前）。"""
        await InferenceJob.filter(id=job.id, status="QUEUED").update(
            status="FAILED",
            failure_reason=reason[:1000],
            finished_at=_now(),
        )

    async def _abandon_launch(
        self,
        job: InferenceJob,
        launcher_pid: int | None,
        reason: str,
    ) -> None:
        """启动失败/被取消后的收敛：回收远程进程、置 FAILED、释放租约。"""
        if launcher_pid is not None and int(launcher_pid) > 1:
            try:
                await self._terminate_launcher(launcher_pid)
            except Exception:
                # 远程回收失败不阻塞本地状态收敛；租约已释放，孤儿进程
                # 由运维侧处理。
                logger.exception("回收推理启动进程失败: %s", job.job_no)
        updated = await InferenceJob.filter(id=job.id, status="STARTING").update(
            status="FAILED",
            assigned_gpu=None,
            failure_reason=reason[:1000],
            finished_at=_now(),
        )
        if updated:
            await gpu_lease_coordinator.release("INFERENCE", job.id)

    async def _terminate_launcher(self, launcher_pid: int | None) -> None:
        """向远程进程组发送 SIGTERM（setsid 后 pgid == launcher_pid）。"""
        pid = int(launcher_pid or 0)
        if pid <= 1:
            return
        connection = await training_executor_service._connect()
        try:
            await training_executor_service._run(
                connection,
                f"/bin/kill -TERM -- -{pid}",
                check=False,
            )
        finally:
            connection.close()
            await connection.wait_closed()

    async def dispatch_queued_jobs(self) -> None:
        running = await InferenceJob.filter(
            server_id=training_executor_service.server_id,
            status__in={"STARTING", "RUNNING"},
        ).count()
        capacity = max(0, self.config["max_concurrent_jobs"] - running)
        jobs = await InferenceJob.filter(
            server_id=training_executor_service.server_id,
            status="QUEUED",
        ).order_by("submitted_at").limit(capacity)
        for job in jobs:
            try:
                dispatched = await self.dispatch_job(job.id)
                if dispatched.status == "QUEUED":
                    break
            except InferenceExecutorError:
                continue

    async def _monitor_once(self) -> None:
        active_jobs = await InferenceJob.filter(
            server_id=training_executor_service.server_id,
            status__in={"STARTING", "RUNNING"},
        )
        if active_jobs:
            # 一轮监控共享一条 SSH 连接，避免逐任务重建会话。
            shared_connection = None
            try:
                shared_connection = await training_executor_service._connect()
            except TrainingExecutorError:
                shared_connection = None  # 回退为逐任务独立连接
            try:
                for job in active_jobs:
                    try:
                        await self.reconcile_job(
                            job, connection=shared_connection
                        )
                    except Exception:
                        # 瞬时 SSH 故障留待下一轮恢复，不能误判任务终态。
                        logger.exception(
                            "Inference job reconcile failed: %s",
                            job.job_no,
                        )
            finally:
                if shared_connection is not None:
                    shared_connection.close()
                    await shared_connection.wait_closed()
        await self.dispatch_queued_jobs()

    async def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._monitor_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 数据库瞬断等基础设施故障不能永久杀死监控协程。此前
                # 任何一个逃逸异常都会让调度与对账永久停摆且无自愈；
                # 本轮失败的收敛工作留给下一轮。
                logger.exception("推理监控本轮执行失败，等待下一轮重试")
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
        await gpu_lease_coordinator.reconcile()
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
        except (FileNotFoundError, asyncssh.SFTPNoSuchFile) as exc:
            raise InferenceExecutorError("推理输出不存在") from exc
        finally:
            connection.close()
            await connection.wait_closed()
        return content, mimetypes.guess_type(relative_path)[0] or "application/octet-stream"


inference_executor_service = InferenceExecutorService()
