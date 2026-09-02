"""Run the frozen retrieval dataset against Chroma and a Qdrant candidate."""

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

from evaluate_rag import evaluate  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402


def _metric(report: dict, name: str) -> float:
    if name == "dense_mrr_at_8":
        return float(report["metrics"][name])
    return float(report["metrics"][name]["all"]["hit_rate"] or 0.0)


def _mean_top_k_overlap(baseline: dict, candidate: dict, k: int = 8) -> float:
    baseline_rows = {row["id"]: row for row in baseline["cases"]}
    overlaps = []
    for row in candidate["cases"]:
        baseline_row = baseline_rows.get(row["id"])
        if baseline_row is None:
            raise RuntimeError(f"候选报告出现基线中不存在的问题: {row['id']}")
        left = set((baseline_row.get("top_dense_node_ids") or [])[:k])
        right = set((row.get("top_dense_node_ids") or [])[:k])
        denominator = max(1, min(k, len(left), len(right)))
        overlaps.append(len(left & right) / denominator)
    return round(sum(overlaps) / len(overlaps), 6) if overlaps else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Chroma/Qdrant 本地检索质量对比")
    parser.add_argument("release_id", help="Qdrant 候选 Release ID")
    parser.add_argument(
        "--dataset",
        default=str(PROJECT_ROOT / "config" / "rag_eval_questions.json"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "reports" / "qdrant_migration" / "comparison.json"),
    )
    parser.add_argument(
        "--enforce",
        action="store_true",
        help="显式要求参考阈值通过；默认只生成开发观察报告",
    )
    args = parser.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    service = KnowledgeService()
    pointer = service.artifact_repository.releases.active()
    if pointer and service.active_vector_store_provider() != "chroma":
        raise RuntimeError("双跑基线阶段 Active Pointer 必须仍指向 Chroma")
    chroma_collection = service.active_collection_name()
    manifest = service.artifact_repository.releases.get(args.release_id)
    if (manifest.get("indexing") or {}).get("vector_store_provider") != "qdrant":
        raise RuntimeError("候选 Release 不是 Qdrant")

    started = time.perf_counter()
    chroma = evaluate(
        dataset,
        collection_name=chroma_collection,
        collection_provider="chroma",
    )
    qdrant = evaluate(
        dataset,
        collection_name=manifest["collection_name"],
        collection_provider="qdrant",
    )
    gate_limits = {
        "dense_candidate_at_8": 0.025,
        "dense_raw_at_4": 0.025,
        "hybrid_at_4": 0.025,
        "dense_mrr_at_8": 0.03,
    }
    gates = {}
    for name, allowed_drop in gate_limits.items():
        baseline = _metric(chroma, name)
        candidate = _metric(qdrant, name)
        drop = round(baseline - candidate, 6)
        gates[name] = {
            "baseline": baseline,
            "candidate": candidate,
            "drop": drop,
            "allowed_drop": allowed_drop,
            "passed": drop <= allowed_drop,
        }
    top_8_overlap = _mean_top_k_overlap(chroma, qdrant)
    gates["mean_top_8_overlap"] = {
        "baseline": 1.0,
        "candidate": top_8_overlap,
        "drop": round(1.0 - top_8_overlap, 6),
        "allowed_drop": 0.15,
        "passed": top_8_overlap >= 0.85,
    }
    report = {
        "schema_version": "chroma-qdrant-comparison-v1",
        "release_id": args.release_id,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "meets_reference_thresholds": all(
            item["passed"] for item in gates.values()
        ),
        "enforced": args.enforce,
        "gates": gates,
        "chroma": chroma,
        "qdrant": qdrant,
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(output),
        "meets_reference_thresholds": report["meets_reference_thresholds"],
        "gates": gates,
    }, ensure_ascii=False, indent=2))
    return (
        0
        if report["meets_reference_thresholds"] or not args.enforce
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
