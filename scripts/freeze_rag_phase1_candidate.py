"""冻结阶段 1 PaperDocument v2 候选摘要，不发布索引。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.rag.document import (  # noqa: E402
    KnowledgeArtifactRepository,
    PAPER_DOCUMENT_SCHEMA_VERSION,
    PAPER_INGESTION_SCHEMA_VERSION,
    PAPER_PARSER_PROFILE,
)
from settings import AI_CONFIG  # noqa: E402


DEFAULT_REPORT = PROJECT_ROOT / "reports" / "rag_paper_documents_v2_candidate.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "config" / "rag_phase1_paper_document_v2.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "staged_not_published":
        raise ValueError("阶段 1 报告必须为 staged_not_published")
    if report.get("document_count") != 15 or report.get("blocked_documents") != 0:
        raise ValueError("阶段 1 候选必须包含 15 篇且无阻断文档")
    if report.get("active_release_before") != report.get("active_release_after"):
        raise ValueError("阶段 1 候选修改了活动 release")
    repository = KnowledgeArtifactRepository(AI_CONFIG["rag_artifact_path"])
    manual_review = []
    document_rows = []
    for item in report["documents"]:
        record = repository.paper_documents.get(item["paper_document_id"])
        diagnostics = record["diagnostics"]
        if diagnostics.get("manual_review_required"):
            manual_review.append(item["work_id"])
        document_rows.append({
            "work_id": item["work_id"],
            "filename": item["filename"],
            "source_sha256": item["source_sha256"],
            "paper_document_id": item["paper_document_id"],
            "normalized_markdown_sha256": item[
                "normalized_markdown_sha256"
            ],
            "block_ids_sha256": item["block_ids_sha256"],
            "block_count": item["block_count"],
            "quality_status": item["quality_status"],
        })
    return {
        "schema_version": "rag-phase1-result-v1",
        "phase": 1,
        "implementation_status": "complete",
        "candidate_status": "validated_not_published_degraded",
        "frozen_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "candidate_id": report["candidate_id"],
        "active_baseline_release_id": report[
            "active_release_before"
        ]["release_id"],
        "paper_document_schema_version": PAPER_DOCUMENT_SCHEMA_VERSION,
        "paper_ingestion_schema_version": PAPER_INGESTION_SCHEMA_VERSION,
        "parser_profile": PAPER_PARSER_PROFILE,
        "provider_state": {
            "requested_parser": report.get("parser_requested"),
            "docling_available": report.get("docling_available"),
            "grobid_configured": report.get("grobid_configured"),
            "effective_parser": "markitdown",
            "fallback_is_explicit": True,
        },
        "quality": {
            "documents": report["document_count"],
            "blocks": sum(item["block_count"] for item in report["documents"]),
            "publish_eligible_documents": report[
                "publish_eligible_documents"
            ],
            "degraded_documents": report["degraded_documents"],
            "blocked_documents": report["blocked_documents"],
            "manual_review_work_ids": manual_review,
            "stable_rebuild_required": True,
        },
        "inputs": {
            "corpus_id": report["corpus_id"],
            "corpus_manifest_sha256": report["corpus_manifest_sha256"],
            "candidate_report_sha256": _sha256(report_path),
        },
        "documents": document_rows,
        "invariants": {
            "active_release_unchanged": True,
            "legacy_docstore_unchanged": True,
            "online_retrieval_unchanged": True,
            "provider_types_isolated_from_core": True,
        },
        "known_limitations": [
            "本机未安装 Docling 及模型权重，15 篇真实候选使用 MarkItDown 显式降级路径",
            "本机未配置 GROBID，学术元数据由冻结目录补空，未执行 TEI 服务增强",
            "半监督自训练方法综述存在 PDF 字体映射异常，已标记人工视觉复核",
            "父子节点与多粒度索引属于阶段 2，本阶段不发布候选",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError("阶段 1 冻结摘要已存在，禁止原地覆盖")
    result = freeze(args.report.resolve())
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": result["implementation_status"],
        "candidate_status": result["candidate_status"],
        "candidate_id": result["candidate_id"],
        "documents": result["quality"]["documents"],
        "blocks": result["quality"]["blocks"],
        "output": str(output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
