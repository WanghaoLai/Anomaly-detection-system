"""Phase 5 RAG 只读观测接口（仅管理员）。"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from common.auth import get_current_admin
from common.result import Result
from models import RagRetrievalTrace
from services.rag.operations.metrics import aggregate_trace_metrics


router = APIRouter(
    prefix="/admin/rag-observability",
    dependencies=[Depends(get_current_admin)],
)


@router.get("/metrics")
async def rag_observability_metrics(
    days: int = Query(default=7, ge=1, le=90),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = await RagRetrievalTrace.filter(created_at__gte=since).values(
        "status",
        "error_code",
        "mode",
        "retrieval_config",
        "candidate_counts",
        "stage_durations_ms",
        "token_usage",
    )
    return Result.success(aggregate_trace_metrics(rows, window_days=days))


__all__ = ["rag_observability_metrics", "router"]
