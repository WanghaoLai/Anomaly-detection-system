"""Cross-Encoder 精排端口与可选本地适配器。"""

from __future__ import annotations

import asyncio
import logging
import threading
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
        model=None,
    ) -> None:
        self.model_name = str(model_name or "")
        self.enabled = bool(enabled and self.model_name)
        self.timeout_seconds = float(timeout_seconds)
        self._model = model
        self._load_lock = threading.Lock()

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
            self._model = CrossEncoder(self.model_name)
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
        if not self.enabled or not original:
            return original[:top_k], {
                "mode": "rrf",
                "model": None,
                "fallback": False,
            }
        try:
            ranked = await asyncio.wait_for(
                asyncio.to_thread(self._predict, query, original),
                timeout=self.timeout_seconds,
            )
            return ranked[:top_k], {
                "mode": "cross_encoder",
                "model": self.model_name,
                "fallback": False,
            }
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Cross-Encoder 失败，已回退 RRF", exc_info=True)
            return original[:top_k], {
                "mode": "rrf_fallback",
                "model": self.model_name,
                "fallback": True,
            }


__all__ = ["CrossEncoderReranker"]
