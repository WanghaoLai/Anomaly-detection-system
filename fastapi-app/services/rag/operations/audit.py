"""RAG MySQL 审计写入；审计故障不得改变用户回答。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from settings import AI_CONFIG


logger = logging.getLogger(__name__)


class RagAuditRecorder:
    _MERGED_JSON_FIELDS = frozenset({
        "retrieval_config",
        "candidate_counts",
        "stage_durations_ms",
        "token_usage",
    })

    def __init__(self, enabled: bool | None = None):
        self.enabled = bool(
            AI_CONFIG.get("rag_audit_enabled", True)
            if enabled is None else enabled
        )

    async def record(self, payload: dict) -> str | None:
        if not self.enabled:
            return None
        trace_id = str(payload.get("id") or uuid.uuid4())
        try:
            # 惰性导入避免纯 RAG 算法测试被 ORM 初始化绑定。
            from tortoise import Tortoise
            from models import RagRetrievalTrace

            if not Tortoise._inited:
                return None

            defaults = {
                key: value for key, value in payload.items() if key != "id"
            }
            if await RagRetrievalTrace.filter(id=trace_id).exists():
                await self.update(trace_id, **defaults)
            else:
                await RagRetrievalTrace.create(
                    id=trace_id,
                    completed_at=datetime.now(timezone.utc),
                    **defaults,
                )
            return trace_id
        except Exception:
            logger.exception("RAG 审计写入失败: trace_id=%s", trace_id)
            return None

    async def update(self, trace_id: str | None, **values) -> bool:
        if not self.enabled or not trace_id:
            return False
        try:
            from tortoise import Tortoise
            from models import RagRetrievalTrace

            if not Tortoise._inited:
                return False

            json_updates = {
                key: values.pop(key)
                for key in tuple(values)
                if key in self._MERGED_JSON_FIELDS
                and isinstance(values.get(key), dict)
            }
            if json_updates:
                row = await RagRetrievalTrace.filter(id=trace_id).first()
                if row is None:
                    return False
                for key, patch in json_updates.items():
                    current = getattr(row, key, None)
                    values[key] = {
                        **(current if isinstance(current, dict) else {}),
                        **patch,
                    }
            updated = await RagRetrievalTrace.filter(id=trace_id).update(
                completed_at=datetime.now(timezone.utc),
                **values,
            )
            return updated == 1
        except Exception:
            logger.exception("RAG 审计更新失败: trace_id=%s", trace_id)
            return False


__all__ = ["RagAuditRecorder"]
