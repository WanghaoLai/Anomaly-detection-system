"""持久化登录限流：同时约束来源+账号组合和目标账号。"""
import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from models import LoginThrottle
from settings import (
    JWT_SECRET_KEY,
    LOGIN_RATE_LIMIT_ATTEMPTS,
    LOGIN_RATE_LIMIT_LOCK_SECONDS,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS,
)
from tortoise.exceptions import IntegrityError, OperationalError
from tortoise.transactions import in_transaction


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class LoginRateLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @staticmethod
    def _keys(client_ip: str, username: str, role: str) -> tuple[str, str]:
        account = f"{role}:{username.strip().lower()}"

        def digest(scope: str, value: str) -> str:
            return hmac.new(
                JWT_SECRET_KEY.encode("utf-8"),
                f"{scope}:{value}".encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

        return (
            digest("source-account", f"{client_ip}:{account}"),
            digest("account", account),
        )

    async def check(self, client_ip: str, username: str, role: str) -> None:
        now = _utcnow()
        records = await LoginThrottle.filter(
            key__in=self._keys(client_ip, username, role)
        )
        retry_after = 0
        for record in records:
            locked_until = _aware(record.locked_until)
            if locked_until and locked_until > now:
                retry_after = max(
                    retry_after,
                    int((locked_until - now).total_seconds()) + 1,
                )
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail="登录尝试过于频繁，请稍后再试",
                headers={"Retry-After": str(retry_after)},
            )

    async def record_failure(
        self,
        client_ip: str,
        username: str,
        role: str,
    ) -> None:
        now = _utcnow()
        window = timedelta(seconds=LOGIN_RATE_LIMIT_WINDOW_SECONDS)
        lock_duration = timedelta(seconds=LOGIN_RATE_LIMIT_LOCK_SECONDS)
        # 进程内锁减少同一 worker 的事务冲突；数据库行锁和唯一键负责
        # 多 worker 间的准确计数。两个互不相同的 key 可以并行落库。
        async with self._lock:
            await asyncio.gather(*(
                self._record_key_failure(key, now, window, lock_duration)
                for key in self._keys(client_ip, username, role)
            ))

    @staticmethod
    async def _record_key_failure(
        key: str,
        now,
        window: timedelta,
        lock_duration: timedelta,
    ) -> None:
        # 新 key 的并发插入可能触发唯一键冲突或数据库死锁；短暂重试后，
        # 后续事务会锁住已存在的行并基于最新 failures 递增。
        for attempt in range(3):
            try:
                async with in_transaction() as connection:
                    record = await LoginThrottle.filter(key=key).using_db(
                        connection
                    ).select_for_update().first()
                    window_started = (
                        _aware(record.window_started) if record else None
                    )
                    if (
                        record is None
                        or window_started is None
                        or now - window_started >= window
                    ):
                        failures = 1
                        window_started = now
                    else:
                        failures = record.failures + 1

                    locked_until = (
                        now + lock_duration
                        if failures >= LOGIN_RATE_LIMIT_ATTEMPTS
                        else None
                    )
                    if record is None:
                        await LoginThrottle.create(
                            key=key,
                            failures=failures,
                            window_started=window_started,
                            locked_until=locked_until,
                            using_db=connection,
                        )
                    else:
                        await LoginThrottle.filter(key=key).using_db(
                            connection
                        ).update(
                            failures=failures,
                            window_started=window_started,
                            locked_until=locked_until,
                        )
                return
            except (IntegrityError, OperationalError):
                if attempt == 2:
                    raise
                await asyncio.sleep(0)

    async def record_success(
        self,
        client_ip: str,
        username: str,
        role: str,
    ) -> None:
        await LoginThrottle.filter(
            key__in=self._keys(client_ip, username, role)
        ).delete()


login_rate_limiter = LoginRateLimiter()
