import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common.registration_rate_limiter import RegistrationRateLimiter  # noqa: E402
from main import app  # noqa: E402
from models import LoginThrottle, User  # noqa: E402
from tortoise import Tortoise, connections  # noqa: E402


class RegistrationRateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["models"]})
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_successful_registrations_from_one_source_are_bounded(self):
        limiter = RegistrationRateLimiter()
        with mock.patch(
            "common.registration_rate_limiter.REGISTRATION_RATE_LIMIT_ATTEMPTS", 2
        ):
            await limiter.record_registration("203.0.113.8")
            await limiter.record_registration("203.0.113.8")
            with self.assertRaises(HTTPException) as ctx:
                await limiter.check("203.0.113.8")

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(await LoginThrottle.all().count(), 1)

    async def test_concurrent_attempts_cannot_exceed_the_limit(self):
        limiter = RegistrationRateLimiter()
        with mock.patch(
            "common.registration_rate_limiter.REGISTRATION_RATE_LIMIT_ATTEMPTS", 2
        ):
            outcomes = await asyncio.gather(
                *(limiter.consume("203.0.113.9") for _ in range(5)),
                return_exceptions=True,
            )
        self.assertEqual(sum(result is None for result in outcomes), 2)
        self.assertEqual(
            sum(isinstance(result, HTTPException) and result.status_code == 429
                for result in outcomes), 3,
        )

    async def test_failed_registration_also_uses_an_attempt(self):
        await User.create(username="taken", password="x", role="用户")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            with (
                mock.patch("api.is_registration_enabled", new=mock.AsyncMock(return_value=True)),
                mock.patch("common.registration_rate_limiter.REGISTRATION_RATE_LIMIT_ATTEMPTS", 1),
            ):
                first = await client.post("/api/register", json={
                    "username": "taken", "password": "safe-pass-123"
                })
                second = await client.post("/api/register", json={
                    "username": "another", "password": "safe-pass-123"
                })
        self.assertEqual(first.status_code, 400)
        self.assertEqual(second.status_code, 429)
        self.assertFalse(await User.filter(username="another").exists())


if __name__ == "__main__":
    unittest.main()
