"""Run a synthetic, self-cleaning preflight against Qdrant Cloud."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "fastapi-app"
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_DIR / ".env")
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from qdrant_client import QdrantClient, models  # noqa: E402


def _cloud_config() -> tuple[str, str]:
    url = os.getenv("AI_QDRANT_URL", "").strip().rstrip("/")
    api_key = os.getenv("AI_QDRANT_API_KEY", "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise RuntimeError("Qdrant Cloud 预检只允许 HTTPS URL")
    if not parsed.hostname or not parsed.hostname.lower().endswith(
        ".cloud.qdrant.io"
    ):
        raise RuntimeError("Qdrant Cloud hostname 必须以 .cloud.qdrant.io 结尾")
    if not api_key:
        raise RuntimeError("AI_QDRANT_API_KEY 未配置")
    return url, api_key


def main() -> int:
    url, api_key = _cloud_config()
    collection_name = f"knowledge_cloud_preflight_{uuid.uuid4().hex}"
    client = QdrantClient(
        url=url,
        port=urlparse(url).port or 443,
        api_key=api_key,
        timeout=15.0,
        prefer_grpc=False,
    )
    created = False
    deleted = False
    try:
        service_info = client.info()
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(
                size=3,
                distance=models.Distance.COSINE,
            ),
        )
        created = True
        client.create_payload_index(
            collection_name=collection_name,
            field_name="doc_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
        client.upsert(
            collection_name=collection_name,
            wait=True,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=[1.0, 0.0, 0.0],
                    payload={"doc_id": "allowed", "content": "synthetic-a"},
                ),
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=[0.0, 1.0, 0.0],
                    payload={"doc_id": "denied", "content": "synthetic-b"},
                ),
            ],
        )
        response = client.query_points(
            collection_name=collection_name,
            query=[1.0, 0.0, 0.0],
            query_filter=models.Filter(must=[models.FieldCondition(
                key="doc_id",
                match=models.MatchAny(any=["allowed"]),
            )]),
            limit=5,
            with_payload=True,
        )
        if len(response.points) != 1:
            raise RuntimeError("Cloud 合成 ACL 过滤结果数量不一致")
        if (response.points[0].payload or {}).get("doc_id") != "allowed":
            raise RuntimeError("Cloud 合成 ACL 过滤发生越权命中")
        count = int(client.count(collection_name, exact=True).count)
        if count != 2:
            raise RuntimeError("Cloud 合成 Point 数量不一致")
        result = {
            "ok": True,
            "schema_version": "qdrant-cloud-preflight-v1",
            "server_version": str(service_info.version),
            "synthetic_point_count": count,
            "acl_filter": "passed",
            "collection_name": collection_name,
        }
    finally:
        try:
            if created and client.collection_exists(collection_name):
                client.delete_collection(collection_name)
                deleted = not client.collection_exists(collection_name)
        finally:
            client.close()
    if not deleted:
        raise RuntimeError(
            f"Cloud 合成 collection 未确认删除: {collection_name}"
        )
    result["collection_deleted"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
