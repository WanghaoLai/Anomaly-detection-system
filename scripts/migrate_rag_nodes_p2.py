"""把当前 DocStore 文档确定性重新解析为 P2 LlamaIndex TextNode。

默认只构建影子索引。``--publish`` 在同一 MySQL 事务内更新文档
指针、切换发布指针并执行四方对账；任一步失败都会回滚 SQL
和发布指针。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import Knowledge  # noqa: E402
from services.knowledge_service import KnowledgeService  # noqa: E402
from settings import TORTOISE_ORM  # noqa: E402
from tortoise import Tortoise  # noqa: E402
from tortoise.transactions import in_transaction  # noqa: E402


def _row_payload(row) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "original_name": row.original_name,
        "file_size": row.file_size,
        "chunk_count": row.chunk_count,
    }


async def _publish(service: KnowledgeService, migration: dict) -> dict:
    manifest = migration["release"]
    previous_pointer = None
    published = False
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        async with in_transaction() as connection:
            for item in migration["documents"]:
                rows = await Knowledge.filter(
                    original_name=item["source_filename"]
                ).using_db(connection).order_by("id")
                if len(rows) != 1:
                    raise RuntimeError(
                        f"MySQL 文档数量异常: {item['source_filename']}={len(rows)}"
                    )
                row = rows[0]
                if row.filename != item["old_document_id"]:
                    raise RuntimeError(
                        f"MySQL 文档指针已变更: {item['source_filename']}"
                    )
                await Knowledge.filter(id=row.id).using_db(connection).update(
                    filename=item["new_document_id"],
                    file_size=item["file_size"],
                    chunk_count=item["new_node_count"],
                )

            previous_pointer = service.publish_staged_release(manifest["release_id"])
            published = True
            projected_rows = await Knowledge.all().using_db(connection).order_by("id")
            reconciliation = service.reconcile_metadata(
                [_row_payload(row) for row in projected_rows]
            )
            if not reconciliation["healthy"]:
                raise RuntimeError(
                    "P2 发布前四方对账失败: "
                    + json.dumps(reconciliation["issues"], ensure_ascii=False)
                )
        return reconciliation
    except Exception:
        if published:
            rollback_ok = service.rollback_published_release(
                manifest["release_id"], previous_pointer
            )
            if not rollback_ok:
                raise RuntimeError("P2 发布失败且指针回滚失败")
        raise
    finally:
        await Tortoise.close_connections()


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 RAG P2 LlamaIndex TextNode")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="在 MySQL 事务内对账并原子发布",
    )
    parser.add_argument(
        "--discard",
        action="store_true",
        help="验证后删除未发布影子 collection",
    )
    args = parser.parse_args()
    if args.publish and args.discard:
        parser.error("--publish 与 --discard 不能同时使用")

    service = KnowledgeService()
    migration = service.stage_node_parser_migration()
    result = {
        "ok": True,
        "published": False,
        "migration": migration,
    }
    if args.publish:
        result["reconciliation"] = asyncio.run(_publish(service, migration))
        result["published"] = True
    elif args.discard:
        result["discarded"] = service.discard_staged_release(
            migration["release"]["release_id"]
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
