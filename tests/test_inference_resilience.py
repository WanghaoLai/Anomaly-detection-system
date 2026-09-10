"""推理侧韧性回归测试（F5 / F4b / F9）。

覆盖三类此前完全缺失的崩溃收敛机制：
- F5：STARTING 卡死任务的启动宽限期收敛、dispatch 被取消时的租约清理；
- F4b：基础设施故障不能永久杀死推理监控协程；
- F9：排队期间训练产物失效的任务落到 FAILED 终态而不是永久 QUEUED。
"""

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import asyncssh

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from tortoise import Tortoise, connections  # noqa: E402

from models import (  # noqa: E402
    Admin,
    Algorithm,
    Dataset,
    GpuLease,
    InferenceJob,
    TrainingJob,
)
from services.inference_executor_service import (  # noqa: E402
    InferenceExecutorError,
    InferenceExecutorService,
    InferencePermanentError,
)
from services.training_executor_service import (  # noqa: E402
    TrainingExecutorError,
    training_executor_service,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _SftpWriter:
    def __init__(self):
        self.chunks = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def write(self, data):
        self.chunks.append(data)


class _LaunchSftp:
    """dispatch 启动阶段所需的最小 SFTP 表面。"""

    def __init__(self, on_makedirs=None):
        self._on_makedirs = on_makedirs

    async def makedirs(self, path, exist_ok=True):
        if self._on_makedirs is not None:
            await self._on_makedirs()

    def open(self, path, mode="r"):
        return _SftpWriter()


class _LaunchConnection:
    def __init__(self, sftp=None, *, on_start_sftp=None):
        self._sftp = sftp
        self._on_start_sftp = on_start_sftp
        self.commands = []
        self.closed = False

    async def start_sftp_client(self):
        if self._on_start_sftp is not None:
            await self._on_start_sftp()
        return self._sftp or _LaunchSftp()

    async def run(self, command, check=True, timeout=None):
        self.commands.append(command)
        return SimpleNamespace(stdout="4242\n", exit_status=0)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.closed = True


def _resolved_tuple():
    """dispatch 所需的 _resolve_source 结果（算法/数据集/适配器替身）。"""
    algorithm = SimpleNamespace(id=1, name="PBAS", abbreviation="PBAS")
    dataset = SimpleNamespace(id=1, name="MVTec AD")
    runtime = {"conda_env_path": "/opt/conda/envs/pbas"}
    adapter = SimpleNamespace(
        key="PBAS",
        build_inference_config=lambda **kwargs: {"gpu": kwargs.get("gpu_index")},
        inference_runner_path=lambda config: "/home/adtrainer/bin/runner.py",
    )
    return algorithm, dataset, runtime, {}, adapter, {"classes": []}


def _connect_returning(connection):
    async def _connect():
        return connection

    return _connect


def _connect_raising(exc):
    async def _connect():
        raise exc

    return _connect


class InferenceResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        await Admin.create(id=1, username="res-admin", password="x", role="管理员")
        await Algorithm.create(id=1, algorithm_no="1", name="PBAS", created_by_id=1)
        await Dataset.create(id=1, dataset_no="1", name="MVTec AD", created_by_id=1)
        await TrainingJob.create(
            id=1,
            job_no="res-train",
            owner_id=1,
            owner_role="用户",
            algorithm_id=1,
            dataset_id=1,
            status="SUCCEEDED",
            cleanup_status="RETAINED",
            remote_run_dir="/runs/res-train",
            config_json={},
        )
        self.service = InferenceExecutorService()

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def _create_inference_job(self, **overrides) -> InferenceJob:
        defaults = dict(
            job_no=overrides.pop("job_no", "res-inf"),
            owner_id=1,
            owner_role="用户",
            training_job_id=1,
            status="QUEUED",
            config_json={"adapter": {"key": "PBAS"}},
        )
        defaults.update(overrides)
        return await InferenceJob.create(**defaults)

    # ------------------------------------------------------------------
    # F9：排队期间训练产物失效 → FAILED 终态
    # ------------------------------------------------------------------

    async def test_dispatch_fails_job_when_source_cleaned(self):
        await TrainingJob.filter(id=1).update(cleanup_status="PURGED")
        job = await self._create_inference_job()

        with self.assertRaises(InferencePermanentError) as ctx:
            await self.service.dispatch_job(job.id)

        self.assertIn("清理", str(ctx.exception))
        await job.refresh_from_db()
        self.assertEqual(job.status, "FAILED")
        self.assertIn("清理", job.failure_reason)
        self.assertIsNotNone(job.finished_at)

    async def test_dispatch_fails_job_when_training_deleted(self):
        # 正常流程下外键 RESTRICT 会阻止删除仍被引用的训练任务；这里
        # 临时关闭 SQLite 外键强制来构造悬挂引用，验证防御路径。
        job = await self._create_inference_job()
        connection = connections.get("default")
        await connection.execute_query("PRAGMA foreign_keys = OFF")
        try:
            await TrainingJob.filter(id=1).delete()
        finally:
            await connection.execute_query("PRAGMA foreign_keys = ON")

        with self.assertRaises(InferencePermanentError):
            await self.service.dispatch_job(job.id)

        await job.refresh_from_db()
        self.assertEqual(job.status, "FAILED")
        self.assertIn("删除", job.failure_reason)

    async def test_transient_ssh_error_keeps_job_queued(self):
        # “无法校验训练 checkpoint” 是瞬时故障：保持 QUEUED 留待下一轮，
        # 不能像永久失效那样落终态。
        job = await self._create_inference_job()
        with mock.patch.object(
            training_executor_service,
            "ensure_artifact_catalog",
            new=self._raise(TrainingExecutorError("ssh down")),
        ):
            with self.assertRaises(InferenceExecutorError) as ctx:
                await self.service.dispatch_job(job.id)

        self.assertNotIsInstance(ctx.exception, InferencePermanentError)
        await job.refresh_from_db()
        self.assertEqual(job.status, "QUEUED")

    @staticmethod
    def _raise(exc):
        async def _impl(*args, **kwargs):
            raise exc

        return _impl

    # ------------------------------------------------------------------
    # F5：STARTING 卡死的启动宽限期收敛
    # ------------------------------------------------------------------

    async def test_stale_starting_expires_to_lost_and_releases_lease(self):
        job = await self._create_inference_job(
            status="STARTING",
            assigned_gpu=0,
            started_at=_now() - timedelta(seconds=120),
        )
        await GpuLease.create(gpu_index=0, workload_type="INFERENCE", workload_id=job.id)

        result = await self.service.reconcile_job(job)

        self.assertEqual(result.status, "LOST")
        self.assertIn("宽限期", result.failure_reason)
        self.assertEqual(await GpuLease.all().count(), 0)

    async def test_fresh_starting_job_within_grace_stays(self):
        job = await self._create_inference_job(
            status="STARTING",
            assigned_gpu=0,
            started_at=_now() - timedelta(seconds=5),
        )
        await GpuLease.create(gpu_index=0, workload_type="INFERENCE", workload_id=job.id)

        result = await self.service.reconcile_job(job)

        self.assertEqual(result.status, "STARTING")
        self.assertEqual(await GpuLease.all().count(), 1)

    async def test_stale_starting_uses_updated_at_fallback_for_legacy_rows(self):
        # 修复上线前已卡死的行没有 started_at：回退 updated_at 作为基准。
        # reconcile 读取的是任务实例上的时间戳，需从库中重载后再触发。
        job = await self._create_inference_job(status="STARTING", assigned_gpu=0)
        await InferenceJob.filter(id=job.id).update(
            updated_at=_now() - timedelta(seconds=120)
        )
        await GpuLease.create(gpu_index=0, workload_type="INFERENCE", workload_id=job.id)
        job = await InferenceJob.get(id=job.id)
        self.assertIsNone(job.started_at)
        self.assertGreater(
            (_now() - job.updated_at).total_seconds(), 60
        )

        result = await self.service.reconcile_job(job)

        self.assertEqual(result.status, "LOST")
        self.assertEqual(await GpuLease.all().count(), 0)

    async def test_cancelled_dispatch_fails_job_and_releases_lease(self):
        # acquire 之后、RUNNING 落库之前请求被取消：CancelledError 不是
        # Exception 的子类，若不在 dispatch 内清理，租约会被永久占用。
        job = await self._create_inference_job()
        service = InferenceExecutorService()
        service._resolve_source = self._async_value(_resolved_tuple())
        service._gpu_candidates = self._async_value([0])
        with mock.patch.object(
            training_executor_service,
            "_connect",
            new=_connect_raising(asyncio.CancelledError()),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await service.dispatch_job(job.id)

        await job.refresh_from_db()
        self.assertEqual(job.status, "FAILED")
        self.assertIn("取消", job.failure_reason)
        self.assertEqual(await GpuLease.all().count(), 0)

    async def test_dispatch_does_not_overwrite_lost_terminal_state(self):
        # 启动期间 reconcile 已把任务收敛为 LOST 并释放租约时，dispatch
        # 的 RUNNING 落库必须条件失败并回收远程进程，而不是覆盖终态。
        job = await self._create_inference_job()
        service = InferenceExecutorService()
        service._resolve_source = self._async_value(_resolved_tuple())
        service._gpu_candidates = self._async_value([0])
        terminated = []

        async def _record_terminate(pid):
            terminated.append(pid)

        service._terminate_launcher = _record_terminate

        async def _flip_to_lost():
            # 模拟并发 reconcile 的宽限期收敛动作。
            await InferenceJob.filter(id=job.id, status="STARTING").update(
                status="LOST", assigned_gpu=None,
            )
            await GpuLease.all().delete()

        connection = _LaunchConnection(on_start_sftp=_flip_to_lost)
        with mock.patch.object(
            training_executor_service, "_connect", new=_connect_returning(connection)
        ):
            with self.assertRaises(InferenceExecutorError) as ctx:
                await service.dispatch_job(job.id)

        self.assertIn("宽限期", str(ctx.exception))
        await job.refresh_from_db()
        self.assertEqual(job.status, "LOST")  # 终态未被覆盖回 RUNNING
        self.assertEqual(terminated, [4242])  # 远程进程被尽力回收
        self.assertEqual(await GpuLease.all().count(), 0)

    @staticmethod
    def _async_value(value):
        async def _impl(*args, **kwargs):
            return value

        return _impl

    # ------------------------------------------------------------------
    # 运行超时（修正后的时间基准）
    # ------------------------------------------------------------------

    async def test_runtime_timeout_fails_job_and_releases_lease(self):
        job = await self._create_inference_job(
            status="RUNNING",
            assigned_gpu=0,
            launcher_pid=4242,
            remote_run_dir="/runs/res-inf",
            started_at=_now()
            - timedelta(seconds=self.service.config["max_runtime_seconds"] + 120),
        )
        await GpuLease.create(gpu_index=0, workload_type="INFERENCE", workload_id=job.id)
        connection = _LaunchConnection()

        with mock.patch.object(
            training_executor_service, "_connect", new=_connect_returning(connection)
        ):
            result = await self.service.reconcile_job(job)

        self.assertEqual(result.status, "FAILED")
        self.assertIn("超时", result.failure_reason)
        self.assertEqual(await GpuLease.all().count(), 0)
        self.assertTrue(
            any("kill -TERM -- -4242" in cmd for cmd in connection.commands)
        )

    async def test_running_job_within_max_runtime_untouched_by_timeout(self):
        job = await self._create_inference_job(
            status="RUNNING",
            assigned_gpu=0,
            launcher_pid=4242,
            remote_run_dir="/runs/res-inf",
            started_at=_now() - timedelta(seconds=30),
        )
        await GpuLease.create(gpu_index=0, workload_type="INFERENCE", workload_id=job.id)
        connection = _LaunchConnection(
            sftp=_NoManifestSftp(asyncssh.SFTPNoSuchFile("no such file"))
        )

        with mock.patch.object(
            training_executor_service, "_connect", new=_connect_returning(connection)
        ):
            result = await self.service.reconcile_job(job)

        self.assertEqual(result.status, "RUNNING")
        self.assertEqual(await GpuLease.all().count(), 1)

    # ------------------------------------------------------------------
    # F4b：监控循环韧性
    # ------------------------------------------------------------------

    async def test_monitor_loop_survives_iteration_exception(self):
        service = InferenceExecutorService()
        calls = []

        async def flaky_monitor_once():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("simulated database outage")
            service._stop_event.set()

        service._monitor_once = flaky_monitor_once
        service.config = {**service.config, "monitor_interval": 0.01}
        await asyncio.wait_for(service._monitor_loop(), timeout=5)
        self.assertEqual(len(calls), 2)  # 第一轮崩溃后仍执行了第二轮

    async def test_dispatch_ssh_failure_does_not_escape_dispatch_queued(self):
        # GPU 状态查询抛出的 TrainingExecutorError 必须在 dispatch 内被
        # 转换为 InferenceExecutorError，否则会杀死监控循环。
        job = await self._create_inference_job()
        service = InferenceExecutorService()
        service._resolve_source = self._async_value(_resolved_tuple())
        service._gpu_candidates = self._raise(TrainingExecutorError("无法连接"))

        with self.assertRaises(InferenceExecutorError) as ctx:
            await service.dispatch_job(job.id)
        self.assertNotIsInstance(ctx.exception, TrainingExecutorError)

        # 监控的批量调度入口不应抛出，任务保持 QUEUED 等待下一轮。
        await service.dispatch_queued_jobs()
        await job.refresh_from_db()
        self.assertEqual(job.status, "QUEUED")

    async def test_dispatch_queued_jobs_fails_permanent_jobs_without_blocking(self):
        # 队列中存在永久失效任务时，调度循环应把它落到 FAILED 并继续，
        # 而不是永远跳过它（也不会阻塞后续任务的处理）。
        await TrainingJob.filter(id=1).update(cleanup_status="PURGED")
        stale = await self._create_inference_job(job_no="res-inf-stale")
        await self.service.dispatch_queued_jobs()

        await stale.refresh_from_db()
        self.assertEqual(stale.status, "FAILED")


class _NoManifestSftp:
    def __init__(self, error):
        self._error = error

    async def makedirs(self, path, exist_ok=True):
        pass

    def open(self, path, mode="r"):
        raise self._error


if __name__ == "__main__":
    unittest.main()
