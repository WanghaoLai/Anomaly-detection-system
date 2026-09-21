import sys
import unittest
from pathlib import Path
from unittest import mock

from httpx import ASGITransport, AsyncClient


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common import auth  # noqa: E402
from common.auth import hash_password, is_bcrypt_hash, verify_password  # noqa: E402
from main import app  # noqa: E402
from migrate_passwords import migrate_legacy_passwords  # noqa: E402
from models import Admin, User  # noqa: E402
from tortoise import Tortoise, connections  # noqa: E402


class PasswordMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        await Admin.create(username="legacy-admin", password="admin-secret", role="管理员")
        await User.create(username="legacy-user", password="user-secret", role="用户")

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_apply_hashes_all_legacy_passwords_without_changing_secrets(self):
        found, migrated = await migrate_legacy_passwords(apply=True)
        self.assertEqual((found, migrated), (2, 2))
        admin = await Admin.get(username="legacy-admin")
        user = await User.get(username="legacy-user")
        self.assertTrue(is_bcrypt_hash(admin.password))
        self.assertTrue(is_bcrypt_hash(user.password))
        self.assertTrue(verify_password("admin-secret", admin.password)[0])
        self.assertTrue(verify_password("user-secret", user.password)[0])

    async def test_closed_compatibility_rejects_startup_with_legacy_rows(self):
        with mock.patch.object(auth, "ALLOW_LEGACY_PLAINTEXT_PASSWORDS", False):
            with self.assertRaises(RuntimeError) as ctx:
                await auth.validate_password_storage()
        self.assertIn("2 个", str(ctx.exception))

    def test_plaintext_login_can_be_disabled(self):
        with mock.patch.object(auth, "ALLOW_LEGACY_PLAINTEXT_PASSWORDS", False):
            self.assertEqual(verify_password("secret123", "secret123"), (False, False))

    async def test_legacy_login_cannot_restore_password_after_concurrent_reset(self):
        original_filter = User.filter
        replacement = hash_password("replacement-secret")

        class RacingQuery:
            async def update(self, **values):
                await original_filter(username="legacy-user").update(
                    password=replacement, token_version=1
                )
                return await original_filter(**match).update(**values)

        def racing_filter(**kwargs):
            nonlocal match
            match = kwargs
            return RacingQuery()

        match = {}
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            with (
                mock.patch.object(auth, "ALLOW_LEGACY_PLAINTEXT_PASSWORDS", True),
                mock.patch.object(User, "filter", side_effect=racing_filter),
            ):
                response = await client.post("/api/login", json={
                    "username": "legacy-user",
                    "password": "user-secret",
                    "role": "用户",
                })

        self.assertEqual(response.status_code, 401)
        account = await User.get(username="legacy-user")
        self.assertEqual(account.password, replacement)
        self.assertFalse(verify_password("user-secret", account.password)[0])


if __name__ == "__main__":
    unittest.main()
