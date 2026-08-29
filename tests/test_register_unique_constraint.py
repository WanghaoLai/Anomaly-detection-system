import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from tortoise import Tortoise, connections  # noqa: E402

from common.auth import validate_password_policy  # noqa: E402
from common.exception_handler import CustomException  # noqa: E402
from models import User  # noqa: E402

# api 包必须在任何 Tortoise.init 之前导入，与 main.py 的生产导入顺序
# 一致（api.algorithm 的 create_model 依赖"未初始化时 FK 不进 pydantic
# 字段"的行为）。
from api import Account, register  # noqa: E402


class RegisterUniqueConstraintTests(unittest.IsolatedAsyncioTestCase):
    """🔴#2 回归：username 唯一索引 + 并发注册竞态的 IntegrityError 兜底。"""

    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        # addAsyncCleanup 即使后续 setup 步骤失败也会执行；
        # aiosqlite 工作线程非 daemon，泄漏连接会让 pytest 无法退出。
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        self.account_model = Account
        self.register = register

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_duplicate_precheck_returns_friendly_error(self):
        first = await self.register(
            self.account_model(username="race-user", password="pass123456")
        )
        self.assertEqual(first.code, "200")
        with self.assertRaises(CustomException) as ctx:
            await self.register(
                self.account_model(username="race-user", password="12345678")
            )
        self.assertIn("已存在", str(ctx.exception))
        self.assertEqual(await User.filter(username="race-user").count(), 1)

    async def test_race_bypassing_precheck_hits_unique_index(self):
        # 模拟竞态：两个并发请求都通过了"先查后建"预检（get_or_none
        # 均返回 None），第二个写入必须被唯一索引拦截并转成友好错误，
        # 而不是产生重复行或冒泡为系统错误。
        await self.register(
            self.account_model(username="race-user-2", password="pass123456")
        )

        async def _no_existing(*args, **kwargs):
            return None

        with mock.patch.object(User, "get_or_none", _no_existing):
            with self.assertRaises(CustomException) as ctx:
                await self.register(
                    self.account_model(
                        username="race-user-2",
                        password="12345678",
                    )
                )
        self.assertIn("已存在", str(ctx.exception))
        self.assertEqual(await User.filter(username="race-user-2").count(), 1)

    async def test_unique_index_exists_in_schema(self):
        # ORM 侧声明与 DB 侧迁移（011）必须一致；schema 由声明生成，
        # 此处同时守住"未来有人移除 unique=True"的回归。
        described = User.describe()
        unique_fields = {
            field["name"]
            for field in described["data_fields"]
            if field.get("unique")
        }
        self.assertIn("username", unique_fields)


class PasswordPolicyTests(unittest.TestCase):
    """🟡#10 回归：密码长度策略（8 位下限 / 72 字节 bcrypt 上限）。"""

    def test_short_password_rejected(self):
        with self.assertRaises(CustomException) as ctx:
            validate_password_policy("abc123")
        self.assertIn("不能少于", str(ctx.exception))

    def test_oversize_password_rejected(self):
        with self.assertRaises(CustomException) as ctx:
            validate_password_policy("x" * 73)
        self.assertIn("不能超过", str(ctx.exception))

    def test_valid_password_accepted(self):
        validate_password_policy("reasonable-pass-1")

    def test_register_applies_policy_before_database(self):
        async def call_register():
            await register(
                Account(username="policy-user", password="abc123")
            )

        with self.assertRaises(CustomException) as ctx:
            asyncio.run(call_register())
        self.assertIn("不能少于", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
