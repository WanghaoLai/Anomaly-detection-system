import sys
import unittest
from pathlib import Path
from unittest import mock

from httpx import ASGITransport, AsyncClient
from tortoise import Tortoise, connections

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common.auth import get_current_admin, get_current_user, hash_password  # noqa: E402
from main import app  # noqa: E402
from models import Admin, RegistrationPolicy, User  # noqa: E402


class RegistrationPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()
        self.client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        )
        self.addAsyncCleanup(self.client.aclose)
        self.addCleanup(app.dependency_overrides.clear)

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_admin_toggle_controls_public_policy_and_registration(self):
        with mock.patch("common.registration_policy.SELF_REGISTRATION_ENABLED", False):
            public = await self.client.get("/api/registration-policy")
            self.assertEqual(public.status_code, 200)
            self.assertFalse(public.json()["data"]["enabled"])

            denied = await self.client.put(
                "/api/admin/registration-policy", json={"enabled": True}
            )
            self.assertEqual(denied.status_code, 401)

            app.dependency_overrides[get_current_user] = lambda: {
                "user_id": 2,
                "role": "用户",
            }
            forbidden = await self.client.put(
                "/api/admin/registration-policy", json={"enabled": True}
            )
            self.assertEqual(forbidden.status_code, 403)
            app.dependency_overrides.clear()

            app.dependency_overrides[get_current_admin] = lambda: {
                "user_id": 1,
                "role": "管理员",
            }
            enabled = await self.client.put(
                "/api/admin/registration-policy", json={"enabled": True}
            )
            self.assertEqual(enabled.status_code, 200)
            self.assertTrue(enabled.json()["data"]["enabled"])
            admin_view = await self.client.get("/api/admin/registration-policy")
            self.assertTrue(admin_view.json()["data"]["enabled"])
            self.assertTrue((await self.client.get("/api/registration-policy")).json()["data"]["enabled"])

            registered = await self.client.post(
                "/api/register",
                json={"username": "self-register", "password": "safe-pass-123", "role": "管理员"},
            )
            self.assertEqual(registered.status_code, 200)
            user = await User.get(username="self-register")
            self.assertEqual(user.role, "用户")

            disabled = await self.client.put(
                "/api/admin/registration-policy", json={"enabled": False}
            )
            self.assertEqual(disabled.status_code, 200)
            self.assertFalse((await self.client.get("/api/registration-policy")).json()["data"]["enabled"])
            blocked = await self.client.post(
                "/api/register",
                json={"username": "blocked", "password": "safe-pass-123"},
            )
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(await User.filter(username="blocked").count(), 0)
            self.assertEqual(await RegistrationPolicy.all().count(), 1)

        # 已保存的数据库设置在环境变量变化后仍是唯一生效值。
        with mock.patch("common.registration_policy.SELF_REGISTRATION_ENABLED", True):
            self.assertFalse((await self.client.get("/api/registration-policy")).json()["data"]["enabled"])

    async def test_admin_update_requires_boolean(self):
        app.dependency_overrides[get_current_admin] = lambda: {
            "user_id": 1,
            "role": "管理员",
        }
        response = await self.client.put(
            "/api/admin/registration-policy", json={"enabled": "true"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(await RegistrationPolicy.all().count(), 0)

    async def test_real_admin_cookie_requires_csrf_for_toggle(self):
        await Admin.create(
            username="policy-admin",
            password=hash_password("safe-pass-123"),
            role="管理员",
        )
        login = await self.client.post(
            "/api/login",
            json={
                "username": "policy-admin",
                "password": "safe-pass-123",
                "role": "管理员",
            },
        )
        self.assertEqual(login.status_code, 200)

        denied = await self.client.put(
            "/api/admin/registration-policy", json={"enabled": True}
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(await RegistrationPolicy.all().count(), 0)

        accepted = await self.client.put(
            "/api/admin/registration-policy",
            json={"enabled": True},
            headers={"X-CSRF-Token": login.json()["data"]["csrfToken"]},
        )
        self.assertEqual(accepted.status_code, 200)
        policy = await RegistrationPolicy.get(id=1)
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.updated_by, 1)
