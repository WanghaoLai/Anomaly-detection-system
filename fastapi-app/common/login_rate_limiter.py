"""持久化登录限流：同时约束来源+账号组合和目标账号。"""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac

from fastapi import HTTPException

from models import LoginThrottle
from settings import (
    LOGIN_RATE_LIMIT_ATTEMPTS,
    LOGIN_RATE_LIMIT_LOCK_SECONDS,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    JWT_SECRET_KEY,
)


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
        # 全局锁只串行化同一对 key 的读改写（单进程假设；多 worker 部署
        # 时窗口计数可能被分摊，需另行评估）。两个 key 的落库并行执行，
        # 锁持有时间从两次串行 DB 往返压缩为一次。
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
        record = await LoginThrottle.get_or_none(key=key)
        window_started = _aware(record.window_started) if record else None
        if record is None or window_started is None or now - window_started >= window:
            failures = 1
            window_started = now
        else:
            failures = record.failures + 1

        locked_until = (
            now + lock_duration
            if failures >= LOGIN_RATE_LIMIT_ATTEMPTS
            else None
        )
        await LoginThrottle.update_or_create(
            key=key,
            defaults={
                "failures": failures,
                "window_started": window_started,
                "locked_until": locked_until,
            },
        )

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
