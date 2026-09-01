"""Measure sequential end-to-end latency for a stratified Phase 0 sample.

The full evaluator runs generation concurrently, so its queue-inclusive batch
completion times are not user-request latency.  This probe runs one request at a
time through ``ChatService.answer`` and appends the reproducible sample to the
existing baseline artifacts without changing the active release.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "rag-phase0-latency-only-not-for-runtime-00000000000000000",
)

from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.llm_service import LLMService  # noqa: E402
from services.rag.operations.audit import RagAuditRecorder  # noqa: E402
from settings import AI_CONFIG  # noqa: E402
from rag_phase0_baseline import ensure_baseline_writable  # noqa: E402


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def stratified_sample(cases: list[dict], per_category: int = 2) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        grouped[str(case.get("category"))].append(case)
    sample = []
    for category in sorted(grouped):
        values = grouped[category]
        if len(values) <= per_category:
            sample.extend(values)
            continue
        # Use both ends of each category rather than only the first sections.
        indices = [round(i * (len(values) - 1) / (per_category - 1))
                   for i in range(per_category)]
        sample.extend(values[index] for index in indices)
    return sample


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 2)


async def run(args: argparse.Namespace) -> dict:
    dataset_path = Path(args.dataset).resolve()
    output = Path(args.output_dir).resolve()
    ensure_baseline_writable(
        output, allow_replace=args.replace_signed_baseline
    )
    dataset = _load(dataset_path)
    sample = stratified_sample(dataset["cases"], args.per_category)
    service = KnowledgeService()
    pointer_before = service.artifact_repository.releases.active()
    llm = LLMService(AI_CONFIG["dashscope_api_key"], AI_CONFIG["model"])
    chat = ChatService(llm, service)
    chat.audit_recorder = RagAuditRecorder(enabled=False)
    rows = []
    try:
        for case in sample:
            started = time.perf_counter()
            error = None
            answer = None
            try:
                answer = await chat.answer(
                    case["question"],
                    [],
                    principal={"user_id": 1, "role": "用户"},
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            rows.append({
                "id": case["id"],
                "category": case["category"],
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "mode": answer.mode if answer else None,
                "status": answer.status if answer else "failed",
                "refusal": answer.refusal if answer else None,
                "error": error,
            })
    finally:
        await llm.aclose()
    pointer_after = service.artifact_repository.releases.active()
    if pointer_before != pointer_after:
        raise RuntimeError("延迟探测期间 Active Release 发生变化")

    latencies = [row["latency_ms"] for row in rows]
    successful = [row for row in rows if row["error"] is None]
    probe = {
        "mode": "sequential_end_to_end_chat_service",
        "sample_strategy": f"up_to_{args.per_category}_per_category",
        "sample_size": len(rows),
        "successful_cases": len(successful),
        "mean_ms": round(statistics.fmean(latencies), 2),
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "release_id": pointer_before["release_id"],
        "cases": rows,
    }
    evaluation_path = output / "baseline_eval.json"
    metrics_path = output / "baseline_metrics.json"
    evaluation = _load(evaluation_path)
    metrics = _load(metrics_path)
    evaluation["latency_probe"] = probe

    latency = metrics["latency"]
    latency["forced_knowledge_batch_completion_queue_inclusive_mean_ms"] = latency.pop(
        "generation_mean_ms", None
    )
    latency["forced_knowledge_batch_completion_queue_inclusive_p50_ms"] = latency.pop(
        "generation_p50_ms", None
    )
    latency["forced_knowledge_batch_completion_queue_inclusive_p95_ms"] = latency.pop(
        "generation_p95_ms", None
    )
    latency["end_to_end_sequential_sample_size"] = probe["sample_size"]
    latency["end_to_end_sequential_mean_ms"] = probe["mean_ms"]
    latency["end_to_end_sequential_p50_ms"] = probe["p50_ms"]
    latency["end_to_end_sequential_p95_ms"] = probe["p95_ms"]

    answers = [row["answer"] for row in evaluation["cases"]]
    grounding_failure_text = "知识依据校验未通过，本次回答已安全终止。"
    terminal_errors = sum(
        bool(answer.get("error"))
        and answer.get("refusal") is True
        and answer.get("text") == grounding_failure_text
        for answer in answers
    )
    metrics["answer"]["evaluation_mode"] = (
        "forced_knowledge_mode_after_router_scored_separately"
    )
    metrics["answer"]["generation_retry_incident_cases"] = sum(
        bool(answer.get("error")) for answer in answers
    )
    metrics["answer"]["terminal_generation_error_cases"] = terminal_errors
    metrics["answer"]["generation_error_cases"] = terminal_errors
    for row in evaluation["cases"]:
        answer = row["answer"]
        answer["error_recovered"] = bool(answer.get("error")) and not (
            answer.get("refusal") is True
            and answer.get("text") == grounding_failure_text
        )

    _write(evaluation_path, evaluation)
    _write(metrics_path, metrics)
    return probe


def main() -> int:
    parser = argparse.ArgumentParser(description="测量 RAG Phase 0 顺序端到端延迟")
    parser.add_argument(
        "--dataset",
        default=str(PROJECT_ROOT / "config" / "rag_golden_dataset_v0.json"),
    )
    parser.add_argument(
        "--output-dir", default=str(PROJECT_ROOT / "reports" / "rag_phase0_v0")
    )
    parser.add_argument("--per-category", type=int, default=2)
    parser.add_argument("--replace-signed-baseline", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
