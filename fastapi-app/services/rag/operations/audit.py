"""RAG MySQL 审计写入；审计故障不得改变用户回答。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from settings import AI_CONFIG


logger = logging.getLogger(__name__)


class RagAuditRecorder:
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
            from models import RagRetrievalTrace

            await RagRetrievalTrace.create(
                id=trace_id,
                completed_at=datetime.now(timezone.utc),
                **{key: value for key, value in payload.items() if key != "id"},
            )
            return trace_id
        except Exception:
            logger.exception("RAG 审计写入失败: trace_id=%s", trace_id)
            return None

    async def update(self, trace_id: str | None, **values) -> bool:
        if not self.enabled or not trace_id:
            return False
        try:
            from models import RagRetrievalTrace

            updated = await RagRetrievalTrace.filter(id=trace_id).update(
                completed_at=datetime.now(timezone.utc),
                **values,
            )
            return updated == 1
        except Exception:
            logger.exception("RAG 审计更新失败: trace_id=%s", trace_id)
            return False


__all__ = ["RagAuditRecorder"]
