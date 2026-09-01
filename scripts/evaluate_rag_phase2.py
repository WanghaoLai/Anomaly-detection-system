"""Run the approved Phase 2 retrieval experiment matrix locally.

The script reuses one embedding batch and the production RRF, reranker, and
context-packing implementations. It performs no generation and does not change
the active release or production feature flags.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
DEFAULT_DATASET = PROJECT_ROOT / "config" / "rag_golden_dataset_v0.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "rag_phase2_retrieval_matrix.json"
DEFAULT_DENSE_CACHE = PROJECT_ROOT / "reports" / "rag_phase2_dense_batch.json"

for path in (BACKEND_DIR, PROJECT_ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluate_rag_phase0 import (  # noqa: E402
    _average,
    _percentile,
    prepare_retrieval_batch,
    retrieval_evaluate,
)
from rag_phase0_baseline import _json_dump, _utc_now  # noqa: E402
from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.rag.search.reranking import CrossEncoderReranker  # noqa: E402


EXPERIMENTS = (
    {"id": "E1", "dense_k": 50, "lexical_k": 50, "union_limit": 100,
     "rerank_input_k": 100, "final_k": 8, "reranker_enabled": False},
    {"id": "E2", "dense_k": 50, "lexical_k": 50, "union_limit": 100,
     "rerank_input_k": 30, "final_k": 8, "reranker_enabled": True},
    {"id": "E3", "dense_k": 50, "lexical_k": 50, "union_limit": 100,
     "rerank_input_k": 50, "final_k": 8, "reranker_enabled": True},
    {"id": "E4", "dense_k": 40, "lexical_k": 40, "union_limit": 80,
     "rerank_input_k": 30, "final_k": 6, "reranker_enabled": True},
    {"id": "E5", "dense_k": 60, "lexical_k": 60, "union_limit": 100,
     "rerank_input_k": 50, "final_k": 6, "reranker_enabled": True},
)


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = [row for row in rows if row["retrieval_applicable"]]
    rerank_stats = [row["rerank"] for row in rows]
    attempted = [item for item in rerank_stats if item["mode"] != "rrf"]
    fallback = [item for item in attempted if item["fallback"]]
    latencies = [float(item["elapsed_ms"]) for item in attempted]
    return {
        "cases": len(rows),
        "retrieval_applicable_cases": len(applicable),
        "retrieval": {
            **{
                f"recall_at_{k}": _average([
                    float(row["recall_at"][str(k)]) for row in applicable
                ])
                for k in (5, 10, 20, 50)
            },
            "mrr": _average([
                float(row["reciprocal_rank"]) for row in applicable
            ]),
            "ndcg_at_10": _average([
                float(row["ndcg_at_10"]) for row in applicable
            ]),
        },
        "context": {
            "recall": _average([
                float(row["context_recall"]) for row in applicable
            ]),
            "precision": _average([
                float(row["context_precision"])
                for row in applicable if row["context_precision"] is not None
            ]),
        },
        "latency_ms": {
            "retrieval_p50": _percentile([
                float(row["retrieval_latency_ms"]) for row in rows
            ], 0.50),
            "retrieval_p95": _percentile([
                float(row["retrieval_latency_ms"]) for row in rows
            ], 0.95),
            "rerank_p50": _percentile(latencies, 0.50),
            "rerank_p95": _percentile(latencies, 0.95),
            "rerank_p99": _percentile(latencies, 0.99),
        },
        "reranker": {
            "attempts": len(attempted),
            "fallbacks": len(fallback),
            "fallback_rate": (
                round(len(fallback) / len(attempted), 4) if attempted else 0.0
            ),
            "fallback_reasons": sorted({
                str(item["fallback_reason"])
                for item in fallback if item.get("fallback_reason")
            }),
        },
    }


def _delta(value: float | None, baseline: float | None) -> float | None:
    if value is None or baseline is None:
        return None
    return round(value - baseline, 4)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    if dataset.get("status") != "signed_baseline_v0":
        raise RuntimeError("Phase 2 只允许使用已签署的 Golden Dataset V0")

    service = KnowledgeService()
    pointer_before = service.artifact_repository.releases.active()
    if not pointer_before or pointer_before["release_id"] != dataset.get("release_id"):
        raise RuntimeError("Golden Dataset V0 与当前 Active Release 不一致")
    manifest = service.artifact_repository.releases.get(pointer_before["release_id"])
    service._validate_shadow_manifest(manifest)

    dense_cache_path = Path(args.dense_batch_cache).resolve()
    if dense_cache_path.exists():
        cached = json.loads(dense_cache_path.read_text(encoding="utf-8"))
        if (
            cached.get("release_id") != dataset["release_id"]
            or cached.get("question_fingerprint")
            != dataset["question_fingerprint"]
        ):
            raise RuntimeError("Dense Batch 缓存与当前 Golden Dataset 不一致")
        dense_batch = (cached["raw"], float(cached["embedding_elapsed_ms"]))
    else:
        dense_batch = prepare_retrieval_batch(dataset, service, dense_k=60)
        _json_dump(dense_cache_path, {
            "release_id": dataset["release_id"],
            "question_fingerprint": dataset["question_fingerprint"],
            "embedding_elapsed_ms": round(dense_batch[1], 2),
            "raw": dense_batch[0],
        })
    shared_reranker = CrossEncoderReranker(
        model_name=str(Path(args.model_path).resolve()),
        enabled=True,
        timeout_seconds=args.timeout,
        max_length=args.max_length,
    )
    warmup_started = time.perf_counter()
    warmup_attempts: list[dict[str, Any]] = []
    while True:
        _, warmup_stats = await shared_reranker.rerank(
            "如何查看 GPU 状态？",
            [{"content": "使用 nvidia-smi 查看 GPU 状态。"}],
            top_k=1,
        )
        warmup_attempts.append(warmup_stats)
        if warmup_stats["mode"] == "cross_encoder":
            break
        if time.perf_counter() - warmup_started > 30:
            raise RuntimeError("Cross-Encoder 预热 30 秒后仍不可用")
        await asyncio.sleep(0.05)
    warmup_elapsed_ms = round(
        (time.perf_counter() - warmup_started) * 1000, 2
    )
    experiments: list[dict[str, Any]] = []
    selected_ids = set(args.experiment or [])
    selected_experiments = [
        config for config in EXPERIMENTS
        if not selected_ids or config["id"] in selected_ids
    ]
    unknown_ids = sorted(selected_ids - {item["id"] for item in EXPERIMENTS})
    if unknown_ids:
        raise RuntimeError("未知实验: " + ", ".join(unknown_ids))
    for config in selected_experiments:
        chat = ChatService(None, service)
        chat.rag_dense_candidate_k = config["dense_k"]
        chat.rag_lexical_candidate_k = config["lexical_k"]
        chat.rag_candidate_union_limit = config["union_limit"]
        chat.rag_rerank_input_k = config["rerank_input_k"]
        chat.rag_rerank_final_k = config["final_k"]
        chat.reranker = (
            shared_reranker
            if config["reranker_enabled"]
            else CrossEncoderReranker(
                model_name="", enabled=False, timeout_seconds=args.timeout
            )
        )
        rows, _, _ = await retrieval_evaluate(
            dataset, service, chat, dense_batch=dense_batch
        )
        experiments.append({
            "config": config,
            "summary": _summary(rows),
            "cases": rows,
        })

    baseline = None
    if args.baseline_report:
        previous = json.loads(
            Path(args.baseline_report).read_text(encoding="utf-8")
        )
        baseline = next(
            item["summary"] for item in previous["experiments"]
            if item["config"]["id"] == "E1"
        )
    if baseline is None:
        baseline = next(
            item["summary"] for item in experiments
            if item["config"]["id"] == "E1"
        )
    for experiment in experiments:
        summary = experiment["summary"]
        summary["delta_vs_e1"] = {
            "recall_at_5": _delta(
                summary["retrieval"]["recall_at_5"],
                baseline["retrieval"]["recall_at_5"],
            ),
            "mrr": _delta(
                summary["retrieval"]["mrr"], baseline["retrieval"]["mrr"]
            ),
            "ndcg_at_10": _delta(
                summary["retrieval"]["ndcg_at_10"],
                baseline["retrieval"]["ndcg_at_10"],
            ),
            "context_recall": _delta(
                summary["context"]["recall"], baseline["context"]["recall"]
            ),
            "context_precision": _delta(
                summary["context"]["precision"],
                baseline["context"]["precision"],
            ),
        }

    pointer_after = service.artifact_repository.releases.active()
    if pointer_after != pointer_before:
        raise RuntimeError("实验期间 Active Release 发生变化")
    report = {
        "phase": "Phase 2",
        "status": "retrieval_matrix_measured_pending_review",
        "captured_at": _utc_now(),
        "dataset": str(Path(args.dataset).resolve()),
        "release_id": dataset["release_id"],
        "model": {
            "source": "BAAI/bge-reranker-base",
            "revision": args.model_revision,
            "local_path": str(Path(args.model_path).resolve()),
            "timeout_seconds": args.timeout,
            "max_length": args.max_length,
        },
        "embedding_batch_elapsed_ms": round(dense_batch[1], 2),
        "warmup": {
            "attempts": warmup_attempts,
            "total_elapsed_ms": warmup_elapsed_ms,
        },
        "experiments": experiments,
        "limitations": [
            "本报告只评估检索与 Context 指标；Answer Accuracy 在候选参数收敛后评估。",
            "生产重排开关仍保持关闭。",
        ],
    }
    _json_dump(Path(args.output).resolve(), report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="执行 RAG Phase 2 E1-E5 检索矩阵")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--max-length", type=int, default=0)
    parser.add_argument(
        "--experiment",
        action="append",
        default=[],
        help="只运行指定实验 ID；可重复使用",
    )
    parser.add_argument(
        "--baseline-report",
        help="复用既有 E1 报告作为对照，避免重复运行 RRF 基线",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--dense-batch-cache", default=str(DEFAULT_DENSE_CACHE)
    )
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps({
        "output": str(Path(args.output).resolve()),
        "status": result["status"],
        "summaries": [
            {"id": item["config"]["id"], **item["summary"]}
            for item in result["experiments"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
