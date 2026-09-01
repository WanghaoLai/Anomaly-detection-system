"""Cross-Encoder 精排端口与可选本地适配器。"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
import time
from typing import Sequence


logger = logging.getLogger(__name__)


class CrossEncoderReranker:
    """
    懒加载 sentence-transformers CrossEncoder。依赖或模型不可用时
    fail-closed 为“不改变传入排序”，不影响 Dense/BM25/RRF 主链路。
    """

    def __init__(
        self,
        *,
        model_name: str,
        enabled: bool,
        timeout_seconds: float,
        max_length: int | None = None,
        model=None,
    ) -> None:
        self.model_name = str(model_name or "")
        self.enabled = bool(enabled and self.model_name)
        self.timeout_seconds = float(timeout_seconds)
        self.max_length = (
            int(max_length) if max_length is not None and int(max_length) > 0
            else None
        )
        self._model = model
        self._load_lock = threading.Lock()
        self._submit_lock = threading.Lock()
        self._active_future: concurrent.futures.Future | None = None
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="rag-cross-encoder",
        )

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RuntimeError(
                    "Cross-Encoder 未安装；请安装并预下载已审核模型"
                ) from exc
            kwargs = (
                {"max_length": self.max_length}
                if self.max_length is not None else {}
            )
            self._model = CrossEncoder(self.model_name, **kwargs)
            return self._model

    @staticmethod
    def _document(candidate: dict) -> str:
        heading = candidate.get("section_path") or candidate.get("heading_path") or ""
        return f"{heading}\n{candidate.get('content') or ''}".strip()

    def _predict(self, query: str, candidates: Sequence[dict]) -> list[dict]:
        model = self._get_model()
        values = model.predict([
            [query, self._document(candidate)] for candidate in candidates
        ])
        scored = []
        for candidate, value in zip(candidates, values):
            item = dict(candidate)
            item["rerank_score"] = float(value)
            scored.append(item)
        return sorted(
            scored, key=lambda item: item["rerank_score"], reverse=True
        )

    async def rerank(
        self,
        query: str,
        candidates: Sequence[dict],
        *,
        top_k: int,
    ) -> tuple[list[dict], dict]:
        original = [dict(item) for item in candidates]
        started_at = time.perf_counter()
        if not self.enabled or not original:
            return original[:top_k], {
                "mode": "rrf",
                "model": None,
                "fallback": False,
                "fallback_reason": None,
                "input_count": len(original),
                "output_count": min(len(original), top_k),
                "elapsed_ms": 0.0,
                "max_length": self.max_length,
            }
        with self._submit_lock:
            if self._active_future is not None and not self._active_future.done():
                results = original[:top_k]
                return results, {
                    "mode": "rrf_fallback",
                    "model": self.model_name,
                    "fallback": True,
                    "fallback_reason": "busy_after_timeout",
                    "input_count": len(original),
                    "output_count": len(results),
                    "elapsed_ms": round(
                        (time.perf_counter() - started_at) * 1000, 2
                    ),
                    "max_length": self.max_length,
                }
            self._active_future = self._executor.submit(
                self._predict, query, original
            )
            active_future = self._active_future
        try:
            ranked = await asyncio.wait_for(
                asyncio.wrap_future(active_future),
                timeout=self.timeout_seconds,
            )
            results = ranked[:top_k]
            return results, {
                "mode": "cross_encoder",
                "model": self.model_name,
                "fallback": False,
                "fallback_reason": None,
                "input_count": len(original),
                "output_count": len(results),
                "elapsed_ms": round(
                    (time.perf_counter() - started_at) * 1000, 2
                ),
                "max_length": self.max_length,
            }
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.warning("Cross-Encoder 超时，已回退 RRF")
            results = original[:top_k]
            return results, {
                "mode": "rrf_fallback",
                "model": self.model_name,
                "fallback": True,
                "fallback_reason": "timeout",
                "input_count": len(original),
                "output_count": len(results),
                "elapsed_ms": round(
                    (time.perf_counter() - started_at) * 1000, 2
                ),
                "max_length": self.max_length,
            }
        except Exception as exc:
            logger.warning("Cross-Encoder 失败，已回退 RRF", exc_info=True)
            results = original[:top_k]
            return results, {
                "mode": "rrf_fallback",
                "model": self.model_name,
                "fallback": True,
                "fallback_reason": type(exc).__name__,
                "input_count": len(original),
                "output_count": len(results),
                "elapsed_ms": round(
                    (time.perf_counter() - started_at) * 1000, 2
                ),
                "max_length": self.max_length,
            }


__all__ = ["CrossEncoderReranker"]
