"""DashScope embedding 基础设施适配器。"""

from __future__ import annotations

import asyncio
import math
import threading
import time
from typing import Sequence

from llama_index.core.base.embeddings.base import BaseEmbedding
from pydantic import PrivateAttr


class LlamaIndexEmbeddingAdapter(BaseEmbedding):
    """将框架无关 EmbeddingModel 接入 LlamaIndex。

    LlamaIndex 负责批处理、回调和异步调度；底层 provider 仍由
    ``EmbeddingModel`` 端口注入，因此 DashScope 可被本地或其他模型替换。
    """

    _backend: object = PrivateAttr()

    def __init__(self, backend, *, model_name: str, embed_batch_size: int):
        super().__init__(
            model_name=model_name,
            embed_batch_size=int(embed_batch_size),
        )
        self._backend = backend

    def _get_query_embedding(self, query: str) -> list[float]:
        return list(self._backend.embed_query(query))

    async def _aget_query_embedding(self, query: str) -> list[float]:
        method = getattr(self._backend, "aembed_query", None)
        if method is not None:
            return list(await method(query))
        return list(await asyncio.to_thread(self._backend.embed_query, query))

    def _get_text_embedding(self, text: str) -> list[float]:
        return list(self._backend.embed_documents([text])[0])

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return [list(vector) for vector in self._backend.embed_documents(texts)]

    async def _aget_text_embedding(self, text: str) -> list[float]:
        values = await self._aget_text_embeddings([text])
        return values[0]

    async def _aget_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        method = getattr(self._backend, "aembed_documents", None)
        if method is not None:
            values = await method(texts)
        else:
            values = await asyncio.to_thread(self._backend.embed_documents, texts)
        return [list(vector) for vector in values]

    def get_query_embeddings(self, queries: Sequence[str]) -> list[list[float]]:
        """LlamaIndex 的 BaseEmbedding 只公开单查询入口，此处保留批量区分。"""

        return [list(vector) for vector in self._backend.embed_queries(queries)]

    def backend_metrics_snapshot(self) -> dict[str, int]:
        method = getattr(self._backend, "metrics_snapshot", None)
        return dict(method() if method is not None else {})


