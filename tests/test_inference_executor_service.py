import sys
import unittest
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
    InferenceJob,
    TrainingJob,
)
from services.inference_executor_service import (  # noqa: E402
    InferenceExecutorError,
    InferenceExecutorService,
)
from services.training_executor_service import (  # noqa: E402
    training_executor_service,
)


class _FakeAsyncReader:
    def __init__(self, content):
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def read(self):
        return self._content


class _FakeSftpClient:
    """按需抛出指定异常或返回固定内容，模拟远程文件状态。"""

    def __init__(self, *, open_error=None, stat_error=None, open_content=None):
        self._open_error = open_error
        self._stat_error = stat_error
        self._open_content = open_content

    def open(self, path, mode="r"):
        # 真实 asyncssh 在进入 async with 上下文时抛出缺失异常；
        # 这里在 open() 调用时同步抛出以覆盖同一代码路径。
        if self._open_error is not None:
            raise self._open_error
        if self._open_content is not None:
            return _FakeAsyncReader(self._open_content)
        raise AssertionError("测试不应读取到远程文件内容")

    async def stat(self, path):
        if self._stat_error is not None:
            raise self._stat_error
        raise AssertionError("测试不应读取到远程文件属性")


class _FakeConnection:
    def __init__(self, sftp, *, run_exit_status=0):
        self._sftp = sftp
        self._run_exit_status = run_exit_status
        self.closed = False
        self.commands = []

    async def start_sftp_client(self):
        return self._sftp

    async def run(self, command, check=True, timeout=None):
        self.commands.append(command)
        return SimpleNamespace(exit_status=self._run_exit_status)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.closed = True


def _running_job():
    # started_at=None 跳过数据库运行时长查询，测试无需初始化 DB。
    return SimpleNamespace(
        id=1,
        status="RUNNING",
        remote_run_dir="/home/adtrainer/inference-runs/job-1",
        started_at=None,
        launcher_pid=None,
        result_json={"visualizations": ["raw.log"]},
    )


class InferenceReconcileMissingManifestTests(unittest.IsolatedAsyncioTestCase):
    async def _reconcile_with_sftp_error(self, error):
        service = InferenceExecutorService()
        job = _running_job()
        connection = _FakeConnection(_FakeSftpClient(open_error=error))
        with mock.patch.object(
            training_executor_service, "_connect", new=_async_value(connection)
        ):
            result = await service.reconcile_job(job)
        self.assertTrue(connection.closed)
        return result, job

    async def test_sftp_no_such_file_returns_job_without_raising(self):
        # 回归：asyncssh 抛 SFTPNoSuchFile（不是 FileNotFoundError 子类），
        # dispatch 后 runner 尚未写出 manifest 时轮询不能冒泡为系统错误。
        result, job = await self._reconcile_with_sftp_error(
            asyncssh.SFTPNoSuchFile("no such file")
        )
        self.assertIs(result, job)
        self.assertEqual(result.status, "RUNNING")

    async def test_file_not_found_still_returns_job(self):
        result, job = await self._reconcile_with_sftp_error(
            FileNotFoundError("/home/adtrainer/inference-runs/job-1/manifest.json")
        )
        self.assertIs(result, job)


class InferenceReadOutputMissingTests(unittest.IsolatedAsyncioTestCase):
    async def _read_output_with_error(self, error):
        service = InferenceExecutorService()
        job = _running_job()
        connection = _FakeConnection(_FakeSftpClient(stat_error=error))
        with mock.patch.object(
            training_executor_service, "_connect", new=_async_value(connection)
        ):
            await service.read_output(job, "raw.log")
        self.assertTrue(connection.closed)

    async def test_sftp_no_such_file_maps_to_friendly_error(self):
        with self.assertRaises(InferenceExecutorError) as ctx:
            await self._read_output_with_error(
                asyncssh.SFTPNoSuchFile("no such file")
            )
        self.assertIn("不存在", str(ctx.exception))

    async def test_file_not_found_maps_to_friendly_error(self):
        with self.assertRaises(InferenceExecutorError) as ctx:
            await self._read_output_with_error(FileNotFoundError("raw.log"))
        self.assertIn("不存在", str(ctx.exception))


def _async_value(value):
    async def _connect():
        return value

    return _connect


class InferenceLivenessTests(unittest.IsolatedAsyncioTestCase):
    """🟡#9 回归：runner 死亡且无终态 manifest 时任务收敛为 LOST。"""

    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        await Admin.create(id=1, username="liveness-admin", password="x", role="管理员")
        await Algorithm.create(id=1, algorithm_no="1", name="PBAS", created_by_id=1)
        await Dataset.create(id=1, dataset_no="1", name="MVTec AD", created_by_id=1)
        await TrainingJob.create(
            id=1,
            job_no="liveness-train",
            owner_id=1,
            owner_role="用户",
            algorithm_id=1,
            dataset_id=1,
            status="SUCCEEDED",
            config_json={},
        )
        self.job = await InferenceJob.create(
            job_no="liveness-inf",
            owner_id=1,
            owner_role="用户",
            training_job_id=1,
            status="RUNNING",
            config_json={"adapter": {"key": "PBAS"}},
            remote_run_dir="/runs/liveness-inf",
            launcher_pid=4242,
        )

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def _reconcile_with(self, sftp, *, run_exit_status):
        connection = _FakeConnection(sftp, run_exit_status=run_exit_status)
        with mock.patch.object(
            training_executor_service, "_connect", new=_async_value(connection)
        ):
            result = await InferenceExecutorService().reconcile_job(self.job)
        self.assertTrue(connection.closed)
        self.probe_commands = list(connection.commands)
        return result

    async def test_dead_runner_without_manifest_marks_lost(self):
        result = await self._reconcile_with(
            _FakeSftpClient(open_error=asyncssh.SFTPNoSuchFile("no such file")),
            run_exit_status=1,
        )
        self.assertEqual(result.status, "LOST")
        self.assertIn("推理进程已消失", result.failure_reason)

    async def test_dead_runner_with_running_manifest_marks_lost(self):
        result = await self._reconcile_with(
            _FakeSftpClient(open_content='{"status": "RUNNING"}'),
            run_exit_status=1,
        )
        self.assertEqual(result.status, "LOST")

    async def test_alive_runner_keeps_running_and_probes_process(self):
        connection_probe = _FakeSftpClient(
            open_error=asyncssh.SFTPNoSuchFile("no such file")
        )
        result = await self._reconcile_with(connection_probe, run_exit_status=0)
        self.assertEqual(result.status, "RUNNING")
        self.assertIn("/bin/kill -0 4242", self.probe_commands)

    async def test_terminal_manifest_finishes_job(self):
        result = await self._reconcile_with(
            _FakeSftpClient(open_content=(
                '{"status": "SUCCEEDED", "exit_code": 0, "result": {"x": 1}}'
            )),
            run_exit_status=0,
        )
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(result.result_json, {"x": 1})
        self.assertIsNone(result.assigned_gpu)

    async def test_failed_manifest_uses_adapter_key_from_job_snapshot(self):
        # 💭#7 回归：失败原因中的算法名来自任务快照，而非硬编码 PBAS。
        self.job.config_json = {"adapter": {"key": "DEMO"}}
        result = await self._reconcile_with(
            _FakeSftpClient(open_content=(
                '{"status": "FAILED", "exit_code": 1, "result": null}'
            )),
            run_exit_status=0,
        )
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(
            result.failure_reason, "DEMO 推理进程执行失败，请查看日志"
        )


if __name__ == "__main__":
    unittest.main()
