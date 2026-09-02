"""Run the fixed Golden V0 retrieval smoke against one candidate release."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "fastapi-app"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.chat_service import ChatService  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from services.rag.document import (  # noqa: E402
    RELEASE_SMOKE_SCHEMA_VERSION,
    sha256_bytes,
    utc_now_iso,
)


DEFAULT_GOLDEN = ROOT / "config" / "rag_golden_dataset_v0.json"
DEFAULT_SMOKE_SET = ROOT / "config" / "rag_phase4_release_smoke_v0.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validated_cases(golden_path: Path, smoke_path: Path) -> tuple[list[dict], dict]:
    golden_bytes = golden_path.read_bytes()
    golden = json.loads(golden_bytes)
    smoke = _load_json(smoke_path)
    if golden.get("status") != "signed_baseline_v0":
        raise RuntimeError("Release Smoke 只允许使用已签署 Golden V0")
    binding = smoke.get("golden") or {}
    if binding.get("version") != golden.get("version"):
        raise RuntimeError("Smoke Set 的 Golden 版本不一致")
    if binding.get("question_fingerprint") != golden.get("question_fingerprint"):
        raise RuntimeError("Smoke Set 的 Golden 问题指纹不一致")
    if binding.get("file_sha256") != hashlib.sha256(golden_bytes).hexdigest():
        raise RuntimeError("Golden V0 文件已变化")
    case_ids = list(smoke.get("case_ids") or [])
    required = int((smoke.get("gate") or {}).get("required_questions") or 0)
    if required != 20 or len(case_ids) != 20 or len(set(case_ids)) != 20:
        raise RuntimeError("Release Smoke Set 必须固定为 20 条唯一问题")
    by_id = {str(case["id"]): case for case in golden.get("cases") or []}
    cases = []
    for case_id in case_ids:
        case = by_id.get(str(case_id))
        if case is None:
            raise RuntimeError(f"Golden V0 不存在 Smoke Case: {case_id}")
        if (case.get("review") or {}).get("status") != "approved":
            raise RuntimeError(f"Smoke Case 未完成人工审核: {case_id}")
        if not case.get("expected_evidence"):
            raise RuntimeError(f"Smoke Case 缺少 Expected Evidence: {case_id}")
        cases.append(case)
    return cases, smoke


def _dense_rows(raw: dict, query_index: int) -> list[dict]:
    ids = (raw.get("ids") or [[]])[query_index]
    documents = (raw.get("documents") or [[]])[query_index]
    metadatas = (raw.get("metadatas") or [[]])[query_index]
    distances = (raw.get("distances") or [[]])[query_index]
    return [{
        "node_id": str(node_id),
        "content": content,
        "score": 1.0 - float(distance),
        "distance": float(distance),
        **dict(metadata or {}),
    } for node_id, content, metadata, distance in zip(
        ids, documents, metadatas, distances
    )]


def evaluate(release_id: str, golden_path: Path, smoke_path: Path) -> dict:
    cases, smoke = _validated_cases(golden_path, smoke_path)
    service = KnowledgeService()
    manifest = service.artifact_repository.releases.get(release_id)
    service._validate_shadow_manifest(manifest)
    provider = str(
        (manifest.get("indexing") or {}).get(
            "vector_store_provider", "chroma"
        )
    )
    collection = service._database_for_provider(provider).get_collection(
        name=manifest["collection_name"]
    )
    snapshot = collection.get(include=["documents", "metadatas"])
    records = [{
        "node_id": str(node_id),
        "content": content,
        **dict(metadata or {}),
    } for node_id, content, metadata in zip(
        list(snapshot.get("ids") or []),
        list(snapshot.get("documents") or []),
        list(snapshot.get("metadatas") or []),
    )]
    if not records:
        raise RuntimeError("候选 Release 为空")
    chat = ChatService(None, service)
    chat.rag_final_k = int((smoke.get("gate") or {})["retrieval_final_k"])
    questions = [str(case["question"]) for case in cases]
    query_vectors = service._get_embeddings(questions, text_type="query")
    raw = collection.query(
        query_embeddings=query_vectors,
        n_results=min(max(chat.rag_candidate_k, chat.rag_final_k), len(records)),
        include=["documents", "metadatas", "distances"],
    )
    rows = []
    for index, case in enumerate(cases):
        selected, stats = chat._select_hybrid_results(
            str(case["question"]), _dense_rows(raw, index), records
        )
        selected_ids = [str(item.get("node_id") or "") for item in selected]
        expected = {str(item) for item in case.get("expected_evidence") or []}
        matched = sorted(expected.intersection(selected_ids))
        rows.append({
            "id": case["id"],
            "source_locator": case.get("source_locator"),
            "expected_evidence": sorted(expected),
            "selected_node_ids": selected_ids,
            "matched_evidence": matched,
            "passed": bool(matched),
            "selection_mode": stats.get("mode"),
        })
    hits = sum(1 for row in rows if row["passed"])
    passed = hits == len(rows) == 20
    manifest_path = service.artifact_repository.releases.release_path(release_id)
    return {
        "schema_version": RELEASE_SMOKE_SCHEMA_VERSION,
        "release_id": release_id,
        "collection_name": manifest["collection_name"],
        "manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
        "smoke_set_sha256": sha256_bytes(smoke_path.read_bytes()),
        "golden_question_fingerprint": (
            smoke.get("golden") or {}
        ).get("question_fingerprint"),
        "evaluated_at": utc_now_iso(),
        "question_count": len(rows),
        "evidence_hits": hits,
        "evidence_hit_rate": round(hits / len(rows), 6),
        "passed": passed,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 4 candidate Release Smoke")
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--smoke-set", default=str(DEFAULT_SMOKE_SET))
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    started = time.perf_counter()
    report = evaluate(
        args.release_id, Path(args.golden).resolve(), Path(args.smoke_set).resolve()
    )
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    service = KnowledgeService()
    service.artifact_repository.release_smokes.put(report)
    output = (
        Path(args.output).resolve()
        if args.output else ROOT / "reports" / f"rag_phase4_smoke_{args.release_id}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"],
        "release_id": report["release_id"],
        "evidence_hits": report["evidence_hits"],
        "question_count": report["question_count"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
