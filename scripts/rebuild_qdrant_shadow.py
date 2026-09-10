"""Rebuild a Qdrant shadow release from DocStore without publishing it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.knowledge_service import KnowledgeService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 DocStore 全量重建未发布 Qdrant 影子索引"
    )
    parser.add_argument(
        "--discard",
        action="store_true",
        help="验证 Manifest 后删除未发布候选集合",
    )
    args = parser.parse_args()

    service = KnowledgeService()
    if service.target_vector_store_provider != "qdrant":
        raise RuntimeError(
            "必须显式配置 AI_VECTOR_STORE_PROVIDER=qdrant；"
            "工具不会隐式改变目标 provider"
        )
    manifest = service.rebuild_shadow_from_active()
    service._validate_shadow_manifest(manifest)
    result = {
        "ok": True,
        "published": False,
        "release_id": manifest["release_id"],
        "collection_name": manifest["collection_name"],
        "provider": manifest["indexing"]["vector_store_provider"],
        "document_count": manifest["document_count"],
        "node_count": manifest["node_count"],
        "embedding": manifest["embedding"],
        "indexing": manifest["indexing"],
    }
    if args.discard:
        result["discarded"] = service.discard_staged_release(
            manifest["release_id"]
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
