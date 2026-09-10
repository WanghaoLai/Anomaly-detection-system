import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from api.admin import AdminCreatePydantic
from api.algorithm import AlgorithmCreatePydantic
from api.algorithm import add as add_algorithm
from api.dataset import DatasetCreatePydantic
from api.dataset import add as add_dataset
from api.user import UserCreatePydantic, UserUpdatePydantic
from api.user import update as update_user
from common.exception_handler import CustomException
from models import Admin, Algorithm, AlgorithmInfo, Dataset, DatasetInfo, User
from pydantic import ValidationError
from fastapi import HTTPException
from tortoise import Tortoise, connections
from tortoise.exceptions import IntegrityError

ADMIN = {"user_id": 1, "role": "管理员"}


class CatalogInvariantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        await Admin.create(
            id=1,
            username="invariant-admin",
            password="x",
            role="管理员",
        )

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_dataset_create_is_atomic_and_has_exactly_one_detail(self):
        result = await add_dataset(
            DatasetCreatePydantic(
                name="MVTec AD",
                root_directory="/datasets/mvtec",
                class_count=15,
            ),
            ADMIN,
        )

        dataset = await Dataset.get(id=result.data).prefetch_related("dataset_info")
        self.assertEqual(dataset.dataset_info.root_directory, "/datasets/mvtec")
        self.assertEqual(
            await DatasetInfo.filter(dataset_id=dataset.id).count(),
            1,
        )
        with self.assertRaises(IntegrityError):
            await DatasetInfo.create(dataset_id=dataset.id)

    async def test_algorithm_detail_failure_rolls_back_primary_record(self):
        payload = AlgorithmCreatePydantic(
            name="PBAS",
            abbreviation="PBAS",
            framework="PyTorch",
            conda_env_name="pbas",
            conda_env_path="/opt/conda/envs/pbas",
            working_directory="/srv/pbas",
            train_entrypoint="train.py",
        )
        with mock.patch.object(
            AlgorithmInfo,
            "create",
            new=mock.AsyncMock(side_effect=IntegrityError("forced")),
        ), self.assertRaises(CustomException) as context:
            await add_algorithm(payload, ADMIN)

        self.assertEqual(context.exception.status_code, 409)
        self.assertFalse(await Algorithm.filter(name="PBAS").exists())

    async def test_user_username_conflict_maps_to_409(self):
        first = await User.create(username="first", password="x", role="用户")
        await User.create(username="second", password="x", role="用户")

        with self.assertRaises(CustomException) as context:
            await update_user(
                UserUpdatePydantic(id=first.id, username="second"),
                ADMIN,
            )

        self.assertEqual(context.exception.status_code, 409)
        await first.refresh_from_db()
        self.assertEqual(first.username, "first")

    async def test_user_cannot_change_own_login_identifier(self):
        user = await User.create(username="first", password="x", role="用户")

        with self.assertRaises(HTTPException) as context:
            await update_user(
                UserUpdatePydantic(id=user.id, username="second"),
                {"user_id": user.id, "username": "first", "role": "用户"},
            )

        self.assertEqual(context.exception.status_code, 403)
        await user.refresh_from_db()
        self.assertEqual(user.username, "first")


class StrictRequestModelTests(unittest.TestCase):
    def test_catalog_create_requires_runtime_details(self):
        with self.assertRaises(ValidationError):
            AlgorithmCreatePydantic(name="Incomplete")

    def test_server_owned_and_unknown_fields_are_rejected(self):
        with self.assertRaises(ValidationError):
            DatasetCreatePydantic(name="dataset", createdBy=99)
        with self.assertRaises(ValidationError):
            UserCreatePydantic(
                username="user",
                password="password",
                role="管理员",
            )
        with self.assertRaises(ValidationError):
            AdminCreatePydantic(
                username="admin",
                password="password",
                token_version=99,
            )

    def test_non_nullable_update_field_rejects_explicit_null(self):
        with self.assertRaises(ValidationError):
            UserUpdatePydantic(id=1, username=None)
