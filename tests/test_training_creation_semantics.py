import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from api import training as training_api  # noqa: E402
from models import (  # noqa: E402
    Admin, Algorithm, AlgorithmInfo, Dataset, TrainingAudit, TrainingEvent, TrainingJob, User,
)
from services.training_executor_service import TrainingExecutorService  # noqa: E402
from tortoise import Tortoise, connections  # noqa: E402


class TrainingCreationSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        admin = await Admin.create(username="creator", password="x", role="管理员")
        user = await User.create(username="trainer", password="x", role="用户")
        algorithm = await Algorithm.create(
            algorithm_no="1", name="PBAS", created_by_id=admin.id
        )
        await AlgorithmInfo.create(
            algorithm_id=algorithm.id, framework="PyTorch", train_entrypoint="main.py"
        )
        dataset = await Dataset.create(
            dataset_no="1", name="MVTec AD", created_by_id=admin.id
        )
        self.owner = {"user_id": user.id, "role": "用户"}
        self.algorithm = await Algorithm.filter(id=algorithm.id).prefetch_related(
            "algorithm_info"
        ).first()
        self.dataset = dataset

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    def _service(self):
        service = TrainingExecutorService()
        service.config = {
            **service.config,
            "enabled": True,
            "host": "gpu.example",
            "ssh_user": "runner",
            "private_key_path": "/tmp/key",
        }
        adapter = SimpleNamespace(
            key="PBAS",
            protocol_version="1.0",
            validate_job_parameters=lambda *args: {},
            total_epochs=lambda *args: 1,
        )
        service._resolve_whitelisted_runtime = mock.AsyncMock(return_value=(
            self.algorithm, self.dataset, {}, {}, adapter
        ))
        return service

    async def test_creation_event_and_audit_commit_with_job(self):
        service = self._service()
        job = await service.submit_job(self.owner, self.algorithm.id, self.dataset.id, {})
        self.assertEqual(await TrainingJob.all().count(), 1)
        self.assertEqual(await TrainingEvent.filter(job_id=job.id).count(), 1)
        self.assertEqual(await TrainingAudit.filter(job_id=job.id).count(), 1)

    async def test_audit_failure_rolls_back_job_and_event(self):
        service = self._service()
        with mock.patch.object(
            TrainingAudit, "create", new=mock.AsyncMock(side_effect=RuntimeError("audit down"))
        ):
            with self.assertRaises(RuntimeError):
                await service.submit_job(self.owner, self.algorithm.id, self.dataset.id, {})
        self.assertEqual(await TrainingJob.all().count(), 0)
        self.assertEqual(await TrainingEvent.all().count(), 0)

    async def test_dispatch_failure_still_returns_persisted_job_identity(self):
        job = await self._service().submit_job(
            self.owner, self.algorithm.id, self.dataset.id, {}
        )
        request = training_api.TrainingJobCreate(
            serverId="primary", algorithmId=self.algorithm.id,
            datasetId=self.dataset.id, parameters={}
        )
        with (
            mock.patch.object(training_api.training_executor_service, "submit_job",
                              new=mock.AsyncMock(return_value=job)),
            mock.patch.object(training_api.training_executor_service, "dispatch_queued_jobs",
                              new=mock.AsyncMock(side_effect=RuntimeError("temporary database outage"))),
        ):
            result = await training_api.create_job(request, current_user=self.owner)
        self.assertEqual(result.code, "200")
        self.assertEqual(result.data["id"], job.id)
        self.assertEqual(result.data["status"], "QUEUED")


if __name__ == "__main__":
    unittest.main()
