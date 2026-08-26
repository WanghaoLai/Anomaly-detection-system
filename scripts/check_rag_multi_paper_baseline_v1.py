"""静态校验已冻结的多论文 RAG baseline_v1。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = PROJECT_ROOT / "config" / "rag_multi_paper_baseline_v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_baseline(baseline: dict) -> list[str]:
    errors: list[str] = []
    if baseline.get("schema_version") != "multi-paper-rag-baseline-v1":
        errors.append("baseline schema_version 非法")
    if baseline.get("baseline_id") != "baseline_v1":
        errors.append("baseline_id 必须为 baseline_v1")
    if baseline.get("status") != "frozen_observational_baseline":
        errors.append("baseline 尚未冻结")
    release = baseline.get("release") or {}
    if release.get("document_count") != 15:
        errors.append("baseline release 文档数必须为 15")
    if release.get("node_count") != 974:
        errors.append("baseline release 节点数漂移")
    if len(baseline.get("documents") or []) != 15:
        errors.append("baseline 文档映射必须为 15 条")
    if (baseline.get("reconciliation") or {}).get("issue_count") != 0:
        errors.append("baseline 发布对账存在问题")
    inputs = baseline.get("inputs") or {}
    for path_key, hash_key in (
        ("corpus_path", "corpus_sha256"),
        ("dataset_path", "dataset_sha256"),
    ):
        path = PROJECT_ROOT / str(inputs.get(path_key) or "")
        if not path.is_file():
            errors.append(f"baseline 输入不存在: {path_key}")
        elif _sha256(path) != inputs.get(hash_key):
            errors.append(f"baseline 输入哈希漂移: {path_key}")
    retrieval = ((baseline.get("metrics") or {}).get("retrieval") or {})
    generation = ((baseline.get("metrics") or {}).get("generation") or {})
    if retrieval.get("questions") != 42:
        errors.append("baseline 检索问题数必须为 42")
    ndcg = retrieval.get("ndcg_at_10")
    if ndcg is None or not 0 <= ndcg <= 1:
        errors.append("baseline nDCG@10 必须位于 [0,1]")
    if generation.get("generation_questions") != 40:
        errors.append("baseline 生成问题数必须为 40")
    if generation.get("unauthorized_leakage_rate") != 0.0:
        errors.append("baseline 未授权泄漏率必须为 0")
    gates = baseline.get("quality_gates") or {}
    summary = baseline.get("quality_gate_summary") or {}
    if summary.get("total") != len(gates):
        errors.append("质量门槛汇总数量不一致")
    if summary.get("passed") != sum(
        bool(item.get("passed")) for item in gates.values()
    ):
        errors.append("质量门槛通过数不一致")
    return errors


async def validate_runtime(baseline: dict) -> list[str]:
    """核对活动指针及 MySQL/文件/DocStore/Chroma 四方运行态。"""

    backend_dir = PROJECT_ROOT / "fastapi-app"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    from models import Knowledge
    from services.knowledge_service import KnowledgeService
    from settings import TORTOISE_ORM
    from tortoise import Tortoise

    errors: list[str] = []
    service = KnowledgeService()
    expected_release = baseline["release"]["release_id"]
    active = service.artifact_repository.releases.active() or {}
    if active.get("release_id") != expected_release:
        errors.append(
            "活动 release 漂移: "
            f"expected={expected_release} actual={active.get('release_id')}"
        )
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        rows = await Knowledge.all().order_by("id")
        sql_documents = [{
            "id": row.id,
            "filename": row.filename,
            "original_name": row.original_name,
            "file_size": row.file_size,
            "chunk_count": row.chunk_count,
        } for row in rows]
        reconciliation = service.reconcile_metadata(sql_documents)
        if not reconciliation.get("healthy"):
            errors.append(
                "运行态对账失败: "
                + json.dumps(reconciliation.get("issues"), ensure_ascii=False)
            )
        summary = reconciliation.get("summary") or {}
        for field, expected in (
            ("mysql_documents", 15),
            ("docstore_documents", 15),
            ("chroma_documents", 15),
            ("mysql_chunks", 974),
            ("docstore_nodes", 974),
            ("chroma_chunks", 974),
        ):
            if summary.get(field) != expected:
                errors.append(
                    f"运行态 {field} 漂移: "
                    f"expected={expected} actual={summary.get(field)}"
                )
    finally:
        await Tortoise.close_connections()
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify-runtime",
        action="store_true",
        help="连接本机 MySQL 并核对活动 release 与四方运行态",
    )
    args = parser.parse_args()
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    errors = validate_baseline(baseline)
    if args.verify_runtime:
        errors.extend(asyncio.run(validate_runtime(baseline)))
    if errors:
        print("baseline_v1 校验失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    suffix = "；运行态对账健康" if args.verify_runtime else ""
    print(
        "baseline_v1 校验通过：15 篇论文，974 个节点，42 条黄金问题"
        f"{suffix}。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
