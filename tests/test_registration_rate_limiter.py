import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException


BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common.registration_rate_limiter import RegistrationRateLimiter  # noqa: E402
from models import LoginThrottle  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
