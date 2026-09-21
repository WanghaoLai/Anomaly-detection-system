import sys
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common import auth  # noqa: E402
from common.auth import is_bcrypt_hash, verify_password  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
