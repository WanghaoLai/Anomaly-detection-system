"""持久化自主注册限流；使用独立命名空间复用登录限流表。"""

import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from tortoise.exceptions import IntegrityError, OperationalError
from tortoise.transactions import in_transaction

from models import LoginThrottle
from settings import (
    JWT_SECRET_KEY,
    REGISTRATION_RATE_LIMIT_ATTEMPTS,
    REGISTRATION_RATE_LIMIT_WINDOW_SECONDS,
)


class RegistrationRateLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(client_ip: str) -> str:
        return hmac.new(
            JWT_SECRET_KEY.encode("utf-8"),
            f"registration-source:{client_ip}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    async def check(self, client_ip: str) -> None:
        record = await LoginThrottle.get_or_none(key=self._key(client_ip))
        if record is None:
            return
        now = datetime.now(timezone.utc)
        started = record.window_started
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if (
            now - started < timedelta(seconds=REGISTRATION_RATE_LIMIT_WINDOW_SECONDS)
            and record.failures >= REGISTRATION_RATE_LIMIT_ATTEMPTS
        ):
            retry_after = max(
                1,
                int(
                    REGISTRATION_RATE_LIMIT_WINDOW_SECONDS
                    - (now - started).total_seconds()
                ),
            )
            raise HTTPException(
                status_code=429,
                detail="注册请求过于频繁，请稍后再试",
                headers={"Retry-After": str(retry_after)},
            )

    async def record_registration(self, client_ip: str) -> None:
        key = self._key(client_ip)
        now = datetime.now(timezone.utc)
        window = timedelta(seconds=REGISTRATION_RATE_LIMIT_WINDOW_SECONDS)
        async with self._lock:
            for attempt in range(3):
                try:
                    async with in_transaction() as connection:
                        record = await LoginThrottle.filter(key=key).using_db(
                            connection
                        ).select_for_update().first()
                        started = record.window_started if record else None
                        if started is not None and started.tzinfo is None:
                            started = started.replace(tzinfo=timezone.utc)
                        if record is None or started is None or now - started >= window:
                            count, started = 1, now
                        else:
                            count = record.failures + 1
                        if record is None:
                            await LoginThrottle.create(
                                key=key,
                                failures=count,
                                window_started=started,
                                using_db=connection,
                            )
                        else:
                            await LoginThrottle.filter(key=key).using_db(
                                connection
                            ).update(failures=count, window_started=started)
                    return
                except (IntegrityError, OperationalError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0)


registration_rate_limiter = RegistrationRateLimiter()
