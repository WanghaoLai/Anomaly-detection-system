import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.training_executor_service import (  # noqa: E402
    TrainingExecutorError,
    TrainingExecutorService,
)


class TrainingExecutionAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_executor_rejects_before_creating_a_job(self):
        service = TrainingExecutorService()
        service.config = {**service.config, "enabled": False}

        with self.assertRaises(TrainingExecutorError) as ctx:
            await service.submit_job(
                owner={"user_id": 1, "role": "用户"},
                algorithm_id=1,
                dataset_id=1,
                parameters={},
            )

        self.assertIn("未启用", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
