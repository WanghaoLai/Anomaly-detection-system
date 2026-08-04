"""在现有 Chroma 知识库上评测 dense、轻量 hybrid 和可选 v4 向量。

默认只调用当前配置的 embedding 模型生成查询向量，不修改 Chroma。传入
--compare-v4 时会在内存中临时生成 v4 文档/查询向量，同样不会写入索引。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
sys.path.insert(0, str(BACKEND_DIR))

from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402


def _is_relevant(item: dict, case: dict) -> bool:
    content = str(item.get("content") or "").lower()
    return all(str(term).lower() in content for term in case.get("expected_all") or [])


def _lexical_score(question: str, document: str) -> float:
    return ChatService._lexical_score(question, document)


def _format_dense_results(raw: dict, query_index: int) -> List[dict]:
    documents = (raw.get("documents") or [[]])[query_index]
    metadatas = (raw.get("metadatas") or [[]])[query_index]
    distances = (raw.get("distances") or [[]])[query_index]
    results = []
    for content, metadata, distance in zip(documents, metadatas, distances):
        metadata = metadata or {}
        results.append({
            "content": content,
            "doc_id": metadata.get("doc_id"),
            "filename": metadata.get("filename"),
            "heading_path": metadata.get("heading_path"),
            "chunk_index": metadata.get("chunk_index"),
            "score": 1.0 - float(distance),
            "distance": float(distance),
        })
    return results


def _hybrid_results(
    question: str,
    dense_results: List[dict],
    all_records: List[dict],
    final_k: int,
    dense_score_threshold: float | None = None,
) -> List[dict]:
    selector = ChatService.__new__(ChatService)
    selector.rag_candidate_k = 8
    selector.rag_final_k = final_k
    selector.rag_score_threshold = (
        -1.0 if dense_score_threshold is None else dense_score_threshold
    )
    selector.rag_lexical_min_score = 0.08
    selected, _ = selector._select_hybrid_results(
        question,
        dense_results,
        all_records,
    )
    return selected


def _dense_results_at_threshold(
    dense_results: List[dict],
    threshold: float,
    final_k: int,
) -> List[dict]:
    """用与线上一致的阈值和去重逻辑选择 dense 结果。"""
    selected = []
    for item in sorted(
        dense_results,
        key=ChatService._result_score,
        reverse=True,
    ):
        if ChatService._result_score(item) < threshold:
            continue
        if not ChatService._is_near_duplicate(item, selected):
            selected.append(item)
        if len(selected) >= final_k:
            break
    return selected


def _metrics(rows: List[dict], field: str, category: str | None = None) -> dict:
    selected = [row for row in rows if category is None or row["category"] == category]
    if not selected:
        return {"questions": 0, "hit_rate": None}
    hits = sum(bool(row[field]) for row in selected)
    return {
        "questions": len(selected),
        "hits": hits,
        "hit_rate": round(hits / len(selected), 4),
    }


def _rank_metrics(rows: List[dict], prefix: str) -> dict:
    return {
        "all": _metrics(rows, prefix),
        "semantic": _metrics(rows, prefix, "semantic"),
        "exact": _metrics(rows, prefix, "exact"),
    }


def _cosine_rank(vectors: List[List[float]], query_vector: List[float], records: List[dict]) -> List[dict]:
    scored = []
    for vector, record in zip(vectors, records):
        score = sum(left * right for left, right in zip(vector, query_vector))
        scored.append({**record, "score": score})
    return sorted(scored, key=lambda item: item["score"], reverse=True)


def evaluate(dataset: dict, *, compare_v4: bool = False) -> dict:
    cases = list(dataset["questions"])
    service = KnowledgeService()
    config_report = service.validate_embedding_config()
    if not config_report["consistent"]:
        raise RuntimeError("现有 Chroma embedding 配置不一致：" + "; ".join(config_report["issues"]))

    chat = ChatService(None, service)
    candidate_k = chat.rag_candidate_k
    final_k = chat.rag_final_k
    index = service.collection.get(include=["documents", "metadatas"])
    documents = list(index.get("documents") or [])
    metadatas = list(index.get("metadatas") or [])
    records = [
        {
            "content": content,
            "doc_id": (metadata or {}).get("doc_id"),
            "filename": (metadata or {}).get("filename"),
            "heading_path": (metadata or {}).get("heading_path"),
            "chunk_index": (metadata or {}).get("chunk_index"),
        }
        for content, metadata in zip(documents, metadatas)
    ]
    if not records:
        raise RuntimeError("Chroma 知识库为空，无法评测")

    questions = [case["question"] for case in cases]
    query_vectors = service._get_embeddings(questions, text_type="query")
    raw = service.collection.query(
        query_embeddings=query_vectors,
        n_results=min(candidate_k, len(records)),
        include=["documents", "metadatas", "distances"],
    )

    threshold_candidates = sorted({
        0.15,
        0.20,
        0.25,
        0.30,
        round(chat.rag_score_threshold, 2),
    })

    rows = []
    for index, case in enumerate(cases):
        dense_candidates = _format_dense_results(raw, index)
        dense_selected, selection = chat._select_rag_results(dense_candidates)
        hybrid_selected, _ = chat._select_hybrid_results(
            case["question"],
            dense_candidates,
            records,
        )
        relevant_dense_ranks = [
            rank for rank, item in enumerate(dense_candidates, start=1) if _is_relevant(item, case)
        ]
        row = {
            "id": case["id"],
            "category": case["category"],
            "question": case["question"],
            "expected_all": case["expected_all"],
            "dense_candidate_hit": bool(relevant_dense_ranks),
            "dense_raw_hit": any(_is_relevant(item, case) for item in dense_candidates[:final_k]),
            "dense_pipeline_hit": any(_is_relevant(item, case) for item in dense_selected),
            "hybrid_hit": any(_is_relevant(item, case) for item in hybrid_selected),
            "first_relevant_rank": relevant_dense_ranks[0] if relevant_dense_ranks else None,
            "selection": selection,
            "top_dense_scores": [round(item["score"], 4) for item in dense_candidates[:final_k]],
            "dense_threshold_hits": {
                f"{threshold:.2f}": any(
                    _is_relevant(item, case)
                    for item in _dense_results_at_threshold(
                        dense_candidates,
                        threshold,
                        final_k,
                    )
                )
                for threshold in threshold_candidates
            },
        }
        rows.append(row)

    metrics = {
        "dense_candidate_at_8": _rank_metrics(rows, "dense_candidate_hit"),
        "dense_raw_at_4": _rank_metrics(rows, "dense_raw_hit"),
        "dense_pipeline_at_4": _rank_metrics(rows, "dense_pipeline_hit"),
        "hybrid_at_4": _rank_metrics(rows, "hybrid_hit"),
    }
    reciprocal_ranks = [
        1.0 / row["first_relevant_rank"] if row["first_relevant_rank"] else 0.0
        for row in rows
    ]
    metrics["dense_mrr_at_8"] = round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4)
    # threshold hit 存在嵌套字典中，这里展开成临时布尔字段以复用统一指标函数。
    metrics["dense_threshold_sweep"] = {}
    for threshold in threshold_candidates:
        key = f"{threshold:.2f}"
        field = f"dense_threshold_{key}"
        for row in rows:
            row[field] = row["dense_threshold_hits"][key]
        metrics["dense_threshold_sweep"][key] = _rank_metrics(rows, field)

    v4_metrics = None
    if compare_v4:
        v4 = KnowledgeService(embedding_model="text-embedding-v4")
        document_vectors = v4._get_embeddings(documents, text_type="document")
        v4_query_vectors = v4._get_embeddings(questions, text_type="query")
        for row, case, query_vector in zip(rows, cases, v4_query_vectors):
            ranked = _cosine_rank(document_vectors, query_vector, records)
            row["v4_raw_hit"] = any(_is_relevant(item, case) for item in ranked[:final_k])
        v4_metrics = _rank_metrics(rows, "v4_raw_hit")
        metrics["v4_raw_at_4"] = v4_metrics

    dense_rate = metrics["dense_pipeline_at_4"]["all"]["hit_rate"] or 0.0
    hybrid_rate = metrics["hybrid_at_4"]["all"]["hit_rate"] or 0.0
    dense_exact = metrics["dense_pipeline_at_4"]["exact"]["hit_rate"] or 0.0
    hybrid_exact = metrics["hybrid_at_4"]["exact"]["hit_rate"] or 0.0
    best_threshold_key, best_threshold_metrics = max(
        metrics["dense_threshold_sweep"].items(),
        key=lambda item: (
            item[1]["all"]["hit_rate"] or 0.0,
            item[1]["exact"]["hit_rate"] or 0.0,
            float(item[0]),
        ),
    )
    tuned_dense_rate = best_threshold_metrics["all"]["hit_rate"] or 0.0
    tuned_dense_exact = best_threshold_metrics["exact"]["hit_rate"] or 0.0
    dense_reference_rate = max(dense_rate, tuned_dense_rate)
    dense_reference_exact = max(dense_exact, tuned_dense_exact)
    recommend_hybrid = (
        hybrid_rate >= dense_reference_rate
        and (
            hybrid_rate - dense_reference_rate >= 0.05
            or hybrid_exact - dense_reference_exact >= 0.08
        )
    )

    if v4_metrics is None:
        v4_decision = {
            "recommend": False,
            "reason": "未运行 --compare-v4；保持现有模型，待隔离 A/B 结果后再决定",
        }
    else:
        v2_raw = metrics["dense_raw_at_4"]["all"]["hit_rate"] or 0.0
        v4_raw = v4_metrics["all"]["hit_rate"] or 0.0
        improvement = round(v4_raw - v2_raw, 4)
        v4_decision = {
            "recommend": improvement >= 0.05,
            "improvement": improvement,
            "reason": (
                "v4 Hit@4 提升达到 5 个百分点，可进入独立 collection 灰度"
                if improvement >= 0.05
                else "v4 提升不足 5 个百分点，不建议承担全量重嵌入成本"
            ),
        }

    return {
        "dataset": {
            "name": dataset.get("name"),
            "version": dataset.get("version"),
            "questions": len(cases),
        },
        "index": {
            "embedding_model": service.embedding_model,
            "documents": len({record.get("doc_id") for record in records}),
            "chunks": len(records),
        },
        "configuration": {
            "candidate_k": candidate_k,
            "final_k": final_k,
            "score_threshold": chat.rag_score_threshold,
        },
        "metrics": metrics,
        "decision": {
            "threshold": {
                "current": chat.rag_score_threshold,
                "suggested": float(best_threshold_key),
                "overall_improvement": round(tuned_dense_rate - dense_rate, 4),
                "reason": (
                    "当前阈值误杀较多相关分块，建议先在灰度环境下调整"
                    if float(best_threshold_key) != round(chat.rag_score_threshold, 2)
                    else "当前阈值无需调整"
                ),
            },
            "hybrid": {
                "recommend": recommend_hybrid,
                "overall_improvement_vs_tuned_dense": round(
                    hybrid_rate - dense_reference_rate,
                    4,
                ),
                "exact_improvement_vs_tuned_dense": round(
                    hybrid_exact - dense_reference_exact,
                    4,
                ),
                "reason": (
                    "轻量 hybrid 相比已调优阈值的 dense 仍有稳定提升"
                    if recommend_hybrid
                    else "轻量 hybrid 相比阈值调优后的 dense 未达到收益门槛"
                ),
            },
            "v4": v4_decision,
        },
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="评测当前 Chroma RAG 检索质量")
    parser.add_argument(
        "--dataset",
        default=str(PROJECT_ROOT / "config" / "rag_eval_questions.json"),
    )
    parser.add_argument("--compare-v4", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    dataset_path = Path(args.dataset).resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not 30 <= len(dataset.get("questions") or []) <= 50:
        raise ValueError("评测集必须包含 30～50 条问题")

    started = time.perf_counter()
    report = evaluate(dataset, compare_v4=args.compare_v4)
    report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    output_path = (
        Path(args.output).resolve()
        if args.output
        else PROJECT_ROOT / "reports" / f"rag_eval_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "output": str(output_path),
        "metrics": report["metrics"],
        "decision": report["decision"],
        "elapsed_seconds": report["elapsed_seconds"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
