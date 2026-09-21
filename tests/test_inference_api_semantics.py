import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from api import inference as inference_api  # noqa: E402
from services.inference_executor_service import InferenceExecutorError  # noqa: E402


class InferenceCreateSemanticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_job_is_returned_when_immediate_dispatch_fails(self):
        job = SimpleNamespace(
            id=42,
            job_no="inference-42",
            training_job_id=7,
            server_id="primary",
            status="QUEUED",
            config_json={},
            result_json=None,
            assigned_gpu=None,
            exit_code=None,
            failure_reason=None,
            submitted_at=None,
            started_at=None,
            finished_at=None,
        )
        request = inference_api.InferenceJobCreate(
            trainingJobId=7,
            classes=[],
        )
        with (
            mock.patch.object(
                inference_api.inference_executor_service,
                "submit_job",
                new=mock.AsyncMock(return_value=job),
            ),
            mock.patch.object(
                inference_api.inference_executor_service,
                "dispatch_job",
                new=mock.AsyncMock(
                    side_effect=InferenceExecutorError("temporary SSH outage")
                ),
            ),
            mock.patch.object(
                inference_api.InferenceJob,
                "get",
                new=mock.AsyncMock(return_value=job),
            ),
            mock.patch.object(
                inference_api,
                "_metadata",
                new=mock.AsyncMock(return_value={}),
            ),
        ):
            result = await inference_api.create_job(
                request,
                current_user={"user_id": 1, "role": "用户"},
            )

        self.assertEqual(result.code, "200")
        self.assertEqual(result.data["id"], 42)
        self.assertEqual(result.data["status"], "QUEUED")


if __name__ == "__main__":
    unittest.main()
