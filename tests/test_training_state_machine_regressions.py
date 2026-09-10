import json
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from models import Admin, Algorithm, Dataset, GpuLease, TrainingJob  # noqa: E402
from services.training_executor_service import (  # noqa: E402
    TrainingExecutorService,
    _utc_now,
)
from tortoise import Tortoise, connections  # noqa: E402


class _Connection:
    async def start_sftp_client(self):
        return object()


class TrainingStateMachineRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        await Admin.create(id=1, username="admin", password="x", role="管理员")
        self.algorithm = await Algorithm.create(
            id=1, algorithm_no="1", name="PBAS", abbreviation="PBAS", created_by_id=1
        )
        self.dataset = await Dataset.create(
            id=1, dataset_no="1", name="MVTec AD", created_by_id=1
        )

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def _job(self, **overrides):
        values = {
            "job_no": "training-state-test",
            "owner_id": 1,
            "owner_role": "管理员",
            "algorithm_id": self.algorithm.id,
            "dataset_id": self.dataset.id,
            "status": "RUNNING",
            "config_json": {"adapter": {"key": "PBAS"}},
            "assigned_gpu": 0,
            "started_at": _utc_now(),
        }
        values.update(overrides)
        return await TrainingJob.create(**values)

    async def test_starting_timeout_uses_immutable_started_at(self):
        job = await self._job(
            status="STARTING",
            launcher_pid=None,
            started_at=_utc_now() - timedelta(seconds=120),
        )
        await GpuLease.create(
            gpu_index=0, workload_type="TRAINING", workload_id=job.id
        )
        service = TrainingExecutorService()
        service.config = {**service.config, "command_timeout": 1}

        with mock.patch.object(service, "_event", new=mock.AsyncMock()), mock.patch.object(
            service, "audit", new=mock.AsyncMock()
        ):
            result = await service.reconcile_job(job)

        self.assertEqual(result.status, "LOST")
        self.assertFalse(await GpuLease.filter(gpu_index=0).exists())

    async def test_dead_process_with_running_manifest_converges_to_lost(self):
        job = await self._job(launcher_pid=123, remote_run_dir="/runs/job")
        await GpuLease.create(
            gpu_index=0, workload_type="TRAINING", workload_id=job.id
        )
        service = TrainingExecutorService()
        reads = mock.AsyncMock(
            side_effect=[json.dumps({"status": "RUNNING"}), json.dumps({})]
        )

        with mock.patch.object(
            service, "_adapter_for_job", new=mock.AsyncMock(return_value=object())
        ), mock.patch.object(
            service, "_sync_remote_log_with_sftp", new=mock.AsyncMock()
        ), mock.patch.object(service, "_read_text", new=reads), mock.patch.object(
            service,
            "_run",
            new=mock.AsyncMock(return_value=SimpleNamespace(exit_status=1)),
        ), mock.patch.object(service, "_event", new=mock.AsyncMock()), mock.patch.object(
            service, "audit", new=mock.AsyncMock()
        ):
            result = await service.reconcile_job(job, connection=_Connection())

        self.assertEqual(result.status, "LOST")
        self.assertFalse(await GpuLease.filter(gpu_index=0).exists())

    async def test_reconcile_outage_keeps_running_job_and_gpu_lease(self):
        job = await self._job()
        await GpuLease.create(
            gpu_index=0, workload_type="TRAINING", workload_id=job.id
        )
        service = TrainingExecutorService()

        with mock.patch.object(service, "_event", new=mock.AsyncMock()), mock.patch.object(
            service, "audit", new=mock.AsyncMock()
        ):
            for _ in range(3):
                current = await TrainingJob.get(id=job.id)
                await service._record_reconcile_failure(current, "temporary SSH outage")

        current = await TrainingJob.get(id=job.id)
        self.assertEqual(current.status, "RUNNING")
        self.assertEqual(current.assigned_gpu, 0)
        self.assertEqual(current.reconcile_failures, 3)
        self.assertTrue(await GpuLease.filter(gpu_index=0).exists())

    async def test_monitor_survives_one_iteration_failure(self):
        service = TrainingExecutorService()

        async def fail_once():
            service._stop_event.set()
            raise RuntimeError("database unavailable")

        service.recover_active_jobs = fail_once
        service.dispatch_queued_jobs = mock.AsyncMock()
        await service._monitor()
        service.dispatch_queued_jobs.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
