"""把已发布 release 与全量评测结果冻结为阶段 0 baseline_v1。"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = PROJECT_ROOT / "config" / "rag_multi_paper_corpus_v1.json"
DEFAULT_DATASET = PROJECT_ROOT / "config" / "rag_multi_paper_eval_v1.json"
DEFAULT_PUBLISHED = PROJECT_ROOT / "reports" / "rag_multi_paper_published.json"
DEFAULT_EVALUATION = PROJECT_ROOT / "reports" / "rag_multi_paper_full_baseline_v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "config" / "rag_multi_paper_baseline_v1.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gate(value: float | int | None, operator: str, threshold: float) -> dict:
    passed = False
    if value is not None:
        passed = {
            "gte": float(value) >= threshold,
            "eq": float(value) == threshold,
            "lte": float(value) <= threshold,
        }[operator]
    return {
        "value": value,
        "operator": operator,
        "threshold": threshold,
        "passed": passed,
    }


def freeze(
    corpus_path: Path,
    dataset_path: Path,
    published_path: Path,
    evaluation_path: Path,
) -> dict[str, Any]:
    corpus = _load(corpus_path)
    dataset = _load(dataset_path)
    published = _load(published_path)
    evaluation = _load(evaluation_path)
    release = published.get("release") or {}
    reconciliation = published.get("reconciliation") or {}
    release_id = str(release.get("release_id") or "")
    if published.get("status") != "published":
        raise ValueError("入库报告不是 published 状态")
    if not release_id or evaluation.get("release_id") != release_id:
        raise ValueError("发布报告与全量评测 release_id 不一致")
    if evaluation.get("mode") != "full":
        raise ValueError("只允许冻结 mode=full 的评测报告")
    if not reconciliation.get("healthy"):
        raise ValueError("发布后对账不健康，禁止冻结 baseline")
    if len(corpus.get("documents") or []) != 15:
        raise ValueError("冻结语料必须恰好包含 15 篇论文")
    if len(dataset.get("questions") or []) != 42:
        raise ValueError("黄金评测集必须恰好包含 42 题")

    retrieval = dict(evaluation["metrics"]["retrieval"])
    generation = dict(evaluation["metrics"]["generation"])
    failures = [
        {
            "id": row["id"],
            "category": row["category"],
            "error": row.get("error"),
        }
        for row in evaluation.get("generation_cases") or []
        if row.get("status") == "failed"
    ]
    refusals = [
        row["id"] for row in evaluation.get("generation_cases") or []
        if row.get("refusal")
    ]
    gates = {
        "paper_recall_at_10": _gate(
            retrieval.get("paper_recall_at_10"), "gte", 0.90
        ),
        "evidence_recall_at_20": _gate(
            retrieval.get("evidence_recall_at_20"), "gte", 0.90
        ),
        "document_coverage_recall": _gate(
            retrieval.get("document_coverage_recall"), "gte", 0.85
        ),
        "citation_validity": _gate(
            generation.get("citation_validity"), "eq", 1.0
        ),
        "claim_faithfulness": _gate(
            generation.get("claim_faithfulness"), "gte", 0.95
        ),
        "negative_pass_rate": _gate(
            generation.get("negative_pass_rate"), "gte", 0.95
        ),
        "permission_bypass_block_rate": _gate(
            generation.get("permission_bypass_block_rate"), "eq", 1.0
        ),
        "unauthorized_leakage_rate": _gate(
            generation.get("unauthorized_leakage_rate"), "eq", 0.0
        ),
        "failed_questions": _gate(
            generation.get("failed_questions"), "eq", 0.0
        ),
    }
    documents = [{
        "work_id": item["work_id"],
        "frozen_document_id": item["frozen_document_id"],
        "runtime_document_id": item["runtime_document_id"],
        "filename": item["filename"],
        "chunk_count": item["chunk_count"],
    } for item in published["documents"]]
    return {
        "schema_version": "multi-paper-rag-baseline-v1",
        "baseline_id": "baseline_v1",
        "status": "frozen_observational_baseline",
        "frozen_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "corpus_id": corpus["corpus_id"],
        "release": {
            "release_id": release_id,
            "collection_name": release["collection_name"],
            "previous_release": published.get("previous_pointer"),
            "document_count": release["document_count"],
            "node_count": release["node_count"],
        },
        "inputs": {
            "corpus_path": str(corpus_path.relative_to(PROJECT_ROOT)),
            "corpus_sha256": _sha256(corpus_path),
            "dataset_path": str(dataset_path.relative_to(PROJECT_ROOT)),
            "dataset_sha256": _sha256(dataset_path),
            "published_report_sha256": _sha256(published_path),
            "full_evaluation_report_sha256": _sha256(evaluation_path),
        },
        "runtime": evaluation["runtime"],
        "index": evaluation["index"],
        "reconciliation": reconciliation["summary"],
        "metrics": evaluation["metrics"],
        "quality_gates": gates,
        "quality_gate_summary": {
            "passed": sum(item["passed"] for item in gates.values()),
            "total": len(gates),
            "all_passed": all(item["passed"] for item in gates.values()),
            "interpretation": (
                "阶段 0 冻结当前真实表现；未通过项是后续阶段改进目标，"
                "不是对历史结果的追溯性放宽。"
            ),
        },
        "known_gaps": {
            "generation_failures": failures,
            "controlled_refusal_case_ids": refusals,
            "unmapped_evidence_anchors": retrieval.get(
                "unmapped_evidence_anchors"
            ),
        },
        "documents": documents,
        "immutability": (
            "Do not edit baseline_v1 in place; create a new baseline version."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--published-report", type=Path, default=DEFAULT_PUBLISHED)
    parser.add_argument("--evaluation-report", type=Path, default=DEFAULT_EVALUATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(
            f"baseline 已存在，禁止原地覆盖；请创建新版本: {output}"
        )
    baseline = freeze(
        args.corpus.resolve(), args.dataset.resolve(),
        args.published_report.resolve(), args.evaluation_report.resolve(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": baseline["status"],
        "output": str(output),
        "release_id": baseline["release"]["release_id"],
        "quality_gates": baseline["quality_gate_summary"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
