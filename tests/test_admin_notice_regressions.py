import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError
from tortoise import Tortoise, connections

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

# 保持与生产一致：API 模块先于 Tortoise 初始化导入。
from api.admin import delete as admin_delete  # noqa: E402
from api.notice import (  # noqa: E402
    NoticeCreatePydantic,
    NoticeUpdatePydantic,
)
from api.notice import (
    delete as notice_delete,
)
from api.notice import (
    router as notice_router,
)
from common.exception_handler import CustomException  # noqa: E402
from models import (  # noqa: E402
    Admin,
    Algorithm,
    AuthSession,
    Dataset,
    InferenceJob,
    TrainingJob,
)

CURRENT_ADMIN = {"user_id": 1, "role": "管理员"}


class NoticePaginationContractTests(unittest.TestCase):
    def test_select_page_accepts_frontend_camel_case_parameters(self):
        app = FastAPI()
        app.include_router(notice_router)
        route = next(
            route
            for route in app.routes
            if getattr(route, "path", None) == "/notice/selectPage"
        )
        aliases = {parameter.alias for parameter in route.dependant.query_params}
        self.assertIn("pageNum", aliases)
        self.assertIn("pageSize", aliases)
        self.assertNotIn("page_num", aliases)
        self.assertNotIn("page_size", aliases)

    def test_notice_create_requires_title_and_content(self):
        with self.assertRaises(ValidationError):
            NoticeCreatePydantic(name="")

    def test_notice_update_rejects_server_owned_time(self):
        with self.assertRaises(ValidationError):
            NoticeUpdatePydantic(id=1, name="更新", time="伪造时间")


class NoticeMutationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_delete_missing_notice_returns_404(self):
        with self.assertRaises(CustomException) as context:
            await notice_delete(999)
        self.assertEqual(context.exception.status_code, 404)


class AdminDeleteTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        await Admin.create(
            id=1,
            username="current-admin",
            password="x",
            role="管理员",
        )
        await Admin.create(
            id=2,
            username="target-admin",
            password="x",
            role="管理员",
        )
        await AuthSession.create(
            id="target-session",
            user_id=2,
            role="管理员",
            refresh_jti="target-refresh-jti",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_algorithm_reference_blocks_delete_without_removing_session(self):
        await Algorithm.create(
            algorithm_no="1",
            name="PBAS",
            created_by_id=2,
        )

        with self.assertRaises(CustomException) as context:
            await admin_delete(2, CURRENT_ADMIN)

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("关联算法", str(context.exception))
        self.assertTrue(await Admin.filter(id=2).exists())
        self.assertTrue(await AuthSession.filter(id="target-session").exists())

    async def test_dataset_reference_blocks_delete_without_removing_session(self):
        await Dataset.create(
            dataset_no="1",
            name="MVTec AD",
            created_by_id=2,
        )

        with self.assertRaises(CustomException) as context:
            await admin_delete(2, CURRENT_ADMIN)

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("关联数据集", str(context.exception))
        self.assertTrue(await Admin.filter(id=2).exists())
        self.assertTrue(await AuthSession.filter(id="target-session").exists())

    async def test_unreferenced_admin_and_session_are_deleted_together(self):
        result = await admin_delete(2, CURRENT_ADMIN)

        self.assertEqual(result.code, "200")
        self.assertFalse(await Admin.filter(id=2).exists())
        self.assertFalse(await AuthSession.filter(id="target-session").exists())

    async def test_training_job_reference_blocks_admin_delete(self):
        algorithm = await Algorithm.create(
            algorithm_no="1", name="PBAS", created_by_id=1
        )
        dataset = await Dataset.create(
            dataset_no="1", name="MVTec AD", created_by_id=1
        )
        await TrainingJob.create(
            job_no="admin-owned-training",
            owner_id=2,
            owner_role="管理员",
            algorithm_id=algorithm.id,
            dataset_id=dataset.id,
            status="RUNNING",
            config_json={},
        )

        with self.assertRaises(CustomException) as context:
            await admin_delete(2, CURRENT_ADMIN)

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("训练任务", str(context.exception))
        self.assertTrue(await Admin.filter(id=2).exists())

    async def test_inference_job_reference_blocks_admin_delete(self):
        algorithm = await Algorithm.create(
            algorithm_no="1", name="PBAS", created_by_id=1
        )
        dataset = await Dataset.create(
            dataset_no="1", name="MVTec AD", created_by_id=1
        )
        training = await TrainingJob.create(
            job_no="source-training",
            owner_id=1,
            owner_role="管理员",
            algorithm_id=algorithm.id,
            dataset_id=dataset.id,
            status="SUCCEEDED",
            config_json={},
        )
        await InferenceJob.create(
            job_no="admin-owned-inference",
            owner_id=2,
            owner_role="管理员",
            training_job_id=training.id,
            status="SUCCEEDED",
            config_json={},
        )

        with self.assertRaises(CustomException) as context:
            await admin_delete(2, CURRENT_ADMIN)

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("推理任务", str(context.exception))
        self.assertTrue(await Admin.filter(id=2).exists())


if __name__ == "__main__":
    unittest.main()
