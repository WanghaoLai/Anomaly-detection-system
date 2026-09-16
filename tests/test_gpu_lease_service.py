import asyncio
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from models import (
    Admin,
    Algorithm,
    Dataset,
    GpuLease,
    InferenceJob,
    TrainingJob,
)
from services.gpu_lease_service import GpuLeaseCoordinator
from tortoise import Tortoise, connections


class GpuLeaseCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        await Admin.create(id=1, username="lease-admin", password="x", role="管理员")
        await Algorithm.create(id=1, algorithm_no="1", name="PBAS", created_by_id=1)
        await Dataset.create(id=1, dataset_no="1", name="MVTec AD", created_by_id=1)
        self.training = await TrainingJob.create(
            id=1,
            job_no="lease-training",
            owner_id=1,
            owner_role="用户",
            algorithm_id=1,
            dataset_id=1,
            status="QUEUED",
            config_json={},
        )
        self.inference = await InferenceJob.create(
            id=1,
            job_no="lease-inference",
            owner_id=1,
            owner_role="用户",
            training_job_id=self.training.id,
            status="QUEUED",
            config_json={},
        )

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_training_and_inference_cannot_claim_same_gpu(self):
        first = GpuLeaseCoordinator()
        second = GpuLeaseCoordinator()
        results = await asyncio.gather(
            first.acquire(
                workload_type="TRAINING",
                workload_id=self.training.id,
                candidates=[0],
            ),
            second.acquire(
                workload_type="INFERENCE",
                workload_id=self.inference.id,
                candidates=[0],
            ),
        )

        self.assertEqual(results.count(0), 1)
        self.assertEqual(results.count(None), 1)
        self.assertEqual(await GpuLease.all().count(), 1)
        jobs = [
            await TrainingJob.get(id=self.training.id),
            await InferenceJob.get(id=self.inference.id),
        ]
        self.assertEqual(sum(job.status == "STARTING" for job in jobs), 1)
        self.assertEqual(sum(job.status == "QUEUED" for job in jobs), 1)

    async def test_same_gpu_index_on_different_servers_can_run_concurrently(self):
        await InferenceJob.filter(id=self.inference.id).update(server_id="a100-server")
        results = await asyncio.gather(
            GpuLeaseCoordinator().acquire(
                workload_type="TRAINING",
                workload_id=self.training.id,
                candidates=[0],
                server_id="primary",
            ),
            GpuLeaseCoordinator().acquire(
                workload_type="INFERENCE",
                workload_id=self.inference.id,
                candidates=[0],
                server_id="a100-server",
            ),
        )

        self.assertEqual(results, [0, 0])
        self.assertEqual(await GpuLease.all().count(), 2)
        self.assertEqual(
            set(await GpuLease.all().values_list("server_id", flat=True)),
            {"primary", "a100-server"},
        )

    async def test_reconcile_removes_terminal_job_lease(self):
        await TrainingJob.filter(id=self.training.id).update(
            status="SUCCEEDED",
            assigned_gpu=0,
        )
        await GpuLease.create(
            gpu_index=0,
            workload_type="TRAINING",
            workload_id=self.training.id,
        )

        await GpuLeaseCoordinator().reconcile()

        self.assertFalse(await GpuLease.filter(gpu_index=0).exists())

    async def test_same_job_cannot_be_claimed_by_two_workers(self):
        results = await asyncio.gather(
            GpuLeaseCoordinator().acquire(
                workload_type="TRAINING",
                workload_id=self.training.id,
                candidates=[0, 1],
            ),
            GpuLeaseCoordinator().acquire(
                workload_type="TRAINING",
                workload_id=self.training.id,
                candidates=[1, 0],
            ),
        )

        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(await GpuLease.all().count(), 1)
        training = await TrainingJob.get(id=self.training.id)
        self.assertEqual(training.status, "STARTING")
        self.assertIn(training.assigned_gpu, {0, 1})
        self.assertIsNotNone(training.started_at)

    async def test_reconcile_backfills_active_job_after_migration(self):
        await TrainingJob.filter(id=self.training.id).update(
            status="RUNNING",
            assigned_gpu=2,
        )

        await GpuLeaseCoordinator().reconcile()

        lease = await GpuLease.get(gpu_index=2)
        self.assertEqual(lease.workload_type, "TRAINING")
        self.assertEqual(lease.workload_id, self.training.id)
