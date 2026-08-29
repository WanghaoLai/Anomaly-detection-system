import asyncio
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from tortoise import Tortoise, connections  # noqa: E402

from models import (  # noqa: E402
    Admin,
    Algorithm,
    Dataset,
    TrainingEvent,
    TrainingJob,
    TrainingMetric,
)
from services.training_executor_service import TrainingExecutorService  # noqa: E402
from services.training_log_parser import ParsedTrainingLine  # noqa: E402


class TrainingExecutorBatchWritesTests(unittest.IsolatedAsyncioTestCase):
    """🟡#6/#7 回归：指标批量 upsert 语义与并发事件序列号唯一性。"""

    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        # Tortoise 的 sqlite 客户端开启外键约束，需按依赖链建父行。
        await Admin.create(id=1, username="batch-admin", password="x", role="管理员")
        await Algorithm.create(
            id=1, algorithm_no="1", name="PBAS", created_by_id=1
        )
        await Dataset.create(
            id=1, dataset_no="1", name="MVTec AD", created_by_id=1
        )
        self.service = TrainingExecutorService()
        self.job = await TrainingJob.create(
            job_no="batch-write-test",
            owner_id=1,
            owner_role="用户",
            algorithm_id=1,
            dataset_id=1,
            status="RUNNING",
            config_json={},
        )

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_concurrent_events_get_distinct_sequences(self):
        await asyncio.gather(*[
            self.service._event(self.job.id, "TEST_EVENT", f"message-{index}")
            for index in range(10)
        ])
        sequences = await TrainingEvent.filter(
            job_id=self.job.id
        ).values_list("sequence", flat=True)
        self.assertEqual(sorted(sequences), list(range(1, 11)))

    async def test_metrics_batch_upsert_last_write_wins(self):
        parsed_lines = [
            ParsedTrainingLine(content="l1", metrics=[("train/loss", 1.0, 1)]),
            ParsedTrainingLine(
                content="l2",
                metrics=[("train/loss", 0.5, 1), ("eval/auc", 0.9, 1)],
            ),
            ParsedTrainingLine(content="l3", metrics=[("train/loss", 0.3, 2)]),
        ]
        await self.service._persist_parsed_log_lines(
            self.job, parsed_lines, remote_offset=100
        )

        metrics = {
            (item.metric_name, item.epoch): item.metric_value
            for item in await TrainingMetric.filter(job_id=self.job.id)
        }
        self.assertEqual(len(metrics), 3)
        self.assertEqual(metrics[("train/loss", 1)], 0.5)  # 同键后写覆盖
        self.assertEqual(metrics[("train/loss", 2)], 0.3)
        self.assertEqual(metrics[("eval/auc", 1)], 0.9)

        # 第二轮同步更新已有键并新增键，不产生重复行。
        await self.service._persist_parsed_log_lines(
            self.job,
            [ParsedTrainingLine(
                content="l4",
                metrics=[("train/loss", 0.2, 1), ("eval/ap", 0.8, 1)],
            )],
            remote_offset=200,
        )
        rows = await TrainingMetric.filter(job_id=self.job.id)
        self.assertEqual(len(rows), 4)
        metrics = {
            (item.metric_name, item.epoch): item.metric_value
            for item in rows
        }
        self.assertEqual(metrics[("train/loss", 1)], 0.2)
        self.assertEqual(metrics[("eval/ap", 1)], 0.8)

        await self.job.refresh_from_db()
        self.assertEqual(self.job.log_offset, 200)


if __name__ == "__main__":
    unittest.main()
