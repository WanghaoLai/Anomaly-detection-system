"""构建或发布阶段 0 多论文基线 release。

默认读取冻结清单并一次性构建未发布的影子 collection。``--publish-report``
读取已评测的入库报告，在同一 MySQL 事务中写入目录并原子切换发布指针；发布后
四方对账失败会恢复旧指针。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import Knowledge  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from settings import TORTOISE_ORM  # noqa: E402
from tortoise import Tortoise  # noqa: E402
from tortoise.transactions import in_transaction  # noqa: E402


DEFAULT_MANIFEST = PROJECT_ROOT / "config" / "rag_multi_paper_corpus_v1.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_entries(
    manifest: dict[str, Any], source_dir: Path, *, visibility: str
) -> list[dict[str, Any]]:
    if not source_dir.is_dir():
        raise ValueError(f"论文目录不存在: {source_dir}")
    documents = list(manifest.get("documents") or [])
    if len(documents) != 15:
        raise ValueError("冻结清单必须恰好包含 15 篇论文")
    expected_names = {item["filename"] for item in documents}
    actual_names = {path.name for path in source_dir.glob("*.pdf")}
    if actual_names != expected_names:
        raise ValueError(
            "论文集合与冻结清单不一致: "
            f"missing={sorted(expected_names - actual_names)} "
            f"added={sorted(actual_names - expected_names)}"
        )
    entries = []
    for doc in documents:
        path = source_dir / doc["filename"]
        if path.stat().st_size != int(doc["byte_size"]):
            raise ValueError(f"冻结文件大小漂移: {doc['filename']}")
        actual_hash = _sha256(path)
        if actual_hash != doc["sha256"]:
            raise ValueError(f"冻结文件 SHA256 漂移: {doc['filename']}")
        entries.append({
            "filename": doc["filename"],
            "file_bytes": path.read_bytes(),
            "sha256": doc["sha256"],
            "work_id": doc["work_id"],
            "frozen_document_id": doc["document_id"],
            "visibility": visibility,
            "allowed_roles": "管理员,用户",
            "allowed_user_ids": "",
        })
    return entries


def build_candidate(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.manifest.resolve()
    source_dir = args.source_dir.resolve()
    manifest = _load_json(manifest_path)
    entries = build_entries(manifest, source_dir, visibility=args.visibility)
    service = KnowledgeService()
    previous_pointer = service.artifact_repository.releases.active()
    started = time.perf_counter()
    staged = service.stage_corpus_release(
        entries,
        preserve_existing=not args.replace_catalog,
    )
    report = {
        "schema_version": 1,
        "status": "staged",
        "corpus_id": manifest["corpus_id"],
        "corpus_version": manifest["version"],
        "corpus_manifest": str(manifest_path),
        "corpus_manifest_sha256": _sha256(manifest_path),
        "source_dir": str(source_dir),
        "previous_pointer": previous_pointer,
        "release": staged["release"],
        "documents": staged["documents"],
        "preserved_existing_documents": staged["preserved_existing_documents"],
        "corpus_document_count": staged["corpus_document_count"],
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }
    if report["corpus_document_count"] != 15:
        service.discard_staged_release(report["release"]["release_id"])
        raise RuntimeError("候选 release 未包含全部 15 篇冻结论文")
    return report


async def _upsert_metadata(
    documents: list[dict[str, Any]], service, release_id: str
) -> dict:
    await Tortoise.init(config=TORTOISE_ORM)
    previous_pointer = None
    published = False
    try:
        async with in_transaction() as connection:
            expected_names = {str(item["filename"]) for item in documents}
            for item in documents:
                rows = await Knowledge.filter(
                    original_name=item["filename"]
                ).using_db(connection).order_by("id")
                values = {
                    "filename": item["runtime_document_id"],
                    "original_name": item["filename"],
                    "file_size": item["file_size"],
                    "chunk_count": item["chunk_count"],
                }
                if rows:
                    await Knowledge.filter(id=rows[0].id).using_db(connection).update(
                        **values
                    )
                    duplicate_ids = [row.id for row in rows[1:]]
                    if duplicate_ids:
                        await Knowledge.filter(id__in=duplicate_ids).using_db(
                            connection
                        ).delete()
                else:
                    await Knowledge.create(using_db=connection, **values)

            # MySQL 目录必须严格镜像目标 release。候选以 --replace-catalog
            # 构建时，旧 release 独有的目录行也必须在同一事务中移除，否则会
            # 形成 orphan_mysql_document，且 API 列表与实际检索索引不一致。
            catalog_rows = await Knowledge.all().using_db(connection).order_by("id")
            obsolete_ids = [
                row.id for row in catalog_rows
                if str(row.original_name or "") not in expected_names
            ]
            if obsolete_ids:
                await Knowledge.filter(id__in=obsolete_ids).using_db(
                    connection
                ).delete()

            previous_pointer = service.publish_staged_release(release_id)
            published = True

            rows = await Knowledge.all().using_db(connection).order_by("id")
            sql_documents = [{
                "id": row.id,
                "filename": row.filename,
                "original_name": row.original_name,
                "file_size": row.file_size,
                "chunk_count": row.chunk_count,
            } for row in rows]
            reconciliation = service.reconcile_metadata(sql_documents)
            if not reconciliation["healthy"]:
                raise RuntimeError(
                    "发布后四方对账失败: "
                    + json.dumps(reconciliation["issues"], ensure_ascii=False)
                )
        return {
            "previous_pointer": previous_pointer,
            "reconciliation": reconciliation,
        }
    except Exception:
        if published:
            service.rollback_published_release(release_id, previous_pointer)
        raise
    finally:
        await Tortoise.close_connections()


def publish_candidate(args: argparse.Namespace) -> dict[str, Any]:
    report_path = args.publish_report.resolve()
    report = _load_json(report_path)
    if report.get("status") != "staged":
        raise ValueError("只允许发布 status=staged 的候选报告")
    service = KnowledgeService()
    release_id = str((report.get("release") or {}).get("release_id") or "")
    if not release_id:
        raise ValueError("候选报告缺少 release_id")
    result = asyncio.run(
        _upsert_metadata(list(report["documents"]), service, release_id)
    )
    return {
        **report,
        "status": "published",
        "published_release_id": release_id,
        "previous_pointer": result["previous_pointer"],
        "reconciliation": result["reconciliation"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument(
        "--visibility", choices=("public", "internal"), default="public"
    )
    parser.add_argument(
        "--replace-catalog",
        action="store_true",
        help="候选 release 只包含冻结论文；默认保留当前知识库文档",
    )
    parser.add_argument(
        "--publish-report",
        type=Path,
        help="发布此前生成并完成评测的候选入库报告",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.publish_report is None and args.source_dir is None:
        parser.error("构建候选时必须传 --source-dir")
    if args.publish_report is not None and args.source_dir is not None:
        parser.error("--publish-report 与 --source-dir 不能同时使用")

    result = (
        publish_candidate(args)
        if args.publish_report is not None
        else build_candidate(args)
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({
        "status": result["status"],
        "output": str(output),
        "release_id": result.get("published_release_id")
        or result["release"]["release_id"],
        "collection_name": result["release"]["collection_name"],
        "corpus_documents": result["corpus_document_count"],
        "total_documents": result["release"]["document_count"],
        "nodes": result["release"]["node_count"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
