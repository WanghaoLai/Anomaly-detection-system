"""Evaluate signed Phase 3 routing and rewrite candidates without enabling production.

The evaluator calls the configured DashScope model only for ambiguous intent
classification and knowledge-base follow-up rewrite. Retrieval comparison uses
the active read-only release and production Dense + BM25 + RRF path. It never
changes feature flags, the active release, or knowledge-base data.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
DEFAULT_DATASET = PROJECT_ROOT / "config" / "rag_phase3_candidate_v1.json"
DEFAULT_GOLDEN = PROJECT_ROOT / "config" / "rag_golden_dataset_v0.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "rag_phase3_offline_evaluation.json"

for path in (BACKEND_DIR, PROJECT_ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault(
    "JWT_SECRET_KEY", "rag-phase3-evaluation-only-not-for-runtime-000000000000000"
)

from evaluate_rag_phase0 import (  # noqa: E402
    _average,
    _percentile,
    retrieval_evaluate,
)
from rag_phase0_baseline import _json_dump, _utc_now  # noqa: E402
from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.llm_service import LLMService  # noqa: E402
from services.rag.answering import (  # noqa: E402
    DashScopeIntentClassifier,
    DashScopeQueryRewriter,
    HistoryAwareQueryTransformer,
    Phase3QueryResolver,
    Phase3RuleRouter,
    QueryModeRouter,
)
from services.rag.operations import RagAuditRecorder  # noqa: E402
from settings import AI_CONFIG  # noqa: E402


GATES = {
    "router_accuracy_min": 0.95,
    "knowledge_base_recall_min": 0.98,
    "general_precision_min": 0.95,
    "classifier_fallback_rate_max": 0.05,
    "rewrite_fallback_rate_max": 0.05,
    "rewrite_recall_at_10_non_decrease": True,
    "rewrite_context_recall_non_decrease": True,
}


def _validate_signed_dataset(dataset: dict[str, Any], golden: dict[str, Any]) -> None:
    if dataset.get("status") != "signed_phase3_dataset":
        raise RuntimeError("Phase 3 只允许使用人工签署的数据集")
    cases = list(dataset.get("cases") or [])
    if len(cases) != 80 or len({case.get("id") for case in cases}) != 80:
        raise RuntimeError("Phase 3 签署集必须包含 80 个唯一 Case")
    if any(
        (case.get("review") or {}).get("status") != "approved"
        or not all(
            (case.get("review") or {}).get(field) is True
            for field in (
                "route_label_approved",
                "rewrite_target_approved",
                "evidence_approved",
            )
        )
        for case in cases
    ):
        raise RuntimeError("Phase 3 存在未逐条批准的 Case")
    actual = hashlib.sha256(
        json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if actual != dataset.get("signed_fingerprint"):
        raise RuntimeError("Phase 3 签署指纹不匹配")
    base = dataset.get("base_dataset") or {}
    if (
        base.get("version") != golden.get("version")
        or base.get("release_id") != golden.get("release_id")
        or base.get("question_fingerprint") != golden.get("question_fingerprint")
    ):
        raise RuntimeError("Phase 3 数据集绑定的 Golden V0 已发生变化")


def _retrieval_dataset(
    source: dict[str, Any], resolutions: dict[str, Any], *, rewritten: bool
) -> dict[str, Any]:
    cases = []
    for case in source["cases"]:
        if case["expected_mode"] != "knowledge_base":
            continue
        resolution = resolutions[case["id"]]
        question = (
            resolution.retrieval_query if rewritten else str(case["question"])
        )
        cases.append({
            "id": case["id"],
            "category": case["set"],
            "question": question,
            "expected_mode": "knowledge_base",
            "expected_evidence": list(case.get("expected_evidence") or []),
        })
    return {"cases": cases}


def _retrieval_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = [row for row in rows if row["retrieval_applicable"]]
    return {
        "cases": len(rows),
        "applicable_cases": len(applicable),
        "recall_at_10": _average([
            float(row["recall_at"]["10"]) for row in applicable
        ]),
        "mrr": _average([
            float(row["reciprocal_rank"]) for row in applicable
        ]),
        "context_recall": _average([
            float(row["context_recall"]) for row in applicable
        ]),
        "context_precision": _average([
            float(row["context_precision"])
            for row in applicable if row["context_precision"] is not None
        ]),
        "retrieval_p95_ms": _percentile([
            float(row["retrieval_latency_ms"]) for row in rows
        ], 0.95),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    _validate_signed_dataset(dataset, golden)
    if not AI_CONFIG.get("dashscope_api_key"):
        raise RuntimeError("DASHSCOPE_API_KEY 未配置")
    if AI_CONFIG.get("rag_phase3_router_enabled") or AI_CONFIG.get(
        "rag_phase3_rewrite_enabled"
    ):
        raise RuntimeError("离线评测要求 Phase 3 生产开关保持关闭")

    service = KnowledgeService()
    pointer_before = service.artifact_repository.releases.active()
    expected_release = (dataset.get("base_dataset") or {}).get("release_id")
    if not pointer_before or pointer_before.get("release_id") != expected_release:
        raise RuntimeError("Phase 3 签署集与当前 Active Release 不一致")
    manifest = service.artifact_repository.releases.get(expected_release)
    service._validate_shadow_manifest(manifest)

    llm = LLMService(AI_CONFIG["dashscope_api_key"], AI_CONFIG["model"])
    classifier = DashScopeIntentClassifier(
        llm,
        confidence_threshold=float(args.classifier_confidence),
        timeout_seconds=float(args.classifier_timeout),
        history_turn_limit=2,
    )
    rewriter = DashScopeQueryRewriter(
        llm,
        timeout_seconds=float(args.rewrite_timeout),
        history_turn_limit=2,
    )
    resolver = Phase3QueryResolver(
        enabled=True,
        rewrite_enabled=True,
        legacy_router=QueryModeRouter(),
        legacy_transformer_factory=lambda: HistoryAwareQueryTransformer(2),
        rule_router=Phase3RuleRouter(),
        classifier=classifier,
        rewriter=rewriter,
    )
    semaphore = asyncio.Semaphore(max(1, int(args.concurrency)))

    async def resolve_case(case: dict[str, Any]):
        async with semaphore:
            return case["id"], await resolver.resolve(
                case["question"], case.get("history") or []
            )

    try:
        resolved_pairs = await asyncio.gather(*[
            resolve_case(case) for case in dataset["cases"]
        ])
        resolutions = dict(resolved_pairs)
        route_rows = []
        for case in dataset["cases"]:
            resolution = resolutions[case["id"]]
            route_rows.append({
                "id": case["id"],
                "set": case["set"],
                "question": case["question"],
                "history_user_turns": sum(
                    item.get("role") == "user"
                    for item in (case.get("history") or [])
                ),
                "expected_mode": case["expected_mode"],
                "actual_mode": resolution.route.mode,
                "route_correct": resolution.route.mode == case["expected_mode"],
                "route": resolution.route.trace(),
                "expected_retrieval_query": case["expected_retrieval_query"],
                "actual_retrieval_query": resolution.retrieval_query,
                "rewrite_exact_match": (
                    resolution.retrieval_query == case["expected_retrieval_query"]
                ),
                "rewrite": resolution.rewrite.trace(case["question"]),
            })

        predicted_general = [
            row for row in route_rows if row["actual_mode"] == "general"
        ]
        expected_kb = [
            row for row in route_rows if row["expected_mode"] == "knowledge_base"
        ]
        classifier_rows = [
            row for row in route_rows
            if row["route"]["route_stage"] == "intent_classifier"
        ]
        rewrite_rows = [
            row for row in route_rows
            if row["expected_mode"] == "knowledge_base"
            and row["history_user_turns"] > 0
        ]
        router_metrics = {
            "accuracy": _average([
                float(row["route_correct"]) for row in route_rows
            ]),
            "knowledge_base_recall": _average([
                float(row["actual_mode"] == "knowledge_base") for row in expected_kb
            ]),
            "general_precision": _average([
                float(row["expected_mode"] == "general")
                for row in predicted_general
            ]) if predicted_general else 0.0,
            "classifier_fallback_rate": _average([
                float(row["route"]["route_fallback"]) for row in classifier_rows
            ]),
            "classifier_latency_p95_ms": _percentile([
                float(row["route"]["route_elapsed_ms"])
                for row in classifier_rows
            ], 0.95),
        }
        rewrite_metrics = {
            "evaluated_cases": len(rewrite_rows),
            "fallback_rate": _average([
                float(row["rewrite"]["rewrite_fallback"])
                for row in rewrite_rows
            ]),
            "exact_match_rate": _average([
                float(row["rewrite_exact_match"]) for row in rewrite_rows
            ]),
            "changed_rate": _average([
                float(row["rewrite"]["query_changed"]) for row in rewrite_rows
            ]),
            "latency_p95_ms": _percentile([
                float(row["rewrite"]["rewrite_elapsed_ms"])
                for row in rewrite_rows
            ], 0.95),
        }

        chat = ChatService(llm, service)
        chat.audit_recorder = RagAuditRecorder(enabled=False)
        original_rows, _, _ = await retrieval_evaluate(
            _retrieval_dataset(dataset, resolutions, rewritten=False), service, chat
        )
        rewritten_rows, _, _ = await retrieval_evaluate(
            _retrieval_dataset(dataset, resolutions, rewritten=True), service, chat
        )
        original_metrics = _retrieval_metrics(original_rows)
        rewritten_metrics = _retrieval_metrics(rewritten_rows)

        checks = {
            "router_accuracy": (
                router_metrics["accuracy"] >= GATES["router_accuracy_min"]
            ),
            "knowledge_base_recall": (
                router_metrics["knowledge_base_recall"]
                >= GATES["knowledge_base_recall_min"]
            ),
            "general_precision": (
                router_metrics["general_precision"]
                >= GATES["general_precision_min"]
            ),
            "classifier_fallback_rate": (
                router_metrics["classifier_fallback_rate"]
                <= GATES["classifier_fallback_rate_max"]
            ),
            "rewrite_fallback_rate": (
                rewrite_metrics["fallback_rate"]
                <= GATES["rewrite_fallback_rate_max"]
            ),
            "rewrite_recall_at_10_non_decrease": (
                rewritten_metrics["recall_at_10"]
                >= original_metrics["recall_at_10"]
            ),
            "rewrite_context_recall_non_decrease": (
                rewritten_metrics["context_recall"]
                >= original_metrics["context_recall"]
            ),
        }
        passed = all(checks.values())
        pointer_after = service.artifact_repository.releases.active()
        if pointer_after != pointer_before:
            raise RuntimeError("评测期间 Active Release 发生变化")
        report = {
            "phase": "Phase 3",
            "status": (
                "offline_gates_passed_pending_production_enablement_signoff"
                if passed else "offline_gates_failed_keep_production_disabled"
            ),
            "captured_at": _utc_now(),
            "dataset": {
                "path": str(Path(args.dataset).resolve()),
                "signed_fingerprint": dataset["signed_fingerprint"],
                "cases": len(dataset["cases"]),
            },
            "release_id": expected_release,
            "production_flags": {
                "router_enabled": False,
                "rewrite_enabled": False,
            },
            "evaluation_config": {
                "model": AI_CONFIG["model"],
                "classifier_confidence": args.classifier_confidence,
                "classifier_timeout_seconds": args.classifier_timeout,
                "rewrite_timeout_seconds": args.rewrite_timeout,
                "history_user_turn_limit": 2,
                "concurrency": args.concurrency,
                "ranking": "production_dense_plus_bm25_rrf_cross_encoder_disabled",
            },
            "gates": GATES,
            "checks": checks,
            "passed": passed,
            "router": router_metrics,
            "rewrite": rewrite_metrics,
            "retrieval": {
                "original_query": original_metrics,
                "rewritten_query": rewritten_metrics,
                "delta": {
                    key: round(
                        float(rewritten_metrics[key]) - float(original_metrics[key]), 4
                    )
                    for key in ("recall_at_10", "mrr", "context_recall", "context_precision")
                },
            },
            "cases": route_rows,
            "limitations": [
                "本轮只评估 Router、Classifier、Rewrite 与检索，不评估最终回答。",
                "离线通过不等于生产启用；生产开关仍需再次人工确认。",
            ],
        }
        _json_dump(Path(args.output).resolve(), report)
        return report
    finally:
        await llm.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="执行 Phase 3 离线路由与改写门禁")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--classifier-confidence", type=float,
        default=float(AI_CONFIG.get("rag_phase3_classifier_confidence", 0.75)),
    )
    parser.add_argument(
        "--classifier-timeout", type=float,
        default=float(AI_CONFIG.get("rag_phase3_classifier_timeout_seconds", 8.0)),
    )
    parser.add_argument(
        "--rewrite-timeout", type=float,
        default=float(AI_CONFIG.get("rag_phase3_rewrite_timeout_seconds", 8.0)),
    )
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps({
        "output": str(Path(args.output).resolve()),
        "status": result["status"],
        "passed": result["passed"],
        "checks": result["checks"],
        "router": result["router"],
        "rewrite": result["rewrite"],
        "retrieval": result["retrieval"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
