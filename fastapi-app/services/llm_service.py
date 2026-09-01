"""DashScope Qwen 生成适配器，提供统一超时与失败语义。"""

from __future__ import annotations

import asyncio
import random
import time

import httpx

from settings import AI_CONFIG
from .rag.answering.llm_types import (
    CircuitBreaker,
    LLMCircuitOpenError,
    LLMError,
    LLMGenerationError,
    LLMProtocolError,
    LLMResult,
    LLMTimeoutError,
)


class LLMService:
    provider = "dashscope"

    def __init__(
        self,
        api_key: str,
        model: str = "qwen-turbo",
        timeout_seconds: float | None = None,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.model = model
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else AI_CONFIG.get("llm_timeout_seconds", 45.0)
        )
        if self.timeout_seconds <= 0:
            raise ValueError("LLM timeout_seconds 必须大于 0")
        self.api_key = api_key
        self.base_url = str(
            base_url
            or AI_CONFIG.get("dashscope_compatible_base_url")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.max_retries = max(0, int(AI_CONFIG.get("llm_max_retries", 2)))
        self.retry_backoff_seconds = max(
            0.0, float(AI_CONFIG.get("llm_retry_backoff_seconds", 0.4))
        )
        self._http_client = http_client
        self._owns_http_client = http_client is None
        self.circuit_breaker = CircuitBreaker(
            AI_CONFIG.get("llm_circuit_failure_threshold", 5),
            AI_CONFIG.get("llm_circuit_recovery_seconds", 30.0),
        )

    @staticmethod
    def _messages(messages: list, system_prompt: str | None) -> list:
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        return full_messages

    async def _call_result(
        self,
        messages: list,
        system_prompt: str | None,
        *,
        structured: bool,
    ) -> LLMResult:
        started_at = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": self._messages(messages, system_prompt),
            "stream": False,
        }
        if structured:
            payload["response_format"] = {"type": "json_object"}
        self.circuit_breaker.before_call()
        deadline = time.monotonic() + self.timeout_seconds
        response = None
        try:
            for attempt in range(self.max_retries + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMTimeoutError(
                        f"Qwen 生成超过 {self.timeout_seconds:g} 秒"
                    )
                try:
                    response = await asyncio.wait_for(
                        self._post(payload), timeout=remaining
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except (TimeoutError, httpx.TimeoutException) as exc:
                    if attempt >= self.max_retries or deadline <= time.monotonic():
                        raise LLMTimeoutError(
                            f"Qwen HTTP 请求超过 {self.timeout_seconds:g} 秒"
                        ) from exc
                except LLMGenerationError as exc:
                    if not exc.retryable or attempt >= self.max_retries:
                        raise
                if self.retry_backoff_seconds:
                    delay = self.retry_backoff_seconds * (2 ** attempt)
                    delay += random.uniform(0.0, delay * 0.2)
                    await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        except asyncio.CancelledError:
            self.circuit_breaker.cancelled()
            raise
        except LLMError:
            self.circuit_breaker.failure()
            raise
        except Exception as exc:
            self.circuit_breaker.failure()
            raise LLMGenerationError(
                "Qwen 异步 HTTP 调用失败", retryable=True
            ) from exc
        self.circuit_breaker.success()
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProtocolError("Qwen HTTP 响应缺少 choices.message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMProtocolError("Qwen HTTP 返回空内容")
        usage = response.get("usage") if isinstance(response, dict) else None
        return LLMResult(
            text=content,
            model=str(response.get("model") or self.model),
            request_id=str(
                response.get("request_id") or response.get("id") or ""
            ) or None,
            usage=dict(usage) if isinstance(usage, dict) else {},
            latency_ms=round((time.perf_counter() - started_at) * 1000, 1),
        )

    async def _call(
        self,
        messages: list,
        system_prompt: str | None,
        *,
        structured: bool,
    ) -> str:
        return (await self._call_result(
            messages, system_prompt, structured=structured
        )).text

    async def _post(self, payload: dict) -> dict:
        timeout = httpx.Timeout(self.timeout_seconds)
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=100, max_keepalive_connections=20
                ),
            )
        response = await self._http_client.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code != 200:
            request_id = response.headers.get("x-request-id", "")
            try:
                detail = response.json().get("error", {}).get("message")
            except (TypeError, ValueError):
                detail = None
            raise LLMGenerationError(
                f"Qwen HTTP 返回失败: status={response.status_code}, "
                f"message={detail or '未知错误'}"
                + (f", request_id={request_id}" if request_id else ""),
                retryable=(response.status_code in {408, 409, 429}
                           or response.status_code >= 500),
            )
        try:
            value = response.json()
        except ValueError as exc:
            raise LLMProtocolError("Qwen HTTP 返回非 JSON") from exc
        if not isinstance(value, dict):
            raise LLMProtocolError("Qwen HTTP 返回结构无效")
        return value

    async def aclose(self) -> None:
        if self._owns_http_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def chat(self, messages: list, system_prompt: str = None) -> str:
        return await self._call(messages, system_prompt, structured=False)

    async def chat_with_metadata(
        self, messages: list, system_prompt: str = None
    ) -> LLMResult:
        return await self._call_result(messages, system_prompt, structured=False)

    async def chat_structured(
        self,
        messages: list,
        system_prompt: str = None,
    ) -> str:
        return await self._call(messages, system_prompt, structured=True)

    async def chat_structured_with_metadata(
        self, messages: list, system_prompt: str = None
    ) -> LLMResult:
        return await self._call_result(messages, system_prompt, structured=True)

    async def chat_stream(self, messages: list, system_prompt: str = None):
        """完整生成成功后再分块，避免超时留下未经验证的半句回答。"""

        content = await self.chat(messages, system_prompt)
        for start in range(0, len(content), 96):
            yield content[start:start + 96]


__all__ = [
    "LLMError",
    "LLMGenerationError",
    "LLMProtocolError",
    "LLMCircuitOpenError",
    "LLMResult",
    "LLMService",
    "LLMTimeoutError",
]
