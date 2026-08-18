"""模型无关的生成结果、错误语义和熔断策略。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


class LLMError(RuntimeError):
    code = "llm_error"


class LLMTimeoutError(LLMError):
    code = "llm_timeout"


class LLMGenerationError(LLMError):
    code = "generation_failed"

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMProtocolError(LLMError):
    code = "llm_protocol_error"


class LLMCircuitOpenError(LLMError):
    code = "llm_circuit_open"


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    request_id: str | None
    usage: dict
    latency_ms: float


class CircuitBreaker:
    """进程内 CLOSED/OPEN/HALF_OPEN 熔断器，不影响取消传播。"""

    def __init__(self, failure_threshold: int, recovery_seconds: float):
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_seconds = max(0.1, float(recovery_seconds))
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_inflight = False
        self._lock = threading.Lock()

    def before_call(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            if time.monotonic() - self._opened_at < self.recovery_seconds:
                raise LLMCircuitOpenError("模型熔断器已打开")
            if self._half_open_inflight:
                raise LLMCircuitOpenError("模型熔断器正在半开探测")
            self._half_open_inflight = True

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_inflight = False

    def failure(self) -> None:
        with self._lock:
            self._half_open_inflight = False
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()

    def cancelled(self) -> None:
        with self._lock:
            self._half_open_inflight = False


__all__ = [
    "CircuitBreaker",
    "LLMCircuitOpenError",
    "LLMError",
    "LLMGenerationError",
    "LLMProtocolError",
    "LLMResult",
    "LLMTimeoutError",
]
