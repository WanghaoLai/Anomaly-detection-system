"""校验阶段 1 PaperDocument v2 冻结摘要，可选核对本地运行制品。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
SUMMARY_PATH = PROJECT_ROOT / "config" / "rag_phase1_paper_document_v2.json"
CORPUS_PATH = PROJECT_ROOT / "config" / "rag_multi_paper_corpus_v1.json"


def validate_static(summary: dict, corpus: dict) -> list[str]:
    errors = []
    if summary.get("schema_version") != "rag-phase1-result-v1":
        errors.append("阶段 1 schema_version 非法")
    if summary.get("implementation_status") != "complete":
        errors.append("阶段 1 实现尚未完成")
    if summary.get("candidate_status") != "validated_not_published_degraded":
        errors.append("阶段 1 候选状态非法")
    quality = summary.get("quality") or {}
    if quality.get("documents") != 15 or len(summary.get("documents") or []) != 15:
        errors.append("阶段 1 必须包含 15 篇 PaperDocument")
    if quality.get("blocks") != sum(
        int(item.get("block_count") or 0) for item in summary.get("documents") or []
    ):
        errors.append("阶段 1 Block 汇总不一致")
    if quality.get("blocked_documents") != 0:
        errors.append("阶段 1 存在阻断文档")
    documents = summary.get("documents") or []
    for field in ("work_id", "paper_document_id", "source_sha256", "block_ids_sha256"):
        values = [item.get(field) for item in documents]
        if any(not value for value in values) or len(values) != len(set(values)):
            errors.append(f"阶段 1 {field} 为空或重复")
    corpus_by_work = {item["work_id"]: item for item in corpus["documents"]}
    if set(corpus_by_work) != {item["work_id"] for item in documents}:
        errors.append("阶段 1 work_id 集合与冻结语料不一致")
    for item in documents:
        expected = corpus_by_work.get(item["work_id"])
        if expected and item["source_sha256"] != expected["sha256"]:
            errors.append(f"阶段 1 来源哈希漂移: {item['work_id']}")
    invariants = summary.get("invariants") or {}
    if not all(invariants.values()):
        errors.append("阶段 1 兼容/隔离不变量未全部满足")
    return errors


def validate_runtime(summary: dict) -> list[str]:
    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from services.rag.document import KnowledgeArtifactRepository
    from settings import AI_CONFIG

    repository = KnowledgeArtifactRepository(AI_CONFIG["rag_artifact_path"])
    errors = []
    active = repository.releases.active() or {}
    if active.get("release_id") != summary.get("active_baseline_release_id"):
        errors.append("阶段 1 后活动 baseline release 漂移")
    for item in summary["documents"]:
        try:
            record = repository.paper_documents.get(item["paper_document_id"])
        except Exception as exc:
            errors.append(f"PaperDocument 读取失败 {item['work_id']}: {exc}")
            continue
        if record["source"]["sha256"] != item["source_sha256"]:
            errors.append(f"PaperDocument 来源漂移: {item['work_id']}")
        if record["block_ids_sha256"] != item["block_ids_sha256"]:
            errors.append(f"PaperDocument Block 漂移: {item['work_id']}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-runtime", action="store_true")
    args = parser.parse_args()
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    errors = validate_static(summary, corpus)
    if args.verify_runtime:
        errors.extend(validate_runtime(summary))
    if errors:
        print("阶段 1 校验失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    suffix = "，运行制品一致" if args.verify_runtime else ""
    print(
        f"阶段 1 校验通过：15 篇 PaperDocument，"
        f"{summary['quality']['blocks']} 个结构块{suffix}。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