class DashScopeEmbeddingModel:
    provider = "dashscope"
    schema_version = "dashscope-text-embedding-v1"
    normalized = True

    def __init__(
        self,
        *,
        api,
        model: str,
        api_key: str,
        batch_size: int,
        max_retries: int,
        retry_backoff_seconds: float,
    ):
        self.api = api
        self.model = model
        self.api_key = api_key
        self.batch_size = int(batch_size)
        self.max_retries = int(max_retries)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.dimension: int | None = None
        self._metrics_lock = threading.Lock()
        self._api_calls_total = 0
        self._retry_count_total = 0

    def metrics_snapshot(self) -> dict[str, int]:
        with self._metrics_lock:
            return {
                "api_calls": self._api_calls_total,
                "retries": self._retry_count_total,
            }

    def _record_attempt(self, *, retry: bool) -> None:
        with self._metrics_lock:
            self._api_calls_total += 1
            if retry:
                self._retry_count_total += 1

    def _batch_limit(self) -> int:
        lowered = self.model.lower()
        return 10 if "text-embedding-v3" in lowered or "text-embedding-v4" in lowered else 25

    @staticmethod
    def _value(response, key: str, default=None):
        return response.get(key, default) if isinstance(response, dict) else getattr(response, key, default)

    def _call(self, batch: list[str], text_type: str):
        for attempt in range(self.max_retries + 1):
            self._record_attempt(retry=attempt > 0)
            try:
                response = self.api.call(
                    model=self.model,
                    input=batch,
                    api_key=self.api_key or None,
                    text_type=text_type,
                )
            except Exception as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"Embedding 网络调用失败（{text_type}，重试 {attempt} 次）"
                    ) from exc
                time.sleep(self.retry_backoff_seconds * (2 ** attempt))
                continue
            raw_status_code = self._value(response, "status_code")
            try:
                status_code = int(raw_status_code)
            except (TypeError, ValueError):
                status_code = 0
            if status_code == 200:
                return response
            retryable = status_code in {408, 409, 429} or status_code >= 500
            if attempt >= self.max_retries or not retryable:
                message = self._value(response, "message", "未知错误")
                request_id = self._value(response, "request_id", "")
                raise RuntimeError(
                    f"Embedding 调用失败: status={status_code}, message={message}"
                    + (f", request_id={request_id}" if request_id else "")
                )
            time.sleep(self.retry_backoff_seconds * (2 ** attempt))
        raise RuntimeError("Embedding API 调用失败")

    @staticmethod
    def _normalize(vector) -> list[float]:
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Embedding 返回了非数值向量") from exc
        if not values:
            raise RuntimeError("Embedding 返回了空向量")
        if not all(math.isfinite(value) for value in values):
            raise RuntimeError("Embedding 返回了包含 NaN/Infinity 的向量")
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0 or not math.isfinite(norm):
            raise RuntimeError("Embedding 返回了零范数向量")
        return [value / norm for value in values]

    def embed(self, texts: Sequence[str], *, text_type: str) -> list[list[float]]:
        if text_type not in {"document", "query"}:
            raise ValueError("text_type 必须为 document 或 query")
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding 输入必须是非空文本")
        embeddings: list[list[float]] = []
        observed_dim = None
        batch_size = min(self.batch_size, self._batch_limit())
        for index in range(0, len(texts), batch_size):
            batch = list(texts[index:index + batch_size])
            response = self._call(batch, text_type)
            output = self._value(response, "output")
            items = output.get("embeddings") if isinstance(output, dict) else None
            if not isinstance(items, (list, tuple)) or len(items) != len(batch):
                raise RuntimeError(
                    "Embedding 返回条数与请求条数不一致："
                    f"expected={len(batch)}, actual={len(items) if items is not None else 0}"
                )
            indexed_items = list(items)
            has_indexes = [isinstance(item, dict) and item.get("text_index") is not None for item in indexed_items]
            if any(has_indexes):
                if not all(has_indexes):
                    raise RuntimeError("Embedding 返回结果缺少部分 text_index")
                try:
                    indexed_items.sort(key=lambda item: int(item["text_index"]))
                except (KeyError, TypeError, ValueError) as exc:
                    raise RuntimeError("Embedding 返回了无效的 text_index") from exc
                indexes = [int(item["text_index"]) for item in indexed_items]
                if indexes != list(range(len(batch))):
                    raise RuntimeError(f"Embedding text_index 不完整或重复：{indexes}")
            for item in indexed_items:
                if not isinstance(item, dict) or "embedding" not in item:
                    raise RuntimeError("Embedding 返回项缺少 embedding 字段")
                vector = self._normalize(item["embedding"])
                if observed_dim is None:
                    observed_dim = len(vector)
                elif len(vector) != observed_dim:
                    raise RuntimeError(
                        f"同一批 Embedding 维度不一致：expected={observed_dim}, actual={len(vector)}"
                    )
                embeddings.append(vector)
        if observed_dim is not None:
            if self.dimension is not None and self.dimension != observed_dim:
                raise RuntimeError(
                    f"Embedding 维度发生变化：expected={self.dimension}, actual={observed_dim}"
                )
            self.dimension = observed_dim
        return embeddings

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self.embed(texts, text_type="document")

    def embed_query(self, query: str) -> list[float]:
        return self.embed([query], text_type="query")[0]

    def embed_queries(self, queries: Sequence[str]) -> list[list[float]]:
        return self.embed(queries, text_type="query")

    async def aembed_documents(
        self, texts: Sequence[str]
    ) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)

    async def aembed_query(self, query: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, query)


__all__ = ["DashScopeEmbeddingModel", "LlamaIndexEmbeddingAdapter"]
