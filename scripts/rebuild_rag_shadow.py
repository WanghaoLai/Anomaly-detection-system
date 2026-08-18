"""从 DocStore 全量重建 P3 LlamaIndex 影子索引。

默认只构建候选 collection，不改变在线发布指针。使用
``--publish`` 时，发布后会立即执行 MySQL/原文件/DocStore/Chroma
四方对账；任一检查失败将恢复原指针。
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


async def _load_mysql_documents() -> list[dict]:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        rows = await Knowledge.all().order_by("id")
        return [{
            "id": row.id,
            "filename": row.filename,
            "original_name": row.original_name,
            "file_size": row.file_size,
            "chunk_count": row.chunk_count,
        } for row in rows]
    finally:
        await Tortoise.close_connections()


def main() -> int:
    parser = argparse.ArgumentParser(description="全量重建 RAG P3 影子索引")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="校验后原子发布；默认仅构建影子索引",
    )
    parser.add_argument(
        "--discard",
        action="store_true",
        help="验证后删除未发布影子 collection",
    )
    parser.add_argument(
        "--release-id",
        default=None,
        help="复用已验收的影子 release，避免重复 embedding",
    )
    args = parser.parse_args()
    if args.publish and args.discard:
        parser.error("--publish 与 --discard 不能同时使用")

    service = KnowledgeService()
    manifest = (
        service.artifact_repository.releases.get(args.release_id)
        if args.release_id
        else service.rebuild_shadow_from_active()
    )
    result = {
        "ok": True,
        "published": False,
        "release": manifest,
    }
    if args.publish:
        previous = None
        published = False
        try:
            previous = service.publish_staged_release(manifest["release_id"])
            published = True
            mysql_documents = asyncio.run(_load_mysql_documents())
            reconciliation = service.reconcile_metadata(mysql_documents)
            if not reconciliation["healthy"]:
                raise RuntimeError(
                    "发布后四方对账失败: "
                    + json.dumps(reconciliation["issues"], ensure_ascii=False)
                )
            result.update({"published": True, "reconciliation": reconciliation})
        except Exception:
            if published:
                service.rollback_published_release(manifest["release_id"], previous)
            raise
    elif args.discard:
        result["discarded"] = service.discard_staged_release(manifest["release_id"])

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
