import asyncio
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).parents[1] / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from common.login_rate_limiter import LoginRateLimiter
from models import LoginThrottle
from tortoise import Tortoise, connections


class LoginRateLimiterConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": ["models"]},
        )
        self.addAsyncCleanup(self._shutdown_db)
        await Tortoise.generate_schemas()

    @staticmethod
    async def _shutdown_db():
        await connections.close_all(discard=True)
        await Tortoise._reset_apps()

    async def test_two_workers_do_not_lose_failure_increments(self):
        workers = [LoginRateLimiter(), LoginRateLimiter()]
        await asyncio.gather(*(
            workers[index % 2].record_failure(
                "203.0.113.10",
                "test-user",
                "用户",
            )
            for index in range(6)
        ))

        keys = LoginRateLimiter._keys("203.0.113.10", "test-user", "用户")
        records = await LoginThrottle.filter(key__in=keys)
        self.assertEqual(len(records), 2)
        self.assertEqual({record.failures for record in records}, {6})

