"""评测阶段 0 多论文候选或已发布 release。

``retrieval`` 模式直接评测指定影子 collection，不切换在线指针；``full`` 模式
要求该 release 已发布，并额外通过 ChatService 执行生成、引用和拒答评测。
权限覆盖题由确定性 ACL 内核评测，避免为了测试而修改冻结论文的真实权限。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.llm_service import LLMService  # noqa: E402
from services.rag.answering import GroundedPromptBuilder  # noqa: E402
from services.rag.core import AccessPrincipal, KnowledgeAccessPolicy  # noqa: E402
from settings import AI_CONFIG, TORTOISE_ORM  # noqa: E402
from tortoise import Tortoise  # noqa: E402


DEFAULT_DATASET = PROJECT_ROOT / "config" / "rag_multi_paper_eval_v1.json"
DEFAULT_CORPUS = PROJECT_ROOT / "config" / "rag_multi_paper_corpus_v1.json"
ACCESS_RANK = {"public": 0, "internal": 1, "admin": 2}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compact(value: object) -> str:
    return "".join(str(value or "").casefold().split())


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 2)
    result = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(result, 2)


def _dcg(relevance: list[int]) -> float:
    return sum(value / math.log2(index + 2) for index, value in enumerate(relevance))


def _ndcg(results: list[dict], relevant_docs: set[str], k: int) -> float:
    # 指标按论文而非节点计算。同一论文的多个节点只在首次出现时产生 gain，
    # 否则分子可能重复计分并使 nDCG 非法超过 1。
    seen_docs: set[str] = set()
    relevance = []
    for item in results[:k]:
        doc_id = str(item.get("doc_id"))
        relevance.append(
            1 if doc_id in relevant_docs and doc_id not in seen_docs else 0
        )
        seen_docs.add(doc_id)
    ideal = [1] * min(len(relevant_docs), k)
    denominator = _dcg(ideal)
    return min(1.0, _dcg(relevance) / denominator) if denominator else 0.0


def _collection_records(collection) -> list[dict]:
    raw = collection.get(include=["documents", "metadatas"])
    return [{
        "node_id": node_id,
        "content": content,
        "doc_id": str((metadata or {}).get("doc_id") or ""),
        "filename": (metadata or {}).get("filename"),
        "chunk_index": (metadata or {}).get("chunk_index"),
        "metadata": dict(metadata or {}),
    } for node_id, content, metadata in zip(
        list(raw.get("ids") or []),
        list(raw.get("documents") or []),
        list(raw.get("metadatas") or []),
    )]


def _allowed_work_ids(
    case: dict, dataset: dict, corpus_by_work: dict[str, dict]
) -> set[str]:
    principal = dataset["principals"][case["access_principal"]]
    clearance = str(principal["clearance"])
    overrides = dict(case.get("corpus_acl_overrides") or {})
    allowed = set()
    for work_id, doc in corpus_by_work.items():
        required = str(overrides.get(work_id, doc.get("access_level", "public")))
        if ACCESS_RANK[clearance] >= ACCESS_RANK[required]:
            allowed.add(work_id)
    return allowed


def _rrf(dense: list[dict], lexical: list[dict], limit: int = 20) -> list[dict]:
    scores: dict[str, float] = {}
    values: dict[str, dict] = {}
    for ranked in (dense, lexical):
        for rank, item in enumerate(ranked, 1):
            key = str(item["node_id"])
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
            values[key] = item
    ordered = sorted(scores, key=lambda key: (-scores[key], key))[:limit]
    return [{**values[key], "fusion_score": scores[key]} for key in ordered]


def _evidence_node_ids(
    case: dict, records: list[dict]
) -> tuple[set[str], list[dict]]:
    node_ids: set[str] = set()
    diagnostics = []
    by_doc: dict[str, list[dict]] = {}
    for record in records:
        by_doc.setdefault(record["doc_id"], []).append(record)
    for anchor in case.get("evidence_anchors") or []:
        terms = [_compact(term) for term in anchor["locator_terms"]]
        matched = []
        for record in by_doc.get(anchor["runtime_document_id"], []):
            text = _compact(record["content"])
            term_hits = sum(term in text for term in terms)
            if term_hits:
                matched.append((term_hits, record["node_id"]))
        if matched:
            best = max(item[0] for item in matched)
            node_ids.update(node_id for hits, node_id in matched if hits == best)
        diagnostics.append({
            "document_id": anchor["runtime_document_id"],
            "page": anchor["page"],
            "locator_terms": anchor["locator_terms"],
            "matched_node_ids": [node_id for _, node_id in matched],
        })
    return node_ids, diagnostics


def _principal_mapping(name: str, dataset: dict) -> dict:
    value = dataset["principals"][name]
    clearance = value["clearance"]
    return {
        "user_id": {"public": 9101, "internal": 9102, "admin": 9103}[clearance],
        "role": "管理员" if clearance == "admin" else "用户",
    }


def _acl_case_result(case: dict, dataset: dict) -> dict:
    principal = AccessPrincipal.from_mapping(
        _principal_mapping(case["access_principal"], dataset)
    )
    policy = KnowledgeAccessPolicy()
    checks = []
    for work_id, visibility in (case.get("corpus_acl_overrides") or {}).items():
        # 现有运行时没有独立的 clearance 字段；黄金集的 internal 层通过
        # allowed_user_ids 映射到固定内部评测主体，真实权限仍由服务端策略判定。
        metadata = {
            "visibility": "admin_only" if visibility == "admin" else visibility,
            "allowed_roles": (
                "管理员" if visibility == "admin" else "管理员,用户"
            ),
            "allowed_user_ids": "9102" if visibility == "internal" else "",
        }
        checks.append({
            "work_id": work_id,
            "required_access": visibility,
            "allowed": policy.is_allowed(metadata, principal),
        })
    expected_allowed = bool(case.get("relevant_work_ids"))
    passed = bool(checks) and all(
        item["allowed"] == expected_allowed for item in checks
    )
    return {"passed": passed, "expected_allowed": expected_allowed, "checks": checks}


def evaluate_retrieval(
    dataset: dict,
    corpus: dict,
    ingestion: dict,
    service: KnowledgeService,
) -> tuple[dict, list[dict], list[dict]]:
    collection_name = ingestion["release"]["collection_name"]
    collection = service.client.get_collection(name=collection_name)
    records = _collection_records(collection)
    runtime_by_work = {
        item["work_id"]: item["runtime_document_id"]
        for item in ingestion["documents"]
    }
    frozen_to_runtime = {
        item["frozen_document_id"]: item["runtime_document_id"]
        for item in ingestion["documents"]
    }
    corpus_by_work = {item["work_id"]: item for item in corpus["documents"]}
    rows = []
    anchor_rows = []
    latencies = []
    evaluable_rows = []
    for case in dataset["questions"]:
        started = time.perf_counter()
        history = list(case.get("history") or [])
        transformer = ChatService.__new__(ChatService)
        transformer.rag_query_history_turns = int(
            AI_CONFIG.get("rag_query_history_turns", 2)
        )
        query = ChatService._build_retrieval_query(
            transformer, case["question"], history
        )
        allowed_works = _allowed_work_ids(case, dataset, corpus_by_work)
        allowed_docs = {
            runtime_by_work[work_id]
            for work_id in allowed_works
            if work_id in runtime_by_work
        }
        candidates = [record for record in records if record["doc_id"] in allowed_docs]
        query_vector = service._get_embeddings([query], text_type="query")[0]
        raw = collection.query(
            query_embeddings=[query_vector],
            n_results=min(20, max(1, collection.count())),
            where={"doc_id": {"$in": sorted(allowed_docs)}} if allowed_docs else None,
            include=["documents", "metadatas", "distances"],
        ) if allowed_docs else {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        dense = []
        for node_id, content, metadata, distance in zip(
            (raw.get("ids") or [[]])[0],
            (raw.get("documents") or [[]])[0],
            (raw.get("metadatas") or [[]])[0],
            (raw.get("distances") or [[]])[0],
        ):
            dense.append({
                "node_id": node_id,
                "content": content,
                "doc_id": str((metadata or {}).get("doc_id") or ""),
                "filename": (metadata or {}).get("filename"),
                "chunk_index": (metadata or {}).get("chunk_index"),
                "score": 1.0 - float(distance),
            })
        lexical = []
        for record in candidates:
            score = ChatService._lexical_score(query, record["content"])
            if score > 0:
                lexical.append({**record, "lexical_score": score})
        lexical.sort(key=lambda item: (-item["lexical_score"], item["node_id"]))
        hybrid = _rrf(dense, lexical[:20], limit=20)

        relevant_docs = {
            runtime_by_work[work_id]
            for work_id in case.get("relevant_work_ids") or []
            if work_id in runtime_by_work
        }
        runtime_case = dict(case)
        runtime_case["evidence_anchors"] = [{
            **anchor,
            "runtime_document_id": frozen_to_runtime[anchor["document_id"]],
        } for anchor in case.get("evidence_anchors") or []]
        evidence_ids, diagnostics = _evidence_node_ids(runtime_case, records)
        anchor_rows.extend({"case_id": case["id"], **item} for item in diagnostics)
        retrieved_docs_10 = {item["doc_id"] for item in hybrid[:10]}
        retrieved_nodes_20 = {item["node_id"] for item in hybrid[:20]}
        first_rank = next((
            index for index, item in enumerate(hybrid, 1)
            if item["doc_id"] in relevant_docs
        ), None)
        elapsed_ms = (time.perf_counter() - started) * 1000
        latencies.append(elapsed_ms)
        row = {
            "id": case["id"],
            "category": case["category"],
            "query": query,
            "relevant_runtime_document_ids": sorted(relevant_docs),
            "evidence_node_ids": sorted(evidence_ids),
            "dense_top20": [item["node_id"] for item in dense],
            "hybrid_top20": [item["node_id"] for item in hybrid],
            "hybrid_top20_document_ids": [item["doc_id"] for item in hybrid],
            "paper_recall_at_10": (
                len(retrieved_docs_10 & relevant_docs) / len(relevant_docs)
                if relevant_docs else None
            ),
            "evidence_recall_at_20": (
                len(retrieved_nodes_20 & evidence_ids) / len(evidence_ids)
                if evidence_ids else None
            ),
            "document_coverage_recall": (
                len(retrieved_docs_10 & relevant_docs) / len(relevant_docs)
                if len(relevant_docs) > 1 else None
            ),
            "mrr": 1.0 / first_rank if first_rank else 0.0,
            "ndcg_at_10": _ndcg(hybrid, relevant_docs, 10),
            "hit_at_4": any(
                item["doc_id"] in relevant_docs for item in hybrid[:4]
            ) if relevant_docs else None,
            "latency_ms": round(elapsed_ms, 2),
        }
        rows.append(row)
        if relevant_docs and case["category"] != "permission":
            evaluable_rows.append(row)

    def average(field: str, *, require_not_none: bool = True) -> float | None:
        values = [row[field] for row in evaluable_rows if row[field] is not None]
        if not values and require_not_none:
            return None
        return round(sum(values) / len(values), 4) if values else None

    metrics = {
        "questions": len(rows),
        "retrieval_questions": len(evaluable_rows),
        "paper_recall_at_10": average("paper_recall_at_10"),
        "evidence_recall_at_20": average("evidence_recall_at_20"),
        "document_coverage_recall": average("document_coverage_recall"),
        "mrr_at_20": average("mrr"),
        "ndcg_at_10": average("ndcg_at_10"),
        "hit_at_4": average("hit_at_4"),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "unmapped_evidence_anchors": sum(
            not item["matched_node_ids"] for item in anchor_rows
        ),
    }
    return metrics, rows, anchor_rows


async def evaluate_generation(dataset: dict, ingestion: dict) -> tuple[dict, list[dict]]:
    service = KnowledgeService()
    active = service.artifact_repository.releases.active()
    release_id = ingestion["release"]["release_id"]
    if active is None or active.get("release_id") != release_id:
        raise RuntimeError("full 模式要求候选 release 已经发布")
    await Tortoise.init(config=TORTOISE_ORM)
    llm = LLMService(
        api_key=str(AI_CONFIG.get("dashscope_api_key") or ""),
        model=str(AI_CONFIG.get("model") or "qwen-turbo"),
    )
    chat = ChatService(llm, service)
    rows: list[dict] = []
    latencies: list[float] = []
    acl_results: list[dict] = []
    concurrency = max(1, min(3, int(AI_CONFIG.get("rag_eval_concurrency", 3))))
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_case(case: dict) -> tuple[dict, float | None]:
        if case["category"] == "permission":
            acl = _acl_case_result(case, dataset)
            return ({
                "id": case["id"], "category": case["category"],
                "acl": acl, "generation_skipped": "synthetic_acl_override",
            }, None)

        async with semaphore:
            started = time.perf_counter()
            error = None
            try:
                answer = await chat.answer(
                    case["question"],
                    list(case.get("history") or []),
                    principal=_principal_mapping(
                        case["access_principal"], dataset
                    ),
                    audit_context={"evaluation_case_id": case["id"]},
                )
                answer_text = answer.text
                refusal = answer.refusal
                citations_valid = bool(answer.refusal) or all(
                    claim.citations for claim in answer.claims
                )
                faithfulness = answer.faithfulness
            except Exception as exc:
                answer_text = ""
                refusal = False
                citations_valid = False
                faithfulness = 0.0
                error = f"{type(exc).__name__}: {exc}"
            elapsed_ms = (time.perf_counter() - started) * 1000
            expected_scores = [
                ChatService._lexical_score(claim, answer_text)
                for claim in case["expected_claims"]
            ]
            forbidden_hits = [
                claim for claim in case["forbidden_claims"]
                if _compact(claim) and _compact(claim) in _compact(answer_text)
            ]
            negative = case["category"] == "negative"
            return ({
                "id": case["id"],
                "category": case["category"],
                "text": answer_text,
                "refusal": refusal,
                "status": "failed" if error else "completed",
                "error": error,
                "citation_validity": citations_valid,
                "faithfulness": faithfulness,
                "expected_claim_coverage": round(
                    sum(score >= 0.20 for score in expected_scores)
                    / len(expected_scores), 4
                ),
                "forbidden_claim_hits": forbidden_hits,
                "negative_pass": (
                    (refusal or not forbidden_hits) if negative else None
                ),
                "latency_ms": round(elapsed_ms, 2),
            }, elapsed_ms)

    try:
        evaluated = await asyncio.gather(*(
            evaluate_case(case) for case in dataset["questions"]
        ))
        rows = [row for row, _ in evaluated]
        latencies = [elapsed for _, elapsed in evaluated if elapsed is not None]
        acl_results = [
            row["acl"] for row in rows if "acl" in row
        ]
    finally:
        await llm.aclose()
        await Tortoise.close_connections()

    generated = [row for row in rows if "citation_validity" in row]
    negatives = [row for row in generated if row["category"] == "negative"]
    metrics = {
        "generation_questions": len(generated),
        "failed_questions": sum(row["status"] == "failed" for row in generated),
        "citation_validity": round(
            sum(bool(row["citation_validity"]) for row in generated)
            / len(generated), 4
        ) if generated else None,
        "claim_faithfulness": round(
            statistics.fmean(float(row["faithfulness"]) for row in generated), 4
        ) if generated else None,
        "expected_claim_coverage": round(
            statistics.fmean(row["expected_claim_coverage"] for row in generated), 4
        ) if generated else None,
        "forbidden_claim_rate": round(
            sum(bool(row["forbidden_claim_hits"]) for row in generated)
            / len(generated), 4
        ) if generated else None,
        "negative_pass_rate": round(
            sum(bool(row["negative_pass"]) for row in negatives)
            / len(negatives), 4
        ) if negatives else None,
        "permission_bypass_block_rate": round(
            sum(bool(item["passed"]) for item in acl_results)
            / len(acl_results), 4
        ) if acl_results else None,
        "unauthorized_leakage_rate": round(
            sum(not item["passed"] for item in acl_results)
            / len(acl_results), 4
        ) if acl_results else None,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
    }
    return metrics, rows


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
        text=True, capture_output=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--ingestion-report", type=Path, required=True)
    parser.add_argument("--mode", choices=("retrieval", "full"), default="retrieval")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    dataset_path = args.dataset.resolve()
    corpus_path = args.corpus.resolve()
    ingestion_path = args.ingestion_report.resolve()
    dataset = _load_json(dataset_path)
    corpus = _load_json(corpus_path)
    ingestion = _load_json(ingestion_path)
    if ingestion.get("corpus_id") != corpus.get("corpus_id"):
        raise ValueError("入库报告与冻结语料 corpus_id 不一致")
    if len(dataset.get("questions") or []) != 42:
        raise ValueError("baseline_v1 黄金集必须恰好包含 42 题")

    service = KnowledgeService()
    started = time.perf_counter()
    retrieval_metrics, retrieval_rows, anchor_rows = evaluate_retrieval(
        dataset, corpus, ingestion, service
    )
    generation_metrics = None
    generation_rows = None
    if args.mode == "full":
        generation_metrics, generation_rows = asyncio.run(
            evaluate_generation(dataset, ingestion)
        )
    report = {
        "schema_version": 1,
        "mode": args.mode,
        "corpus_id": corpus["corpus_id"],
        "release_id": ingestion["release"]["release_id"],
        "collection_name": ingestion["release"]["collection_name"],
        "git_commit": _git_commit(),
        "inputs": {
            "corpus_path": str(corpus_path),
            "corpus_sha256": _file_sha256(corpus_path),
            "dataset_path": str(dataset_path),
            "dataset_sha256": _file_sha256(dataset_path),
            "ingestion_report_path": str(ingestion_path),
            "ingestion_report_sha256": _file_sha256(ingestion_path),
        },
        "runtime": {
            "embedding_model": service.embedding_model,
            "embedding_provider": service.embedding_provider,
            "llm_model": AI_CONFIG.get("model"),
            "candidate_k": AI_CONFIG.get("rag_candidate_k"),
            "dense_candidate_k": AI_CONFIG.get("rag_dense_candidate_k"),
            "lexical_candidate_k": AI_CONFIG.get("rag_lexical_candidate_k"),
            "final_k": AI_CONFIG.get("rag_final_k"),
            "score_threshold": AI_CONFIG.get("rag_score_threshold"),
            "hybrid_enabled": AI_CONFIG.get("rag_hybrid_enabled"),
            "context_tokens": AI_CONFIG.get("rag_context_tokens"),
            "knowledge_prompt_version": GroundedPromptBuilder.KNOWLEDGE_PROMPT_VERSION,
            "knowledge_prompt_sha256": hashlib.sha256(
                GroundedPromptBuilder.KNOWLEDGE_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
        },
        "index": {
            "documents": ingestion["release"]["document_count"],
            "nodes": ingestion["release"]["node_count"],
            "corpus_documents": ingestion["corpus_document_count"],
            "preserved_existing_documents": ingestion[
                "preserved_existing_documents"
            ],
        },
        "metrics": {
            "retrieval": retrieval_metrics,
            "generation": generation_metrics,
        },
        "retrieval_cases": retrieval_rows,
        "evidence_anchor_mapping": anchor_rows,
        "generation_cases": generation_rows,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "mode": args.mode,
        "release_id": report["release_id"],
        "metrics": report["metrics"],
        "elapsed_seconds": report["elapsed_seconds"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
