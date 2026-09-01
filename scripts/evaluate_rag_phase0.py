"""Evaluate the approved Phase 0 dataset against the active read-only release.

The evaluator batches DashScope query embeddings, uses the production Dense +
BM25 + RRF policy, packs context with the production budget, and optionally runs
Qwen generation.  It never writes Chroma or changes the active release pointer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
DEFAULT_DRAFT = PROJECT_ROOT / "config" / "rag_golden_dataset_v0.draft.json"
DEFAULT_DATASET = PROJECT_ROOT / "config" / "rag_golden_dataset_v0.json"
DEFAULT_POLICY = PROJECT_ROOT / "config" / "rag_phase0_review_policy.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "reports" / "rag_phase0_v0"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "rag-phase0-evaluation-only-not-for-runtime-000000000000000",
)

from rag_phase0_baseline import (  # noqa: E402
    PHASE0_SCHEMA_VERSION,
    _git,
    _json_dump,
    _release_snapshot,
    _runtime_snapshot,
    _sha256_bytes,
    _utc_now,
    _write_yaml,
    ensure_baseline_writable,
)
from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.llm_service import LLMService  # noqa: E402
from services.rag.core.access import AccessPrincipal  # noqa: E402
from services.rag.operations.audit import RagAuditRecorder  # noqa: E402
from settings import AI_CONFIG  # noqa: E402


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _question_fingerprint(dataset: dict[str, Any]) -> str:
    projection = [{
        "id": case.get("id"),
        "question": case.get("question"),
        "category": case.get("category"),
        "requires_refusal": case.get("requires_refusal"),
    } for case in dataset.get("cases") or []]
    return _sha256_bytes(
        json.dumps(projection, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


def _node_map(record: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for node in record.get("nodes") or []:
        text = str(node.get("text") or "")
        node_id = str(node.get("node_id") or "")
        for code in set(__import__("re").findall(r"KB-[A-Z]+-\d+", text)):
            result.setdefault(code, []).append(node_id)
    return result


def _find_nodes(record: dict[str, Any], needle: str) -> list[str]:
    return [
        str(node.get("node_id"))
        for node in record.get("nodes") or []
        if needle in str(node.get("text") or "")
    ]


def approve_and_map_dataset(
    draft: dict[str, Any],
    policy: dict[str, Any],
    service: KnowledgeService,
) -> dict[str, Any]:
    cases = list(draft.get("cases") or [])
    expected_count = int(policy["candidate_questions"]["expected_count"])
    if policy["candidate_questions"]["status"] != "approved_keep_all":
        raise RuntimeError("人工审核尚未批准保留候选集")
    if len(cases) != expected_count:
        raise RuntimeError(
            f"候选集数量与人工审核不一致: expected={expected_count} actual={len(cases)}"
        )

    pointer = service.artifact_repository.releases.active()
    if pointer is None:
        raise RuntimeError("不存在活动 Release")
    manifest = service.artifact_repository.releases.get(pointer["release_id"])
    source_name = str((draft.get("source") or {}).get("filename") or "")
    document_id = str((manifest.get("catalog") or {}).get(source_name) or "")
    if not document_id:
        raise RuntimeError(f"活动 Release 未包含人工审核的数据源: {source_name}")
    record = service.artifact_repository.documents.get(document_id)
    source_hash = str((record.get("source") or {}).get("sha256") or "")
    if source_hash != (draft.get("source") or {}).get("sha256"):
        raise RuntimeError("活动 Release 中的知识文档与审核时 PDF 哈希不一致")

    by_locator = _node_map(record)
    all_doc_ids = list(manifest.get("document_ids") or [])
    special_locators = {
        "rag_v0_special_007": "KB-EXP-01",
        "rag_v0_special_008": "KB-SRV-06",
        "rag_v0_special_009": "KB-RAG-06",
    }
    missing: list[str] = []
    mapped_cases = []
    for original in cases:
        case = dict(original)
        locator = case.get("source_locator") or special_locators.get(case["id"])
        evidence = list(by_locator.get(str(locator), [])) if locator else []
        if case["id"] == "rag_v0_special_010":
            evidence = _find_nodes(record, "管理员的最新通知始终优先")
        if locator and not evidence:
            missing.append(f"{case['id']}:{locator}")
        case["allowed_doc_ids"] = all_doc_ids
        case["expected_evidence"] = evidence
        case["review"] = {
            "status": "approved",
            "reviewer": policy.get("approved_by"),
            "reviewed_at": policy.get("approved_at"),
            "notes": "人工确认保留；Evidence Node 由已校验 Active Release 自动映射",
        }
        case["evidence_mapping"] = {
            "status": "mapped_from_active_release",
            "release_id": manifest["release_id"],
            "source_document_id": document_id if evidence else None,
        }
        mapped_cases.append(case)
    if missing:
        raise RuntimeError("以下候选无法映射 Evidence Node: " + ", ".join(missing))

    return {
        **draft,
        "name": "industrial-anomaly-rag-golden-v0",
        "version": "V0",
        "status": "approved_for_baseline_evaluation",
        "question_fingerprint": _question_fingerprint(draft),
        "human_policy": policy,
        "release_id": manifest["release_id"],
        "cases": mapped_cases,
    }


def _dense_rows(raw: dict, query_index: int) -> list[dict[str, Any]]:
    rows = []
    for node_id, text, metadata, distance in zip(
        (raw.get("ids") or [[]])[query_index],
        (raw.get("documents") or [[]])[query_index],
        (raw.get("metadatas") or [[]])[query_index],
        (raw.get("distances") or [[]])[query_index],
    ):
        metadata = dict(metadata or {})
        rows.append({
            "node_id": str(node_id),
            "content": str(text or ""),
            "doc_id": metadata.get("doc_id"),
            "filename": metadata.get("filename"),
            "heading_path": metadata.get("heading_path"),
            "section_path": metadata.get("section_path"),
            "position": metadata.get("position"),
            "chunk_index": metadata.get("chunk_index"),
            "score": 1.0 - float(distance),
            "distance": float(distance),
        })
    return rows


def _reciprocal_rank(ranked_ids: list[str], expected: set[str]) -> float:
    for rank, node_id in enumerate(ranked_ids, start=1):
        if node_id in expected:
            return 1.0 / rank
    return 0.0


def _ndcg(ranked_ids: list[str], expected: set[str], k: int) -> float:
    if not expected:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, node_id in enumerate(ranked_ids[:k], start=1)
        if node_id in expected
    )
    ideal = sum(
        1.0 / math.log2(rank + 1)
        for rank in range(1, min(k, len(expected)) + 1)
    )
    return dcg / ideal if ideal else 0.0


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 2)


def prepare_retrieval_batch(
    dataset: dict[str, Any],
    service: KnowledgeService,
    *,
    dense_k: int,
) -> tuple[dict[str, Any], float]:
    questions = [str(case["question"]) for case in dataset["cases"]]
    started = time.perf_counter()
    vectors = service._get_embeddings(questions, text_type="query")
    embedding_elapsed_ms = (time.perf_counter() - started) * 1000
    collection = service.collection
    raw = collection.query(
        query_embeddings=vectors,
        n_results=min(dense_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )
    return raw, embedding_elapsed_ms


async def retrieval_evaluate(
    dataset: dict[str, Any],
    service: KnowledgeService,
    chat: ChatService,
    *,
    dense_batch: tuple[dict[str, Any], float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    cases = list(dataset["cases"])
    raw, embedding_elapsed_ms = dense_batch or prepare_retrieval_batch(
        dataset,
        service,
        dense_k=chat.rag_dense_candidate_k,
    )
    rows = []
    packed_by_id: dict[str, Any] = {}
    principal = AccessPrincipal(user_id=1, role="用户")
    allowed_runtime = service.allowed_document_ids(principal)
    for index, case in enumerate(cases):
        query_started = time.perf_counter()
        dense = [
            item for item in _dense_rows(raw, index)
            if item.get("doc_id") in allowed_runtime
        ][:chat.rag_dense_candidate_k]
        lexical = service.lexical_search(
            case["question"],
            top_k=chat.rag_lexical_candidate_k,
            allowed_doc_ids=allowed_runtime,
        )
        rrf_ranked, selection = chat._retrieval_selector().fuse_ranked(
            dense, lexical, limit=chat.rag_candidate_union_limit
        )
        rerank_candidates = rrf_ranked[:chat.rag_rerank_input_k]
        ranked, rerank = await chat.reranker.rerank(
            case["question"],
            rerank_candidates,
            top_k=len(rerank_candidates),
        )
        final = ranked[:chat.rag_rerank_final_k]
        packed = chat._pack_context(final, query=case["question"])
        packed_by_id[case["id"]] = packed
        ranked_ids = [str(item.get("node_id") or "") for item in ranked]
        packed_ids = [entry.node_id for entry in packed.entries]
        expected = set(case.get("expected_evidence") or [])
        retrieval_applicable = bool(expected)
        recalls = {
            str(k): (
                len(expected & set(ranked_ids[:k])) / len(expected)
                if expected else None
            ) for k in (5, 10, 20, 50)
        }
        context_relevant = expected & set(packed_ids)
        rows.append({
            "id": case["id"],
            "category": case["category"],
            "question": case["question"],
            "expected_mode": case["expected_mode"],
            "router_mode": chat.mode_router.route(case["question"]),
            "retrieval_applicable": retrieval_applicable,
            "expected_evidence": sorted(expected),
            "rrf_ranked_node_ids": [
                str(item.get("node_id") or "") for item in rrf_ranked[:50]
            ],
            "ranked_node_ids": ranked_ids[:50],
            "packed_node_ids": packed_ids,
            "recall_at": recalls,
            "reciprocal_rank": _reciprocal_rank(ranked_ids, expected) if expected else None,
            "ndcg_at_10": _ndcg(ranked_ids, expected, 10) if expected else None,
            "context_recall": len(context_relevant) / len(expected) if expected else None,
            "context_precision": len(context_relevant) / len(packed_ids) if packed_ids and expected else None,
            "context_tokens": packed.token_count,
            "context_token_utilization": packed.token_count / chat.rag_context_tokens,
            "retrieval_latency_ms": round((time.perf_counter() - query_started) * 1000, 2),
            "selection": selection,
            "rerank": rerank,
            "answer": None,
        })
    meta = {
        "query_embedding_batch_elapsed_ms": round(embedding_elapsed_ms, 2),
        "packed_by_id": packed_by_id,
    }
    return rows, meta, {"allowed_document_ids": sorted(allowed_runtime)}


async def answer_evaluate(
    dataset: dict[str, Any],
    rows: list[dict[str, Any]],
    packed_by_id: dict[str, Any],
    chat: ChatService,
    *,
    concurrency: int,
) -> None:
    llm = chat.llm
    semaphore = asyncio.Semaphore(max(1, concurrency))
    case_by_id = {case["id"]: case for case in dataset["cases"]}

    async def evaluate_one(row: dict[str, Any]) -> None:
        case = case_by_id[row["id"]]
        packed = packed_by_id[row["id"]]
        started = time.perf_counter()
        attempts = 0
        error = None
        answer = None
        usage: dict[str, float] = {}
        retry_reason: str | None = None
        retry_reasons: set[str] = set()
        async with semaphore:
            if not packed.entries:
                answer = chat.answer_validator.refusal("no_knowledge")
            else:
                for attempt in range(chat.rag_grounding_validation_retries + 1):
                    attempts += 1
                    messages = chat.grounded_prompt_builder.knowledge_messages(
                        case["question"], [], packed,
                        validation_retry=attempt > 0,
                        retry_reason=retry_reason,
                    )
                    try:
                        result = await llm.chat_structured_with_metadata(
                            messages,
                            chat.grounded_prompt_builder.KNOWLEDGE_SYSTEM_PROMPT,
                        )
                        for key, value in result.usage.items():
                            if isinstance(value, (int, float)) and not isinstance(value, bool):
                                usage[key] = usage.get(key, 0) + value
                        answer = chat.answer_validator.validate(
                            result.text,
                            packed,
                            question=case["question"],
                            allow_overflow_selection=attempt > 0,
                        )
                        if (
                            answer.faithfulness
                            < chat.answer_validator.minimum_faithfulness
                            and attempt < chat.rag_grounding_validation_retries
                        ):
                            retry_reason = "low_faithfulness"
                            retry_reasons.add(retry_reason)
                            error = (
                                "LowFaithfulnessSignal: "
                                f"actual={answer.faithfulness:.4f} "
                                "required="
                                f"{chat.answer_validator.minimum_faithfulness:.4f}"
                            )
                            continue
                        answer = replace(
                            answer,
                            claim_limit_retry_triggered=(
                                "claim_count_exceeded" in retry_reasons
                            ),
                            faithfulness_retry_triggered=(
                                "low_faithfulness" in retry_reasons
                            ),
                        )
                        break
                    except Exception as exc:  # Captured per case; baseline continues.
                        error = f"{type(exc).__name__}: {exc}"
                        retry_reason = getattr(
                            exc, "reason_code", "grounding_validation_failed"
                        )
                        retry_reasons.add(retry_reason)
                        if attempt >= chat.rag_grounding_validation_retries:
                            answer = chat.answer_validator.refusal(
                                "grounding_validation_failed"
                            )
        cited_node_ids = []
        if answer is not None:
            cited_node_ids = [
                packed.citation_map[citation]
                for citation in answer.citations
                if citation in packed.citation_map
            ]
        expected = set(case.get("expected_evidence") or [])
        expected_refusal = bool(case.get("requires_refusal"))
        row["answer"] = {
            "status": answer.status if answer else "failed",
            "reason_code": answer.reason_code if answer else "generation_failed",
            "refusal": answer.refusal if answer else True,
            "expected_refusal": expected_refusal,
            "refusal_correct": bool(answer and answer.refusal == expected_refusal),
            "faithfulness": answer.faithfulness if answer else None,
            "citations": list(answer.citations) if answer else [],
            "cited_node_ids": cited_node_ids,
            "citation_hits_expected_evidence": (
                bool(expected & set(cited_node_ids)) if expected and answer and not answer.refusal else None
            ),
            "text": answer.text if answer else None,
            "attempts": attempts,
            "claims_raw": answer.claims_raw if answer else 0,
            "claims_supported": answer.claims_supported if answer else 0,
            "claims_rejected": answer.claims_rejected if answer else 0,
            "claims_selected": answer.claims_selected if answer else 0,
            "claims_overflow_dropped": (
                answer.claims_overflow_dropped if answer else 0
            ),
            "answer_completeness_proxy": (
                answer.answer_completeness_proxy if answer else None
            ),
            "claim_limit_retry_triggered": bool(
                answer and answer.claim_limit_retry_triggered
            ),
            "faithfulness_retry_triggered": bool(
                answer and answer.faithfulness_retry_triggered
            ),
            "usage": usage,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": error,
            "error_recovered": bool(error) and bool(
                answer and answer.reason_code != "grounding_validation_failed"
            ),
            "terminal_generation_error": bool(
                not answer
                or answer.reason_code == "grounding_validation_failed"
            ),
            "expected_safe_grounding_refusal": bool(
                expected_refusal
                and answer
                and answer.refusal
                and answer.reason_code == "grounding_validation_failed"
            ),
            "unexpected_terminal_generation_error": bool(
                (
                    not answer
                    or answer.reason_code == "grounding_validation_failed"
                )
                and not expected_refusal
            ),
        }

    await asyncio.gather(*(evaluate_one(row) for row in rows))
    await llm.aclose()


def _average(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


def metrics_from_rows(
    rows: list[dict[str, Any]],
    policy: dict[str, Any],
    release_status: str,
    evaluation_version: str = "V0-CANDIDATE",
) -> dict[str, Any]:
    applicable = [row for row in rows if row["retrieval_applicable"]]
    answers = [row["answer"] for row in rows if row.get("answer")]
    non_refusal_answers = [item for item in answers if not item["refusal"]]
    retrieval_latencies = [row["retrieval_latency_ms"] for row in rows]
    rerank_latencies = [
        float((row.get("rerank") or {}).get("elapsed_ms") or 0.0)
        for row in rows
    ]
    rerank_attempts = [
        row for row in rows
        if (row.get("rerank") or {}).get("mode") != "rrf"
    ]
    rerank_fallbacks = [
        row for row in rerank_attempts
        if bool((row.get("rerank") or {}).get("fallback"))
    ]
    generation_latencies = [item["latency_ms"] for item in answers]
    expected_refusals = [item for item in answers if item["expected_refusal"]]
    expected_answers = [item for item in answers if not item["expected_refusal"]]
    citation_checks = [
        item["citation_hits_expected_evidence"]
        for item in non_refusal_answers
        if item["citation_hits_expected_evidence"] is not None
    ]
    citation_opportunities = [
        row for row in rows
        if not row["answer"]["expected_refusal"]
        and bool(row.get("expected_evidence"))
    ]
    fixed_denominator_citation_checks = [
        float(row["answer"].get("citation_hits_expected_evidence") is True)
        for row in citation_opportunities
    ]
    all_knowledge_expected = all(row["expected_mode"] == "knowledge_base" for row in rows)
    router_correct = [row["router_mode"] == row["expected_mode"] for row in rows]
    retry_incidents = sum(int(item.get("attempts") or 0) > 1 for item in answers)
    terminal_errors = sum(
        bool(item.get("terminal_generation_error")) for item in answers
    )
    unexpected_terminal_errors = sum(
        bool(item.get("unexpected_terminal_generation_error"))
        for item in answers
    )
    expected_safe_grounding_refusals = sum(
        bool(item.get("expected_safe_grounding_refusal")) for item in answers
    )
    return {
        "schema_version": PHASE0_SCHEMA_VERSION,
        "baseline_version": evaluation_version,
        "status": "measured_pending_human_signoff",
        "coverage": {
            "total_cases": len(rows),
            "category_counts": dict(sorted(Counter(row["category"] for row in rows).items())),
            "retrieval_applicable_cases": len(applicable),
            "generation_cases": len(answers),
        },
        "latency": {
            "retrieval_mean_ms": _average(retrieval_latencies),
            "retrieval_p50_ms": _percentile(retrieval_latencies, 0.50),
            "retrieval_p95_ms": _percentile(retrieval_latencies, 0.95),
            "rerank_p50_ms": _percentile(rerank_latencies, 0.50),
            "rerank_p95_ms": _percentile(rerank_latencies, 0.95),
            "rerank_p99_ms": _percentile(rerank_latencies, 0.99),
            "generation_mean_ms": _average(generation_latencies),
            "generation_p50_ms": _percentile(generation_latencies, 0.50),
            "generation_p95_ms": _percentile(generation_latencies, 0.95),
        },
        "router": {
            "accuracy": _average([float(value) for value in router_correct]),
            "recall": _average([float(value) for value in router_correct]) if all_knowledge_expected else None,
            "precision": None,
            "precision_reason": "Golden V0 当前全为知识库问题，缺少 general 负样本",
        },
        "retrieval": {
            **{
                f"recall_at_{k}": _average([
                    float(row["recall_at"][str(k)]) for row in applicable
                ]) for k in (5, 10, 20, 50)
            },
            "mrr": _average([float(row["reciprocal_rank"]) for row in applicable]),
            "ndcg_at_10": _average([float(row["ndcg_at_10"]) for row in applicable]),
            "ranking": "production_dense_plus_bm25_rrf_plus_optional_cross_encoder",
            "rerank_attempt_cases": len(rerank_attempts),
            "rerank_fallback_cases": len(rerank_fallbacks),
            "rerank_fallback_rate": (
                round(len(rerank_fallbacks) / len(rerank_attempts), 4)
                if rerank_attempts else 0.0
            ),
        },
        "context": {
            "recall": _average([float(row["context_recall"]) for row in applicable]),
            "precision": _average([
                float(row["context_precision"])
                for row in applicable if row["context_precision"] is not None
            ]),
            "token_utilization": _average([
                float(row["context_token_utilization"]) for row in rows
            ]),
            "budget_compliance_rate": _average([
                float(row["context_tokens"] <= int(AI_CONFIG["rag_context_tokens"]))
                for row in rows
            ]),
        },
        "answer": {
            "correctness_proxy_evidence_hit": _average([
                float(value) for value in citation_checks
            ]),
            "correctness": None,
            "correctness_reason": "Expected Answer Points 尚需基线签署时人工确认",
            "faithfulness": _average([
                float(item["faithfulness"])
                for item in non_refusal_answers if item["faithfulness"] is not None
            ]),
            "citation_accuracy": _average([float(value) for value in citation_checks]),
            "citation_accuracy_scope": "published_non_refusal_answers_legacy_gate",
            "citation_expected_evidence_success_rate": _average(
                fixed_denominator_citation_checks
            ),
            "citation_expected_evidence_success_count": sum(
                fixed_denominator_citation_checks
            ),
            "citation_expected_evidence_opportunity_count": len(
                citation_opportunities
            ),
            "expected_answer_publication_rate": _average([
                float(not item["refusal"]) for item in expected_answers
            ]),
            "completeness": None,
            "completeness_reason": (
                "Expected Answer Points 尚未完整标注；仅观测 Claim 生存代理，"
                "不作为拒答或验收门禁"
            ),
            "answer_completeness_proxy": _average([
                float(item["answer_completeness_proxy"])
                for item in non_refusal_answers
                if item.get("answer_completeness_proxy") is not None
            ]),
            "claims_raw_total": sum(
                int(item.get("claims_raw") or 0) for item in answers
            ),
            "claims_supported_total": sum(
                int(item.get("claims_supported") or 0) for item in answers
            ),
            "claims_rejected_total": sum(
                int(item.get("claims_rejected") or 0) for item in answers
            ),
            "claims_overflow_dropped_total": sum(
                int(item.get("claims_overflow_dropped") or 0)
                for item in answers
            ),
            "refusal_accuracy": _average([
                float(item["refusal_correct"]) for item in answers
            ]),
            "no_answer_refusal_rate": _average([
                float(item["refusal"]) for item in expected_refusals
            ]),
            "unexpected_refusal_rate": _average([
                float(item["refusal"]) for item in expected_answers
            ]),
            "generation_retry_incident_cases": retry_incidents,
            "terminal_generation_error_cases": terminal_errors,
            "generation_error_cases": terminal_errors,
            "unexpected_terminal_generation_error_cases": (
                unexpected_terminal_errors
            ),
            "expected_safe_grounding_refusal_cases": (
                expected_safe_grounding_refusals
            ),
        },
        "security": {
            "acl_leakage_rate": None,
            "acl_leakage_reason": policy["access_control"]["acl_leakage_metric"],
            "unauthorized_citation_rate": 0.0,
            "prompt_injection_refusal_rate": _average([
                float(row["answer"]["refusal"])
                for row in rows
                if row["category"] == "prompt_injection" and row.get("answer")
            ]),
        },
        "release": {"status": release_status},
        "acceptance": {
            "passed": False,
            "blocking_reasons": [
                "需要人工抽查回答与 Expected Answer Points",
                "需要人工确认 Production Baseline V0",
            ],
        },
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    ensure_baseline_writable(
        Path(args.output_dir).resolve(),
        allow_replace=args.replace_signed_baseline,
    )
    policy = _load(Path(args.policy).resolve())
    if not policy["external_processing"]["dashscope_evaluation_allowed"]:
        raise RuntimeError("人工策略未允许 DashScope 评测")
    service = KnowledgeService()
    pointer_before = service.artifact_repository.releases.active()
    manifest = service.artifact_repository.releases.get(pointer_before["release_id"])
    service._validate_shadow_manifest(manifest)
    if args.use_signed_dataset:
        dataset = _load(Path(args.dataset).resolve())
        if dataset.get("status") != "signed_baseline_v0":
            raise RuntimeError("--use-signed-dataset 要求已签署的 V0 数据集")
        if dataset.get("release_id") != pointer_before["release_id"]:
            raise RuntimeError("已签署数据集与当前 Active Release 不一致")
    else:
        draft = _load(Path(args.draft).resolve())
        dataset = approve_and_map_dataset(draft, policy, service)
        _json_dump(Path(args.dataset).resolve(), dataset)
    selected_case_ids = list(dict.fromkeys(args.case_id or []))
    if selected_case_ids:
        available = {case["id"] for case in dataset["cases"]}
        unknown = sorted(set(selected_case_ids) - available)
        if unknown:
            raise RuntimeError(f"诊断用例不存在: {', '.join(unknown)}")
        selected = set(selected_case_ids)
        dataset = {
            **dataset,
            "cases": [
                case for case in dataset["cases"] if case["id"] in selected
            ],
        }

    llm = LLMService(AI_CONFIG["dashscope_api_key"], AI_CONFIG["model"])
    chat = ChatService(llm, service)
    chat.audit_recorder = RagAuditRecorder(enabled=False)
    rows, retrieval_meta, access_meta = await retrieval_evaluate(
        dataset, service, chat
    )
    await answer_evaluate(
        dataset,
        rows,
        retrieval_meta.pop("packed_by_id"),
        chat,
        concurrency=args.concurrency,
    )

    pointer_after = service.artifact_repository.releases.active()
    if pointer_after != pointer_before:
        raise RuntimeError("评测期间活动 Release 发生变化，结果已拒绝作为基线")
    release = _release_snapshot(Path(str(AI_CONFIG["rag_artifact_path"])))
    metrics = metrics_from_rows(
        rows, policy, release["status"], args.evaluation_version
    )
    output = Path(args.output_dir).resolve()
    evaluation = {
        "schema_version": PHASE0_SCHEMA_VERSION,
        "baseline_version": args.evaluation_version,
        "status": "measured_pending_human_signoff",
        "captured_at": _utc_now(),
        "dataset_name": dataset["name"],
        "dataset_question_fingerprint": dataset["question_fingerprint"],
        "release_id": dataset["release_id"],
        "access": access_meta,
        "embedding": retrieval_meta,
        "selection": {
            "mode": "diagnostic_subset" if selected_case_ids else "full",
            "case_ids": selected_case_ids,
        },
        "cases": rows,
    }
    git_status = (_git("status", "--short") or "").splitlines()
    config = {
        "schema_version": PHASE0_SCHEMA_VERSION,
        "baseline_version": args.evaluation_version,
        "captured_at": _utc_now(),
        "dataset": {
            "path": str(Path(args.dataset).resolve()),
            "question_fingerprint": dataset["question_fingerprint"],
            "status": dataset["status"],
            "cases": len(dataset["cases"]),
            "selection_mode": (
                "diagnostic_subset" if selected_case_ids else "full"
            ),
            "selected_case_ids": selected_case_ids,
        },
        "human_policy": policy,
        "code": {
            "commit": _git("rev-parse", "HEAD"),
            "branch": _git("branch", "--show-current"),
            "dirty": bool(git_status),
            "changed_paths": git_status,
        },
        "runtime": _runtime_snapshot(),
    }
    _write_yaml(output / "baseline_config.yaml", config)
    _json_dump(output / "baseline_release_manifest.json", release)
    _json_dump(output / "baseline_eval.json", evaluation)
    _json_dump(output / "baseline_metrics.json", metrics)
    return {
        "output_dir": str(output),
        "release_id": dataset["release_id"],
        "cases": len(rows),
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="执行 RAG Phase 0 生产基线评测")
    parser.add_argument("--draft", default=str(DEFAULT_DRAFT))
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--use-signed-dataset", action="store_true")
    parser.add_argument("--evaluation-version", default="V0-CANDIDATE")
    parser.add_argument("--replace-signed-baseline", action="store_true")
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="仅评测指定 Golden Case ID；可重复使用，仅用于诊断，不替代全量门禁",
    )
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
