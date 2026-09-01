"""聊天 SSE 事件编码和公开错误状态。"""

from __future__ import annotations

import asyncio
import json


PUBLIC_FAILURE_MESSAGES = {
    "request_deadline_exceeded": "本次请求处理超时，未经完整校验的回答未发布，请稍后重试。",
    "llm_timeout": "模型响应超时，请稍后重试。",
    "generation_failed": "模型生成失败，请稍后重试。",
    "llm_protocol_error": "模型返回格式异常，本次回答未发布。",
    "llm_circuit_open": "模型服务暂时不可用，请稍后重试。",
    "stream_disconnected": "连接已断开，生成已终止。",
}


def encode_sse(payload: dict, *, event: str = "message") -> str:
    safe_event = event if event in {"status", "content", "done"} else "message"
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {safe_event}\ndata: {data}\n\n"


async def iter_until_disconnected(events, is_disconnected, poll_seconds: float = 0.2):
    """在下一个生成事件到达前也持续观察 SSE 断连，并取消上游任务。"""

    iterator = events.__aiter__()
    while True:
        next_event = asyncio.create_task(anext(iterator))
        disconnected = asyncio.create_task(_wait_disconnected(
            is_disconnected, poll_seconds
        ))
        done, pending = await asyncio.wait(
            {next_event, disconnected}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if disconnected in done and disconnected.result():
            next_event.cancel()
            try:
                await next_event
            except (asyncio.CancelledError, StopAsyncIteration):
                pass
            raise asyncio.CancelledError("SSE client disconnected")
        try:
            yield next_event.result()
        except StopAsyncIteration:
            return


async def _wait_disconnected(is_disconnected, poll_seconds: float) -> bool:
    while True:
        if await is_disconnected():
            return True
        await asyncio.sleep(poll_seconds)


__all__ = ["PUBLIC_FAILURE_MESSAGES", "encode_sse", "iter_until_disconnected"]
