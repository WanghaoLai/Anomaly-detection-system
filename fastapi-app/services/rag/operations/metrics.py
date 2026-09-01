"""从持久 Trace 派生 Phase 5 只读观测指标。"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime


TERMINAL_STATUSES = frozenset({"completed", "refused", "failed"})


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 1)


def aggregate_trace_metrics(records: list[dict], *, window_days: int) -> dict:
    """只聚合状态和数值字段，不返回问题、上下文或候选正文。"""
    valid = [row for row in records if row.get("status") in TERMINAL_STATUSES]
    statuses = Counter(str(row.get("status") or "unknown") for row in valid)
    errors = Counter(
        str(row.get("error_code"))
        for row in valid if row.get("error_code")
    )
    modes = Counter(str(row.get("mode") or "unknown") for row in valid)
    degradations = Counter()
    durations: dict[str, list[float]] = defaultdict(list)
    numeric_usage: dict[str, list[float]] = defaultdict(list)
    grounding_totals = Counter()
    empty_retrievals = 0
    for row in valid:
        config = row.get("retrieval_config") or {}
        selection_mode = config.get("selection_mode")
        if selection_mode in {
            "dense_fallback", "bm25_fallback", "retrieval_unavailable"
        }:
            degradations[str(selection_mode)] += 1
        if config.get("rerank_fallback"):
            degradations["reranker_fallback"] += 1
        counts = row.get("candidate_counts") or {}
        if (
            row.get("mode") == "knowledge_base"
            and isinstance(counts, dict)
            and counts.get("packed") == 0
        ):
            empty_retrievals += 1
        for name, value in (row.get("stage_durations_ms") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                durations[str(name)].append(float(value))
        usage = row.get("token_usage") or {}
        for name, value in usage.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric_usage[str(name)].append(float(value))
        grounding = usage.get("grounding") if isinstance(usage, dict) else None
        if isinstance(grounding, dict):
            for name, value in grounding.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    grounding_totals[str(name)] += value

    count = len(valid)
    latency = {
        name: {
            "samples": len(values),
            "p50_ms": _percentile(values, 0.50),
            "p95_ms": _percentile(values, 0.95),
            "p99_ms": _percentile(values, 0.99),
            "max_ms": round(max(values), 1),
        }
        for name, values in sorted(durations.items())
    }
    return {
        "policy": "observe_only",
        "window_days": int(window_days),
        "valid_requests": count,
        "status_counts": dict(statuses),
        "mode_counts": dict(modes),
        "mode_rates": {
            name: round(value / count, 4) if count else None
            for name, value in modes.items()
        },
        "error_counts": dict(errors),
        "degradation_counts": dict(degradations),
        "rates": {
            "failure": round(statuses["failed"] / count, 4) if count else None,
            "refusal": round(statuses["refused"] / count, 4) if count else None,
            "deadline_exceeded": round(
                errors["request_deadline_exceeded"] / count, 4
            ) if count else None,
            "empty_retrieval": round(empty_retrievals / count, 4)
            if count else None,
        },
        "throughput": {
            "average_requests_per_day": round(count / window_days, 2),
            "average_qps": round(count / (window_days * 86400), 6),
        },
        "latency": latency,
        "token_usage": {
            name: {
                "samples": len(values),
                "average": round(sum(values) / len(values), 1),
                "p95": _percentile(values, 0.95),
            }
            for name, values in sorted(numeric_usage.items())
        },
        "grounding_totals": dict(grounding_totals),
        "hard_slo_review_readiness": {
            "required_window_days": 7,
            "required_valid_requests": 500,
            "window_satisfied": window_days >= 7,
            "volume_satisfied": count >= 500,
            "ready_for_human_review": window_days >= 7 and count >= 500,
        },
        "generated_at": datetime.now().astimezone().isoformat(),
    }


__all__ = ["TERMINAL_STATUSES", "aggregate_trace_metrics"]
