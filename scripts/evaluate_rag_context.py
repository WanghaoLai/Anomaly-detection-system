"""评测 P4 Context Packing 的精度、预算、引用和去重契约。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from evaluate_rag import _format_dense_results, _is_relevant  # noqa: E402
from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.rag.answering.context import _canonical_text  # noqa: E402
from settings import AI_CONFIG  # noqa: E402


def context_precision(relevance: list[bool]) -> float:
    """Ragas 风格 AP：只在相关上下文所在排名累计 Precision@k。"""

    relevant_count = sum(relevance)
    if not relevant_count:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for rank, relevant in enumerate(relevance, start=1):
        if relevant:
            hits += 1
            precision_sum += hits / rank
    return precision_sum / relevant_count


def _contains_any_expected(text: str, case: dict) -> bool:
    lowered = str(text or "").lower()
    return any(
        str(term).lower() in lowered
        for term in case.get("expected_all") or []
    )


def evaluate(dataset: dict, collection_name: str | None = None) -> dict:
    service = KnowledgeService()
    collection = (
        service.client.get_collection(name=collection_name)
        if collection_name
        else service.collection
    )
    config = service.validate_embedding_config()
    if not collection_name and not config["consistent"]:
        raise RuntimeError("在线 Embedding 契约不一致：" + "; ".join(config["issues"]))

    chat = ChatService(None, service)
    index = collection.get(include=["documents", "metadatas"])
    documents = list(index.get("documents") or [])
    metadatas = list(index.get("metadatas") or [])
    node_ids = list(index.get("ids") or [])
    records = [{
        "node_id": node_id,
        "content": content,
        "doc_id": (metadata or {}).get("doc_id"),
        "filename": (metadata or {}).get("filename"),
        "heading_path": (metadata or {}).get("heading_path"),
        "section_path": (metadata or {}).get("section_path"),
        "position": (metadata or {}).get("position"),
        "line_start": (metadata or {}).get("line_start"),
        "line_end": (metadata or {}).get("line_end"),
        "char_start": (metadata or {}).get("char_start"),
        "char_end": (metadata or {}).get("char_end"),
        "chunk_index": (metadata or {}).get("chunk_index"),
    } for node_id, content, metadata in zip(node_ids, documents, metadatas)]
    if not records:
        raise RuntimeError("知识库为空，无法评测 Context Packing")

    cases = list(dataset.get("questions") or [])
    questions = [case["question"] for case in cases]
    query_vectors = service._get_embeddings(questions, text_type="query")
    raw = collection.query(
        query_embeddings=query_vectors,
        n_results=min(chat.rag_candidate_k, len(records)),
        include=["documents", "metadatas", "distances"],
    )

    rows = []
    for query_index, case in enumerate(cases):
        dense = _format_dense_results(raw, query_index)
        selected, _ = chat._select_hybrid_results(
            case["question"], dense, records
        )
        selection_hit = any(_is_relevant(item, case) for item in selected)
        packed = chat._pack_context(selected, query=case["question"])
        relevance = [
            _contains_any_expected(entry.text, case)
            for entry in packed.entries
        ]
        canonical_entries = [
            _canonical_text(entry.text) for entry in packed.entries
        ]
        citation_ids = [entry.citation_id for entry in packed.entries]
        node_ids_in_context = [entry.node_id for entry in packed.entries]
        rows.append({
            "id": case["id"],
            "category": case["category"],
            "context_precision": round(context_precision(relevance), 6),
            # 去重可能把互补证据分布到不同引用；覆盖率必须基于完整上下文，
            # Precision 则逐引用判断是否提供了至少一项有效证据。
            "context_hit": _is_relevant({"content": packed.text}, case),
            "selection_hit": selection_hit,
            "packed_nodes": len(packed.entries),
            "context_tokens": packed.token_count,
            "budget_ok": packed.token_count <= chat.rag_context_tokens,
            "citation_integrity": (
                citation_ids == [f"K{i}" for i in range(1, len(citation_ids) + 1)]
                and len(node_ids_in_context) == len(set(node_ids_in_context))
                and len(packed.citation_map) == len(packed.entries)
            ),
            "duplicate_free": len(canonical_entries) == len(set(canonical_entries)),
            "duplicate_nodes_removed": packed.duplicate_node_count,
            "omitted_nodes": packed.omitted_node_count,
            "selected_diagnostics": [{
                "node_id": item.get("node_id"),
                "relevant": _is_relevant(item, case),
                "expected_term_presence": {
                    str(term): str(term).lower() in str(
                        item.get("content") or ""
                    ).lower()
                    for term in case.get("expected_all") or []
                },
            } for item in selected],
            "packed_diagnostics": [{
                "citation_id": entry.citation_id,
                "node_id": entry.node_id,
                "expected_term_presence": {
                    str(term): str(term).lower() in entry.text.lower()
                    for term in case.get("expected_all") or []
                },
            } for entry in packed.entries],
        })

    count = len(rows)
    precision = sum(row["context_precision"] for row in rows) / count
    hit_rate = sum(row["context_hit"] for row in rows) / count
    selection_hit_rate = sum(row["selection_hit"] for row in rows) / count
    target = float(AI_CONFIG.get("rag_context_precision_target", 0.70))
    metrics = {
        "context_precision": round(precision, 4),
        "context_precision_target": target,
        "context_precision_passed": precision >= target,
        "context_hit_rate": round(hit_rate, 4),
        "selected_node_hit_rate": round(selection_hit_rate, 4),
        "packing_recall_vs_selected": round(
            hit_rate / selection_hit_rate if selection_hit_rate else 0.0, 4
        ),
        "budget_compliance_rate": round(
            sum(row["budget_ok"] for row in rows) / count, 4
        ),
        "citation_integrity_rate": round(
            sum(row["citation_integrity"] for row in rows) / count, 4
        ),
        "duplicate_free_rate": round(
            sum(row["duplicate_free"] for row in rows) / count, 4
        ),
        "max_context_tokens": max(row["context_tokens"] for row in rows),
        "configured_context_tokens": chat.rag_context_tokens,
        "average_packed_nodes": round(
            sum(row["packed_nodes"] for row in rows) / count, 2
        ),
    }
    passed = (
        metrics["context_precision_passed"]
        and metrics["budget_compliance_rate"] == 1.0
        and metrics["citation_integrity_rate"] == 1.0
        and metrics["duplicate_free_rate"] == 1.0
    )
    return {
        "ok": passed,
        "dataset": {
            "name": dataset.get("name"),
            "version": dataset.get("version"),
            "questions": count,
        },
        "index": {
            "collection_name": collection.name,
            "chunks": len(records),
        },
        "metrics": metrics,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="评测 P4 Context Packing")
    parser.add_argument(
        "--dataset",
        default=str(PROJECT_ROOT / "config" / "rag_eval_questions.json"),
    )
    parser.add_argument("--collection", default=None)
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "reports" / "rag_p4_context_eval.json"),
    )
    args = parser.parse_args()
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    if not 30 <= len(dataset.get("questions") or []) <= 50:
        raise ValueError("评测集必须包含 30～50 条问题")
    started = time.perf_counter()
    report = evaluate(dataset, args.collection)
    report["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "ok": report["ok"],
        "output": str(output),
        "metrics": report["metrics"],
        "elapsed_seconds": report["elapsed_seconds"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
