"""进程级共享资源容量门，生产部署的单 worker 由测试与 service 文件约束。"""

import asyncio

from fastapi import HTTPException

from settings import AI_CONFIG


class AsyncCapacityLimiter:
    def __init__(self, capacity: int, label: str) -> None:
        if capacity <= 0:
            raise ValueError("共享资源并发上限必须大于 0")
        self.capacity = capacity
        self.label = label
        self._semaphore = asyncio.BoundedSemaphore(capacity)

    async def acquire(self) -> None:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=0.05)
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=429,
                detail=f"{self.label}当前请求较多，请稍后再试",
                headers={"Retry-After": "2"},
            ) from exc

    def release(self) -> None:
        self._semaphore.release()


llm_capacity_limiter = AsyncCapacityLimiter(
    int(AI_CONFIG["max_concurrent_requests"]),
    "智能问答",
)
