import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from tortoise import Tortoise, connections  # noqa: E402
from tortoise.transactions import in_transaction  # noqa: E402

# api 包先于 Tortoise 初始化导入（生产顺序，见 test_api_import_order）。
from api.algorithm import add as algorithm_add  # noqa: E402
from api.algorithm import delete as algorithm_delete  # noqa: E402
from api.algorithm import AlgorithmCreatePydantic  # noqa: E402
from api.user import delete as user_delete  # noqa: E402
from common.exception_handler import CustomException  # noqa: E402
from common.sequential_number import next_sequential_number  # noqa: E402
from models import (  # noqa: E402
    Admin,
    Algorithm,
    Conversation,
    Dataset,
    Message,
    TrainingJob,
    User,
)

ADMIN = {"user_id": 1, "role": "管理员"}


class CatalogNumberingAndDeleteTests(unittest.IsolatedAsyncioTestCase):
    """🟡#12 回归：编号单调递增且删除不重排；被引用目录拒绝删除。"""

    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        await Admin.create(id=1, username="catalog-admin", password="x", role="管理员")
        await Dataset.create(id=1, dataset_no="1", name="MVTec AD", created_by_id=1)
        # 模拟历史删除留下的编号空洞：1、2、5。
        for no in (1, 2, 5):
            await Algorithm.create(
                id=no, algorithm_no=str(no), name=f"ALG-{no}", created_by_id=1
            )

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        Tortoise._reset_apps()

    async def test_next_number_is_max_plus_one_not_count_plus_one(self):
        async with in_transaction() as connection:
            number = await next_sequential_number(
                Algorithm, "algorithm_no", connection
            )
        self.assertEqual(number, 6)

    async def test_add_assigns_max_plus_one_and_delete_keeps_numbers_stable(self):
        result = await algorithm_add(
            AlgorithmCreatePydantic(name="NewAlg"), ADMIN
        )
        new_id = result.data
        created = await Algorithm.get(id=new_id)
        self.assertEqual(created.algorithm_no, "6")

        # 删除中间编号的算法，剩余编号保持稳定，不触发全表重排。
        await algorithm_delete(2)
        remaining = await Algorithm.all().order_by("id").values_list(
            "algorithm_no", flat=True
        )
        self.assertEqual(list(remaining), ["1", "5", "6"])

        result = await algorithm_add(
            AlgorithmCreatePydantic(name="AnotherAlg"), ADMIN
        )
        another = await Algorithm.get(id=result.data)
        self.assertEqual(another.algorithm_no, "7")

    async def test_delete_referenced_algorithm_is_rejected_with_friendly_error(self):
        await TrainingJob.create(
            job_no="catalog-job",
            owner_id=1,
            owner_role="管理员",
            algorithm_id=5,
            dataset_id=1,
            status="SUCCEEDED",
            config_json={},
        )
        with self.assertRaises(CustomException) as ctx:
            await algorithm_delete(5)
        self.assertIn("仍被训练任务引用", str(ctx.exception))
        # 事务回滚后算法仍在。
        self.assertTrue(await Algorithm.filter(id=5).exists())


class UserDeleteGuardTests(unittest.IsolatedAsyncioTestCase):
    """🟡#13 回归：删除用户前检查任务归属并清理会话数据。"""

    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        await Admin.create(id=1, username="guard-admin", password="x", role="管理员")
        await Algorithm.create(id=1, algorithm_no="1", name="PBAS", created_by_id=1)
        await Dataset.create(id=1, dataset_no="1", name="MVTec AD", created_by_id=1)
        self.user = await User.create(
            username="guard-user", password="x", name="u", role="用户"
        )

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        Tortoise._reset_apps()

    async def test_running_training_job_blocks_user_delete(self):
        await TrainingJob.create(
            job_no="guard-job",
            owner_id=self.user.id,
            owner_role="用户",
            algorithm_id=1,
            dataset_id=1,
            status="RUNNING",
            config_json={},
        )
        with self.assertRaises(CustomException) as ctx:
            await user_delete(self.user.id)
        self.assertIn("进行中的训练任务", str(ctx.exception))
        self.assertTrue(await User.filter(id=self.user.id).exists())

    async def test_historical_jobs_block_user_delete(self):
        await TrainingJob.create(
            job_no="guard-job-done",
            owner_id=self.user.id,
            owner_role="用户",
            algorithm_id=1,
            dataset_id=1,
            status="SUCCEEDED",
            config_json={},
        )
        with self.assertRaises(CustomException) as ctx:
            await user_delete(self.user.id)
        self.assertIn("历史任务记录", str(ctx.exception))

    async def test_user_without_jobs_is_deleted_along_with_chat_data(self):
        conversation = await Conversation.create(user_id=self.user.id, title="t")
        await Message.create(
            conversation_id=conversation.id, role="user", content="hi"
        )
        await user_delete(self.user.id)
        self.assertFalse(await User.filter(id=self.user.id).exists())
        self.assertFalse(await Conversation.filter(user_id=self.user.id).exists())
        self.assertFalse(await Message.filter(
            conversation_id=conversation.id
        ).exists())


if __name__ == "__main__":
    unittest.main()
