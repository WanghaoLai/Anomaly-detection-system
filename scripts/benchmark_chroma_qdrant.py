"""Compare provider query latency without changing the Active Pointer."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.knowledge_service import KnowledgeService  # noqa: E402


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 3)


def _measure(collection, vectors: list[list[float]], repeats: int, top_k: int) -> dict:
    for vector in vectors[: min(3, len(vectors))]:
        collection.query(query_embeddings=[vector], n_results=top_k)
    samples = []
    for _ in range(repeats):
        for vector in vectors:
            started = time.perf_counter()
            result = collection.query(
                query_embeddings=[vector], n_results=top_k,
                include=["documents", "metadatas", "distances"],
            )
            if len((result.get("ids") or [[]])[0]) == 0:
                raise RuntimeError("性能测试检索结果为空")
            samples.append((time.perf_counter() - started) * 1000)
    return {
        "samples": len(samples),
        "mean_ms": round(statistics.fmean(samples), 3),
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": round(max(samples), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Chroma/Qdrant 本地检索延迟对比")
    parser.add_argument("release_id", help="Qdrant 候选 Release ID")
    parser.add_argument(
        "--dataset",
        default=str(PROJECT_ROOT / "config" / "rag_eval_questions.json"),
    )
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--allowed-p95-ratio", type=float, default=1.20)
    parser.add_argument("--absolute-noise-ms", type=float, default=5.0)
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="显式要求参考阈值通过；默认只生成开发观察报告",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "reports" / "qdrant_migration" / "latency.json"),
    )
    args = parser.parse_args()
    if args.repeats <= 0 or args.top_k <= 0:
        raise ValueError("repeats 和 top-k 必须大于 0")

    service = KnowledgeService()
    pointer = service.artifact_repository.releases.active()
    if pointer is None or service.active_vector_store_provider() != "chroma":
        raise RuntimeError("性能双跑时 Active Pointer 必须指向 Chroma")
    manifest = service.artifact_repository.releases.get(args.release_id)
    if (manifest.get("indexing") or {}).get("vector_store_provider") != "qdrant":
        raise RuntimeError("候选 Release 不是 Qdrant")

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    cases = list(dataset.get("questions") or dataset.get("cases") or [])
    if not cases:
        raise RuntimeError("性能数据集为空")
    vectors = service._get_embeddings(
        [str(case["question"]) for case in cases], text_type="query"
    )
    chroma = service._database_for_provider("chroma").get_collection(
        name=service.active_collection_name()
    )
    qdrant = service._database_for_provider("qdrant").get_collection(
        name=manifest["collection_name"]
    )
    baseline = _measure(chroma, vectors, args.repeats, args.top_k)
    candidate = _measure(qdrant, vectors, args.repeats, args.top_k)
    limit = max(
        baseline["p95_ms"] * args.allowed_p95_ratio,
        baseline["p95_ms"] + args.absolute_noise_ms,
    )
    passed = candidate["p95_ms"] <= limit
    report = {
        "schema_version": "chroma-qdrant-latency-v1",
        "release_id": args.release_id,
        "scope": "vector_query_only_embeddings_excluded",
        "configuration": {
            "questions": len(cases),
            "repeats": args.repeats,
            "top_k": args.top_k,
            "allowed_p95_ratio": args.allowed_p95_ratio,
            "absolute_noise_ms": args.absolute_noise_ms,
        },
        "chroma": baseline,
        "qdrant": candidate,
        "p95_limit_ms": round(limit, 3),
        "meets_reference_threshold": passed,
        "enforced": args.enforce,
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output), **report}, ensure_ascii=False, indent=2))
    return 0 if passed or not args.enforce else 2


if __name__ == "__main__":
    raise SystemExit(main())
