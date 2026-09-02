"""Validate a Qdrant shadow release against its immutable manifest."""

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
    parser = argparse.ArgumentParser(description="校验 Qdrant 候选 Release")
    parser.add_argument("release_id")
    args = parser.parse_args()

    service = KnowledgeService()
    manifest = service.artifact_repository.releases.get(args.release_id)
    provider = str(
        (manifest.get("indexing") or {}).get("vector_store_provider") or ""
    )
    if provider != "qdrant":
        raise RuntimeError(f"候选 Release 不是 Qdrant: {provider!r}")
    if getattr(service.index_writer, "provider", None) != "qdrant":
        raise RuntimeError("请使用 AI_VECTOR_STORE_PROVIDER=qdrant 运行校验")
    service._validate_shadow_manifest(manifest)
    validation = service.index_writer.validate_collection(
        collection_name=manifest["collection_name"],
        expected_node_ids=[
            str(node["node_id"])
            for document_id in manifest["document_ids"]
            for node in service.artifact_repository.documents.get(document_id)["nodes"]
        ],
        expected_dimension=int(manifest["embedding"]["dimension"]),
    )
    print(json.dumps({
        "ok": True,
        "release_id": manifest["release_id"],
        "collection_name": manifest["collection_name"],
        "validation": validation,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
